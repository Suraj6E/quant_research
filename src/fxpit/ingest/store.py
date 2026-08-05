"""ClickHouse writer for `tick_raw`.

Insert-only. There is no update path and no delete path except the one used to
undo a crashed claim, which is why that function is named for what it is
rather than offered as general-purpose maintenance.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from fxpit.config import settings
from fxpit.ingest.dukascopy import Tick

COLUMNS = ["instrument", "ts", "bid", "ask", "bid_volume", "ask_volume", "source"]


def connect() -> Client:
    s = settings()
    return clickhouse_connect.get_client(
        host=s.ch_host,
        port=s.ch_http_port,
        username=s.ch_user,
        password=s.ch_password,
        database=s.ch_db,
    )


def insert_ticks(client: Client, ticks: Sequence[Tick]) -> int:
    """Insert one hour's ticks as a single block.

    A whole hour goes in one call so the insert is atomic at the block level:
    the hour is either present or absent, never half-written. Nothing is
    sorted, de-duplicated or filtered on the way in - `tick_raw` records what
    the feed actually sent, including its defects.
    """
    if not ticks:
        return 0
    rows = [
        [t.instrument, t.ts, t.bid, t.ask, t.bid_volume, t.ask_volume, t.source]
        for t in ticks
    ]
    client.insert("tick_raw", rows, column_names=COLUMNS)
    return len(rows)


def delete_hour(client: Client, instrument: str, hour: datetime, source: str = "dukascopy") -> None:
    """Remove one instrument-hour. Used ONLY to undo a crashed claim.

    This is the single exception to tick_raw's immutability, and it is not a
    real exception: it removes rows whose write never completed, restoring the
    state that would have existed had the process died a moment earlier. It is
    never used to correct, clean or re-shape data that landed successfully -
    that would be exactly the destructive editing the design forbids.
    """
    client.command(
        "ALTER TABLE tick_raw DELETE WHERE instrument = %(i)s "
        "AND ts >= %(start)s AND ts < %(start)s + INTERVAL 1 HOUR "
        "AND source = %(s)s",
        parameters={"i": instrument, "start": hour, "s": source},
    )


def count_hour(client: Client, instrument: str, hour: datetime, source: str = "dukascopy") -> int:
    result = client.query(
        "SELECT count() FROM tick_raw WHERE instrument = %(i)s "
        "AND ts >= %(start)s AND ts < %(start)s + INTERVAL 1 HOUR AND source = %(s)s",
        parameters={"i": instrument, "start": hour, "s": source},
    )
    return int(result.result_rows[0][0])


def total_rows(client: Client) -> int:
    return int(client.query("SELECT count() FROM tick_raw").result_rows[0][0])


def rows_by_instrument(client: Client) -> list[dict]:
    result = client.query(
        "SELECT instrument, count() AS ticks, min(ts) AS first_ts, max(ts) AS last_ts, "
        "       countIf(bid > ask) AS crossed, round(avg(ask - bid), 7) AS mean_spread "
        "  FROM tick_raw GROUP BY instrument ORDER BY instrument"
    )
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
