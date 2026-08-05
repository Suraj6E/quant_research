"""Phase 6 — the contamination experiment.

    python -m fxpit.experiment --plan --start 2022-01-01 --end 2025-01-01
    python -m fxpit.experiment --run  --start 2022-01-01 --end 2025-01-01

The only phase where strategy code is permitted, and it exists solely as a
measuring instrument. The rule is deliberately dull: an interesting one would
invite tuning, and tuning would reintroduce exactly the selection effects the
design exists to isolate.

The estimand is the set of DIFFERENCES between four evaluations of the same
rule. See docs/preregistration.md, which was written before any variant ran.
"""

from fxpit.experiment.releases import Release, build_all, release_instant
from fxpit.experiment.run import (
    Experiment,
    VariantResult,
    preregistration_hash,
    run_experiment,
)
from fxpit.experiment.variants import HOLD_MINUTES, VARIANTS, Variant

__all__ = [
    "HOLD_MINUTES",
    "VARIANTS",
    "Experiment",
    "Release",
    "Variant",
    "VariantResult",
    "build_all",
    "preregistration_hash",
    "release_instant",
    "run_experiment",
]
