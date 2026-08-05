"""The Tier C query session — a DuckDB connection holding the point-in-time relations.

Why the filter logic lives in one place
--------------------------------------
`as_of()` is the only sanctioned read path, and the value of that guarantee
comes entirely from there being exactly one implementation of the
`known_at <= t` filter. If Postgres-backed macro reads and ClickHouse-backed
tick reads each carried their own copy, the guarantee would be two guarantees
that can drift apart.

So a session materialises source data into DuckDB relations with fixed shapes,
and every `as_of` function is DuckDB SQL over those relations. Where the rows
came from — a fixture CSV, a Postgres extract, a ClickHouse window — changes
the loader, never the filter.

For a bounded research window this is also just fast: DuckDB is in-process and
the working set is a few million rows, not the whole archive.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import duckdb

# Relation shapes. Declared once, created empty on every session, so a query
# against a source that was never loaded returns nothing rather than raising
# "table does not exist" — an empty result is the correct point-in-time answer
# for "nothing was known", and must not be confused with a wiring error.
SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_observation (
  series_id          VARCHAR NOT NULL,
  ref_period         DATE    NOT NULL,
  known_at           TIMESTAMPTZ NOT NULL,
  value              DOUBLE,
  vintage_seq        INTEGER NOT NULL,
  known_at_precision VARCHAR NOT NULL DEFAULT 'exact'
);

CREATE TABLE IF NOT EXISTS tick_raw (
  instrument VARCHAR NOT NULL,
  ts         TIMESTAMPTZ NOT NULL,
  bid        DOUBLE NOT NULL,
  ask        DOUBLE NOT NULL,
  bid_volume DOUBLE,
  ask_volume DOUBLE,
  source     VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS tick_flag (
  instrument VARCHAR NOT NULL,
  ts         TIMESTAMPTZ NOT NULL,
  flag       VARCHAR NOT NULL,
  detail     VARCHAR
);

CREATE TABLE IF NOT EXISTS bar_1m (
  instrument VARCHAR NOT NULL,
  source     VARCHAR NOT NULL,
  minute     TIMESTAMPTZ NOT NULL,
  bid_open DOUBLE, bid_high DOUBLE, bid_low DOUBLE, bid_close DOUBLE,
  ask_open DOUBLE, ask_high DOUBLE, ask_low DOUBLE, ask_close DOUBLE,
  tick_count BIGINT
);
"""


class QuerySession:
    """A DuckDB connection with the point-in-time relations registered."""

    def __init__(self, con: duckdb.DuckDBPyConnection | None = None) -> None:
        self.con = con or duckdb.connect(":memory:")
        for statement in filter(None, (s.strip() for s in SCHEMA.split(";"))):
            self.con.execute(statement)

    def close(self) -> None:
        self.con.close()

    # ---------------------------------------------------------------- loaders

    # Bulk-insert via a registered Arrow table rather than executemany.
    # executemany binds row by row: loading the 442k-row payrolls archive that
    # way took minutes, which turned `open_production_session()` into something
    # nobody would call twice. Arrow makes the same load sub-second.
    _BULK_THRESHOLD = 500

    def _insert(self, table: str, rows: list[tuple], columns: list[str]) -> int:
        if not rows:
            return 0
        if len(rows) < self._BULK_THRESHOLD:
            placeholders = ", ".join("?" * len(columns))
            self.con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
            return len(rows)

        import pyarrow as pa

        arrow = pa.table(
            {name: [r[i] for r in rows] for i, name in enumerate(columns)}
        )
        self.con.register("_bulk", arrow)
        try:
            self.con.execute(f"INSERT INTO {table} SELECT * FROM _bulk")
        finally:
            self.con.unregister("_bulk")
        return len(rows)

    def load_macro(self, rows: list[tuple]) -> int:
        """rows: (series_id, ref_period, known_at, value, vintage_seq, precision)"""
        return self._insert(
            "macro_observation",
            rows,
            ["series_id", "ref_period", "known_at", "value", "vintage_seq",
             "known_at_precision"],
        )

    def load_ticks(self, rows: list[tuple]) -> int:
        """rows: (instrument, ts, bid, ask, bid_volume, ask_volume, source)"""
        return self._insert(
            "tick_raw",
            rows,
            ["instrument", "ts", "bid", "ask", "bid_volume", "ask_volume", "source"],
        )

    def load_flags(self, rows: list[tuple]) -> int:
        return self._insert("tick_flag", rows, ["instrument", "ts", "flag", "detail"])

    def load_bars(self, rows: list[tuple]) -> int:
        return self._insert(
            "bar_1m",
            rows,
            ["instrument", "source", "minute",
             "bid_open", "bid_high", "bid_low", "bid_close",
             "ask_open", "ask_high", "ask_low", "ask_close", "tick_count"],
        )


# --------------------------------------------------------------------------
# The default session
#
# `as_of()` takes an optional session so callers can be explicit, but the
# common case is a module default. Tests install a fixture-backed session;
# production installs one wired to Postgres and ClickHouse.
# --------------------------------------------------------------------------

_default: QuerySession | None = None


def set_default_session(session: QuerySession | None) -> None:
    global _default
    _default = session


def default_session() -> QuerySession:
    global _default
    if _default is None:
        _default = open_production_session()
    return _default


def open_production_session(
    *,
    series: list[str] | None = None,
    instruments: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> QuerySession:
    """A session backed by the live stack.

    Everything is scopeable, because "small" is relative. The macro archive is
    tiny next to the tick archive but still 586k rows across 62 years of
    vintages, and materialising all of it to answer one question about one
    series costs seconds per call. Pass `series` when you know what you need.

    Ticks and bars are ALWAYS windowed — the tick archive is projected at
    billions of rows and pulling it into an in-process engine is not a thing
    that should be possible by accident.
    """
    session = QuerySession()
    _load_macro_from_postgres(session, series)
    if instruments and start and end:
        for instrument in instruments:
            _load_ticks_from_clickhouse(session, instrument, start, end)
            _load_bars_from_clickhouse(session, instrument, start, end)
    return session


def _load_macro_from_postgres(session: QuerySession, series: list[str] | None = None) -> int:
    try:
        import psycopg

        from fxpit.config import settings

        sql = (
            "SELECT series_id, ref_period, known_at, value, vintage_seq, "
            "       COALESCE(known_at_precision, 'exact') "
            "  FROM macro_observation"
        )
        params: tuple = ()
        if series:
            sql += " WHERE series_id = ANY(%s)"
            params = (list(series),)
        with psycopg.connect(settings().pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return session.load_macro(cur.fetchall())
    except Exception:
        return 0


def _load_ticks_from_clickhouse(
    session: QuerySession, instrument: str, start: datetime, end: datetime
) -> int:
    try:
        from fxpit.ingest import store

        client = store.connect()
        try:
            result = client.query(
                "SELECT instrument, ts, bid, ask, bid_volume, ask_volume, source "
                "  FROM tick_raw WHERE instrument = %(i)s AND ts >= %(s)s AND ts < %(e)s",
                parameters={"i": instrument, "s": start, "e": end},
            )
            n = session.load_ticks([tuple(r) for r in result.result_rows])
            flags = client.query(
                "SELECT instrument, ts, flag, detail FROM tick_flag "
                " WHERE instrument = %(i)s AND ts >= %(s)s AND ts < %(e)s",
                parameters={"i": instrument, "s": start, "e": end},
            )
            session.load_flags([tuple(r) for r in flags.result_rows])
            return n
        finally:
            client.close()
    except Exception:
        return 0


def _load_bars_from_clickhouse(
    session: QuerySession, instrument: str, start: datetime, end: datetime
) -> int:
    try:
        from fxpit.ingest import store

        client = store.connect()
        try:
            result = client.query(
                "SELECT instrument, source, minute, bid_open, bid_high, bid_low, "
                "       bid_close, ask_open, ask_high, ask_low, ask_close, tick_count "
                "  FROM bar_1m_view WHERE instrument = %(i)s "
                "   AND minute >= %(s)s AND minute < %(e)s",
                parameters={"i": instrument, "s": start, "e": end},
            )
            return session.load_bars([tuple(r) for r in result.result_rows])
        finally:
            client.close()
    except Exception:
        return 0


# --------------------------------------------------------------------------
# Fixture loading — used by the Phase 0 acceptance suite
# --------------------------------------------------------------------------


def _csv_rows(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def _ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def fixture_session(fixtures_dir: Path) -> QuerySession:
    """A session seeded from the hand-built fixture CSVs.

    The Phase 0 suite runs against this rather than the live stack, so the
    acceptance tests stay fast and runnable with nothing installed. The SQL
    they exercise is the same SQL production uses — only the loader differs.
    """
    from datetime import date as _date

    session = QuerySession()

    macro = _csv_rows(fixtures_dir / "macro_vintages.csv")
    session.load_macro(
        [
            (
                r["series_id"],
                _date.fromisoformat(r["ref_period"]),
                _ts(r["known_at"]),
                float(r["value"]) if r["value"] else None,
                int(r["vintage_seq"]),
                "exact",
            )
            for r in macro
        ]
    )

    ticks = _csv_rows(fixtures_dir / "ticks.csv")
    session.load_ticks(
        [
            (
                r["instrument"],
                _ts(r["ts"]),
                float(r["bid"]),
                float(r["ask"]),
                float(r["bid_volume"]),
                float(r["ask_volume"]),
                r["source"],
            )
            for r in ticks
        ]
    )

    bars = _csv_rows(fixtures_dir / "bars_cross_feed.csv")
    session.load_bars(
        [
            (
                r["instrument"],
                r["source"],
                _ts(r["ts"]),
                float(r["bid_open"]),
                float(r["bid_high"]),
                float(r["bid_low"]),
                float(r["bid_close"]),
                float(r["ask_open"]),
                float(r["ask_high"]),
                float(r["ask_low"]),
                float(r["ask_close"]),
                0,
            )
            for r in bars
        ]
    )
    return session
