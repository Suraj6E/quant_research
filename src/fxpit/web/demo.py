"""DEMO data generators — synthetic, deterministic, and describing nothing.

Every function here exists so that a page which cannot yet have real data can
still be judged as a layout. The numbers are shaped to be plausible, which is
precisely why they are dangerous: plausible-looking wrong data is the failure
mode this whole project is built to detect.

Two safeguards, both deliberate:

* Seeded from a constant, so a demo number never changes between reloads. A
  figure that drifts on refresh invites someone to read it as live.
* Every caller must pair the output with `Provenance.DEMO`, and `Panel`
  refuses to construct a DEMO panel that does not name the phase replacing it.

Deleting this module is a project milestone, not a chore.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

SEED = 20260804

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]

FLAGS = [
    "crossed", "zero_spread", "stale", "spread_outlier",
    "rollover_window", "weekend_gap", "holiday_thin", "feed_disagreement",
]


def _rng(salt: str) -> random.Random:
    return random.Random(f"{SEED}:{salt}")


# Phase 1's generators (ingest_coverage, ingest_ledger) were DELETED when the
# real pipeline landed on 2026-08-04. The panels now read from the ingest
# ledger and tick_raw via fxpit.web.live. Deleting rather than commenting out
# is deliberate: a retired generator left in the file is precisely what the
# next person reaches for when adding a panel.


# Phase 2's generator (flag_density) was DELETED on 2026-08-05 when the real
# detectors landed. The flag panels now read tick_flag via fxpit.web.live.
# Deleting rather than commenting out is deliberate — see the Phase 1 note.


def spread_by_hour() -> tuple[list[str], list[tuple[str, list[float]]]]:
    """Median spread in pips by hour for three pairs. One axis, three series -
    within the validated all-pairs limit.
    """
    rng = _rng("spread")
    hours = [f"{h:02d}" for h in range(24)]
    out = []
    for inst, floor in (("EURUSD", 0.18), ("GBPUSD", 0.34), ("USDJPY", 0.26)):
        vals = []
        for h in range(24):
            v = floor + rng.uniform(0, 0.06)
            if h in (21, 22):
                v *= rng.uniform(3.4, 4.6)          # rollover widening
            elif h in (0, 1, 2):
                v *= rng.uniform(1.5, 2.1)          # thin Asia session
            elif 7 <= h <= 16:
                v *= rng.uniform(0.85, 1.0)         # London/NY overlap
            vals.append(round(v, 3))
        out.append((inst, vals))
    return hours, out


def session_windows() -> list[dict[str, str]]:
    """DST-shifted session boundaries. The US and EU switch on different dates,
    producing a two-to-three week window each spring and autumn where the
    London-New York overlap is an hour different from normal.
    """
    return [
        {"window": "2024-03-10 to 2024-03-30", "note": "US on DST, EU not yet",
         "overlap": "12:00-16:00 UTC", "delta": "-1h", "status": "anomaly"},
        {"window": "2024-03-31 to 2024-10-26", "note": "both on DST",
         "overlap": "12:00-16:00 UTC", "delta": "normal", "status": "normal"},
        {"window": "2024-10-27 to 2024-11-02", "note": "EU off DST, US still on",
         "overlap": "13:00-17:00 UTC", "delta": "+1h", "status": "anomaly"},
        {"window": "2024-11-03 to 2025-03-08", "note": "both off DST",
         "overlap": "13:00-17:00 UTC", "delta": "normal", "status": "normal"},
    ]


def rollover_windows() -> list[dict[str, str]]:
    return [
        {"date": "2024-01-09", "start": "21:00 UTC", "end": "22:00 UTC",
         "median_spread": "0.81 pip", "vs_baseline": "4.2x"},
        {"date": "2024-01-10", "start": "21:00 UTC", "end": "22:00 UTC",
         "median_spread": "0.77 pip", "vs_baseline": "3.9x"},
        {"date": "2024-03-13", "start": "20:00 UTC", "end": "21:00 UTC",
         "median_spread": "0.94 pip", "vs_baseline": "4.8x"},
    ]


def cross_feed_reconciliation() -> tuple[list[str], list[float]]:
    """Disagreement rate between Dukascopy and HistData by month, in basis
    points of bars compared. Success criterion #3 is that this number exists
    and is documented - not that it is small.
    """
    rng = _rng("recon")
    months = [f"2024-{m:02d}" for m in range(1, 13)]
    return months, [round(rng.uniform(18, 47), 1) for _ in months]


def tick_rate_anomalies() -> tuple[list[str], list[tuple[str, list[float]]]]:
    """Ticks per minute over a day. A sudden drop usually means a feed gap,
    not a quiet market - which is why this is monitored rather than assumed.
    """
    rng = _rng("tickrate")
    start = date(2024, 1, 9)
    labels = [(start + timedelta(days=i)).isoformat()[5:] for i in range(14)]
    vals = []
    for i in range(14):
        v = rng.uniform(1150, 1450)
        if i == 8:
            v *= 0.21          # the gap this panel exists to catch
        vals.append(round(v))
    return labels, [("EURUSD ticks/min", vals)]


def contamination_variants() -> list[dict]:
    """The Phase 6 result table: four data regimes, one pre-registered rule.

    The ordering (D > C > B > A) is hypothesis H1, not a finding. These
    numbers are invented; the experiment has not been run.
    """
    return [
        {"variant": "A - Honest", "macro": "First print, as_of release",
         "timestamp": "Exact release timestamp", "costs": "Actual bid/ask",
         "sharpe": 0.31, "highlight": True},
        {"variant": "B - Revised values", "macro": "Final revised value",
         "timestamp": "Exact release timestamp", "costs": "Actual bid/ask",
         "sharpe": 0.58, "highlight": False},
        {"variant": "C - Revised + date-only", "macro": "Final revised value",
         "timestamp": "Date only, entry at day open", "costs": "Actual bid/ask",
         "sharpe": 1.04, "highlight": False},
        {"variant": "D - C plus mid-price", "macro": "Final revised value",
         "timestamp": "Date only", "costs": "Mid price, no spread",
         "sharpe": 1.67, "highlight": False},
    ]


def hypotheses() -> list[dict[str, str]]:
    return [
        {"id": "H1", "claim": "Contamination ordering D > C > B > A in reported Sharpe",
         "confidence": "high"},
        {"id": "H2", "claim": "Spread is the largest single contamination source",
         "confidence": "moderate"},
        {"id": "H3", "claim": "Timestamp precision matters more than revision magnitude",
         "confidence": "moderate to low"},
        {"id": "H4", "claim": "Cross-feed disagreement is non-trivial and non-random",
         "confidence": "high on existence, low on magnitude"},
        {"id": "H5", "claim": "Flagged-tick share is higher than expected",
         "confidence": "moderate"},
        {"id": "H6", "claim": "DST transition weeks show structural anomalies",
         "confidence": "high that the artefact exists"},
    ]
