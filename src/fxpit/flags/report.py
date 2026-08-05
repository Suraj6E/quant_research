"""Data-quality reporting over `tick_flag`.

`explain_day()` is the Phase 2 exit criterion made executable: for any
instrument-day, list every tick that was flagged and why. The rest are the
distributions that make the flag table a research deliverable rather than
plumbing — the shape of pathologies by hour is one of the more interesting
outputs of the project.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from clickhouse_connect.driver.client import Client


def _rows(client: Client, sql: str, params: dict) -> list[dict]:
    result = client.query(sql, parameters=params)
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def flag_totals(client: Client) -> list[dict]:
    """Flag counts by instrument and flag, with share of ticks."""
    return _rows(
        client,
        """
        SELECT f.instrument, f.flag, count() AS flags,
               any(t.total) AS ticks,
               round(100.0 * count() / any(t.total), 4) AS pct_of_ticks
          FROM tick_flag AS f
          INNER JOIN (SELECT instrument, count() AS total FROM tick_raw GROUP BY instrument)
                AS t ON t.instrument = f.instrument
         GROUP BY f.instrument, f.flag
         ORDER BY f.instrument, flags DESC
        """,
        {},
    )


def flags_by_hour(client: Client) -> list[dict]:
    """The hour-of-day distribution. Rollover and thin-session clustering are
    the shapes this is built to expose.
    """
    return _rows(
        client,
        """
        SELECT flag, toHour(ts) AS hour_utc, count() AS flags
          FROM tick_flag
         GROUP BY flag, hour_utc
         ORDER BY flag, hour_utc
        """,
        {},
    )


def flagged_share(client: Client) -> dict:
    """What fraction of all ticks carry at least one flag.

    Distinct ticks, not flag rows — a tick can carry several flags and counting
    rows would overstate contamination.
    """
    total = int(client.query("SELECT count() FROM tick_raw").result_rows[0][0])
    distinct = int(
        client.query(
            "SELECT count() FROM (SELECT DISTINCT instrument, ts FROM tick_flag)"
        ).result_rows[0][0]
    )
    return {
        "ticks": total,
        "flagged_ticks": distinct,
        "pct": round(100.0 * distinct / total, 3) if total else 0.0,
    }


def explain_day(client: Client, instrument: str, day: date) -> list[dict]:
    """EXIT CRITERION: every flagged tick for one instrument-day, and why.

    Flags are grouped per tick so a quote carrying three of them appears once
    with all three reasons, which is how a human reads it.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    return _rows(
        client,
        """
        SELECT f.ts,
               groupArray(f.flag)   AS flags,
               groupArray(f.detail) AS details,
               any(r.bid) AS bid, any(r.ask) AS ask,
               round(any(r.ask) - any(r.bid), 7) AS spread
          FROM tick_flag AS f
          LEFT JOIN tick_raw AS r
                 ON r.instrument = f.instrument AND r.ts = f.ts
         WHERE f.instrument = %(i)s AND f.ts >= %(start)s AND f.ts < %(end)s
         GROUP BY f.ts
         ORDER BY f.ts
        """,
        {"i": instrument, "start": start, "end": end},
    )


def bar_sample(client: Client, instrument: str, limit: int = 12) -> list[dict]:
    """A slice of the bid/ask bars. Both sides, never a mid."""
    return _rows(
        client,
        """
        SELECT minute, source,
               round(bid_open, 6) AS bid_open, round(bid_high, 6) AS bid_high,
               round(bid_low, 6)  AS bid_low,  round(bid_close, 6) AS bid_close,
               round(ask_open, 6) AS ask_open, round(ask_high, 6) AS ask_high,
               round(ask_low, 6)  AS ask_low,  round(ask_close, 6) AS ask_close,
               round(ask_close - bid_close, 7) AS close_spread,
               tick_count
          FROM bar_1m_view
         WHERE instrument = %(i)s
         ORDER BY minute
         LIMIT %(n)s
        """,
        {"i": instrument, "n": limit},
    )


def bar_coverage(client: Client) -> list[dict]:
    return _rows(
        client,
        """
        SELECT instrument, source, count() AS bars, sum(tick_count) AS ticks,
               min(minute) AS first_minute, max(minute) AS last_minute
          FROM bar_1m_view
         GROUP BY instrument, source
         ORDER BY instrument
        """,
        {},
    )


def bars_reconcile(client: Client) -> dict:
    """Bars must account for every tick.

    A shortfall means the materialised view missed rows — most likely because
    it was created after they were inserted and never backfilled, which is the
    classic ClickHouse MV trap and produces no error of its own.
    """
    ticks = int(client.query("SELECT count() FROM tick_raw").result_rows[0][0])
    binned = int(
        client.query("SELECT sum(tick_count) FROM bar_1m_view").result_rows[0][0] or 0
    )
    return {"ticks": ticks, "ticks_in_bars": binned, "agree": ticks == binned}
