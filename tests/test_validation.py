"""Phase 5 validation-harness tests.

The anchor's whole purpose is to catch a class of bug that produces no error,
so the tests are mostly about whether it has POWER — would it actually fire on
the bug it exists to detect.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import pytest

from fxpit.validation import ecb, harness

# --------------------------------------------------------------------------
# The concertation instant — derived, never a UTC constant
# --------------------------------------------------------------------------


def test_concertation_instant_moves_with_european_dst():
    """14:15 Frankfurt is 13:15 UTC under CET and 12:15 under CEST.

    Writing either as a constant would make the anchor wrong for half the year
    — an anchor built to catch timezone bugs, itself containing one, reporting
    drift that is really just looking at the wrong minute.
    """
    winter = ecb.concertation_instant(date(2024, 1, 8))
    summer = ecb.concertation_instant(date(2024, 7, 8))
    assert winter.hour == 13 and winter.minute == 15
    assert summer.hour == 12 and summer.minute == 15


def test_concertation_zone_is_a_real_iana_zone():
    """`Europe/Frankfurt` is not an IANA zone and raises at import time. This
    pins the working name so a plausible-looking edit cannot reintroduce it.
    """
    from zoneinfo import ZoneInfo

    assert ecb.CONCERTATION_ZONE == "Europe/Berlin"
    ZoneInfo(ecb.CONCERTATION_ZONE)  # must not raise


def test_concertation_instant_is_timezone_aware():
    assert ecb.concertation_instant(date(2024, 3, 15)).tzinfo is not None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _zip_csv(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("eurofxref-hist.csv", text)
    return buf.getvalue()


def test_parse_reads_one_rate_per_currency_per_day():
    payload = _zip_csv("Date,USD,JPY,GBP\n2024-01-08,1.0944,158.30,0.8600\n")
    rates = ecb.parse(payload)
    assert {r.currency for r in rates} == {"USD", "JPY", "GBP"}
    usd = next(r for r in rates if r.currency == "USD")
    assert usd.rate == pytest.approx(1.0944)
    assert usd.fix_date == date(2024, 1, 8)


def test_parse_skips_na_rather_than_storing_zero():
    """The ECB publishes 'N/A' for currencies that joined the euro or were not
    yet quoted. Storing those as zero would create a real number that happens
    to be wrong, and any comparison using it would be silently poisoned.
    """
    payload = _zip_csv("Date,USD,CYP\n2024-01-08,1.0944,N/A\n")
    rates = ecb.parse(payload)
    assert {r.currency for r in rates} == {"USD"}
    assert all(r.rate != 0 for r in rates)


def test_parse_ignores_malformed_rows_without_failing_the_file():
    payload = _zip_csv("Date,USD\n2024-01-08,1.0944\nnot-a-date,1.1\n2024-01-09,oops\n")
    rates = ecb.parse(payload)
    assert len(rates) == 1


def test_non_zip_payload_is_rejected():
    """A soft-404 HTML page must fail at parse rather than yielding zero rates,
    which would look like "the ECB published nothing today".
    """
    with pytest.raises(zipfile.BadZipFile):
        ecb.parse(b"<html>not a zip</html>")


# --------------------------------------------------------------------------
# Orientation and scope
# --------------------------------------------------------------------------


def test_only_eur_based_pairs_have_a_direct_anchor():
    """ECB quotes units of foreign currency per ONE euro, so EURUSD compares
    one-to-one. Anything else needs a cross, which carries the error of both
    legs and is a weaker test — so it is refused rather than silently done.
    """
    assert ecb.DIRECT_ANCHORS == {"EURUSD": "USD"}


@pytest.mark.integration
def test_drift_anchor_refuses_a_pair_it_cannot_compare_directly():
    conn = harness.connect()
    try:
        with pytest.raises(ValueError, match="no direct ECB anchor"):
            harness.run_drift_anchor(conn, "USDJPY", date(2024, 1, 5), date(2024, 1, 9))
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Does the anchor have power?
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_feed_agrees_with_the_ecb_fix():
    """The headline check: the Dukascopy mid at the concertation instant should
    match the published fix to within about a pip.
    """
    conn = harness.connect()
    try:
        if not harness.ecb_coverage(conn)["rates"]:
            pytest.skip("ECB rates not loaded")
        report = harness.run_drift_anchor(conn, "EURUSD", date(2024, 1, 5), date(2024, 1, 9))
        if not report.compared:
            pytest.skip("no overlapping tick data")
        assert abs(report.mean_pips) <= harness.DRIFT_ALERT_PIPS, (
            f"mean difference {report.mean_pips} pips exceeds the alert threshold - "
            "suspect a timezone or session-boundary bug before suspecting the feed"
        )
    finally:
        conn.close()


@pytest.mark.integration
def test_the_anchor_would_catch_an_hour_sized_timezone_error():
    """A validation check that cannot fail is decoration.

    This deliberately offsets the anchor instant and asserts the difference
    blows past the alert threshold — including by the exact 5h45m the Phase 4
    export bug produced on a UTC+5:45 machine.

    The +1h and +5h45m cases are asserted; -1h is NOT, because with only a
    handful of observations the anchor genuinely lacks the power to catch it.
    Asserting it would be pretending to a sensitivity the data does not
    support.
    """
    from fxpit.ingest import store as ch_store

    conn = harness.connect()
    client = ch_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fix_date, rate FROM ecb_reference_rate "
                " WHERE currency='USD' AND fix_date >= %s AND fix_date < %s",
                (date(2024, 1, 5), date(2024, 1, 9)),
            )
            fixes = cur.fetchall()
        if not fixes:
            pytest.skip("ECB rates not loaded")

        pip = ecb.pip_size("EURUSD")

        def mean_diff(shift: timedelta) -> float | None:
            diffs = []
            for fix_date, rate in fixes:
                anchor = ecb.concertation_instant(fix_date) + shift
                r = client.query(
                    "SELECT count(), avg((bid+ask)/2) FROM tick_raw "
                    " WHERE instrument='EURUSD' AND ts >= %(lo)s AND ts <= %(hi)s "
                    "   AND bid <= ask",
                    parameters={"lo": anchor - timedelta(seconds=60),
                                "hi": anchor + timedelta(seconds=60)},
                )
                n, mid = r.result_rows[0]
                if n and mid is not None:
                    diffs.append((float(mid) - float(rate)) / pip)
            return sum(diffs) / len(diffs) if diffs else None

        correct = mean_diff(timedelta(0))
        if correct is None:
            pytest.skip("no overlapping tick data")
        assert abs(correct) <= harness.DRIFT_ALERT_PIPS

        for label, shift in [
            ("+1h", timedelta(hours=1)),
            ("machine-local +5h45m", timedelta(hours=5, minutes=45)),
        ]:
            broken = mean_diff(shift)
            assert broken is not None, f"{label}: no ticks to compare"
            assert abs(broken) > harness.DRIFT_ALERT_PIPS, (
                f"{label} offset produced {broken:.2f} pips, under the "
                f"{harness.DRIFT_ALERT_PIPS} pip threshold - the anchor would not "
                "catch this class of bug"
            )
    finally:
        conn.close()
        client.close()


# --------------------------------------------------------------------------
# Monitors
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_spread_monitor_reports_quantiles_not_means():
    """Spreads are positive and heavy-tailed; a mean is dragged by exactly the
    rollover and news spikes the monitor exists to surface.
    """
    rows = harness.spread_by_hour()
    if not rows:
        pytest.skip("no ticks ingested")
    r = rows[0]
    assert {"median_spread", "p95_spread", "max_spread"} <= set(r)
    assert "mean_spread" not in r
    for row in rows:
        assert row["median_spread"] <= row["p95_spread"] <= row["max_spread"]


@pytest.mark.integration
def test_tick_rate_monitor_is_relative_not_absolute():
    """Tick rates differ by an order of magnitude between instruments and
    sessions, so an absolute floor would flag every Asian hour and miss a real
    outage in London.
    """
    rows = harness.tick_rate_by_hour()
    if not rows:
        pytest.skip("no ticks ingested")
    assert "ratio_to_median" in rows[0]
    assert rows == sorted(rows, key=lambda r: r["ratio_to_median"]), (
        "quietest hours should come first - the monitor exists to surface drops"
    )


@pytest.mark.integration
def test_drift_observations_are_persisted_for_trend_analysis():
    """A single day's difference means nothing. Observations are stored so the
    series can be watched for a consistent sign over time.
    """
    conn = harness.connect()
    try:
        harness.ensure_schema(conn)
        obs = harness.drift_observations(conn)
        if not obs:
            pytest.skip("drift anchor not run")
        for o in obs:
            assert o["anchor_ts"].tzinfo is not None, "anchor instant must stay tz-aware"
            assert o["ticks_in_window"] > 0
            assert isinstance(o["diff_pips"], float)
    finally:
        conn.close()
