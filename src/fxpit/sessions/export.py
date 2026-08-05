"""Mirror the calendar into ClickHouse so tick detectors can join against it.

The calendar is authored in Postgres (Tier A) because it is small, relational
and needs the exclusion constraints. The detectors run in ClickHouse (Tier B)
because they scan billions of ticks. Neither can join across to the other, so
the calendar is exported.

This is a mirror, not a second source of truth: `build()` in Postgres is
authoritative and `export()` overwrites whatever is here. If the two disagree,
Postgres is right and the export is stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from fxpit.ingest import store as ch_store
from fxpit.sessions import definitions as defs
from fxpit.sessions import store as pg_store

# The mirror is stored as HOUR BUCKETS, not ranges.
#
# Postgres holds the calendar as TSTZRANGE because containment is the natural
# query there and GiST makes it fast. ClickHouse cannot do the same join:
# inequality join conditions need an experimental flag, and correlated
# subqueries are unsupported outright. Both failed on first attempt.
#
# Flattening to one row per UTC hour turns every detector join into plain
# equality on `toStartOfHour(ts)`, which ClickHouse does well. The precision
# loss is nil for this data: the FX week opens and closes at 17:00 NY and
# rollover runs 17:00-18:00 NY, so every boundary already falls on an hour.
#
# Two years is ~17,500 rows. Storing what is conceptually a range as an
# enumeration is only reasonable because the enumeration is this small — the
# same trick on tick data would be absurd.
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS calendar_hour (
      hour        DateTime('UTC'),
      market_open UInt8,
      rollover    UInt8
    ) ENGINE = MergeTree ORDER BY hour
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_holiday (
      currency LowCardinality(String),
      holiday_date Date,
      name String
    ) ENGINE = MergeTree ORDER BY (holiday_date, currency)
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_pair_leg (
      instrument LowCardinality(String),
      currency   LowCardinality(String)
    ) ENGINE = MergeTree ORDER BY (instrument, currency)
    """,
]


@dataclass
class ExportReport:
    hours: int = 0
    open_hours: int = 0
    rollover_hours: int = 0
    holidays: int = 0
    pair_legs: int = 0


def export() -> ExportReport:
    report = ExportReport()
    pg = pg_store.connect()
    ch = ch_store.connect()
    try:
        for statement in SCHEMA:
            ch.command(statement)
        for table in ("calendar_hour", "calendar_holiday", "calendar_pair_leg"):
            ch.command(f"TRUNCATE TABLE {table}")

        with pg.cursor() as cur:
            # Flatten both range tables into one row per hour. generate_series
            # does the enumeration in Postgres, where the ranges live.
            cur.execute(
                """
                SELECT h AS hour,
                       (EXISTS (SELECT 1 FROM market_window   m WHERE m.span @> h))::int,
                       (EXISTS (SELECT 1 FROM rollover_window r WHERE r.span @> h))::int
                  FROM generate_series(
                         (SELECT date_trunc('hour', min(lower(span))) FROM session_window),
                         (SELECT date_trunc('hour', max(upper(span))) FROM session_window),
                         INTERVAL '1 hour'
                       ) AS h
                 ORDER BY h
                """
            )
            rows = cur.fetchall()
            if rows:
                # Pass timezone-AWARE UTC datetimes. Stripping tzinfo here was a
                # real bug, and a perfectly illustrative one: clickhouse-connect
                # reads a naive datetime as machine-local and converts it to UTC.
                # On a machine at UTC+5:45 every calendar hour landed at :15 past,
                # so `toStartOfHour(ts) = c.hour` matched nothing and the rollover
                # detector silently returned zero flags instead of erroring.
                #
                # This is exactly the failure planning.md rates as high-likelihood:
                # a silent timezone bug producing plausible-looking wrong data. The
                # Phase 5 ECB anchor exists to catch this class.
                ch.insert(
                    "calendar_hour",
                    [[r[0].astimezone(UTC), r[1], r[2]] for r in rows],
                    column_names=["hour", "market_open", "rollover"],
                )
            report.hours = len(rows)
            report.open_hours = sum(r[1] for r in rows)
            report.rollover_hours = sum(r[2] for r in rows)

            cur.execute("SELECT currency, holiday_date, name FROM currency_holiday")
            rows = cur.fetchall()
            if rows:
                ch.insert("calendar_holiday", [list(r) for r in rows],
                          column_names=["currency", "holiday_date", "name"])
            report.holidays = len(rows)

        legs = [
            [instrument, currency]
            for instrument, pair in defs.PAIR_LEGS.items()
            for currency in pair
        ]
        ch.insert("calendar_pair_leg", legs, column_names=["instrument", "currency"])
        report.pair_legs = len(legs)
        return report
    finally:
        pg.close()
        ch.close()
