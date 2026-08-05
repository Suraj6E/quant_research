"""Phase 2 — cleaning as a reversible layer.

    python -m fxpit.flags --scan --instruments EURUSD --start 2024-01-05 --end 2024-01-09
    python -m fxpit.flags --bars --instruments EURUSD --start 2024-01-05 --end 2024-01-09
    python -m fxpit.flags --report
    python -m fxpit.flags --explain EURUSD 2024-01-08

`tick_raw` is never modified. Detectors only INSERT into `tick_flag`, and a
wrong detector is corrected by deleting its flags and re-running.
"""

from fxpit.flags.detectors import ALL, BLOCKED, BY_NAME, RUNNABLE, Detector
from fxpit.flags.runner import ScanReport, backfill_bars, ensure_bars, scan

__all__ = [
    "ALL",
    "BLOCKED",
    "BY_NAME",
    "RUNNABLE",
    "Detector",
    "ScanReport",
    "backfill_bars",
    "ensure_bars",
    "scan",
]
