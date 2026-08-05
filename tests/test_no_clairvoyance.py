"""Family 1 — no-clairvoyance.

The guarantee: for any fact, a query one second before it became public must
not return it. This is the guarantee the entire project exists to provide;
if it fails, nothing else about the database matters.

Failure mode in one sentence: `as_of(t)` returned information that did not
exist at `t`, so every backtest built on it can see the future.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fxpit.query import macro_as_of, ticks_as_of

pytestmark = pytest.mark.acceptance

ONE_SECOND = timedelta(seconds=1)


def test_macro_fact_invisible_one_second_before_release(macro_rows):
    """The core assertion. One second before `known_at`, the fact is not there."""
    for row in macro_rows:
        facts = macro_as_of(row.known_at - ONE_SECOND, row.series_id, row.ref_period)
        assert all(f.known_at < row.known_at for f in facts), (
            f"{row.series_id} {row.ref_period}: a fact known at {row.known_at} "
            f"was visible one second earlier"
        )


def test_macro_fact_visible_at_exact_release_instant(macro_rows):
    """The boundary is inclusive: `known_at <= t`, so the fact IS visible at
    exactly `known_at`. Tested separately because an off-by-one here is the
    difference between a correct filter and one that hides a whole vintage.
    """
    for row in macro_rows:
        facts = macro_as_of(row.known_at, row.series_id, row.ref_period)
        assert facts, f"{row.series_id} {row.ref_period} invisible at its own known_at"
        assert max(f.known_at for f in facts) >= row.known_at


def test_nothing_visible_before_the_earliest_vintage(macro_rows):
    """Before any vintage exists, the answer is an empty result — not a
    placeholder, not a zero, and above all not a later vintage.
    """
    earliest = min(r.known_at for r in macro_rows)
    for series_id in {r.series_id for r in macro_rows}:
        assert macro_as_of(earliest - ONE_SECOND, series_id) == []


def test_ticks_never_returned_from_the_future(tick_rows):
    """A price is known when it prints. `ticks_as_of(t)` must not return a
    tick stamped after `t`.
    """
    for instrument in {r.instrument for r in tick_rows}:
        stamps = sorted(r.ts for r in tick_rows if r.instrument == instrument)
        midpoint = stamps[len(stamps) // 2]
        visible = ticks_as_of(midpoint, instrument)
        assert all(tk.ts <= midpoint for tk in visible), (
            f"{instrument}: ticks_as_of({midpoint}) returned a future tick"
        )


def test_a_date_only_query_is_not_accepted(macro_rows):
    """An 08:30 ET release and an 08:31 ET price are different objects. Passing
    a bare `date` where a `datetime` belongs must not silently succeed by being
    coerced to midnight — that is precisely how timestamp precision is lost.
    """
    row = macro_rows[0]
    with pytest.raises((TypeError, ValueError)):
        macro_as_of(row.ref_period, row.series_id)  # type: ignore[arg-type]
