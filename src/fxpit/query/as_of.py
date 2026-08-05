"""`as_of(t)` — the point-in-time read contract.

PHASE 0: these are unimplemented on purpose. The acceptance suite is written
against this contract *before* any pipeline exists, so the tests fail for a
principled reason (the guarantee is not yet provided) rather than an
incidental one (the module is missing). Implemented in Phase 3.

The contract, in one sentence: a call with argument `t` may return only facts
whose `known_at <= t`, keeping the latest `known_at` per key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

__all__ = ["Bar", "MacroFact", "Tick", "bars_as_of", "macro_as_of", "ticks_as_of"]


@dataclass(frozen=True)
class MacroFact:
    """One macro observation as it was known at a moment in time.

    `ref_period` is the period described; `known_at` is when it became public.
    Both are required — a fact carrying only one of them is not point-in-time
    and cannot be made so retroactively.
    """

    series_id: str
    ref_period: date
    known_at: datetime
    value: float | None
    vintage_seq: int


@dataclass(frozen=True)
class Tick:
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
class Bar:
    """A one-minute bar. Bid and ask are carried separately, never collapsed
    to a mid price — an assumed spread is one of the four contamination
    sources Phase 6 measures.
    """

    instrument: str
    ts: datetime
    bid_open: float
    bid_high: float
    bid_low: float
    bid_close: float
    ask_open: float
    ask_high: float
    ask_low: float
    ask_close: float
    source: str


def macro_as_of(
    t: datetime,
    series_id: str,
    ref_period: date | None = None,
) -> list[MacroFact]:
    """Macro facts publicly known at `t`.

    Filters `known_at <= t` and keeps the row with the greatest `known_at`
    per (series_id, ref_period). Returns [] when nothing was known yet —
    never a later vintage, never a placeholder.
    """
    raise NotImplementedError(
        "as_of() is not implemented (Phase 3). The acceptance suite is "
        "expected to be red until then — see tests/SPEC.md."
    )


def ticks_as_of(
    t: datetime,
    instrument: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Tick]:
    """Ticks observable at `t`. A price is known when it prints, so this
    filters `ts <= t` in addition to any [start, end) window.
    """
    raise NotImplementedError(
        "as_of() is not implemented (Phase 3). The acceptance suite is "
        "expected to be red until then — see tests/SPEC.md."
    )


def bars_as_of(
    t: datetime,
    instrument: str,
    source: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Bar]:
    """Bid/ask bars observable at `t`, from a named feed.

    `source` is required rather than defaulted: there is no consolidated tape
    in FX, so every bar is conditional on which feed produced it. Making the
    caller name the feed keeps that conditionality visible.
    """
    raise NotImplementedError(
        "as_of() is not implemented (Phase 3). The acceptance suite is "
        "expected to be red until then — see tests/SPEC.md."
    )
