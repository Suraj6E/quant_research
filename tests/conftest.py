"""Shared fixtures for the Phase 0 acceptance suite.

Fixture data is loaded from CSV rather than a live database on purpose: these
tests state the *guarantees* the system must provide, and must be runnable
before any storage tier is populated. Phase 5 re-runs the same assertions
against the real stack.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Half a pip on a 5-decimal pair. Cross-feed differences at or below this are
# noise; above it, at least one feed is wrong.
CROSS_FEED_TOLERANCE = 0.00005


@pytest.fixture(scope="session")
def tolerance() -> float:
    """Exposed as a fixture rather than imported, so test modules never depend
    on `tests/` being importable as a package.
    """
    return CROSS_FEED_TOLERANCE


def _rows(name: str) -> list[dict[str, str]]:
    """Read a fixture CSV, skipping the leading '#' provenance comments."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def _ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


@dataclass(frozen=True)
class MacroRow:
    series_id: str
    ref_period: date
    known_at: datetime
    value: float | None
    vintage_seq: int


@dataclass(frozen=True)
class TickRow:
    instrument: str
    ts: datetime
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float
    source: str

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class BarRow:
    instrument: str
    ts: datetime
    bid_close: float
    ask_close: float
    source: str


@pytest.fixture(scope="session", autouse=True)
def _point_in_time_session():
    """Back `as_of()` with the hand-built fixtures for the whole test session.

    Phase 0 wrote these tests against an empty schema, before any storage
    existed, and their bodies have not been touched since. Phase 3 supplies the
    backing store they always assumed — it does not adjust the assertions to
    fit the implementation, which would invert the point of writing them first.

    The SQL exercised here is the same SQL production uses; only the loader
    differs, so a test passing against fixtures is evidence about the real
    filter rather than about a test double.
    """
    from fxpit.query import session as qs

    s = qs.fixture_session(FIXTURES)
    qs.set_default_session(s)
    yield s
    qs.set_default_session(None)
    s.close()


@pytest.fixture(scope="session")
def macro_rows() -> list[MacroRow]:
    return [
        MacroRow(
            series_id=r["series_id"],
            ref_period=date.fromisoformat(r["ref_period"]),
            known_at=_ts(r["known_at"]),
            value=float(r["value"]) if r["value"] else None,
            vintage_seq=int(r["vintage_seq"]),
        )
        for r in _rows("macro_vintages.csv")
    ]


@pytest.fixture(scope="session")
def tick_rows() -> list[TickRow]:
    return [
        TickRow(
            instrument=r["instrument"],
            ts=_ts(r["ts"]),
            bid=float(r["bid"]),
            ask=float(r["ask"]),
            bid_volume=float(r["bid_volume"]),
            ask_volume=float(r["ask_volume"]),
            source=r["source"],
        )
        for r in _rows("ticks.csv")
    ]


@pytest.fixture(scope="session")
def bar_rows() -> list[BarRow]:
    return [
        BarRow(
            instrument=r["instrument"],
            ts=_ts(r["ts"]),
            bid_close=float(r["bid_close"]),
            ask_close=float(r["ask_close"]),
            source=r["source"],
        )
        for r in _rows("bars_cross_feed.csv")
    ]


@pytest.fixture(scope="session")
def revised_periods(macro_rows: list[MacroRow]) -> list[tuple[str, date]]:
    """(series_id, ref_period) pairs whose first print differs from their final
    value. These are the only periods where a revision test can distinguish a
    correct implementation from one that always returns the latest value.
    """
    by_key: dict[tuple[str, date], list[MacroRow]] = {}
    for r in macro_rows:
        by_key.setdefault((r.series_id, r.ref_period), []).append(r)
    out = []
    for key, rows in by_key.items():
        rows.sort(key=lambda r: r.known_at)
        if rows[0].value != rows[-1].value:
            out.append(key)
    return sorted(out)
