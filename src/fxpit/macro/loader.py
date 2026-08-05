"""Load RTDSM observations into Postgres `macro_observation`.

Idempotent by primary key (series_id, ref_period, known_at) with ON CONFLICT
UPDATE, so re-running a load corrects values in place rather than duplicating
vintages. Macro data is small enough that recomputing is cheap, and unlike
ticks it genuinely can be corrected — the archival source is authoritative and
re-downloadable.

`vintage_seq` is assigned per (series_id, ref_period) in known_at order after
loading. It exists so "first print vs final" needs no self-join, and it is only
trustworthy if it agrees with the chronology — which is why it is derived here
rather than taken from the source.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from fxpit.config import ROOT, settings
from fxpit.macro.rtdsm import Observation, vintage_known_at

PRECISION_SCHEMA = ROOT / "infra" / "postgres" / "init" / "03_macro_precision.sql"

SERIES_META = {
    "EMPLOY": ("rtdsm", "US", "M", "thousands of persons", True),
    "CPI": ("rtdsm", "US", "M", "index", True),
    "ROUTPUT": ("rtdsm", "US", "Q", "billions of chained dollars", True),
}


@dataclass
class LoadReport:
    series: str = ""
    observations: int = 0
    inserted: int = 0
    ref_periods: int = 0
    vintages: int = 0
    first_vintage: str = ""
    last_vintage: str = ""


def connect() -> psycopg.Connection:
    return psycopg.connect(settings().pg_dsn)


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(PRECISION_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def ensure_series(conn: psycopg.Connection, series_id: str) -> None:
    source, country, freq, unit, sa = SERIES_META.get(
        series_id, ("rtdsm", "US", "M", "unknown", False)
    )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO macro_series (series_id, source, country, frequency, unit, "
            "seasonal_adj) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (series_id) DO NOTHING",
            (series_id, source, country, freq, unit, sa),
        )
    conn.commit()


def load(conn: psycopg.Connection, observations: list[Observation]) -> LoadReport:
    if not observations:
        return LoadReport()

    series_id = observations[0].series_id
    ensure_series(conn, series_id)

    rows = [
        (
            o.series_id,
            o.ref_period,
            vintage_known_at(o.vintage_year, o.vintage_month),
            o.value,
            0,  # vintage_seq, assigned below once the chronology is known
            "month",
            "rtdsm_vintage_month",
        )
        for o in observations
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO macro_observation
              (series_id, ref_period, known_at, value, vintage_seq,
               known_at_precision, known_at_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (series_id, ref_period, known_at) DO UPDATE
              SET value = EXCLUDED.value,
                  known_at_precision = EXCLUDED.known_at_precision,
                  known_at_source = EXCLUDED.known_at_source
            """,
            rows,
        )
    conn.commit()

    _assign_vintage_seq(conn, series_id)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT ref_period), count(DISTINCT known_at), "
            "       min(known_at), max(known_at) "
            "  FROM macro_observation WHERE series_id = %s",
            (series_id,),
        )
        total, periods, vintages, first, last = cur.fetchone()

    return LoadReport(
        series=series_id,
        observations=len(observations),
        inserted=total,
        ref_periods=periods,
        vintages=vintages,
        first_vintage=str(first)[:10] if first else "",
        last_vintage=str(last)[:10] if last else "",
    )


def _assign_vintage_seq(conn: psycopg.Connection, series_id: str) -> None:
    """1 for the first print, 2 for the first revision, and so on.

    Derived from known_at order rather than trusted from the source, because
    the column is only useful if it agrees with the chronology — a Phase 0
    acceptance test asserts exactly that.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE macro_observation AS m
               SET vintage_seq = s.seq
              FROM (
                SELECT series_id, ref_period, known_at,
                       row_number() OVER (PARTITION BY series_id, ref_period
                                          ORDER BY known_at) AS seq
                  FROM macro_observation
                 WHERE series_id = %s
              ) AS s
             WHERE m.series_id = s.series_id
               AND m.ref_period = s.ref_period
               AND m.known_at = s.known_at
               AND m.vintage_seq IS DISTINCT FROM s.seq
            """,
            (series_id,),
        )
    conn.commit()


def summary(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT series_id,
                   count(*) AS observations,
                   count(DISTINCT ref_period) AS ref_periods,
                   count(DISTINCT known_at)   AS vintages,
                   count(*) FILTER (WHERE known_at_precision = 'exact') AS exact_ts,
                   count(*) FILTER (WHERE known_at_precision = 'month') AS month_ts,
                   min(ref_period) AS first_period, max(ref_period) AS last_period
              FROM macro_observation
             GROUP BY series_id ORDER BY series_id
            """
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


    # Comparing a first print to the LATEST vintage looks like the obvious
    # measure of revision and is wrong for any rebased series. Real GDP is
    # published in chained dollars of a base year, and the base year has moved
    # several times — so 1985Q3 goes from 1,684.8 (1982 dollars) to 8,604.2
    # (2017 dollars). That is a units change, not new information, and
    # reporting it as a 6,919-point "revision" would be exactly the kind of
    # plausible-looking wrong number this project exists to catch.
    #
    # The fix is to compare within a window short enough that the base year is
    # almost certainly unchanged. Early revisions are also what actually
    # matters for research: they are the ones a contemporaneous trader would
    # have lived through.
# Series whose values can be compared across vintages as plain levels.
#
# EMPLOY is a headcount in thousands of persons and has never been rebased, so
# a difference between vintages is genuinely new information.
#
# ROUTPUT and CPI are index/chained-dollar series. Their base year moves, and a
# rebasing shifts every historical period at once — 1985Q3 real GNP reads
# 1,684.8 in 1982 dollars and 3,584.1 after a rebasing, a 113% "revision" that
# is purely a units change. That contamination survives even a single-vintage
# step, so no window is tight enough to fix it.
#
# Ranking those series by level difference would produce exactly the kind of
# plausible-looking wrong number this project exists to catch, so they are
# EXCLUDED from the ranking rather than silently included. Comparing them
# properly needs rebasing detection — identifying a vintage that shifts every
# historical period by a common ratio — which is not implemented.
UNITS_STABLE = {"EMPLOY"}

REBASED = {
    "ROUTPUT": "chained dollars; base year moves (1982 -> 2017 and others)",
    "CPI": "index series; reference base can be re-anchored",
}


def revision_examples(
    conn: psycopg.Connection, limit: int = 12, series: str | None = None
) -> list[dict]:
    """Largest FIRST revisions — vintage 1 vs vintage 2 for the same period.

    Restricted by default to series whose units are stable across vintages.
    See UNITS_STABLE above for why that restriction is not optional.

    One step, not first-vs-final and not a fixed window. Both of the obvious
    alternatives measure rebasing on a chained series:

      first vs final   1985Q3 real GNP goes 1,684.8 -> 8,604.2, entirely
                       because the base year moved from 1982 to 2017 dollars
      first vs +400d   still 113%, because base-year changes fall inside a
                       year of the first print too

    A single adjacent step is short enough that a rebasing almost never
    intervenes, and it is the most research-relevant number anyway: how much
    did the figure a trader acted on move at its very first correction.

    Longer-horizon revision analysis on a chained series needs explicit
    rebasing detection — a vintage that shifts every historical period by a
    common ratio — which is not implemented and is flagged rather than faked.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT v1.series_id, v1.ref_period,
                   v1.value AS first_value,
                   v2.value AS later_value,
                   round((v2.value - v1.value)::numeric, 3) AS revision,
                   round((100 * (v2.value - v1.value) /
                          nullif(abs(v1.value), 0))::numeric, 3) AS pct,
                   (SELECT count(*) FROM macro_observation m
                     WHERE m.series_id = v1.series_id
                       AND m.ref_period = v1.ref_period) AS vintages
              FROM macro_observation AS v1
              JOIN macro_observation AS v2
                ON v2.series_id = v1.series_id
               AND v2.ref_period = v1.ref_period
               AND v2.vintage_seq = 2
             WHERE v1.vintage_seq = 1
               AND v1.value IS NOT NULL AND v2.value IS NOT NULL
               AND v1.value <> v2.value
               AND v1.series_id = ANY(%s)
             ORDER BY abs(v2.value - v1.value) DESC
             LIMIT %s
            """,
            ([series] if series else sorted(UNITS_STABLE), limit),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
