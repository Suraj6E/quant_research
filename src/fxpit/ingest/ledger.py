"""The ingest ledger — resumability and idempotency.

The Phase 1 exit criterion is that **a re-run produces zero new rows and zero
errors**. That is this module's job, and it is achieved by never fetching an
hour that is already settled rather than by de-duplicating afterwards. Dedup
after the fact would need either a mutable tick table (forbidden — `tick_raw`
is immutable) or an expensive scan of billions of rows.

CRASH SAFETY
------------
The ordering matters more than it looks. An hour is written as:

    1. claim   -> ledger row, status='in_progress'
    2. insert  -> ticks into ClickHouse
    3. settle  -> ledger row, status='ok'

If the process dies between 2 and 3, the ledger says `in_progress` while rows
may or may not be present. `reset_stale_claims()` therefore treats every
`in_progress` row as suspect on startup: it deletes any ticks for that hour and
clears the claim, so the hour is re-fetched cleanly.

The reverse ordering — settle before insert — would be much worse: a crash
would leave an hour permanently marked done with no data in it, and the ledger
would confidently report coverage that does not exist.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import psycopg

from fxpit.config import ROOT, settings
from fxpit.ingest.dukascopy import FetchStatus

SCHEMA_FILE = ROOT / "infra" / "postgres" / "init" / "02_ingest_ledger.sql"

# Hours in these states are finished. Anything else gets re-attempted.
SETTLED = ("ok", "empty", "missing")


def connect() -> psycopg.Connection:
    return psycopg.connect(settings().pg_dsn)


def ensure_schema(conn: psycopg.Connection | None = None) -> None:
    """Create the ledger table if absent.

    The DDL is read from the same file the container bootstrap runs, so there
    is one definition rather than two that can drift. Container init scripts
    only execute against an empty volume, and this project's database is
    already running with data — see docs/setup.md.
    """
    sql = Path(SCHEMA_FILE).read_text(encoding="utf-8")
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        if own:
            conn.close()


def reset_stale_claims(
    conn: psycopg.Connection, source: str = "dukascopy"
) -> list[tuple[str, datetime]]:
    """Clear `in_progress` rows left by a crashed run.

    Returns the (instrument, hour) pairs that were reset so the caller can
    delete any ticks those hours may have written before re-fetching.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT instrument, hour FROM ingest_ledger "
            "WHERE status = 'in_progress' AND source = %s",
            (source,),
        )
        stale = [(r[0], r[1]) for r in cur.fetchall()]
        if stale:
            cur.execute(
                "DELETE FROM ingest_ledger WHERE status = 'in_progress' AND source = %s",
                (source,),
            )
    conn.commit()
    return stale


def settled_hours(
    conn: psycopg.Connection,
    instrument: str,
    hours: Iterable[datetime],
    source: str = "dukascopy",
) -> set[datetime]:
    """Which of these hours are already finished and must not be re-fetched.

    This is the idempotency check. It is the reason a second run does no work
    rather than doing the same work and discarding it.
    """
    hours = list(hours)
    if not hours:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hour FROM ingest_ledger "
            "WHERE instrument = %s AND source = %s AND status = ANY(%s) "
            "AND hour = ANY(%s)",
            (instrument, source, list(SETTLED), hours),
        )
        return {row[0] for row in cur.fetchall()}


def claim(
    conn: psycopg.Connection,
    instrument: str,
    hour: datetime,
    source: str = "dukascopy",
) -> None:
    """Step 1: record intent before any data is written."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingest_ledger (instrument, hour, source, status)
            VALUES (%s, %s, %s, 'in_progress')
            ON CONFLICT (instrument, hour, source) DO UPDATE
              SET status = 'in_progress',
                  attempts = ingest_ledger.attempts + 1,
                  completed_at = NULL
            """,
            (instrument, hour, source),
        )
    conn.commit()


def settle(
    conn: psycopg.Connection,
    instrument: str,
    hour: datetime,
    status: FetchStatus,
    tick_count: int,
    bytes_downloaded: int,
    detail: str = "",
    source: str = "dukascopy",
) -> None:
    """Step 3: record the outcome, only after any rows are committed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingest_ledger
               SET status = %s, tick_count = %s, bytes_downloaded = %s,
                   detail = %s, completed_at = now()
             WHERE instrument = %s AND hour = %s AND source = %s
            """,
            (status.value, tick_count, bytes_downloaded, detail, instrument, hour, source),
        )
    conn.commit()


def summary(conn: psycopg.Connection, source: str = "dukascopy") -> list[dict]:
    """Per-instrument totals for the coverage report."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT instrument,
                   count(*) FILTER (WHERE status = 'ok')      AS ok,
                   count(*) FILTER (WHERE status = 'empty')   AS empty,
                   count(*) FILTER (WHERE status = 'missing') AS missing,
                   count(*) FILTER (WHERE status = 'error')   AS error,
                   coalesce(sum(tick_count), 0)               AS ticks,
                   min(hour) AS first_hour, max(hour) AS last_hour
              FROM ingest_ledger
             WHERE source = %s
             GROUP BY instrument
             ORDER BY instrument
            """,
            (source,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def monthly_coverage(conn: psycopg.Connection, source: str = "dukascopy") -> list[dict]:
    """Hours attempted and hours with data, by instrument-month."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT instrument,
                   to_char(date_trunc('month', hour), 'YYYY-MM') AS month,
                   count(*)                                   AS attempted,
                   count(*) FILTER (WHERE status = 'ok')      AS with_data,
                   count(*) FILTER (WHERE status = 'empty')   AS empty,
                   count(*) FILTER (WHERE status IN ('error','missing')) AS failed,
                   coalesce(sum(tick_count), 0)               AS ticks
              FROM ingest_ledger
             WHERE source = %s
             GROUP BY instrument, date_trunc('month', hour)
             ORDER BY instrument, month
            """,
            (source,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
