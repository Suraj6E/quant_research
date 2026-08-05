"""Ingest CLI.

    python -m fxpit.ingest --instruments EURUSD --start 2024-01-08 --end 2024-01-10
    python -m fxpit.ingest --majors --start 2024-01-08 --end 2024-01-09
    python -m fxpit.ingest --report
    python -m fxpit.ingest --verify-idempotent --instruments EURUSD \
        --start 2024-01-08 --end 2024-01-10
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from fxpit.ingest import ledger, store
from fxpit.ingest.dukascopy import MAJORS
from fxpit.ingest.runner import ingest


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)


def _print_report() -> int:
    pg = ledger.connect()
    ch = store.connect()
    try:
        ledger.ensure_schema(pg)
        rows = ledger.summary(pg)
        if not rows:
            print("Ledger is empty — nothing has been ingested yet.")
            return 0

        print(f"{'instrument':<11}{'ok':>7}{'empty':>7}{'missing':>9}{'error':>7}"
              f"{'ticks':>14}  range")
        print("-" * 78)
        for r in rows:
            span = f"{r['first_hour']:%Y-%m-%d %H}Z .. {r['last_hour']:%Y-%m-%d %H}Z"
            print(f"{r['instrument']:<11}{r['ok']:>7}{r['empty']:>7}{r['missing']:>9}"
                  f"{r['error']:>7}{r['ticks']:>14,}  {span}")

        print()
        print("tick_raw contents (ClickHouse):")
        ch_rows = store.rows_by_instrument(ch)
        if not ch_rows:
            print("  (empty)")
        for r in ch_rows:
            print(f"  {r['instrument']:<9}{r['ticks']:>12,} ticks   "
                  f"crossed={r['crossed']}   mean spread={r['mean_spread']}")
        print(f"  {'TOTAL':<9}{store.total_rows(ch):>12,} ticks")

        # Ledger and store must agree. A mismatch means rows were written
        # without being recorded, or recorded without being written.
        ledger_ticks = sum(r["ticks"] for r in rows)
        store_ticks = store.total_rows(ch)
        print()
        if ledger_ticks == store_ticks:
            print(f"OK  ledger and tick_raw agree ({ledger_ticks:,} rows)")
        else:
            print(f"MISMATCH  ledger says {ledger_ticks:,}, tick_raw holds {store_ticks:,}")
            return 1
        return 0
    finally:
        pg.close()
        ch.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fxpit.ingest", description=__doc__)
    p.add_argument("--instruments", nargs="+", metavar="PAIR")
    p.add_argument("--majors", action="store_true", help=f"shorthand for {' '.join(MAJORS)}")
    p.add_argument("--start", type=_day, metavar="YYYY-MM-DD")
    p.add_argument("--end", type=_day, metavar="YYYY-MM-DD", help="exclusive")
    p.add_argument("--workers", type=int, default=2,
                   help="concurrent fetches; >2 draws HTTP 503 throttling (measured)")
    p.add_argument("--pause", type=float, default=0.25, help="courtesy delay per request")
    p.add_argument("--dry-run", action="store_true", help="show what would be fetched")
    p.add_argument("--report", action="store_true", help="coverage report, then exit")
    p.add_argument("--verify-idempotent", action="store_true",
                   help="ingest, then ingest again and assert the second run is a no-op")
    args = p.parse_args(argv)

    if args.report:
        return _print_report()

    instruments = MAJORS if args.majors else (args.instruments or [])
    if not instruments or not args.start or not args.end:
        p.error("need --instruments (or --majors) with --start and --end")
    if args.end <= args.start:
        p.error("--end must be after --start")

    first = ingest(instruments, args.start, args.end, workers=args.workers,
                   pause=args.pause, dry_run=args.dry_run)
    print()
    print(f"run 1: {first.line()}")
    for e in first.errors[:10]:
        print(f"  error: {e}")

    if not args.verify_idempotent:
        return 1 if first.errors else 0

    print()
    print("re-running the same range to verify idempotency...")
    second = ingest(instruments, args.start, args.end, workers=args.workers,
                    pause=args.pause, progress=False)
    print(f"run 2: {second.line()}")
    print()

    # This is the Phase 1 exit criterion, checked rather than asserted in prose.
    if second.is_noop:
        print("PASS  re-run produced zero new rows and zero errors")
        return 0
    print("FAIL  re-run was not a no-op — Phase 1 exit criterion not met")
    for e in second.errors[:10]:
        print(f"  error: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
