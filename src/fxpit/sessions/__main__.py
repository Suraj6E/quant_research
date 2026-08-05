"""Session and calendar CLI."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from fxpit.sessions import definitions as defs
from fxpit.sessions import export as exporter
from fxpit.sessions import holidays as hol
from fxpit.sessions import store


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d")


def _cmd_build(start, end) -> int:
    conn = store.connect()
    try:
        rep = store.build(conn, start.date(), end.date())
        print(f"sessions   {rep.sessions:>8,}")
        print(f"markets    {rep.markets:>8,}")
        print(f"rollovers  {rep.rollovers:>8,}")
        print(f"holidays   {rep.holidays:>8,}")
        if not rep.holidays_available:
            print()
            print("WARNING  the `holidays` package is not installed, so no currency")
            print("         holidays were loaded. Every day will look like a normal")
            print("         trading day, which is wrong rather than merely incomplete.")
            print("         pip install -e \".[dev,web]\"")
        if rep.unmapped_currencies:
            print(f"unmapped currencies: {', '.join(rep.unmapped_currencies)}")
        for currency, caveat in sorted(hol.CAVEATS.items()):
            print(f"caveat  {currency}: {caveat}")
        return 0
    finally:
        conn.close()


def _cmd_describe(ts_text: str, pair: str | None) -> int:
    ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
    conn = store.connect()
    try:
        moment = store.describe(conn, ts, pair)
        print(f"{moment.ts.isoformat()}" + (f"   {pair}" if pair else ""))
        print()
        print(f"  market        {'open' if moment.market_open else 'CLOSED'}")
        print(f"  sessions      {', '.join(moment.sessions) or 'none'}")
        print(f"  overlap       {'yes' if moment.in_overlap else 'no'}")
        print(f"  rollover      {'YES' if moment.is_rollover else 'no'}")
        if pair:
            if moment.holidays:
                for currency, name in moment.holidays.items():
                    print(f"  holiday       {currency}: {name}")
            else:
                print("  holiday       neither leg")
        print()
        print(f"  {moment.describe()}")
        return 0
    finally:
        conn.close()


def _cmd_dst(year: int) -> int:
    weeks = defs.dst_offset_weeks(year)
    print(f"London-New York overlap anomalies in {year}")
    print()
    print("The US and EU shift daylight saving on DIFFERENT DATES, so for a few")
    print("weeks each spring and autumn the overlap is an hour longer or shorter")
    print("than normal. These are computed from local-time rules, not tabulated.")
    print()
    if not weeks:
        print("  none found")
        return 0
    print(f"  {'window':<26}{'days':>6}{'overlap':>10}{'normal':>9}{'delta':>8}")
    print("  " + "-" * 57)
    for w in weeks:
        span = f"{w['start']} .. {w['end']}"
        print(f"  {span:<26}{w['trading_days']:>6}{w['overlap_hours']:>10}"
              f"{w['normal_hours']:>9}{w['delta_hours']:>+8}")
    return 0


def _cmd_report() -> int:
    conn = store.connect()
    try:
        cov = store.coverage(conn)
        if not cov["sessions"]:
            print("No session windows. Run --build first.")
            return 0
        print("SESSION LAYER")
        print(f"  session windows   {cov['sessions']:>8,}")
        print(f"  market windows    {cov['markets']:>8,}")
        print(f"  rollover windows  {cov['rollovers']:>8,}")
        print(f"  holidays          {cov['holidays']:>8,} across {cov['currencies']} currencies")
        print(f"  covers            {cov['first']} .. {cov['last']}")
        print()
        print("HOLIDAYS BY CURRENCY")
        for r in store.holidays_by_currency(conn):
            print(f"  {r['currency']:<5}{r['days']:>5} days   {r['first']} .. {r['last']}")
        print()
        for currency, caveat in sorted(hol.CAVEATS.items()):
            print(f"  caveat {currency}: {caveat}")
        print()
        print("  A currency holiday means THIN LIQUIDITY, not a closed market.")
        print("  FX trades 24/5 through national holidays; what changes is how many")
        print("  participants are at their desks. National holidays are a proxy for")
        print("  market holidays, not the same thing.")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fxpit.sessions", description=__doc__)
    p.add_argument("--build", action="store_true")
    p.add_argument("--start", type=_day, metavar="YYYY-MM-DD")
    p.add_argument("--end", type=_day, metavar="YYYY-MM-DD", help="exclusive")
    p.add_argument("--describe", metavar="ISO8601", help="e.g. 2024-01-08T21:30:00Z")
    p.add_argument("--pair", metavar="PAIR", help=f"one of {sorted(defs.PAIR_LEGS)}")
    p.add_argument("--dst", type=int, metavar="YEAR", help="overlap anomalies for a year")
    p.add_argument("--report", action="store_true")
    p.add_argument("--export", action="store_true",
                   help="mirror the calendar into ClickHouse for tick detectors")
    args = p.parse_args(argv)

    if args.build:
        if not args.start or not args.end:
            p.error("--build needs --start and --end")
        return _cmd_build(args.start, args.end)
    if args.describe:
        return _cmd_describe(args.describe, args.pair)
    if args.dst:
        return _cmd_dst(args.dst)
    if args.report:
        return _cmd_report()
    if args.export:
        rep = exporter.export()
        print(f"calendar hours    {rep.hours:>8,}")
        print(f"  market open     {rep.open_hours:>8,}")
        print(f"  rollover        {rep.rollover_hours:>8,}")
        print(f"holidays          {rep.holidays:>8,}")
        print(f"pair legs         {rep.pair_legs:>8,}")
        print()
        print("Mirrored into ClickHouse. Postgres remains authoritative - re-run")
        print("this after any --build, or the detectors join against stale windows.")
        return 0
    p.error("choose --build, --describe, --dst, --report or --export")
    return 2


if __name__ == "__main__":
    sys.exit(main())
