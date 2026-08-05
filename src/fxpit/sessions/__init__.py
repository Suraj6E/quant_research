"""Phase 4 — session and calendar layer.

    python -m fxpit.sessions --build --start 2024-01-01 --end 2025-01-01
    python -m fxpit.sessions --describe 2024-01-08T21:30:00Z --pair EURUSD
    python -m fxpit.sessions --dst 2024
    python -m fxpit.sessions --report

Windows are defined in local wall-clock time and converted to UTC, so daylight
saving falls out of the conversion instead of being special-cased. Nothing in
this package contains a UTC constant.
"""

from fxpit.sessions.definitions import (
    CURRENCY_COUNTRY,
    PAIR_LEGS,
    SESSIONS,
    Window,
    dst_offset_weeks,
    london_ny_overlap,
    market_windows,
    rollover_windows,
    session_windows,
)
from fxpit.sessions.store import BuildReport, Moment, build, describe

__all__ = [
    "CURRENCY_COUNTRY",
    "PAIR_LEGS",
    "SESSIONS",
    "BuildReport",
    "Moment",
    "Window",
    "build",
    "describe",
    "dst_offset_weeks",
    "london_ny_overlap",
    "market_windows",
    "rollover_windows",
    "session_windows",
]
