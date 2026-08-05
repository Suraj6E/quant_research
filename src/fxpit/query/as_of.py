"""`as_of(t)` — the point-in-time read contract.

The contract in one sentence: **a call with argument `t` may return only facts
whose `known_at <= t`, keeping the latest `known_at` per key.**

This is the only sanctioned read path. Convenience accessors that query
Postgres or ClickHouse directly must not be added — that is exactly how
look-ahead bias re-enters a system that had eliminated it, and it is the
project's first success criterion that no such accessor exists.

Implemented in Phase 3 over the DuckDB session in `fxpit.query.session`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from fxpit.query.session import QuerySession, default_session

__all__ = ["Bar", "MacroFact", "Tick", "bars_as_of", "macro_as_of", "ticks_as_of"]


@dataclass(frozen=True)
class MacroFact:
    """One macro observation as it was known at a moment in time.

    `ref_period` is the period described; `known_at` is when it became public.
    Both are required — a fact carrying only one of them is not point-in-time
    and cannot be made so retroactively.

    `known_at_precision` records how exactly `known_at` is known. Archival
    sources give a vintage *month* or *date*, not a release *time*, and where
    the precision is coarse `known_at` is placed at the LATEST instant
    consistent with it. That biases the filter toward withholding rather than
    leaking: a value whose exact release time is unknown is treated as
    not-yet-public for longer, never for less.
    """

    series_id: str
    ref_period: date
    known_at: datetime
    value: float | None
    vintage_seq: int
    known_at_precision: str = "exact"


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


def _require_instant(t: object) -> datetime:
    """Reject a bare `date` where a `datetime` belongs.

    `datetime` subclasses `date`, so the isinstance order matters. An 08:30 ET
    release and an 08:31 ET price are different objects; silently coercing a
    date to midnight would destroy that distinction and hand a strategy hours
    of information it did not have. Cheapest check in the codebase, guarding
    the most expensive mistake.
    """
    if isinstance(t, datetime):
        return t
    if isinstance(t, date):
        raise TypeError(
            "as_of() needs a datetime, not a date. A date-only query cannot "
            "distinguish an 08:30 release from an 08:31 price; pass an explicit "
            "instant with a timezone."
        )
    raise TypeError(f"as_of() needs a timezone-aware datetime, got {type(t).__name__}")


def _session(session: QuerySession | None) -> QuerySession:
    return session or default_session()


# --------------------------------------------------------------------------
# Macro
# --------------------------------------------------------------------------

_MACRO_SQL = """
SELECT series_id, ref_period, known_at, value, vintage_seq, known_at_precision
  FROM (
    SELECT *,
           row_number() OVER (PARTITION BY series_id, ref_period
                              ORDER BY known_at DESC, vintage_seq DESC) AS rn
      FROM macro_observation
     WHERE known_at <= ?
       AND series_id = ?
       AND (? IS NULL OR ref_period = ?)
  )
 WHERE rn = 1
 ORDER BY ref_period
"""


def macro_as_of(
    t: datetime,
    series_id: str,
    ref_period: date | None = None,
    *,
    session: QuerySession | None = None,
) -> list[MacroFact]:
    """Macro facts publicly known at `t`.

    Filters `known_at <= t` and keeps the row with the greatest `known_at` per
    (series_id, ref_period). Returns [] when nothing was known yet — never a
    later vintage, never a placeholder, never a zero.

    A NULL value is a real fact, not an absence: a release published as missing
    has a `known_at` and must not be skipped in favour of the next numeric
    vintage. The window ranks by `known_at` alone, so NULLs rank normally.
    """
    t = _require_instant(t)
    rows = _session(session).con.execute(
        _MACRO_SQL, [t, series_id, ref_period, ref_period]
    ).fetchall()
    return [
        MacroFact(
            series_id=r[0],
            ref_period=r[1],
            known_at=r[2],
            value=r[3],
            vintage_seq=r[4],
            known_at_precision=r[5],
        )
        for r in rows
    ]


# --------------------------------------------------------------------------
# Ticks
# --------------------------------------------------------------------------

# A price is known when it prints, so the point-in-time filter on ticks is
# simply ts <= t.
#
# Two further rules are applied on the READ side only. `tick_raw` remains
# immutable and keeps every defect the feed sent; what the query layer refuses
# to do is hand a structurally impossible quote to research code as though it
# were clean.
#
#   * A crossed quote (bid > ask) is excluded unconditionally. This is a safety
#     floor, deliberately independent of whether the Phase 2 detectors have
#     been run — as_of must be safe on freshly ingested data too.
#   * Duplicates on (instrument, ts, source) are collapsed, and rows are
#     returned in timestamp order. Ordering is a presentation choice; the store
#     preserves the feed's original order so a decode bug stays visible there.
_TICKS_SQL = """
SELECT instrument, ts, bid, ask, bid_volume, ask_volume, source
  FROM (
    SELECT *,
           row_number() OVER (PARTITION BY instrument, ts, source ORDER BY bid) AS rn
      FROM tick_raw
     WHERE ts <= ?
       AND instrument = ?
       AND (? IS NULL OR ts >= ?)
       AND (? IS NULL OR ts <  ?)
       AND bid <= ask
  )
 WHERE rn = 1
 ORDER BY ts, source
"""


def ticks_as_of(
    t: datetime,
    instrument: str,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    session: QuerySession | None = None,
) -> list[Tick]:
    """Ticks observable at `t`, within an optional [start, end) window."""
    t = _require_instant(t)
    rows = _session(session).con.execute(
        _TICKS_SQL, [t, instrument, start, start, end, end]
    ).fetchall()
    return [Tick(*r) for r in rows]


# --------------------------------------------------------------------------
# Bars
# --------------------------------------------------------------------------

_BARS_SQL = """
SELECT instrument, minute, bid_open, bid_high, bid_low, bid_close,
       ask_open, ask_high, ask_low, ask_close, source
  FROM bar_1m
 WHERE minute <= ?
   AND instrument = ?
   AND source = ?
   AND (? IS NULL OR minute >= ?)
   AND (? IS NULL OR minute <  ?)
 ORDER BY minute
"""


def bars_as_of(
    t: datetime,
    instrument: str,
    source: str,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    session: QuerySession | None = None,
) -> list[Bar]:
    """Bid/ask bars observable at `t`, from a named feed.

    `source` is required rather than defaulted: there is no consolidated tape
    in FX, so every bar is conditional on which feed produced it, and making
    the caller name the feed keeps that conditionality visible.

    Feeds are never merged, averaged, or backfilled from one another here.
    Where two feeds disagree the disagreement survives the read, because the
    disagreement rate is itself a project deliverable — silently reconciling
    it would destroy an output rather than merely hide a bug.
    """
    t = _require_instant(t)
    rows = _session(session).con.execute(
        _BARS_SQL, [t, instrument, source, start, start, end, end]
    ).fetchall()
    return [
        Bar(
            instrument=r[0], ts=r[1],
            bid_open=r[2], bid_high=r[3], bid_low=r[4], bid_close=r[5],
            ask_open=r[6], ask_high=r[7], ask_low=r[8], ask_close=r[9],
            source=r[10],
        )
        for r in rows
    ]
