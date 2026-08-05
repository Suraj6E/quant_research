"""Family 4 - cross-feed reconciliation.

The guarantee: where Dukascopy and HistData disagree materially on the same
bar, the disagreement is REPORTED. HistData is a second opinion, not a
fallback - silently preferring one feed destroys the disagreement rate, which
is itself a deliverable (success criterion #3).

Failure mode in one sentence: the two feeds disagreed and the system picked a
winner without telling anyone, so a data-quality problem was laundered into a
confident-looking number.
"""

from __future__ import annotations

import pytest

from fxpit.query import bars_as_of

pytestmark = pytest.mark.acceptance


def _by_ts(bar_rows, source):
    return {r.ts: r for r in bar_rows if r.source == source}


def _material(duka, hist, tolerance):
    return [
        ts
        for ts in duka.keys() & hist.keys()
        if abs(duka[ts].bid_close - hist[ts].bid_close) > tolerance
        or abs(duka[ts].ask_close - hist[ts].ask_close) > tolerance
    ]


# ----------------------------------------------------------------- fixture


def test_fixture_has_both_feeds(bar_rows):
    sources = {r.source for r in bar_rows}
    assert sources == {"dukascopy", "histdata"}


def test_fixture_contains_material_disagreements(bar_rows, tolerance):
    """Two bars differ beyond tolerance by construction. Without them the
    reconciliation tests below would pass on a system that never compares.
    """
    duka, hist = _by_ts(bar_rows, "dukascopy"), _by_ts(bar_rows, "histdata")
    disagreements = _material(duka, hist, tolerance)
    assert len(disagreements) == 2, (
        f"expected 2 material disagreements in the fixture, found {len(disagreements)}"
    )


def test_fixture_contains_a_within_tolerance_difference(bar_rows, tolerance):
    """Not every difference is a disagreement. A detector that flags every
    non-identical bar is useless - it would flag rounding.
    """
    duka, hist = _by_ts(bar_rows, "dukascopy"), _by_ts(bar_rows, "histdata")
    small = [
        ts
        for ts in duka.keys() & hist.keys()
        if 0 < abs(duka[ts].bid_close - hist[ts].bid_close) <= tolerance
    ]
    assert small, "fixture must contain a difference within tolerance"


def test_fixture_contains_a_coverage_gap(bar_rows):
    """A bar present in one feed and absent from the other is a coverage gap,
    not agreement. Treating missing as matching is how gaps become invisible.
    """
    duka, hist = _by_ts(bar_rows, "dukascopy"), _by_ts(bar_rows, "histdata")
    assert duka.keys() - hist.keys(), "fixture must contain a bar missing from histdata"


# ------------------------------------------------------------ query layer


def test_feeds_are_addressable_separately(bar_rows):
    """`source` is a required argument precisely so that 'the price' is never
    answerable without naming a feed. There is no consolidated tape in FX.
    """
    latest = max(r.ts for r in bar_rows)
    duka = bars_as_of(latest, "EURUSD", source="dukascopy")
    hist = bars_as_of(latest, "EURUSD", source="histdata")
    assert duka, "no dukascopy bars returned"
    assert hist, "no histdata bars returned"
    assert len(duka) != len(hist), (
        "both feeds returned the same number of bars; the fixture has a "
        "coverage gap, so this suggests one feed was silently substituted "
        "for the other"
    )


def test_disagreements_are_not_silently_reconciled(bar_rows, tolerance):
    """The two feeds must come back as they are. If the values have been
    averaged, snapped together, or overwritten from a preferred feed, the
    disagreement rate can no longer be measured.
    """
    latest = max(r.ts for r in bar_rows)
    duka = {b.ts: b for b in bars_as_of(latest, "EURUSD", source="dukascopy")}
    hist = {b.ts: b for b in bars_as_of(latest, "EURUSD", source="histdata")}

    differing = _material(duka, hist, tolerance)
    assert differing, (
        "the feeds agreed on every overlapping bar, but the fixture contains "
        "two material disagreements - they were reconciled away in the read path"
    )


def test_a_gap_in_one_feed_is_not_filled_from_the_other(bar_rows):
    """Backfilling a missing HistData bar from Dukascopy turns a coverage gap
    into fabricated agreement.
    """
    latest = max(r.ts for r in bar_rows)
    duka = {b.ts for b in bars_as_of(latest, "EURUSD", source="dukascopy")}
    hist = {b.ts for b in bars_as_of(latest, "EURUSD", source="histdata")}
    assert duka - hist, "histdata gained a bar it does not have; gap was backfilled"
