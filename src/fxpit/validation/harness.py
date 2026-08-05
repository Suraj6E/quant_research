"""The validation harness: drift anchor, spread monitor, tick-rate monitor.

Each check answers a question that no single observation can settle, which is
why they all report distributions or trends rather than pass/fail on one point.
A one-day price difference against the ECB fix means nothing — spreads and
timing noise dominate. A month of differences with a consistent sign is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import psycopg

from fxpit.config import ROOT, settings
from fxpit.ingest import store as ch_store
from fxpit.validation import ecb

SCHEMA_FILE = ROOT / "infra" / "postgres" / "init" / "05_validation.sql"

# How far either side of the concertation instant to look for a tick. The ECB
# fix is a single moment; the feed may not have printed in that exact second.
ANCHOR_WINDOW_SECONDS = 60

# A drift this large is a finding rather than noise. EURUSD spreads run about
# 0.3 pip, so a persistent 2-pip gap cannot be explained by the bid-ask.
DRIFT_ALERT_PIPS = 2.0


def connect() -> psycopg.Connection:
    return psycopg.connect(settings().pg_dsn)


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()


def load_ecb(conn: psycopg.Connection, payload: bytes | None = None) -> int:
    ensure_schema(conn)
    rates = ecb.parse(payload if payload is not None else ecb.fetch())
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ecb_reference_rate (fix_date, currency, rate) "
            "VALUES (%s, %s, %s) ON CONFLICT (fix_date, currency) DO UPDATE "
            "SET rate = EXCLUDED.rate",
            [(r.fix_date, r.currency, r.rate) for r in rates],
        )
    conn.commit()
    return len(rates)


# --------------------------------------------------------------------------
# Drift anchor
# --------------------------------------------------------------------------


@dataclass
class DriftReport:
    compared: int = 0
    skipped_no_ticks: int = 0
    skipped_no_fix: int = 0
    alerts: int = 0
    mean_pips: float = 0.0
    max_abs_pips: float = 0.0


def run_drift_anchor(
    conn: psycopg.Connection, instrument: str, start: date, end: date
) -> DriftReport:
    """Compare the feed's mid at the ECB concertation instant to the ECB fix.

    MID IS USED HERE ON PURPOSE, and it is the one place in the project where
    that is correct. The ECB publishes a mid-market reference rate, so a
    like-for-like comparison needs a mid. This is reconciliation, not
    execution — nothing downstream of this function trades on the number, and
    bars remain bid/ask throughout.
    """
    report = DriftReport()
    ecb_currency = ecb.DIRECT_ANCHORS.get(instrument)
    if not ecb_currency:
        raise ValueError(
            f"{instrument} has no direct ECB anchor. Only EUR-based pairs compare "
            f"one-to-one; anything else needs a cross, which carries the error of "
            f"both legs and is a weaker test."
        )

    ensure_schema(conn)
    client = ch_store.connect()
    diffs: list[float] = []
    pip = ecb.pip_size(instrument)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fix_date, rate FROM ecb_reference_rate "
                " WHERE currency = %s AND fix_date >= %s AND fix_date < %s "
                " ORDER BY fix_date",
                (ecb_currency, start, end),
            )
            fixes = cur.fetchall()

        if not fixes:
            report.skipped_no_fix = (end - start).days
            return report

        for fix_date, rate in fixes:
            anchor = ecb.concertation_instant(fix_date)
            lo = anchor - timedelta(seconds=ANCHOR_WINDOW_SECONDS)
            hi = anchor + timedelta(seconds=ANCHOR_WINDOW_SECONDS)
            result = client.query(
                "SELECT count(), avg((bid + ask) / 2) FROM tick_raw "
                " WHERE instrument = %(i)s AND ts >= %(lo)s AND ts <= %(hi)s "
                "   AND bid <= ask",
                parameters={"i": instrument, "lo": lo, "hi": hi},
            )
            n, mid = result.result_rows[0]
            if not n or mid is None:
                report.skipped_no_ticks += 1
                continue

            diff_pips = (float(mid) - float(rate)) / pip
            diffs.append(diff_pips)
            report.compared += 1
            if abs(diff_pips) > DRIFT_ALERT_PIPS:
                report.alerts += 1

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO drift_observation
                      (fix_date, instrument, anchor_ts, feed_mid, ecb_rate,
                       diff_pips, ticks_in_window)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (fix_date, instrument) DO UPDATE
                      SET anchor_ts = EXCLUDED.anchor_ts,
                          feed_mid = EXCLUDED.feed_mid,
                          ecb_rate = EXCLUDED.ecb_rate,
                          diff_pips = EXCLUDED.diff_pips,
                          ticks_in_window = EXCLUDED.ticks_in_window
                    """,
                    (fix_date, instrument, anchor, float(mid), float(rate),
                     round(diff_pips, 4), int(n)),
                )
            conn.commit()

        if diffs:
            report.mean_pips = round(sum(diffs) / len(diffs), 4)
            report.max_abs_pips = round(max(abs(d) for d in diffs), 4)
        return report
    finally:
        client.close()


def drift_observations(conn: psycopg.Connection, limit: int = 60) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fix_date, instrument, anchor_ts, feed_mid, ecb_rate, diff_pips, "
            "       ticks_in_window FROM drift_observation "
            " ORDER BY fix_date DESC LIMIT %s",
            (limit,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


# --------------------------------------------------------------------------
# Monitors — spread distribution and tick rate
# --------------------------------------------------------------------------


def spread_by_hour() -> list[dict]:
    """Median and tail spread by instrument and hour of day.

    Reported as quantiles rather than a mean because spreads are positive and
    heavy-tailed: the mean is dragged by exactly the rollover and news spikes
    that matter, and a strategy backtested on an average spread pays neither
    the wide one nor the tight one.
    """
    client = ch_store.connect()
    try:
        result = client.query(
            """
            SELECT instrument, toHour(ts) AS hour_utc, count() AS ticks,
                   round(quantileExact(0.5)(ask - bid), 7)  AS median_spread,
                   round(quantileExact(0.95)(ask - bid), 7) AS p95_spread,
                   round(max(ask - bid), 7)                 AS max_spread
              FROM tick_raw WHERE bid <= ask
             GROUP BY instrument, hour_utc
             ORDER BY instrument, hour_utc
            """
        )
        return [dict(zip(result.column_names, r, strict=True)) for r in result.result_rows]
    finally:
        client.close()


def tick_rate_by_hour() -> list[dict]:
    """Ticks per hour, with each hour's ratio to that instrument's median hour.

    A sudden drop usually means a feed gap, not a quiet market. Ratio to the
    median rather than an absolute threshold, because tick rates differ by an
    order of magnitude between instruments and between sessions.
    """
    client = ch_store.connect()
    try:
        result = client.query(
            """
            SELECT instrument, hour_start, ticks,
                   round(ticks / median_ticks, 3) AS ratio_to_median
              FROM (
                SELECT instrument, toStartOfHour(ts) AS hour_start, count() AS ticks
                  FROM tick_raw GROUP BY instrument, hour_start
              ) AS h
              INNER JOIN (
                SELECT instrument AS i, quantileExact(0.5)(c) AS median_ticks
                  FROM (SELECT instrument, toStartOfHour(ts) AS hs, count() AS c
                          FROM tick_raw GROUP BY instrument, hs)
                 GROUP BY instrument
              ) AS m ON h.instrument = m.i
             WHERE median_ticks > 0
             ORDER BY ratio_to_median ASC
             LIMIT 40
            """
        )
        return [dict(zip(result.column_names, r, strict=True)) for r in result.result_rows]
    finally:
        client.close()


def ecb_coverage(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT currency), count(DISTINCT fix_date), "
            "       min(fix_date), max(fix_date) FROM ecb_reference_rate"
        )
        n, currencies, days, first, last = cur.fetchone()
    return {
        "rates": n or 0,
        "currencies": currencies or 0,
        "days": days or 0,
        "first": first,
        "last": last,
    }
