"""Family 3 — tick sanity.

The guarantee: ticks returned by the query layer are internally coherent.

Note the asymmetry with the other families. `tick_raw` is immutable and
pathological ticks are NOT deleted — they are flagged additively in
`tick_flag`. So these tests assert two different things:

  * the FIXTURE contains known pathologies (proving the detectors have
    something to find), and
  * the QUERY LAYER either excludes flagged ticks or reports them, but never
    silently returns a crossed quote as if it were clean.

Failure mode in one sentence: a corrupt quote reached research code without
announcing itself, so a spread or return computed from it is silently wrong.
"""

from __future__ import annotations

from collections import Counter

import pytest

from fxpit.query import ticks_as_of

pytestmark = pytest.mark.acceptance


# ----------------------------------------------------------------- fixture
# These run without the query layer: they verify the fixture itself still
# contains the pathologies the detectors are supposed to catch. If someone
# "cleans up" the fixture, the detector tests below become vacuous.


def test_fixture_contains_a_crossed_quote(tick_rows):
    crossed = [r for r in tick_rows if r.bid > r.ask]
    assert len(crossed) == 1, "fixture must contain exactly one crossed quote"


def test_fixture_contains_a_zero_spread_quote(tick_rows):
    zero = [r for r in tick_rows if r.bid == r.ask]
    assert len(zero) == 1, "fixture must contain exactly one zero-spread quote"


def test_fixture_contains_a_duplicate_key(tick_rows):
    keys = Counter((r.instrument, r.ts, r.source) for r in tick_rows)
    dupes = [k for k, n in keys.items() if n > 1]
    assert len(dupes) == 1, "fixture must contain exactly one duplicated key"


def test_fixture_contains_a_backwards_timestamp(tick_rows):
    """Dukascopy ticks are ordered within a file, so a backwards stamp means
    either a decode bug or a genuine feed defect. Either way it must be caught.
    """
    eur = [r for r in tick_rows if r.instrument == "EURUSD"]
    backwards = sum(1 for a, b in zip(eur, eur[1:], strict=False) if b.ts < a.ts)
    assert backwards == 1, "fixture must contain exactly one out-of-order tick"


def test_fixture_contains_a_stale_run(tick_rows):
    """A repeated identical quote is the signature of a stalled feed, and it is
    indistinguishable from a genuinely quiet market without a threshold.
    """
    eur = [r for r in tick_rows if r.instrument == "EURUSD"]
    longest = best = 1
    for a, b in zip(eur, eur[1:], strict=False):
        longest = longest + 1 if (b.bid, b.ask) == (a.bid, a.ask) else 1
        best = max(best, longest)
    assert best >= 4, f"fixture must contain a stale run of >= 4; longest was {best}"


# ------------------------------------------------------------ query layer


def test_query_layer_never_returns_a_crossed_quote(tick_rows):
    latest = max(r.ts for r in tick_rows)
    for instrument in {r.instrument for r in tick_rows}:
        for tk in ticks_as_of(latest, instrument):
            assert tk.bid <= tk.ask, f"{instrument} {tk.ts}: crossed quote returned unflagged"


def test_query_layer_never_returns_a_negative_spread(tick_rows):
    latest = max(r.ts for r in tick_rows)
    for instrument in {r.instrument for r in tick_rows}:
        for tk in ticks_as_of(latest, instrument):
            assert tk.spread >= 0, f"{instrument} {tk.ts}: negative spread {tk.spread}"


def test_query_layer_returns_monotonic_timestamps(tick_rows):
    latest = max(r.ts for r in tick_rows)
    for instrument in {r.instrument for r in tick_rows}:
        stamps = [tk.ts for tk in ticks_as_of(latest, instrument)]
        assert stamps == sorted(stamps), f"{instrument}: timestamps not non-decreasing"


def test_query_layer_deduplicates_on_instrument_ts_source(tick_rows):
    latest = max(r.ts for r in tick_rows)
    for instrument in {r.instrument for r in tick_rows}:
        got = ticks_as_of(latest, instrument)
        keys = Counter((tk.instrument, tk.ts, tk.source) for tk in got)
        dupes = {k: n for k, n in keys.items() if n > 1}
        assert not dupes, f"{instrument}: duplicate keys returned: {dupes}"


def test_bid_and_ask_are_never_collapsed_to_mid(tick_rows):
    """A feed that forces an assumed spread is how backtests lie. Bid and ask
    must survive the round trip as distinct values.
    """
    latest = max(r.ts for r in tick_rows)
    got = ticks_as_of(latest, "EURUSD")
    assert any(tk.bid != tk.ask for tk in got), (
        "every returned tick had bid == ask, which means the query layer "
        "collapsed to a mid price"
    )
