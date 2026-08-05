"""Phase 5 — validation harness.

    python -m fxpit.validation --load-ecb
    python -m fxpit.validation --drift --instrument EURUSD --start 2024-01-01 --end 2024-02-01
    python -m fxpit.validation --monitors
    python -m fxpit.validation --report

The ECB fix is the independent anchor: useless for trading, valuable because it
comes from somebody else's process and therefore cannot share a bug with this
pipeline.
"""

from fxpit.validation.ecb import DIRECT_ANCHORS, Rate, concertation_instant, parse
from fxpit.validation.harness import (
    DRIFT_ALERT_PIPS,
    DriftReport,
    load_ecb,
    run_drift_anchor,
    spread_by_hour,
    tick_rate_by_hour,
)

__all__ = [
    "DIRECT_ANCHORS",
    "DRIFT_ALERT_PIPS",
    "DriftReport",
    "Rate",
    "concertation_instant",
    "load_ecb",
    "parse",
    "run_drift_anchor",
    "spread_by_hour",
    "tick_rate_by_hour",
]
