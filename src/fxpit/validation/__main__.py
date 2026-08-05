"""Validation harness CLI."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from fxpit.validation import ecb, harness


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d")


def _cmd_load_ecb() -> int:
    conn = harness.connect()
    try:
        n = harness.load_ecb(conn)
        cov = harness.ecb_coverage(conn)
        print(f"loaded {n:,} reference rates")
        print(f"  {cov['currencies']} currencies x {cov['days']:,} days")
        print(f"  {cov['first']} .. {cov['last']}")
        return 0
    finally:
        conn.close()


def _cmd_drift(instrument: str, start, end) -> int:
    conn = harness.connect()
    try:
        rep = harness.run_drift_anchor(conn, instrument, start.date(), end.date())
        print(f"DRIFT ANCHOR  {instrument}  {start:%Y-%m-%d} .. {end:%Y-%m-%d}")
        print()
        print(f"  compared          {rep.compared:>8,}")
        print(f"  no ticks in window{rep.skipped_no_ticks:>8,}")
        print(f"  no ECB fix        {rep.skipped_no_fix:>8,}")
        if rep.compared:
            print(f"  mean difference   {rep.mean_pips:>8} pips")
            print(f"  largest |diff|    {rep.max_abs_pips:>8} pips")
            print(f"  over {harness.DRIFT_ALERT_PIPS} pips     {rep.alerts:>8,}")
            print()
            if abs(rep.mean_pips) > harness.DRIFT_ALERT_PIPS:
                print("  DRIFT DETECTED. A mean difference this large is not spread noise.")
                print("  Suspect a timezone or session-boundary bug before suspecting the feed.")
            else:
                print("  No systematic drift. One day's difference means nothing; what")
                print("  matters is whether the MEAN stays near zero as the sample grows.")
        else:
            print()
            print("  Nothing compared. Either no ticks are ingested for this range, or")
            print("  the ECB fixes are not loaded. Run --load-ecb and check Phase 1 coverage.")
        return 0
    finally:
        conn.close()


def _cmd_monitors() -> int:
    print("SPREAD DISTRIBUTION BY HOUR (UTC)")
    rows = harness.spread_by_hour()
    if not rows:
        print("  no ticks ingested")
    else:
        print(f"  {'instrument':<10}{'hour':>5}{'ticks':>9}{'median':>12}{'p95':>12}{'max':>12}")
        for r in rows[:24]:
            print(f"  {r['instrument']:<10}{r['hour_utc']:>5}{r['ticks']:>9,}"
                  f"{r['median_spread']:>12}{r['p95_spread']:>12}{r['max_spread']:>12}")
        if len(rows) > 24:
            print(f"  ... {len(rows) - 24} more rows")
        print()
        print("  Quantiles, not means: spreads are positive and heavy-tailed, so a mean")
        print("  is dragged by exactly the rollover and news spikes that matter.")

    print()
    print("LOWEST TICK-RATE HOURS (ratio to that instrument's median hour)")
    rates = harness.tick_rate_by_hour()
    if not rates:
        print("  no ticks ingested")
    else:
        print(f"  {'instrument':<10}{'hour':<22}{'ticks':>9}{'ratio':>9}")
        for r in rates[:12]:
            print(f"  {r['instrument']:<10}{str(r['hour_start']):<22}{r['ticks']:>9,}"
                  f"{r['ratio_to_median']:>9}")
        print()
        print("  A sudden drop usually means a feed gap, not a quiet market. Ratio to")
        print("  median rather than an absolute floor, because tick rates differ by an")
        print("  order of magnitude between instruments and sessions.")
    return 0


def _cmd_report() -> int:
    conn = harness.connect()
    try:
        harness.ensure_schema(conn)
        cov = harness.ecb_coverage(conn)
        print("ECB REFERENCE RATES")
        if cov["rates"]:
            print(f"  {cov['rates']:,} rates, {cov['currencies']} currencies, "
                  f"{cov['days']:,} days")
            print(f"  {cov['first']} .. {cov['last']}")
        else:
            print("  not loaded - run --load-ecb")

        print()
        print("DRIFT OBSERVATIONS")
        obs = harness.drift_observations(conn, 15)
        if not obs:
            print("  none - run --drift")
        else:
            print(f"  {'date':<12}{'instrument':<10}{'anchor (UTC)':<22}"
                  f"{'feed mid':>11}{'ECB':>11}{'diff pips':>11}")
            for o in obs:
                print(f"  {str(o['fix_date']):<12}{o['instrument']:<10}"
                      f"{o['anchor_ts'].strftime('%Y-%m-%d %H:%M:%S'):<22}"
                      f"{o['feed_mid']:>11.5f}{o['ecb_rate']:>11.5f}"
                      f"{o['diff_pips']:>11.2f}")

        print()
        print("CROSS-FEED RECONCILIATION")
        print("  Success criterion #3 asks for two independent price feeds reconciled")
        print("  with a documented disagreement rate. Status:")
        print()
        print("    ECB reference fix   AVAILABLE - independent, daily granularity")
        print("    HistData M1 bars    NOT AVAILABLE without a browser (measured")
        print("                        2026-08-05: every download page returns a")
        print("                        15,599-byte shell with no form or token, and")
        print("                        get.php returns HTTP 500)")
        print()
        print("  The criterion is therefore PARTIALLY met. The ECB comparison is a")
        print("  genuine independent check but daily, so it cannot measure a bar-by-bar")
        print("  disagreement RATE the way an M1 feed would. Stated rather than papered")
        print("  over - see planning.md.")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fxpit.validation", description=__doc__)
    p.add_argument("--load-ecb", action="store_true")
    p.add_argument("--drift", action="store_true")
    p.add_argument("--instrument", default="EURUSD",
                   help=f"one of {sorted(ecb.DIRECT_ANCHORS)}")
    p.add_argument("--start", type=_day, metavar="YYYY-MM-DD")
    p.add_argument("--end", type=_day, metavar="YYYY-MM-DD", help="exclusive")
    p.add_argument("--monitors", action="store_true")
    p.add_argument("--report", action="store_true")
    args = p.parse_args(argv)

    if args.load_ecb:
        return _cmd_load_ecb()
    if args.drift:
        if not args.start or not args.end:
            p.error("--drift needs --start and --end")
        return _cmd_drift(args.instrument, args.start, args.end)
    if args.monitors:
        return _cmd_monitors()
    if args.report:
        return _cmd_report()
    p.error("choose --load-ecb, --drift, --monitors or --report")
    return 2


if __name__ == "__main__":
    sys.exit(main())
