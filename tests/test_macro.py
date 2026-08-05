"""Phase 3 macro tests - RTDSM parsing and the conservative timestamp policy."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fxpit.macro import loader, rtdsm

# --------------------------------------------------------------------------
# Vintage and reference-period parsing
# --------------------------------------------------------------------------


def test_two_digit_vintage_year_pivots_at_1964():
    """RTDSM begins in 1964, so 64-99 are 19xx and 00-63 are 20xx. A pivot in
    the wrong place would silently date 2020s vintages to the 1920s.
    """
    assert rtdsm._parse_vintage("EMPLOY64M12") == (1964, 12)
    assert rtdsm._parse_vintage("EMPLOY99M1") == (1999, 1)
    assert rtdsm._parse_vintage("EMPLOY00M1") == (2000, 1)
    assert rtdsm._parse_vintage("EMPLOY26M6") == (2026, 6)


def test_quarterly_vintage_maps_to_the_last_month_of_its_quarter():
    """Conservative: a Q1 vintage is dated to March, never to January. Dating
    it to the first month would claim the data was available before it was.
    """
    assert rtdsm._parse_vintage("ROUTPUT65Q1") == (1965, 3)
    assert rtdsm._parse_vintage("ROUTPUT65Q4") == (1965, 12)


def test_non_vintage_headers_are_ignored():
    assert rtdsm._parse_vintage("DATE") is None
    assert rtdsm._parse_vintage("") is None
    assert rtdsm._parse_vintage("EMPLOY64M13") is None


def test_reference_periods_parse_monthly_and_quarterly():
    assert rtdsm._parse_ref_period("1943:11") == date(1943, 11, 1)
    assert rtdsm._parse_ref_period("1947:Q1") == date(1947, 1, 1)
    assert rtdsm._parse_ref_period("1947:Q4") == date(1947, 10, 1)
    assert rtdsm._parse_ref_period("garbage") is None


# --------------------------------------------------------------------------
# The conservative timestamp policy
# --------------------------------------------------------------------------


def test_vintage_timestamp_is_the_last_instant_of_its_month():
    """RTDSM says only that a value was current during a month. Placing
    known_at at month END means a point-in-time query treats it as
    not-yet-public for as long as the data permits.

    Under-reporting what was knowable is a conservative research error.
    Over-reporting it is look-ahead bias, which is the failure this database
    exists to prevent - so the bias must point this way, not the other.
    """
    ts = rtdsm.vintage_known_at(2024, 2)
    assert ts.year == 2024 and ts.month == 2 and ts.day == 29  # leap year
    assert ts.tzinfo is not None
    assert ts < datetime(2024, 3, 1, tzinfo=UTC)
    assert ts > datetime(2024, 2, 28, 23, 59, tzinfo=UTC)


def test_december_vintage_rolls_into_the_next_year():
    ts = rtdsm.vintage_known_at(2024, 12)
    assert ts.year == 2024 and ts.month == 12 and ts.day == 31


def test_month_end_placement_never_precedes_the_month():
    for month in range(1, 13):
        ts = rtdsm.vintage_known_at(2023, month)
        assert ts >= datetime(2023, month, 1, tzinfo=UTC)


# --------------------------------------------------------------------------
# Rebasing - the trap that produced a wrong number before it was caught
# --------------------------------------------------------------------------


def test_rebased_series_are_excluded_from_revision_rankings():
    """Real GNP reads 1,684.8 in 1982 dollars and 3,584.1 after rebasing. That
    113% "revision" is a units change, not new information, and it survives
    even a single-vintage step - no comparison window is tight enough.

    Ranking those series by level difference would produce exactly the kind of
    plausible-looking wrong number this project exists to catch, so they are
    excluded explicitly rather than quietly included.
    """
    assert "ROUTPUT" in loader.REBASED
    assert "CPI" in loader.REBASED
    assert "EMPLOY" in loader.UNITS_STABLE
    assert not (loader.UNITS_STABLE & set(loader.REBASED)), (
        "a series cannot be both stable-unit and rebased"
    )


def test_every_catalogued_series_is_classified():
    """A new series must be deliberately placed on one side or the other. An
    unclassified series would silently default into whichever behaviour the
    query happened to have.
    """
    for series in rtdsm.CATALOGUE:
        assert series.series_id in loader.UNITS_STABLE or series.series_id in loader.REBASED, (
            f"{series.series_id} is neither declared unit-stable nor declared rebased"
        )


# --------------------------------------------------------------------------
# Download hardening
# --------------------------------------------------------------------------


def test_soft_404_html_is_rejected_rather_than_saved_as_xlsx(monkeypatch):
    """The Phila Fed serves HTTP 200 with an HTML error page when the Sitecore
    hash is missing. Saving that under a .xlsx name fails much later with a
    confusing message, so it is caught at download time.
    """
    monkeypatch.setattr(rtdsm, "discover_file_url", lambda s: "https://example.invalid/x.xlsx")
    monkeypatch.setattr(rtdsm, "_fetch", lambda url, timeout=120: b"<!DOCTYPE html><html>")
    with pytest.raises(RuntimeError, match="not a valid xlsx"):
        rtdsm.download(rtdsm.CATALOGUE[0])


def test_missing_link_raises_rather_than_guessing_a_url(monkeypatch):
    monkeypatch.setattr(rtdsm, "discover_file_url", lambda s: None)
    with pytest.raises(RuntimeError, match="No vintage file found"):
        rtdsm.download(rtdsm.CATALOGUE[0])


# --------------------------------------------------------------------------
# Integration - the real store
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_as_of_returns_first_print_then_revision_on_real_data():
    """The whole mechanism, against the loaded RTDSM archive rather than a
    fixture: December 2009 payrolls had 1.36 million jobs revised away at the
    first revision, and a point-in-time query must show the pre-revision figure
    to anyone asking in February 2010.
    """
    from fxpit.query import macro_as_of, open_production_session

    session = open_production_session(series=["EMPLOY"])
    try:
        n = session.con.execute("SELECT count(*) FROM macro_observation").fetchone()[0]
        if n == 0:
            pytest.skip("no macro observations loaded")

        ref = date(2009, 12, 1)
        before = macro_as_of(datetime(2010, 1, 15, tzinfo=UTC), "EMPLOY", ref, session=session)
        first = macro_as_of(datetime(2010, 2, 15, tzinfo=UTC), "EMPLOY", ref, session=session)
        later = macro_as_of(datetime(2010, 3, 15, tzinfo=UTC), "EMPLOY", ref, session=session)

        assert before == [], "December 2009 payrolls were not public in January 2010"
        assert first and later
        assert first[0].vintage_seq == 1
        assert first[0].value != later[0].value, "expected a revision between these dates"
        assert first[0].value > later[0].value, "the first print was revised downward"
    finally:
        session.close()


@pytest.mark.integration
def test_vintage_seq_agrees_with_chronology_on_real_data():
    """vintage_seq exists so "first print vs final" needs no self-join, and is
    only trustworthy if it agrees with known_at order. It is derived on load
    rather than taken from the source for exactly this reason.
    """
    conn = loader.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM macro_observation WHERE series_id = %s", ("EMPLOY",))
            if cur.fetchone()[0] == 0:
                pytest.skip("no macro observations loaded")
            cur.execute(
                """
                SELECT count(*) FROM (
                  SELECT series_id, ref_period, vintage_seq,
                         row_number() OVER (PARTITION BY series_id, ref_period
                                            ORDER BY known_at) AS expected
                    FROM macro_observation WHERE series_id = %s
                ) AS t WHERE vintage_seq <> expected
                """,
                ("EMPLOY",),
            )
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()
