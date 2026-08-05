"""Phase 1 — Dukascopy tick ingest.

    python -m fxpit.ingest --instruments EURUSD --start 2024-01-08 --end 2024-01-10
    python -m fxpit.ingest --report

`tick_raw` is immutable: nothing here filters, de-duplicates, reorders or
corrects what the feed sent. Quality detection is Phase 2 and is additive.
"""

from fxpit.ingest.dukascopy import (
    DECIMAL_FACTORS,
    MAJORS,
    FetchStatus,
    HourResult,
    Tick,
    decode,
    fetch_hour,
    hour_url,
    hours_between,
)
from fxpit.ingest.runner import RunReport, ingest

__all__ = [
    "DECIMAL_FACTORS",
    "MAJORS",
    "FetchStatus",
    "HourResult",
    "RunReport",
    "Tick",
    "decode",
    "fetch_hour",
    "hour_url",
    "hours_between",
    "ingest",
]
