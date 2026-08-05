"""Ingest orchestration — resumable, idempotent, rate-limited.

The run loop is deliberately boring. Its only interesting property is the
ordering described in `ledger`: claim, insert, settle. Everything else is
bookkeeping in service of one guarantee, which is the phase's exit criterion:

    a second run over the same range fetches nothing, inserts nothing,
    and reports zero errors.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

from fxpit.ingest import ledger, store
from fxpit.ingest.dukascopy import FetchStatus, fetch_hour, hours_between


@dataclass
class RunReport:
    requested: int = 0
    skipped_settled: int = 0
    fetched: int = 0
    rows_inserted: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    stale_recovered: int = 0

    @property
    def is_noop(self) -> bool:
        """True when this run changed nothing — the exit criterion."""
        return self.fetched == 0 and self.rows_inserted == 0 and not self.errors

    def line(self) -> str:
        parts = [
            f"requested={self.requested}",
            f"skipped={self.skipped_settled}",
            f"fetched={self.fetched}",
            f"rows={self.rows_inserted:,}",
        ]
        for k in sorted(self.by_status):
            parts.append(f"{k}={self.by_status[k]}")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return "  ".join(parts)


def ingest(
    instruments: list[str],
    start: datetime,
    end: datetime,
    *,
    workers: int = 2,
    pause: float = 0.25,
    dry_run: bool = False,
    progress: bool = True,
) -> RunReport:
    """Ingest [start, end) for each instrument.

    `workers` is capped low on purpose, and the default was lowered from 4 to 2
    after measurement: four concurrent workers at a 0.15s pause drew a burst of
    HTTP 503s from the live feed. Being polite here is not just courtesy to a
    free service — a throttled run is slower end-to-end than a gentle one,
    because every 503 costs a multi-second backoff.
    """
    report = RunReport()
    pg = ledger.connect()
    ch = store.connect()

    try:
        ledger.ensure_schema(pg)

        # Recover from any previous crash before deciding what work remains.
        # A claimed-but-unsettled hour may have written rows; those rows are
        # removed so the re-fetch cannot duplicate them.
        stale = ledger.reset_stale_claims(pg)
        for instrument, hour in stale:
            if not dry_run:
                store.delete_hour(ch, instrument, hour)
        report.stale_recovered = len(stale)
        if stale and progress:
            print(f"recovered {len(stale)} interrupted hour(s) from a previous run")

        for instrument in instruments:
            hours = hours_between(start, end)
            report.requested += len(hours)

            settled = ledger.settled_hours(pg, instrument, hours)
            todo = [h for h in hours if h not in settled]
            report.skipped_settled += len(settled)

            if progress:
                print(
                    f"{instrument}: {len(hours)} hour(s), {len(settled)} already settled, "
                    f"{len(todo)} to fetch"
                )
            if dry_run or not todo:
                continue

            _run_instrument(pg, ch, instrument, todo, workers, pause, report, progress)

    finally:
        pg.close()
        ch.close()

    return report


def _run_instrument(pg, ch, instrument, todo, workers, pause, report, progress) -> None:
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_hour, instrument, hour, pause=pause): hour for hour in todo
        }
        for future in as_completed(futures):
            hour = futures[future]
            report.fetched += 1
            done += 1

            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                report.errors.append(f"{instrument} {hour:%Y-%m-%d %H}Z: {exc}")
                continue

            # claim -> insert -> settle. Never reorder these.
            ledger.claim(pg, instrument, hour)
            inserted = 0
            try:
                if result.ticks:
                    inserted = store.insert_ticks(ch, result.ticks)
                    report.rows_inserted += inserted
            except Exception as exc:
                store.delete_hour(ch, instrument, hour)
                ledger.settle(pg, instrument, hour, FetchStatus.ERROR, 0, 0, str(exc))
                report.errors.append(f"{instrument} {hour:%Y-%m-%d %H}Z insert: {exc}")
                continue

            ledger.settle(
                pg, instrument, hour, result.status, inserted,
                result.bytes_downloaded, result.detail,
            )
            report.by_status[result.status.value] = (
                report.by_status.get(result.status.value, 0) + 1
            )
            if result.status is FetchStatus.ERROR:
                report.errors.append(
                    f"{instrument} {hour:%Y-%m-%d %H}Z: {result.detail}"
                )

            if progress and done % 25 == 0:
                print(f"  {instrument}: {done}/{len(todo)} hours")
