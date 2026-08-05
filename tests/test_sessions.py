"""Phase 4 session and calendar tests.

The unit tests need no database: they exercise the local-time-to-UTC
conversion, which is where every interesting property of this phase lives.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fxpit.sessions import definitions as defs

# --------------------------------------------------------------------------
# Daylight saving — the reason this phase exists
# --------------------------------------------------------------------------


def test_london_new_york_overlap_is_four_hours_normally():
    """Both jurisdictions on the same DST footing: London 08:00-17:00 and
    New York 08:00-17:00 are five hours apart, leaving a four-hour overlap.
    """
    _, _, hours = defs.london_ny_overlap(date(2024, 1, 15))  # deep winter, both off
    assert hours == pytest.approx(4.0)
    _, _, hours = defs.london_ny_overlap(date(2024, 7, 15))  # deep summer, both on
    assert hours == pytest.approx(4.0)


def test_overlap_stretches_when_us_and_eu_are_out_of_step():
    """The US springs forward on the 2nd Sunday of March and the EU on the
    last, so for about three weeks New York is an hour closer to London and
    the overlap is FIVE hours instead of four.

    This is the artefact hypothesis H6 predicts, and it is computed here rather
    than tabulated so it stays correct if a jurisdiction changes its rules.
    """
    _, _, hours = defs.london_ny_overlap(date(2024, 3, 20))
    assert hours == pytest.approx(5.0)


def test_dst_offset_weeks_finds_both_windows_each_year():
    """Two anomaly runs per year: spring (US shifts first) and autumn (EU
    shifts first). Both stretch the overlap rather than shrinking it.
    """
    weeks = defs.dst_offset_weeks(2024)
    assert len(weeks) == 2, f"expected 2 anomaly windows, got {len(weeks)}"
    for w in weeks:
        assert w["delta_hours"] == pytest.approx(1.0)
        assert w["overlap_hours"] == pytest.approx(5.0)
        assert w["normal_hours"] == pytest.approx(4.0)
    spring, autumn = weeks
    assert spring["start"].month == 3
    assert autumn["start"].month in (10, 11)


def test_the_anomaly_recurs_in_other_years():
    """If this were tabulated for one year it would rot silently. It is
    derived, so it holds for years nobody thought about.
    """
    for year in (2023, 2025, 2026):
        weeks = defs.dst_offset_weeks(year)
        assert len(weeks) == 2, f"{year}: expected 2 windows, got {len(weeks)}"


def test_tokyo_has_no_daylight_saving():
    """Japan does not observe DST, so the Tokyo session sits at a fixed UTC
    offset all year. A session layer that applied a blanket northern-hemisphere
    rule would move it twice a year and be wrong both times.
    """
    winter = [w for w in defs.session_windows(date(2024, 1, 15), date(2024, 1, 16))
              if w.label == "tokyo"][0]
    summer = [w for w in defs.session_windows(date(2024, 7, 15), date(2024, 7, 16))
              if w.label == "tokyo"][0]
    assert winter.start.hour == summer.start.hour


def test_sydney_shifts_in_the_opposite_season():
    """Australia is in the southern hemisphere: its clocks go FORWARD in
    October and back in April, opposite to Europe and North America. Sydney's
    UTC offset in January differs from July in the reverse direction to
    London's.
    """
    def start_hour(day: date, label: str) -> int:
        return [w for w in defs.session_windows(day, date(day.year, day.month, day.day + 1))
                if w.label == label][0].start.hour

    syd_jan = start_hour(date(2024, 1, 15), "sydney")
    syd_jul = start_hour(date(2024, 7, 15), "sydney")
    lon_jan = start_hour(date(2024, 1, 15), "london")
    lon_jul = start_hour(date(2024, 7, 15), "london")

    assert syd_jan != syd_jul, "Sydney should observe DST"
    assert lon_jan != lon_jul, "London should observe DST"
    # Opposite seasons: one is earlier in January, the other later.
    assert (syd_jan - syd_jul) * (lon_jan - lon_jul) < 0, (
        "Sydney and London should shift in opposite directions between "
        "January and July"
    )


# --------------------------------------------------------------------------
# Rollover — the Phase 2 defect this phase corrects
# --------------------------------------------------------------------------


def test_rollover_is_2200_utc_in_winter_and_2100_in_summer():
    """17:00 New York. That is 22:00 UTC under EST and 21:00 under EDT.

    The Phase 2 detector hardcoded 21:00 UTC, which made it right in summer and
    an hour wrong for every winter tick — including the entire January sample
    the project has ingested.
    """
    winter = defs.rollover_windows(date(2024, 1, 8), date(2024, 1, 9))[0]
    summer = defs.rollover_windows(date(2024, 7, 8), date(2024, 7, 9))[0]
    assert winter.start.astimezone(UTC).hour == 22
    assert summer.start.astimezone(UTC).hour == 21


def test_rollover_windows_are_exactly_one_hour():
    """The ClickHouse mirror flattens these to hour buckets, which is only
    lossless because every window is exactly one hour on an hour boundary.
    """
    for window in defs.rollover_windows(date(2024, 3, 1), date(2024, 4, 1)):
        assert window.duration_hours == pytest.approx(1.0)
        assert window.start.astimezone(UTC).minute == 0


# --------------------------------------------------------------------------
# The FX week
# --------------------------------------------------------------------------


def test_market_week_runs_sunday_to_friday():
    windows = defs.market_windows(date(2024, 1, 1), date(2024, 2, 1))
    assert windows
    for w in windows:
        assert w.start.astimezone(UTC).weekday() in (6, 0), "week should open Sunday NY"
        assert w.duration_hours == pytest.approx(120.0, abs=1.0), "about five days"


def test_market_week_boundary_moves_with_daylight_saving():
    """Defined at 17:00 New York, so the UTC boundary shifts by an hour across
    the DST transition without anything in the code knowing DST exists.
    """
    winter = defs.market_windows(date(2024, 1, 1), date(2024, 1, 20))[0]
    summer = defs.market_windows(date(2024, 7, 1), date(2024, 7, 20))[0]
    assert winter.start.astimezone(UTC).hour != summer.start.astimezone(UTC).hour


# --------------------------------------------------------------------------
# Pair legs
# --------------------------------------------------------------------------


def test_every_pair_has_two_distinct_legs():
    for pair, legs in defs.PAIR_LEGS.items():
        assert len(legs) == 2, pair
        assert legs[0] != legs[1], pair


def test_every_leg_has_a_holiday_calendar():
    """A currency with no calendar would make every day look like a normal
    trading day — wrong rather than merely incomplete.
    """
    currencies = {c for legs in defs.PAIR_LEGS.values() for c in legs}
    missing = currencies - set(defs.CURRENCY_COUNTRY)
    assert not missing, f"no holiday calendar mapped for {sorted(missing)}"


# --------------------------------------------------------------------------
# Integration — the store, and the timezone regression
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_describe_rejects_a_naive_timestamp():
    """A naive timestamp would be read in the server's local time and silently
    answer about a different instant.
    """
    from fxpit.sessions import store

    conn = store.connect()
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            store.describe(conn, datetime(2024, 1, 8, 13, 0))  # noqa: DTZ001
    finally:
        conn.close()


@pytest.mark.integration
def test_exit_criterion_any_timestamp_answers_all_four_questions():
    """PHASE 4 EXIT CRITERION: for any timestamp, which session, is it
    rollover, is it a holiday for either leg.
    """
    from fxpit.sessions import store

    conn = store.connect()
    try:
        if not store.coverage(conn)["sessions"]:
            pytest.skip("no session windows built")

        overlap = store.describe(conn, datetime(2024, 1, 8, 13, 0, tzinfo=UTC), "EURUSD")
        assert overlap.market_open
        assert set(overlap.sessions) >= {"london", "new_york"}
        assert overlap.in_overlap
        assert not overlap.is_rollover

        roll = store.describe(conn, datetime(2024, 1, 8, 22, 30, tzinfo=UTC), "EURUSD")
        assert roll.is_rollover, "22:30 UTC in January is inside 17:00-18:00 New York"

        july4 = store.describe(conn, datetime(2024, 7, 4, 14, 0, tzinfo=UTC), "EURUSD")
        assert "USD" in july4.holidays

        saturday = store.describe(conn, datetime(2024, 1, 6, 12, 0, tzinfo=UTC), "EURUSD")
        assert not saturday.market_open
    finally:
        conn.close()


@pytest.mark.integration
def test_exported_calendar_hours_are_hour_aligned():
    """REGRESSION TEST.

    The first export stripped tzinfo before inserting, so clickhouse-connect
    read each naive datetime as machine-local and converted it to UTC. On a
    machine at UTC+5:45 every calendar hour landed at :15 past, the detector's
    `toStartOfHour(ts) = c.hour` join matched nothing, and `rollover_window`
    silently returned zero flags instead of raising.

    A silent timezone bug producing plausible-looking wrong data is the exact
    risk planning.md rates as high-likelihood, so it gets a test rather than
    just a fix.
    """
    from fxpit.ingest import store as ch_store

    client = ch_store.connect()
    try:
        total = int(client.query("SELECT count() FROM calendar_hour").result_rows[0][0])
        if total == 0:
            pytest.skip("calendar not exported")
        misaligned = int(
            client.query(
                "SELECT count() FROM calendar_hour WHERE toMinute(hour) != 0 "
                "OR toSecond(hour) != 0"
            ).result_rows[0][0]
        )
        assert misaligned == 0, (
            f"{misaligned} of {total} calendar hours are not hour-aligned - "
            "the export is applying a timezone conversion it should not"
        )
    finally:
        client.close()


@pytest.mark.integration
def test_rollover_flags_land_on_the_correct_utc_hour():
    """The corrected detector must flag 22:00 UTC for January ticks, not the
    21:00 the old hardcoded version used.
    """
    from fxpit.ingest import store as ch_store

    client = ch_store.connect()
    try:
        rows = client.query(
            "SELECT DISTINCT toHour(ts) FROM tick_flag "
            " WHERE flag = 'rollover_window' AND toYYYYMM(ts) = 202401"
        ).result_rows
        if not rows:
            pytest.skip("no rollover flags for January 2024")
        hours = {int(r[0]) for r in rows}
        assert hours == {22}, (
            f"January rollover should be 22:00 UTC (17:00 EST), got {sorted(hours)}"
        )
    finally:
        client.close()
