"""Phase 1 ingest tests.

Unlike the Phase 0 acceptance suite, these test code that exists and are
expected to PASS. They are unit tests with no network: a .bi5 payload is
synthesised in-process so the decoder can be checked against a byte pattern
whose correct interpretation is known exactly.

The integration tests at the bottom need a running stack and are marked
accordingly; `pytest -m "not integration"` skips them.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

import pytest

from fxpit.ingest import dukascopy as dk

# --------------------------------------------------------------------------
# URL construction — the zero-based month gotcha
# --------------------------------------------------------------------------


def test_month_index_is_zero_based():
    """January is month 00. Confirmed against the live feed on 2026-08-04:
    month 00 day 09 returned mean EURUSD bid 1.09409 (January levels) and
    month 01 returned 1.07665 (February). An off-by-one silently returns a
    different month's prices, which are entirely plausible.
    """
    url = dk.hour_url("EURUSD", datetime(2024, 1, 9, 10, tzinfo=UTC))
    assert url.endswith("/EURUSD/2024/00/09/10h_ticks.bi5")


def test_december_is_month_eleven():
    url = dk.hour_url("EURUSD", datetime(2024, 12, 31, 23, tzinfo=UTC))
    assert url.endswith("/EURUSD/2024/11/31/23h_ticks.bi5")


def test_naive_datetime_is_rejected():
    """A naive timestamp would be interpreted in local time and silently fetch
    the wrong hour for anyone outside UTC.
    """
    with pytest.raises(ValueError):
        dk.hour_url("EURUSD", datetime(2024, 1, 9, 10))  # noqa: DTZ001


def test_non_utc_input_is_converted_not_truncated():
    """15:00+05:00 is 10:00 UTC and must fetch hour 10, not hour 15."""
    from datetime import timezone

    plus5 = timezone(timedelta(hours=5))
    url = dk.hour_url("EURUSD", datetime(2024, 1, 9, 15, tzinfo=plus5))
    assert url.endswith("/2024/00/09/10h_ticks.bi5")


# --------------------------------------------------------------------------
# Decoding — field order and decimal factors
# --------------------------------------------------------------------------


def _make_bi5(records: list[tuple[int, int, int, float, float]]) -> bytes:
    """Build a .bi5 payload: raw LZMA over 20-byte big-endian records of
    (ms_offset, ask_int, bid_int, ask_volume, bid_volume).
    """
    raw = b"".join(struct.pack(">IIIff", *r) for r in records)
    compressor = lzma.LZMACompressor(format=lzma.FORMAT_ALONE)
    return compressor.compress(raw) + compressor.flush()


def test_decode_field_order_is_ask_before_bid():
    """The wire format puts ASK first. Swapping them yields a uniformly
    negative spread, which reads as a broken feed rather than a decode bug —
    the failure would be blamed on the wrong thing.
    """
    payload = _make_bi5([(20, 109443, 109440, 3.6, 0.9)])
    ticks = dk.decode(payload, "EURUSD", datetime(2024, 1, 9, 10, tzinfo=UTC))

    assert len(ticks) == 1
    assert ticks[0].ask == pytest.approx(1.09443)
    assert ticks[0].bid == pytest.approx(1.09440)
    assert ticks[0].ask > ticks[0].bid
    assert ticks[0].ask_volume == pytest.approx(3.6)
    assert ticks[0].bid_volume == pytest.approx(0.9)


def test_decode_applies_five_decimal_factor_for_eur_pairs():
    payload = _make_bi5([(0, 109443, 109440, 1.0, 1.0)])
    tick = dk.decode(payload, "EURUSD", datetime(2024, 1, 9, 10, tzinfo=UTC))[0]
    assert tick.bid == pytest.approx(1.09440)


def test_decode_applies_three_decimal_factor_for_jpy_pairs():
    """Same integers, different instrument, different price by 100x. Using the
    EUR factor on a JPY pair gives 0.143946 instead of 143.946.
    """
    payload = _make_bi5([(0, 143952, 143946, 1.0, 1.0)])
    tick = dk.decode(payload, "USDJPY", datetime(2024, 1, 9, 10, tzinfo=UTC))[0]
    assert tick.bid == pytest.approx(143.946)
    assert tick.ask == pytest.approx(143.952)


def test_unknown_instrument_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="decimal factor"):
        dk.decimal_factor("XAUUSD")


def test_timestamps_are_hour_start_plus_offset():
    hour = datetime(2024, 1, 9, 10, tzinfo=UTC)
    payload = _make_bi5([(0, 1, 1, 0.0, 0.0), (1_500, 1, 1, 0.0, 0.0),
                         (3_599_999, 1, 1, 0.0, 0.0)])
    ticks = dk.decode(payload, "EURUSD", hour)
    assert ticks[0].ts == hour
    assert ticks[1].ts == hour + timedelta(milliseconds=1500)
    assert ticks[2].ts == hour + timedelta(milliseconds=3_599_999)
    assert all(t.ts.tzinfo is not None for t in ticks)


def test_truncated_payload_raises_rather_than_dropping_a_record():
    """A partial trailing record means the file is damaged. Silently discarding
    it would lose a tick and leave no trace that anything was wrong.
    """
    raw = struct.pack(">IIIff", 0, 1, 1, 0.0, 0.0)[:13]
    comp = lzma.LZMACompressor(format=lzma.FORMAT_ALONE)
    payload = comp.compress(raw) + comp.flush()
    with pytest.raises(ValueError, match="multiple of"):
        dk.decode(payload, "EURUSD", datetime(2024, 1, 9, 10, tzinfo=UTC))


def test_empty_payload_decodes_to_no_ticks():
    """An empty body is the normal weekend response, not an error."""
    assert dk.decode(b"", "EURUSD", datetime(2024, 1, 7, 3, tzinfo=UTC)) == []


# --------------------------------------------------------------------------
# Immutability — ingest must not clean
# --------------------------------------------------------------------------


def test_decode_preserves_pathological_ticks():
    """A crossed quote, a zero spread and a duplicate stamp all survive decode.

    tick_raw records what the feed sent, including its defects. Filtering here
    would destroy the evidence that the feed produced them, and the flag
    distribution is a Phase 2 research deliverable rather than debris.
    """
    payload = _make_bi5([
        (0, 109440, 109450, 1.0, 1.0),   # crossed: ask < bid
        (100, 109444, 109444, 1.0, 1.0),  # zero spread
        (200, 109448, 109445, 1.0, 1.0),
        (200, 109448, 109445, 1.0, 1.0),  # duplicate timestamp
    ])
    ticks = dk.decode(payload, "EURUSD", datetime(2024, 1, 9, 10, tzinfo=UTC))

    assert len(ticks) == 4, "no tick may be dropped on ingest"
    assert ticks[0].bid > ticks[0].ask, "crossed quote must survive"
    assert ticks[1].bid == ticks[1].ask, "zero spread must survive"
    assert ticks[2].ts == ticks[3].ts, "duplicate stamp must survive"


def test_decode_preserves_feed_order():
    """Out-of-order stamps are not re-sorted. Sorting would hide a decode bug
    or a genuine feed defect, and both need to be visible.
    """
    payload = _make_bi5([(500, 1, 1, 0.0, 0.0), (100, 1, 1, 0.0, 0.0),
                         (900, 1, 1, 0.0, 0.0)])
    ticks = dk.decode(payload, "EURUSD", datetime(2024, 1, 9, 10, tzinfo=UTC))
    offsets = [int((t.ts - datetime(2024, 1, 9, 10, tzinfo=UTC)).total_seconds() * 1000)
               for t in ticks]
    assert offsets == [500, 100, 900]


# --------------------------------------------------------------------------
# Hour enumeration
# --------------------------------------------------------------------------


def test_hours_between_is_half_open():
    hours = dk.hours_between(datetime(2024, 1, 9, tzinfo=UTC), datetime(2024, 1, 10, tzinfo=UTC))
    assert len(hours) == 24
    assert hours[0].hour == 0
    assert hours[-1].hour == 23


def test_weekend_hours_are_enumerated_not_skipped():
    """Closed hours are fetched, come back empty, and are recorded as empty.
    Skipping them would leave the ledger unable to distinguish "market closed"
    from "never attempted" — and since Dukascopy returns HTTP 200 with a
    zero-byte body rather than a 404, that distinction needs the Phase 4
    session calendar anyway.
    """
    sunday = datetime(2024, 1, 7, tzinfo=UTC)
    hours = dk.hours_between(sunday, sunday + timedelta(days=1))
    assert len(hours) == 24


# --------------------------------------------------------------------------
# Integration — needs a live stack
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_ledger_roundtrip_and_idempotency():
    """claim -> settle -> settled_hours reports the hour as done.

    This is the mechanism behind the Phase 1 exit criterion: a settled hour is
    never fetched again, so a re-run does no work rather than doing the work
    and discarding the result.
    """
    from fxpit.ingest import ledger

    hour = datetime(2019, 6, 3, 7, tzinfo=UTC)  # a date the real run won't touch
    inst = "TESTPAIR"
    conn = ledger.connect()
    try:
        ledger.ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ingest_ledger WHERE instrument = %s", (inst,))
        conn.commit()

        assert ledger.settled_hours(conn, inst, [hour]) == set()

        ledger.claim(conn, inst, hour)
        assert ledger.settled_hours(conn, inst, [hour]) == set(), (
            "an in_progress hour must NOT count as settled - it may have died mid-write"
        )

        ledger.settle(conn, inst, hour, dk.FetchStatus.OK, 42, 1234)
        assert ledger.settled_hours(conn, inst, [hour]) == {hour}

        with conn.cursor() as cur:
            cur.execute("DELETE FROM ingest_ledger WHERE instrument = %s", (inst,))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.integration
def test_stale_claim_is_reset_on_startup():
    """A crash between insert and settle leaves an in_progress row. Startup
    must clear it and report the hour so its rows can be removed before the
    re-fetch, otherwise the retry duplicates them.
    """
    from fxpit.ingest import ledger

    hour = datetime(2019, 6, 4, 8, tzinfo=UTC)
    inst = "TESTPAIR2"
    conn = ledger.connect()
    try:
        ledger.ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ingest_ledger WHERE instrument = %s", (inst,))
        conn.commit()

        ledger.claim(conn, inst, hour)          # simulate the crash point
        stale = ledger.reset_stale_claims(conn)

        assert (inst, hour) in stale
        assert ledger.settled_hours(conn, inst, [hour]) == set()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ingest_ledger WHERE instrument = %s", (inst,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()
