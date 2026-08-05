"""Contamination experiment CLI."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from fxpit.config import ROOT
from fxpit.experiment import releases as rel
from fxpit.experiment import run as runner
from fxpit.experiment import variants as V
from fxpit.experiment.run import release_days_needed, run_experiment

CACHE = ROOT / "data" / "experiment_releases.json"


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d")


def _load_releases(start, end, refresh: bool) -> list[rel.Release]:
    """Release events, cached because building them costs one ALFRED call per
    vintage date and the set does not change.
    """
    if CACHE.exists() and not refresh:
        raw = json.loads(CACHE.read_text(encoding="utf-8"))
        out = []
        for r in raw:
            out.append(
                rel.Release(
                    series_id=r["series_id"],
                    release_date=datetime.fromisoformat(r["release_date"]).date(),
                    release_ts=datetime.fromisoformat(r["release_ts"]),
                    ref_period=datetime.fromisoformat(r["ref_period"]).date(),
                    first_print=r["first_print"],
                    final_value=r["final_value"],
                    surprise_first=r["surprise_first"],
                    surprise_final=r["surprise_final"],
                )
            )
        return [r for r in out if start.date() <= r.release_date < end.date()]

    print("building release calendar from ALFRED (one call per vintage date)...")
    built = rel.build_all(start.date(), end.date())
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps(
            [
                {
                    "series_id": r.series_id,
                    "release_date": r.release_date.isoformat(),
                    "release_ts": r.release_ts.isoformat(),
                    "ref_period": r.ref_period.isoformat(),
                    "first_print": r.first_print,
                    "final_value": r.final_value,
                    "surprise_first": r.surprise_first,
                    "surprise_final": r.surprise_final,
                }
                for r in built
            ],
            indent=1,
        ),
        encoding="utf-8",
    )
    return built


def _cmd_plan(start, end, refresh) -> int:
    events = _load_releases(start, end, refresh)
    print(f"{len(events)} genuine releases in window")
    print()
    by_series: dict[str, int] = {}
    for e in events:
        by_series[e.series_id] = by_series.get(e.series_id, 0) + 1
    for series, n in sorted(by_series.items()):
        print(f"  {series:<10}{n:>4} releases")

    needed = release_days_needed(events)
    hours = sorted({h for _, hs in needed for h in hs})
    print()
    print(f"tick ingest needs {len(needed)} dates x hours {hours}")
    print(f"  = {len(needed) * len(hours)} instrument-hours for EURUSD")
    print()
    print("Fetch them with:")
    for day, _ in needed[:3]:
        print(f"  python -m fxpit.ingest --instruments EURUSD --start {day} "
              f"--end <next day> --hours {' '.join(str(h) for h in hours)}")
    print("  ... (the --run command does this for you if data is missing)")
    return 0


def _fmt_pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _cmd_run(start, end, refresh, instrument) -> int:
    events = _load_releases(start, end, refresh)
    if not events:
        print("No release events. Check the window and the FRED key.")
        return 1

    exp = run_experiment(events, instrument)
    a = exp.results["A"]

    print("=" * 78)
    print("THE CONTAMINATION EXPERIMENT")
    print("=" * 78)
    print(f"  instrument            {exp.instrument}")
    print(f"  window                {start:%Y-%m-%d} .. {end:%Y-%m-%d}")
    print(f"  release events        {exp.events_considered}")
    print(f"  events traded         {a.n}")
    print(f"  holding period        {V.HOLD_MINUTES} minutes")
    print(f"  pre-registration      sha256:{exp.prereg_hash}")
    print()
    print("  The rule was fixed in docs/preregistration.md before any variant ran.")
    print("  The hash above is of that document, so the claim is checkable.")

    if a.n < 2:
        print()
        print("  NOT ENOUGH EVENTS TO REPORT. A Sharpe ratio needs a distribution of")
        print("  returns; with fewer than two trades there is nothing to measure.")
        print("  Ingest tick data covering the release days - see --plan.")
        return 1

    if exp.underpowered:
        print()
        print("  " + "!" * 74)
        print(f"  UNDERPOWERED: {a.n} events, below the {runner.MIN_EVENTS_FOR_INTERPRETATION}"
              " needed for these gaps to mean anything.")
        print("  The numbers below are arithmetic, not evidence. The differences between")
        print("  arms are small against the variance of 30-minute FX returns, and at this")
        print("  sample size that variance swamps them entirely. Reporting the ordering as")
        print("  a finding would be exactly the overclaiming this project criticises.")
        print("  " + "!" * 74)

    print()
    print("-" * 78)
    print(f"  {'variant':<24}{'n':>4}{'mean bps':>11}{'std':>9}{'Sharpe':>9}"
          f"{'hit':>7}{'total bps':>11}")
    print("-" * 78)
    for v in V.VARIANTS:
        r = exp.results[v.key]
        print(f"  {v.key + ' - ' + v.name:<24}{r.n:>4}{r.mean_bps:>11.2f}"
              f"{r.std_bps:>9.2f}{r.sharpe:>9.3f}{_fmt_pct(r.hit_rate):>7}"
              f"{r.total_bps:>11.1f}")

    print()
    print("  THE ESTIMAND - what each contamination channel is worth")
    print("-" * 78)
    labels = {
        ("A", "B"): "revision leakage",
        ("B", "C"): "timestamp coarsening",
        ("C", "D"): "mid-price assumption",
    }
    for g in exp.gaps:
        label = labels[(g["from"], g["to"])]
        print(f"  {g['from']} -> {g['to']}  {label:<24}"
              f"{g['sharpe_delta']:>+9.3f} Sharpe {g['mean_bps_delta']:>+9.2f} bps")

    total = exp.results["D"].sharpe - exp.results["A"].sharpe
    print(f"  {'A -> D':<8}{'all three combined':<24}{total:>+9.3f} Sharpe")

    print()
    print("  HYPOTHESES")
    print("-" * 78)
    if exp.underpowered:
        print("  NOT ASSESSED - the sample is too small to judge any hypothesis.")
        print(f"  H1 ordering would read {'HOLDS' if exp.ordering_holds else 'DOES NOT HOLD'}, "
              "but at this N that is a coin toss.")
        return 0

    print(f"  H1  D > C > B > A ordering      {'HOLDS' if exp.ordering_holds else 'DOES NOT HOLD'}")
    print(f"  H2  largest single source       {exp.largest_source}")
    print()
    if not exp.ordering_holds:
        print("  H1 did not hold. That is a legitimate result and is reported as one.")
        print("  Each variant strictly adds an advantage, so a broken ordering means")
        print("  the advantages are small relative to sampling noise at this N.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fxpit.experiment", description=__doc__)
    p.add_argument("--plan", action="store_true", help="list events and the ticks needed")
    p.add_argument("--run", action="store_true", help="run all four variants")
    p.add_argument("--start", type=_day, required=True, metavar="YYYY-MM-DD")
    p.add_argument("--end", type=_day, required=True, metavar="YYYY-MM-DD")
    p.add_argument("--instrument", default="EURUSD")
    p.add_argument("--refresh", action="store_true", help="rebuild the release cache")
    args = p.parse_args(argv)

    if args.plan:
        return _cmd_plan(args.start, args.end, args.refresh)
    if args.run:
        return _cmd_run(args.start, args.end, args.refresh, args.instrument)
    p.error("choose --plan or --run")
    return 2


if __name__ == "__main__":
    sys.exit(main())
