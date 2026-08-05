"""Cleaning-layer CLI."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from fxpit.flags import detectors as det
from fxpit.flags import report, runner
from fxpit.ingest import store


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)


def _cmd_report() -> int:
    client = store.connect()
    try:
        share = report.flagged_share(client)
        print("FLAGGED SHARE")
        print(f"  {share['flagged_ticks']:,} of {share['ticks']:,} ticks carry at least "
              f"one flag  ({share['pct']}%)")
        print("  (distinct ticks, not flag rows - a tick can carry several)")

        print()
        print("FLAGS BY INSTRUMENT")
        rows = report.flag_totals(client)
        if not rows:
            print("  none - run --scan first")
        else:
            print(f"  {'instrument':<11}{'flag':<18}{'flags':>10}{'% of ticks':>12}")
            for r in rows:
                print(f"  {r['instrument']:<11}{r['flag']:<18}{r['flags']:>10,}"
                      f"{r['pct_of_ticks']:>12}")

        print()
        print("BARS")
        for r in report.bar_coverage(client):
            print(f"  {r['instrument']:<9}{r['source']:<12}{r['bars']:>8,} bars   "
                  f"{r['first_minute']} .. {r['last_minute']}")
        rec = report.bars_reconcile(client)
        if rec["agree"]:
            print(f"  OK  bars account for all {rec['ticks']:,} ticks")
        else:
            print(f"  MISMATCH  {rec['ticks']:,} ticks vs {rec['ticks_in_bars']:,} in bars")
            print("  (a materialised view does not backfill - run --bars)")

        print()
        print("NOT YET IMPLEMENTABLE")
        for d in det.BLOCKED:
            print(f"  {d.name:<18} {d.caveat}")
        return 0 if rec["agree"] else 1
    finally:
        client.close()


def _cmd_explain(instrument: str, day_text: str) -> int:
    day = datetime.strptime(day_text, "%Y-%m-%d").date()
    client = store.connect()
    try:
        rows = report.explain_day(client, instrument, day)
        print(f"Flagged ticks for {instrument} on {day}: {len(rows):,}")
        if not rows:
            print("  none")
            return 0
        print()
        print(f"  {'timestamp':<26}{'bid':>11}{'ask':>11}{'spread':>11}  flags")
        for r in rows:
            flags = ",".join(r["flags"])
            print(f"  {str(r['ts']):<26}{r['bid']:>11.5f}{r['ask']:>11.5f}"
                  f"{r['spread']:>11.6f}  {flags}")
            for flag, detail in zip(r["flags"], r["details"], strict=True):
                print(f"      {flag}: {detail}")
        return 0
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fxpit.flags", description=__doc__)
    p.add_argument("--scan", action="store_true", help="run detectors over a range")
    p.add_argument("--bars", action="store_true", help="create and backfill bar views")
    p.add_argument("--report", action="store_true", help="data-quality report")
    p.add_argument("--explain", nargs=2, metavar=("INSTRUMENT", "YYYY-MM-DD"),
                   help="every flagged tick for one instrument-day, and why")
    p.add_argument("--instruments", nargs="+", metavar="PAIR")
    p.add_argument("--start", type=_day, metavar="YYYY-MM-DD")
    p.add_argument("--end", type=_day, metavar="YYYY-MM-DD", help="exclusive")
    p.add_argument("--only", nargs="+", metavar="FLAG",
                   help=f"subset of {[d.name for d in det.RUNNABLE]}")
    args = p.parse_args(argv)

    if args.report:
        return _cmd_report()
    if args.explain:
        return _cmd_explain(args.explain[0], args.explain[1])

    if not (args.scan or args.bars):
        p.error("choose --scan, --bars, --report or --explain")
    if not args.instruments or not args.start or not args.end:
        p.error("--scan/--bars need --instruments, --start and --end")

    exit_code = 0

    if args.bars:
        client = store.connect()
        try:
            runner.ensure_bars(client)
            print("bar table, materialised view and read view ready")
            for inst in args.instruments:
                n = runner.backfill_bars(client, inst, args.start, args.end)
                print(f"  {inst:<9}{n:>8,} bars")
            rec = report.bars_reconcile(client)
            print(f"  ticks={rec['ticks']:,}  in bars={rec['ticks_in_bars']:,}  "
                  f"{'OK' if rec['agree'] else 'MISMATCH'}")
        finally:
            client.close()

    if args.scan:
        print(f"scanning {len(args.instruments)} instrument(s), "
              f"{args.start:%Y-%m-%d} to {args.end:%Y-%m-%d}")
        rep = runner.scan(args.instruments, args.start, args.end, only=args.only)
        print()
        print(rep.line())
        if rep.skipped_blocked:
            print(f"not implementable yet: {', '.join(sorted(set(rep.skipped_blocked)))}")
        for e in rep.errors:
            print(f"  error: {e}")
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
