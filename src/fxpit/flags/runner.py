"""Run detectors and build bars.

Detector runs are idempotent by delete-then-insert, scoped to
(flag, instrument, time range). This is the opposite of the ingest strategy and
deliberately so:

  ingest   never re-fetches a settled hour  — raw data is expensive and immutable
  flags    always recomputes its own scope   — flags are cheap and disposable

"Delete the flags and re-run" is the sanctioned correction path for a wrong
detector, so the code makes it the *only* path rather than an exceptional one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from clickhouse_connect.driver.client import Client

from fxpit.config import ROOT
from fxpit.flags import detectors as det
from fxpit.ingest import store

BARS_SCHEMA = ROOT / "infra" / "clickhouse" / "init" / "02_bars.sql"


@dataclass
class ScanReport:
    flags_written: dict[str, int] = field(default_factory=dict)
    flags_deleted: int = 0
    skipped_blocked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.flags_written.values())

    def line(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.flags_written.items()) if v]
        return f"flags={self.total:,}  " + "  ".join(parts) if parts else "flags=0"


def ensure_flag_schema(client: Client) -> None:
    """`tick_flag` ships in 01_schema.sql; this is a no-op safety net for a
    database created before that file existed.
    """
    client.command(
        """
        CREATE TABLE IF NOT EXISTS tick_flag (
          instrument LowCardinality(String),
          ts         DateTime64(3, 'UTC'),
          flag       LowCardinality(String),
          detail     String
        ) ENGINE = MergeTree
        PARTITION BY (instrument, toYYYYMM(ts))
        ORDER BY (instrument, ts, flag)
        """
    )


def ensure_bars(client: Client) -> None:
    """Create the bar table, materialised view and read view."""
    sql = Path(BARS_SCHEMA).read_text(encoding="utf-8")
    for statement in _split_statements(sql):
        client.command(statement)


def _split_statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def backfill_bars(client: Client, instrument: str, start: datetime, end: datetime) -> int:
    """Replay existing ticks into the bar table.

    A materialised view only sees rows inserted AFTER it was created — it does
    not backfill. Every tick ingested in Phase 1 is therefore invisible to
    `bar_1m_mv` until replayed here. Forgetting this is the classic ClickHouse
    materialised-view mistake: the view exists, reports no error, and quietly
    covers only the future.

    Idempotent by deleting the range first, since re-running must not double
    the aggregates.
    """
    client.command(
        "ALTER TABLE bar_1m DELETE WHERE instrument = %(i)s "
        "AND minute >= %(start)s AND minute < %(end)s",
        parameters={"i": instrument, "start": start, "end": end},
    )
    client.command(
        """
        INSERT INTO bar_1m
        SELECT instrument, source, toStartOfMinute(ts) AS minute,
               argMinState(bid, ts), max(bid), min(bid), argMaxState(bid, ts),
               argMinState(ask, ts), max(ask), min(ask), argMaxState(ask, ts),
               count()
          FROM tick_raw
         WHERE instrument = %(i)s AND ts >= %(start)s AND ts < %(end)s
         GROUP BY instrument, source, minute
        """,
        parameters={"i": instrument, "start": start, "end": end},
    )
    result = client.query(
        "SELECT count() FROM bar_1m WHERE instrument = %(i)s "
        "AND minute >= %(start)s AND minute < %(end)s",
        parameters={"i": instrument, "start": start, "end": end},
    )
    return int(result.result_rows[0][0])


def scan(
    instruments: list[str],
    start: datetime,
    end: datetime,
    *,
    only: list[str] | None = None,
    progress: bool = True,
) -> ScanReport:
    """Run every runnable detector over [start, end) for each instrument."""
    report = ScanReport()
    client = store.connect()
    try:
        ensure_flag_schema(client)
        chosen = det.RUNNABLE
        if only:
            chosen = [d for d in det.ALL if d.name in only]
            for d in chosen:
                if d.blocked:
                    report.skipped_blocked.append(d.name)
            chosen = [d for d in chosen if not d.blocked]

        for d in det.BLOCKED:
            if d.name not in report.skipped_blocked:
                report.skipped_blocked.append(d.name)

        for instrument in instruments:
            for d in chosen:
                try:
                    written = _run_one(client, d, instrument, start, end, report)
                    if progress:
                        print(f"  {instrument:<9} {d.name:<16} {written:>8,}")
                except Exception as exc:
                    report.errors.append(f"{instrument} {d.name}: {exc}")
                    if progress:
                        print(f"  {instrument:<9} {d.name:<16}    ERROR  {exc}")
    finally:
        client.close()
    return report


def _run_one(
    client: Client,
    d: det.Detector,
    instrument: str,
    start: datetime,
    end: datetime,
    report: ScanReport,
) -> int:
    # Delete this detector's own prior flags for the scope, then recompute.
    # Scoped to the flag name so re-running one detector never disturbs another.
    client.command(
        "ALTER TABLE tick_flag DELETE WHERE instrument = %(i)s AND flag = %(f)s "
        "AND ts >= %(start)s AND ts < %(end)s",
        parameters={"i": instrument, "f": d.name, "start": start, "end": end},
        settings={"mutations_sync": 1},
    )
    client.command(
        f"INSERT INTO tick_flag (instrument, ts, flag, detail) {d.sql}",
        parameters={"instrument": instrument, "start": start, "end": end},
    )
    result = client.query(
        "SELECT count() FROM tick_flag WHERE instrument = %(i)s AND flag = %(f)s "
        "AND ts >= %(start)s AND ts < %(end)s",
        parameters={"i": instrument, "f": d.name, "start": start, "end": end},
    )
    written = int(result.result_rows[0][0])
    report.flags_written[d.name] = report.flags_written.get(d.name, 0) + written
    return written
