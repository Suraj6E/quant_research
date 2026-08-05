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


# Phase 5's generators (spread_by_hour, cross_feed_reconciliation,
# tick_rate_anomalies) were DELETED on 2026-08-05 when the validation
# harness landed. Those panels now read the real monitors via
# fxpit.web.live.


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
