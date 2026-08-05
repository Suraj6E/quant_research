"""ECB euro foreign exchange reference rates — the independent anchor.

WHY THIS SOURCE
---------------
One official fix per currency per day since 1999, free, no account. Useless for
trading: a single daily point supports no strategy. Its entire value is that it
is produced by somebody else, from a different process, and therefore cannot
share a bug with the Dukascopy pipeline.

THE CONCERTATION INSTANT
------------------------
The rates come from a daily concertation procedure between European central
banks at **14:15 Central European Time**. That is a LOCAL time, so the UTC
instant moves with European daylight saving — 13:15 UTC in summer, 12:15 in
winter.

Getting that wrong would be poetic: an anchor built to catch timezone bugs,
itself containing one, quietly reporting drift that is really just the anchor
looking at the wrong minute. So the instant is derived through the Phase 4
local-time machinery rather than written as a UTC constant.

ORIENTATION
-----------
ECB quotes units of the foreign currency per ONE euro. EUR/USD 1.1515 means
1 EUR = 1.1515 USD, matching a Dukascopy EURUSD quote exactly. Anything not
EUR-based must be crossed, and a cross carries the error of both legs — so
EURUSD is the clean comparison and the others are weaker.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref.zip"

# The concertation procedure. Local European time, never a UTC constant.
#
# Europe/Berlin, not Europe/Frankfurt: the latter is not an IANA zone and
# raises ZoneInfoNotFoundError. Berlin is the canonical CET/CEST zone for
# Germany and carries the same offsets and transition dates the ECB observes.
CONCERTATION_ZONE = "Europe/Berlin"
CONCERTATION_TIME = time(14, 15)

# Instruments the anchor can check directly, and the ECB column each needs.
# EURUSD is the only exact one-to-one; everything else needs a cross.
DIRECT_ANCHORS = {"EURUSD": "USD"}

PIP_FACTOR = {"EURUSD": 10_000, "GBPUSD": 10_000, "USDJPY": 100}


@dataclass(frozen=True)
class Rate:
    fix_date: date
    currency: str
    rate: float


def concertation_instant(day: date) -> datetime:
    """14:15 Frankfurt on `day`, as UTC.

    Derived, not hardcoded. 12:15 UTC in winter and 13:15 in summer — writing
    either as a constant would make the anchor wrong for half the year, which
    is exactly the failure it exists to detect.
    """
    naive = datetime.combine(day, CONCERTATION_TIME).replace(fold=0)
    return naive.replace(tzinfo=ZoneInfo(CONCERTATION_ZONE)).astimezone(UTC)


def fetch(url: str = HIST_URL, timeout: int = 240) -> bytes:
    """Download the reference-rate archive.

    urllib times out on the full-history zip often enough to be annoying, so
    curl is used with a long timeout. Verified 2026-08-05: 7,064 daily rows
    covering 1999-01-04 to 2026-08-04.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ecb.zip"
        proc = subprocess.run(
            ["curl.exe", "-sSL", "--max-time", str(timeout), "-o", str(out),
             "-w", "%{http_code}", url],
            capture_output=True, text=True,
        )
        if proc.stdout.strip() != "200" or not out.exists():
            raise RuntimeError(f"ECB download failed: HTTP {proc.stdout.strip()}")
        payload = out.read_bytes()
    if payload[:2] != b"PK":
        raise RuntimeError(
            f"ECB returned {len(payload)} bytes that are not a zip - the URL or "
            f"the publication format may have changed."
        )
    return payload


def parse(payload: bytes) -> list[Rate]:
    """Parse the zipped CSV into one Rate per (date, currency).

    Missing values are published as 'N/A' — currencies that joined the euro,
    or were not yet quoted. They are SKIPPED rather than stored as zero: a rate
    of zero would be a real number that happens to be wrong, and would poison
    any comparison that used it.
    """
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        text = archive.read(name).decode("utf-8", "replace")

    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader)]
    out: list[Rate] = []
    for row in reader:
        if not row or not row[0].strip():
            continue
        try:
            fix_date = date.fromisoformat(row[0].strip())
        except ValueError:
            continue
        for i, currency in enumerate(header[1:], start=1):
            if i >= len(row) or not currency:
                continue
            raw = row[i].strip()
            if not raw or raw.upper() == "N/A":
                continue
            try:
                out.append(Rate(fix_date, currency, float(raw)))
            except ValueError:
                continue
    return out


def pip_size(instrument: str) -> float:
    return 1.0 / PIP_FACTOR.get(instrument, 10_000)
