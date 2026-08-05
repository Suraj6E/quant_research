"""Phase 6 contamination-experiment tests.

The experiment's credibility rests on two things being true: the variants differ
in exactly one dimension each, and the rule was fixed before the results were
seen. Both are testable, so both are tested.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import pytest

from fxpit.experiment import releases as rel
from fxpit.experiment import run as runner  # module, not the function
from fxpit.experiment import variants as V

# --------------------------------------------------------------------------
# Pre-registration
# --------------------------------------------------------------------------


def test_preregistration_document_exists_and_is_hashed():
    """The point of pre-registering is that the rule was fixed before the
    numbers were seen. That claim is only checkable if the document can be
    shown to be the one the run used, so its hash travels with every result.
    """
    assert runner.PREREG.exists(), "docs/preregistration.md is missing"
    digest = runner.preregistration_hash()
    assert digest != "MISSING"
    assert digest == hashlib.sha256(runner.PREREG.read_bytes()).hexdigest()[:16]


def test_preregistration_fixes_the_parameters_the_code_uses():
    """A pre-registration that disagrees with the code is worse than none: it
    documents a rule nobody ran.
    """
    text = runner.PREREG.read_text(encoding="utf-8")
    assert f"HOLD_MINUTES = {V.HOLD_MINUTES}" in text
    assert str(runner.EVENTS_PER_YEAR) in text
    assert "EURUSD" in text
    for key in ("A", "B", "C", "D"):
        assert f"**{key} —" in text or f"**{key} -" in text


# --------------------------------------------------------------------------
# The variants differ in exactly one dimension each
# --------------------------------------------------------------------------


def test_each_variant_adds_exactly_one_advantage():
    """A -> B revised values, B -> C date-only timing, C -> D mid price.

    If a variant changed two things at once, its gap would measure both and the
    decomposition would be meaningless — which is the entire deliverable.
    """
    a, b, c, d = (V.BY_KEY[k] for k in "ABCD")

    assert (a.use_revised, a.date_only, a.use_mid) == (False, False, False)
    assert (b.use_revised, b.date_only, b.use_mid) == (True, False, False)
    assert (c.use_revised, c.date_only, c.use_mid) == (True, True, False)
    assert (d.use_revised, d.date_only, d.use_mid) == (True, True, True)

    def flags(v):
        return (v.use_revised, v.date_only, v.use_mid)

    for x, y in ((a, b), (b, c), (c, d)):
        changed = sum(1 for i, j in zip(flags(x), flags(y), strict=True) if i != j)
        assert changed == 1, f"{x.key}->{y.key} changes {changed} dimensions, not 1"


def test_variant_a_is_the_only_honest_one():
    a = V.BY_KEY["A"]
    assert not any((a.use_revised, a.date_only, a.use_mid))
    for key in "BCD":
        assert any((V.BY_KEY[key].use_revised, V.BY_KEY[key].date_only,
                    V.BY_KEY[key].use_mid))


# --------------------------------------------------------------------------
# Execution model
# --------------------------------------------------------------------------


def test_real_execution_crosses_the_spread_both_ways():
    """A buy enters at the ask and exits at the bid; a sell does the reverse.
    Getting this backwards would hand every variant a free spread and destroy
    the C -> D measurement.
    """
    a = V.BY_KEY["A"]
    bid, ask = 1.0900, 1.0902

    assert V.fill_prices(a, bid, ask, +1) == ask, "a buy pays the ask"
    assert V.exit_price(a, bid, ask, +1) == bid, "closing a long receives the bid"
    assert V.fill_prices(a, bid, ask, -1) == bid, "a sell receives the bid"
    assert V.exit_price(a, bid, ask, -1) == ask, "covering a short pays the ask"


def test_mid_variant_saves_exactly_one_spread_per_round_trip():
    """C -> D measures the mid-price assumption, so D's advantage must be
    exactly one full spread — no more, no less.
    """
    c, d = V.BY_KEY["C"], V.BY_KEY["D"]
    bid, ask = 1.0900, 1.0902
    spread = ask - bid

    real_cost = V.fill_prices(c, bid, ask, +1) - V.exit_price(c, bid, ask, +1)
    mid_cost = V.fill_prices(d, bid, ask, +1) - V.exit_price(d, bid, ask, +1)
    assert real_cost == pytest.approx(spread)
    assert mid_cost == pytest.approx(0.0)


def test_positive_surprise_sells_eurusd():
    """Stronger-than-trend US data implies a stronger dollar, so a positive
    surprise is short EURUSD. A sign error here would flip every result.
    """
    r = _release(surprise_first=50.0, surprise_final=-50.0)
    assert V.signal(V.BY_KEY["A"], r) == -1, "positive surprise -> short EURUSD"
    assert V.signal(V.BY_KEY["B"], r) == +1, "B reads the revised surprise"


def test_zero_surprise_does_not_trade():
    assert V.signal(V.BY_KEY["A"], _release(0.0, 0.0)) == 0


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


def test_release_instant_moves_with_us_dst():
    """08:30 New York is 13:30 UTC in winter and 12:30 in summer. Written as a
    UTC constant it would be wrong for half the year — and B -> C exists
    precisely to measure what timestamp precision is worth.
    """
    assert rel.release_instant(date(2024, 1, 5)).hour == 13
    assert rel.release_instant(date(2024, 7, 5)).hour == 12


def test_date_only_variants_enter_before_the_release():
    """This is the contamination, not a bug: a date-only macro column cannot
    distinguish an 08:30 print from an 08:29 price, so a simulation built on
    one acts on news it had not heard — here, about thirteen hours early.
    """
    r = _release()
    exact = V.entry_instant(V.BY_KEY["B"], r)
    coarse = V.entry_instant(V.BY_KEY["C"], r)
    assert coarse < exact
    assert coarse.hour == 0 and coarse.minute == 0
    assert (exact - coarse).total_seconds() / 3600 > 12


def test_hold_period_is_the_preregistered_value():
    entry = datetime(2024, 1, 5, 13, 30, tzinfo=UTC)
    assert (V.hold_until(entry) - entry).total_seconds() / 60 == V.HOLD_MINUTES


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def _release(surprise_first: float = 100.0, surprise_final: float = 100.0) -> rel.Release:
    day = date(2024, 1, 5)
    return rel.Release(
        series_id="PAYEMS",
        release_date=day,
        release_ts=rel.release_instant(day),
        ref_period=date(2023, 12, 1),
        first_print=157000.0,
        final_value=156800.0,
        surprise_first=surprise_first,
        surprise_final=surprise_final,
    )


def _trade(direction: int, entry: float, exit_: float) -> runner.Trade:
    return runner.Trade(
        release=_release(),
        direction=direction,
        entry_ts=datetime(2024, 1, 5, 13, 30, tzinfo=UTC),
        exit_ts=datetime(2024, 1, 5, 14, 0, tzinfo=UTC),
        entry_price=entry,
        exit_price=exit_,
    )


def test_short_profits_when_price_falls():
    t = _trade(-1, 1.1000, 1.0900)
    assert t.return_bps > 0
    assert t.return_bps == pytest.approx(90.909, abs=0.01)


def test_long_loses_when_price_falls():
    assert _trade(+1, 1.1000, 1.0900).return_bps < 0


def test_sharpe_needs_a_distribution():
    """A Sharpe ratio from one trade is not a small sample, it is undefined.
    Returning 0 rather than raising keeps the report renderable, but the CLI
    refuses to print a result table below two trades.
    """
    r = runner.VariantResult(V.BY_KEY["A"])
    assert r.sharpe == 0.0
    r.trades.append(_trade(+1, 1.10, 1.11))
    assert r.sharpe == 0.0, "one trade cannot produce a Sharpe ratio"


def test_metrics_on_a_known_series():
    r = runner.VariantResult(V.BY_KEY["A"])
    r.trades = [_trade(+1, 1.0, 1.0 + x / 10_000) for x in (10, -5, 20, -5)]
    assert r.n == 4
    assert r.mean_bps == pytest.approx(5.0, abs=0.01)
    assert r.hit_rate == pytest.approx(0.5)
    assert r.total_bps == pytest.approx(20.0, abs=0.05)
    assert r.sharpe != 0.0


# --------------------------------------------------------------------------
# Integration — the real run
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_all_variants_trade_the_same_event_set():
    """If the arms traded different events their differences would partly
    measure sample composition rather than contamination. An event is only used
    when every variant can price it.
    """
    import json

    from fxpit.config import ROOT

    cache = ROOT / "data" / "experiment_releases.json"
    if not cache.exists():
        pytest.skip("release cache not built")
    raw = json.loads(cache.read_text(encoding="utf-8"))
    events = [
        rel.Release(
            series_id=r["series_id"],
            release_date=date.fromisoformat(r["release_date"]),
            release_ts=datetime.fromisoformat(r["release_ts"]),
            ref_period=date.fromisoformat(r["ref_period"]),
            first_print=r["first_print"],
            final_value=r["final_value"],
            surprise_first=r["surprise_first"],
            surprise_final=r["surprise_final"],
        )
        for r in raw
    ]
    exp = runner.run_experiment(events)
    traded = {k: {t.release.release_date for t in v.trades} for k, v in exp.results.items()}
    if not traded["A"]:
        pytest.skip("no tick data covering the release days")

    # Variants may differ in WHICH events produce a signal (a revised surprise
    # can flip sign or go to zero), but never in which events were priceable.
    priced = {k: v.skipped_no_price for k, v in exp.results.items()}
    assert len(set(priced.values())) == 1, (
        f"variants saw different priceable event counts: {priced}"
    )
