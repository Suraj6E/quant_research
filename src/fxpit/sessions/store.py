"""Materialise session windows into Postgres, and query them.

Windows are generated from local-time rules and written as TSTZRANGE rows.
Regenerating a range deletes it first, so a rule change is applied by re-running
rather than by patching rows — the same disposability principle as the Phase 2
flags, and for the same reason: this is derived data, cheap to rebuild.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import psycopg

from fxpit.config import ROOT, settings
from fxpit.sessions import definitions as defs
from fxpit.sessions import holidays as hol

SCHEMA_FILE = ROOT / "infra" / "postgres" / "init" / "04_sessions.sql"


@dataclass
class BuildReport:
    sessions: int = 0
    markets: int = 0
    rollovers: int = 0
    holidays: int = 0
    unmapped_currencies: list[str] = field(default_factory=list)
    holidays_available: bool = True


def connect() -> psycopg.Connection:
    return psycopg.connect(settings().pg_dsn)


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()


def build(conn: psycopg.Connection, start: date, end: date) -> BuildReport:
    """Generate every span in [start, end) and replace what is there."""
    ensure_schema(conn)
    report = BuildReport()

    with conn.cursor() as cur:
        # Delete by overlap, not by equality: a regenerated range must clear
        # whatever previously covered it, including windows that straddle the
        # boundary. Missing those would trip the exclusion constraint.
        for table in ("session_window", "market_window", "rollover_window"):
            cur.execute(
                f"DELETE FROM {table} WHERE span && tstzrange(%s, %s)",
                (_utc(start), _utc(end)),
            )
        cur.execute(
            "DELETE FROM currency_holiday WHERE holiday_date >= %s AND holiday_date < %s",
            (start, end),
        )

        rows = [
            (w.label, w.start, w.end, w.zone) for w in defs.session_windows(start, end)
        ]
        cur.executemany(
            "INSERT INTO session_window (session, span, local_zone) "
            "VALUES (%s, tstzrange(%s, %s, '[)'), %s)",
            rows,
        )
        report.sessions = len(rows)

        markets = [
            (w.start, w.end)
            for w in defs.market_windows(start, end)
            if w.end > _utc(start) and w.start < _utc(end)
        ]
        cur.executemany(
            "INSERT INTO market_window (span) VALUES (tstzrange(%s, %s, '[)')) "
            "ON CONFLICT DO NOTHING",
            markets,
        )
        report.markets = len(markets)

        rollovers = [(w.start, w.end) for w in defs.rollover_windows(start, end)]
        cur.executemany(
            "INSERT INTO rollover_window (span) VALUES (tstzrange(%s, %s, '[)')) "
            "ON CONFLICT DO NOTHING",
            rollovers,
        )
        report.rollovers = len(rollovers)

        report.holidays_available = hol.available()
        currencies = sorted({c for pair in defs.PAIR_LEGS.values() for c in pair})
        report.unmapped_currencies = hol.unmapped(currencies)
        holiday_rows = []
        for currency in currencies:
            for day, name in hol.for_currency(currency, start.year, end.year):
                if start <= day < end:
                    holiday_rows.append((currency, day, name))
        cur.executemany(
            "INSERT INTO currency_holiday (currency, holiday_date, name) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            holiday_rows,
        )
        report.holidays = len(holiday_rows)

    conn.commit()
    return report


def _utc(day: date) -> datetime:
    from datetime import UTC, time

    return datetime.combine(day, time(0, 0), tzinfo=UTC)


# --------------------------------------------------------------------------
# The exit criterion, as a query
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Moment:
    """Everything the session layer knows about one instant."""

    ts: datetime
    sessions: list[str]
    market_open: bool
    is_rollover: bool
    holidays: dict[str, str]
    pair: str | None = None

    @property
    def in_overlap(self) -> bool:
        return len(self.sessions) > 1

    def describe(self) -> str:
        parts = []
        parts.append("open" if self.market_open else "CLOSED")
        parts.append("+".join(self.sessions) if self.sessions else "no session")
        if self.is_rollover:
            parts.append("ROLLOVER")
        if self.holidays:
            parts.append(
                "holiday: " + ", ".join(f"{c} ({n})" for c, n in self.holidays.items())
            )
        return " | ".join(parts)


def describe(conn: psycopg.Connection, ts: datetime, pair: str | None = None) -> Moment:
    """PHASE 4 EXIT CRITERION.

    For any timestamp: which session, is it rollover, is it a holiday for
    either leg of the pair.
    """
    if ts.tzinfo is None:
        raise ValueError(
            "describe() needs a timezone-aware timestamp. A naive one would be "
            "read in local time and silently answer about a different instant."
        )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT session FROM session_window WHERE span @> %s ORDER BY session",
            (ts,),
        )
        sessions = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT EXISTS (SELECT 1 FROM market_window WHERE span @> %s)", (ts,))
        market_open = bool(cur.fetchone()[0])

        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM rollover_window WHERE span @> %s)", (ts,)
        )
        is_rollover = bool(cur.fetchone()[0])

        holidays: dict[str, str] = {}
        if pair:
            legs = defs.PAIR_LEGS.get(pair.upper())
            if legs:
                cur.execute(
                    "SELECT currency, name FROM currency_holiday "
                    " WHERE holiday_date = %s AND currency = ANY(%s)",
                    (ts.date(), list(legs)),
                )
                holidays = {r[0]: r[1] for r in cur.fetchall()}

    return Moment(ts, sessions, market_open, is_rollover, holidays, pair)


def coverage(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(lower(span)), max(upper(span)) FROM session_window"
        )
        n_sessions, first, last = cur.fetchone()
        cur.execute("SELECT count(*) FROM market_window")
        n_markets = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM rollover_window")
        n_rollovers = cur.fetchone()[0]
        cur.execute("SELECT count(*), count(DISTINCT currency) FROM currency_holiday")
        n_holidays, n_currencies = cur.fetchone()
    return {
        "sessions": n_sessions,
        "markets": n_markets,
        "rollovers": n_rollovers,
        "holidays": n_holidays,
        "currencies": n_currencies,
        "first": first,
        "last": last,
    }


def holidays_by_currency(conn: psycopg.Connection, limit: int = 40) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT currency, count(*) AS days, min(holiday_date) AS first, "
            "       max(holiday_date) AS last "
            "  FROM currency_holiday GROUP BY currency ORDER BY currency LIMIT %s",
            (limit,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
