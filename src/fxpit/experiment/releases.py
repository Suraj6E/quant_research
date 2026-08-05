"""Macro release events with exact timestamps, and their surprise measure.

This also closes the task Phase 3 left open. RTDSM publishes a vintage MONTH and
cannot tell you which day a value became public; ALFRED's vintage dates are the
actual publication dates. Combined with the standing 08:30 ET release time they
give a release instant precise enough for an intraday experiment.

TWO CORRECTNESS DETAILS
-----------------------
**Not every vintage date is a release.** CPI produces about thirteen vintages a
year because the annual seasonal-factor revision creates a vintage that
republishes old periods without adding a new one. Trading those would be trading
an event that did not happen, so a vintage counts as a release only if it
introduces a new maximum reference period.

**08:30 is New York local time**, so the UTC instant is 13:30 in winter and
12:30 in summer. Written as a UTC constant it would be wrong for half the year —
and the whole point of variant B→C is that timestamp precision matters.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from fxpit.config import ROOT

API = "https://api.stlouisfed.org/fred/"

# Both the Employment Situation and the CPI release at 08:30 Eastern.
RELEASE_ZONE = "America/New_York"
RELEASE_TIME = time(8, 30)

# Trailing window for the synthetic surprise. Pre-registered at 12.
SURPRISE_LOOKBACK = 12

SERIES = {
    "PAYEMS": "US nonfarm payroll employment",
    "CPIAUCSL": "US consumer price index",
}


@dataclass(frozen=True)
class Release:
    """One macro release, with both the value the market saw and the value
    history settled on."""

    series_id: str
    release_date: date
    release_ts: datetime      # 08:30 New York, in UTC
    ref_period: date
    first_print: float        # as published on the day
    final_value: float        # as published today
    surprise_first: float     # from the first-print series
    surprise_final: float     # from the revised series

    @property
    def revision(self) -> float:
        return self.final_value - self.first_print


def release_instant(day: date) -> datetime:
    """08:30 New York on `day`, as UTC. Derived, never a constant."""
    naive = datetime.combine(day, RELEASE_TIME).replace(fold=0)
    return naive.replace(tzinfo=ZoneInfo(RELEASE_ZONE)).astimezone(UTC)


def _api_key() -> str:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("FRED_API_KEY"):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    raise RuntimeError(
        "FRED_API_KEY is not set. The experiment needs ALFRED vintage dates to "
        "know when each value became public; RTDSM publishes only a vintage month."
    )


def _api(path: str, **params) -> dict:
    params.update(api_key=_api_key(), file_type="json")
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "fxpit-experiment/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def _observations(series_id: str, realtime: str) -> dict[date, float]:
    """The whole series as it stood on one real-time date."""
    data = _api(
        "series/observations",
        series_id=series_id,
        realtime_start=realtime,
        realtime_end=realtime,
        observation_start="1990-01-01",
    )
    out: dict[date, float] = {}
    for obs in data["observations"]:
        if obs["value"] in (".", ""):
            continue
        out[date.fromisoformat(obs["date"])] = float(obs["value"])
    return out


def _surprise(series: dict[date, float], ref: date) -> float | None:
    """Headline change minus the mean of the previous changes.

    A synthetic surprise, and labelled as one everywhere it appears. It measures
    deviation from recent trend, not deviation from expectation. The same
    construction is used in every variant, so its imperfection is a constant
    across the comparison and cannot manufacture a difference between arms.
    """
    periods = sorted(p for p in series if p <= ref)
    if len(periods) < SURPRISE_LOOKBACK + 2:
        return None
    idx = periods.index(ref)
    if idx < SURPRISE_LOOKBACK + 1:
        return None
    changes = [
        series[periods[i]] - series[periods[i - 1]]
        for i in range(idx - SURPRISE_LOOKBACK, idx + 1)
    ]
    current = changes[-1]
    baseline = sum(changes[:-1]) / len(changes[:-1])
    return current - baseline


def build(series_id: str, start: date, end: date) -> list[Release]:
    """Every genuine release of `series_id` in [start, end)."""
    vintages = sorted(
        date.fromisoformat(d)
        for d in _api("series/vintagedates", series_id=series_id, limit=10000)[
            "vintage_dates"
        ]
    )
    in_window = [v for v in vintages if start <= v < end]
    if not in_window:
        return []

    final = _observations(series_id, date.today().isoformat())
    releases: list[Release] = []
    previous_max: date | None = None

    # Walk from one vintage before the window so the first in-window release can
    # be compared against what came before it.
    scan_from = max(0, vintages.index(in_window[0]) - 1)
    for vintage in vintages[scan_from:]:
        if vintage >= end:
            break
        try:
            as_published = _observations(series_id, vintage.isoformat())
        except Exception:
            continue
        if not as_published:
            continue
        current_max = max(as_published)

        is_release = previous_max is None or current_max > previous_max
        previous_max = current_max if previous_max is None else max(previous_max, current_max)

        if not is_release or vintage < start:
            continue

        ref = current_max
        first_print = as_published.get(ref)
        final_value = final.get(ref)
        if first_print is None or final_value is None:
            continue

        s_first = _surprise(as_published, ref)
        s_final = _surprise(final, ref)
        if s_first is None or s_final is None:
            continue

        releases.append(
            Release(
                series_id=series_id,
                release_date=vintage,
                release_ts=release_instant(vintage),
                ref_period=ref,
                first_print=first_print,
                final_value=final_value,
                surprise_first=s_first,
                surprise_final=s_final,
            )
        )
    return releases


def build_all(start: date, end: date) -> list[Release]:
    out: list[Release] = []
    for series_id in SERIES:
        out.extend(build(series_id, start, end))
    return sorted(out, key=lambda r: r.release_ts)
