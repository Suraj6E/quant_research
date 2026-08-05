"""Phase 2 cleaning-layer tests.

The unit tests assert structural properties of the detector definitions and
need no database. The integration tests exercise the reversibility guarantee
that the whole phase rests on, and need a live stack with ingested ticks.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fxpit.flags import detectors as det

# --------------------------------------------------------------------------
# Detector definitions
# --------------------------------------------------------------------------


def test_every_detector_has_a_distinct_name():
    names = [d.name for d in det.ALL]
    assert len(names) == len(set(names))


def test_blocked_detectors_declare_why():
    """A flag that cannot be computed must say so rather than silently absent
    itself. The flag taxonomy is a research deliverable; an unexplained gap in
    it reads as "no such pathology" instead of "not measured".
    """
    assert det.BLOCKED, "the blocked set should not be empty while Phase 4 is pending"
    for d in det.BLOCKED:
        assert d.caveat, f"{d.name} is blocked but gives no reason"
        assert not d.sql.strip()


def test_runnable_detectors_are_scoped_by_instrument_and_time():
    """Every detector must be restrictable to a range, otherwise re-running one
    would rescan the entire archive and the delete-then-insert idempotency
    could not be scoped either.
    """
    for d in det.RUNNABLE:
        assert "{instrument:String}" in d.sql, f"{d.name} is not scoped by instrument"
        assert "{start:DateTime64(3, 'UTC')}" in d.sql, f"{d.name} has no start bound"
        assert "{end:DateTime64(3, 'UTC')}" in d.sql, f"{d.name} has no end bound"


def test_detectors_only_read_tick_raw():
    """Detectors must never write to, or read their own output from, tick_raw.
    A detector that consumed tick_flag would make results order-dependent.
    """
    for d in det.RUNNABLE:
        lowered = d.sql.lower()
        assert "insert" not in lowered, f"{d.name} contains INSERT"
        assert "alter" not in lowered, f"{d.name} contains ALTER"
        assert "drop" not in lowered, f"{d.name} contains DROP"
        assert "tick_flag" not in lowered, f"{d.name} reads tick_flag"


def test_rollover_detector_admits_its_dst_limitation():
    """The rollover window moves with daylight saving and the US and EU shift
    on different dates. A fixed UTC hour is wrong for two to three weeks each
    spring and autumn, and that must be recorded rather than discovered later.
    """
    assert "daylight saving" in det.ROLLOVER_WINDOW.caveat.lower()


def test_session_gap_does_not_claim_to_know_the_cause():
    """It reports silence. Calling it `weekend_gap` would assert the cause,
    which needs the Phase 4 calendar - and Dukascopy's empty-body response for
    closed hours means the ingest ledger cannot settle it either.
    """
    assert det.SESSION_GAP.name == "session_gap"
    assert det.WEEKEND_GAP.blocked


# --------------------------------------------------------------------------
# Integration — the reversibility guarantee
# --------------------------------------------------------------------------

RANGE = (datetime(2024, 1, 5, tzinfo=UTC), datetime(2024, 1, 9, tzinfo=UTC))


@pytest.fixture
def client():
    from fxpit.ingest import store

    c = store.connect()
    yield c
    c.close()


def _has_ticks(client) -> bool:
    return int(client.query("SELECT count() FROM tick_raw").result_rows[0][0]) > 0


@pytest.mark.integration
def test_rerunning_a_detector_is_idempotent(client):
    """Delete-then-insert means a second run reproduces the first exactly.

    This is the mechanism behind "delete the flags and re-run" being the
    sanctioned correction path for a wrong detector.
    """
    from fxpit.flags import runner

    if not _has_ticks(client):
        pytest.skip("no ticks ingested")

    first = runner.scan(["EURUSD"], *RANGE, only=["stale"], progress=False)
    second = runner.scan(["EURUSD"], *RANGE, only=["stale"], progress=False)
    assert first.flags_written["stale"] == second.flags_written["stale"]
    assert not second.errors


@pytest.mark.integration
def test_detector_run_does_not_touch_tick_raw(client):
    """The whole design rests on raw being immutable. A detector that altered
    it would be wrong even if its flags were correct.
    """
    from fxpit.flags import runner

    if not _has_ticks(client):
        pytest.skip("no ticks ingested")

    before = int(client.query("SELECT count() FROM tick_raw").result_rows[0][0])
    runner.scan(["EURUSD"], *RANGE, only=["spread_outlier"], progress=False)
    after = int(client.query("SELECT count() FROM tick_raw").result_rows[0][0])
    assert before == after


@pytest.mark.integration
def test_flags_are_deletable_without_losing_ticks(client):
    """Flags are disposable; raw is not. Deleting every flag must leave the
    tick archive untouched and be fully recoverable by re-running.
    """
    from fxpit.flags import runner

    if not _has_ticks(client):
        pytest.skip("no ticks ingested")

    ticks_before = int(client.query("SELECT count() FROM tick_raw").result_rows[0][0])
    client.command("ALTER TABLE tick_flag DELETE WHERE flag = 'stale'",
                   settings={"mutations_sync": 1})
    assert int(client.query(
        "SELECT count() FROM tick_flag WHERE flag = 'stale'").result_rows[0][0]) == 0

    rep = runner.scan(["EURUSD", "GBPUSD", "USDJPY"], *RANGE, only=["stale"], progress=False)
    assert rep.flags_written["stale"] > 0, "flags must be recoverable by re-running"
    assert int(client.query(
        "SELECT count() FROM tick_raw").result_rows[0][0]) == ticks_before


@pytest.mark.integration
def test_bars_keep_bid_and_ask_separate(client):
    """Never mid-only. Mid-price collapse is one of the four contamination
    sources Phase 6 measures, so collapsing here would destroy the experiment
    before it runs.
    """
    if not _has_ticks(client):
        pytest.skip("no ticks ingested")

    cols = {r[0] for r in client.query("DESCRIBE TABLE bar_1m").result_rows}
    for side in ("bid", "ask"):
        for field in ("open", "high", "low", "close"):
            assert f"{side}_{field}" in cols, f"{side}_{field} missing from bar_1m"
    assert not any(c.startswith("mid") for c in cols), "bars must not carry a mid price"


@pytest.mark.integration
def test_bars_account_for_every_tick(client):
    """A shortfall means the materialised view missed rows — most likely
    because it was created after they were inserted and never backfilled,
    which is the classic ClickHouse MV trap and raises no error of its own.
    """
    from fxpit.flags import report

    if not _has_ticks(client):
        pytest.skip("no ticks ingested")

    rec = report.bars_reconcile(client)
    assert rec["agree"], (
        f"{rec['ticks']:,} ticks but {rec['ticks_in_bars']:,} binned - "
        "the materialised view needs a backfill"
    )


@pytest.mark.integration
def test_exit_criterion_explain_any_instrument_day(client):
    """PHASE 2 EXIT CRITERION: for any instrument-day, list every flagged tick
    and why.
    """
    from fxpit.flags import report

    if not _has_ticks(client):
        pytest.skip("no ticks ingested")

    rows = report.explain_day(client, "EURUSD", datetime(2024, 1, 8).date())
    assert rows, "expected flagged ticks on this day"
    for r in rows:
        assert r["flags"], "a flagged tick with no flag name is unexplainable"
        assert len(r["flags"]) == len(r["details"])
        assert all(d for d in r["details"]), "every flag must carry a reason"
