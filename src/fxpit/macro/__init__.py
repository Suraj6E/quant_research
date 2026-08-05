"""Phase 3 — bitemporal macro store.

    python -m fxpit.macro --load            # download and load every RTDSM series
    python -m fxpit.macro --load --series EMPLOY
    python -m fxpit.macro --report
    python -m fxpit.macro --revisions

Vintages carry `known_at_precision`. RTDSM publishes a vintage MONTH, not a
release time, so those rows are placed at the last instant of the month and
labelled 'month'. Assuming 08:30 ET would invent precision that was never
measured.
"""

from fxpit.macro.loader import LoadReport, load, summary
from fxpit.macro.rtdsm import CATALOGUE, Observation, Series, download, parse

__all__ = [
    "CATALOGUE",
    "LoadReport",
    "Observation",
    "Series",
    "download",
    "load",
    "parse",
    "summary",
]
