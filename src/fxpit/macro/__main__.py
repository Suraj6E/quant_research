"""Bitemporal macro CLI."""

from __future__ import annotations

import argparse
import sys

from fxpit.macro import loader, rtdsm


def _cmd_load(series_filter: list[str] | None) -> int:
    chosen = [s for s in rtdsm.CATALOGUE if not series_filter or s.series_id in series_filter]
    if not chosen:
        print(f"No series matched. Known: {[s.series_id for s in rtdsm.CATALOGUE]}")
        return 1

    conn = loader.connect()
    try:
        loader.ensure_schema(conn)
        failures = 0
        for series in chosen:
            print(f"{series.series_id}: {series.description}")
            try:
                payload = rtdsm.download(series)
                print(f"  downloaded {len(payload):,} bytes")
                observations = rtdsm.parse(payload, series.series_id)
                print(f"  parsed {len(observations):,} observations")
                report = loader.load(conn, observations)
                print(f"  loaded  {report.inserted:,} rows  "
                      f"{report.ref_periods:,} periods x {report.vintages:,} vintages  "
                      f"{report.first_vintage} .. {report.last_vintage}")
            except Exception as exc:
                failures += 1
                print(f"  FAILED  {type(exc).__name__}: {exc}")
        return 1 if failures else 0
    finally:
        conn.close()


def _cmd_report() -> int:
    conn = loader.connect()
    try:
        loader.ensure_schema(conn)
        rows = loader.summary(conn)
        if not rows:
            print("No macro observations. Run --load first.")
            return 0
        print(f"{'series':<10}{'obs':>12}{'periods':>10}{'vintages':>10}"
              f"{'exact ts':>10}{'month ts':>10}  range")
        print("-" * 84)
        for r in rows:
            print(f"{r['series_id']:<10}{r['observations']:>12,}{r['ref_periods']:>10,}"
                  f"{r['vintages']:>10,}{r['exact_ts']:>10,}{r['month_ts']:>10,}  "
                  f"{r['first_period']} .. {r['last_period']}")
        print()
        total = sum(r["observations"] for r in rows)
        month = sum(r["month_ts"] for r in rows)
        print(f"{month:,} of {total:,} observations ({100 * month / total:.1f}%) carry a "
              f"month-precision timestamp.")
        print("Those are placed at the LAST instant of the vintage month, so queries")
        print("withhold rather than leak. Release times are not published by RTDSM;")
        print("assuming 08:30 ET would invent precision that was never measured.")
        return 0
    finally:
        conn.close()


def _cmd_revisions(limit: int) -> int:
    conn = loader.connect()
    try:
        rows = loader.revision_examples(conn, limit)
        if not rows:
            print("No revised periods found. Run --load first.")
            return 0
        print("Largest FIRST revisions: first print vs its very next vintage.")
        print("These are the numbers a backtest silently substitutes when it queries")
        print("a revised series instead of a point-in-time one.")
        print()
        print(f"{'series':<9}{'ref period':<13}{'first print':>13}{'next vintage':>14}"
              f"{'revision':>12}{'%':>9}")
        print("-" * 70)
        for r in rows:
            print(f"{r['series_id']:<9}{str(r['ref_period']):<13}"
                  f"{r['first_value']:>13,.1f}{r['later_value']:>14,.1f}"
                  f"{r['revision']:>12,.1f}{r['pct']:>9}")
        print()
        print("EXCLUDED from this ranking, deliberately:")
        for sid, why in sorted(loader.REBASED.items()):
            print(f"  {sid:<9} {why}")
        print()
        print("  A rebasing shifts every historical period at once, so a level")
        print("  difference across vintages measures the units change rather than new")
        print("  information: 1985Q3 real GNP reads 1,684.8 in 1982 dollars and 3,584.1")
        print("  after rebasing - a 113% 'revision' that is not a revision at all.")
        print("  Ranking those series by level would produce exactly the kind of")
        print("  plausible-looking wrong number this database exists to catch, so they")
        print("  are excluded rather than quietly included. Comparing them properly")
        print("  needs rebasing detection, which is not implemented.")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fxpit.macro", description=__doc__)
    p.add_argument("--load", action="store_true", help="download and load RTDSM vintages")
    p.add_argument("--series", nargs="+", metavar="ID",
                   help=f"subset of {[s.series_id for s in rtdsm.CATALOGUE]}")
    p.add_argument("--report", action="store_true", help="vintage coverage and precision")
    p.add_argument("--revisions", action="store_true",
                   help="largest first-print vs final gaps")
    p.add_argument("--limit", type=int, default=12)
    args = p.parse_args(argv)

    if args.load:
        return _cmd_load(args.series)
    if args.report:
        return _cmd_report()
    if args.revisions:
        return _cmd_revisions(args.limit)
    p.error("choose --load, --report or --revisions")
    return 2


if __name__ == "__main__":
    sys.exit(main())
