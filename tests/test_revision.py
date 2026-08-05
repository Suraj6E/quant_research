"""Family 2 — revisions.

The guarantee: asked about a moment between the first print and a later
revision, `as_of` returns the FIRST PRINT — what was believed then, not what
turned out to be true.

Failure mode in one sentence: the query returned a revised value that was not
published until later, so the backtest traded on a number nobody had.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fxpit.query import macro_as_of

pytestmark = pytest.mark.acceptance

# planning.md Phase 0 requires >= 50 revised series-periods against the real
# RTDSM data. The hand-built fixture is deliberately smaller; Phase 3 raises
# this to 50 once real vintages are loaded.
MIN_REVISED_PERIODS_FIXTURE = 8
MIN_REVISED_PERIODS_PRODUCTION = 50


def _vintages(macro_rows, series_id, ref_period):
    rows = [r for r in macro_rows if r.series_id == series_id and r.ref_period == ref_period]
    return sorted(rows, key=lambda r: r.known_at)


def test_fixture_contains_enough_revised_periods(revised_periods):
    """A revision suite that runs on unrevised data proves nothing: an
    implementation that always returns the latest value would pass it.
    """
    assert len(revised_periods) >= MIN_REVISED_PERIODS_FIXTURE, (
        f"only {len(revised_periods)} revised series-periods in the fixture; "
        f"need >= {MIN_REVISED_PERIODS_FIXTURE} for this suite to have power"
    )


def test_returns_first_print_between_publication_and_revision(macro_rows, revised_periods):
    """The central revision assertion."""
    for series_id, ref_period in revised_periods:
        vintages = _vintages(macro_rows, series_id, ref_period)
        first, second = vintages[0], vintages[1]
        midpoint = first.known_at + (second.known_at - first.known_at) / 2

        facts = macro_as_of(midpoint, series_id, ref_period)

        assert len(facts) == 1, f"expected exactly one fact for {series_id} {ref_period}"
        assert facts[0].value == first.value, (
            f"{series_id} {ref_period} as of {midpoint}: got {facts[0].value}, "
            f"expected the first print {first.value} (final was {vintages[-1].value})"
        )


def test_returns_latest_vintage_when_asked_after_all_revisions(macro_rows, revised_periods):
    """The mirror case: after everything is published, the latest vintage wins.
    Without this, an implementation that always returns the FIRST print would
    pass the test above.
    """
    for series_id, ref_period in revised_periods:
        vintages = _vintages(macro_rows, series_id, ref_period)
        final = vintages[-1]
        facts = macro_as_of(final.known_at + timedelta(days=1), series_id, ref_period)
        assert len(facts) == 1
        assert facts[0].value == final.value


def test_each_intermediate_vintage_is_reachable(macro_rows, revised_periods):
    """Every vintage must be observable from some point in time. A store that
    keeps only first-and-final silently discards the middle of the revision
    path, which is where most of the interesting behaviour lives.
    """
    for series_id, ref_period in revised_periods:
        vintages = _vintages(macro_rows, series_id, ref_period)
        for i, v in enumerate(vintages):
            facts = macro_as_of(v.known_at, series_id, ref_period)
            assert len(facts) == 1
            assert facts[0].value == v.value, (
                f"{series_id} {ref_period} vintage {i + 1}/{len(vintages)} unreachable"
            )


def test_vintage_seq_increases_with_known_at(macro_rows, revised_periods):
    """`vintage_seq` exists so 'first print vs final' needs no self-join. It is
    only trustworthy if it agrees with the chronology.
    """
    for series_id, ref_period in revised_periods:
        vintages = _vintages(macro_rows, series_id, ref_period)
        seqs = [v.vintage_seq for v in vintages]
        assert seqs == sorted(seqs), f"{series_id} {ref_period}: vintage_seq out of order"
        assert seqs[0] == 1, f"{series_id} {ref_period}: first print must be vintage_seq=1"


def test_a_null_value_is_a_real_vintage(macro_rows):
    """Unknown means unknown. A release published as missing is a fact with a
    known_at, not an absence — it must not be skipped over in favour of the
    next numeric value.
    """
    nulls = [r for r in macro_rows if r.value is None]
    assert nulls, "fixture should contain at least one NULL-valued vintage"
    for row in nulls:
        facts = macro_as_of(row.known_at, row.series_id, row.ref_period)
        assert len(facts) == 1
        assert facts[0].value is None, (
            f"{row.series_id} {row.ref_period}: NULL vintage was skipped, "
            f"got {facts[0].value} from a later publication"
        )
