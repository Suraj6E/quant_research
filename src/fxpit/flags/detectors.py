"""Tick quality detectors.

Each detector is independent, re-runnable, and expressed as SQL that executes
inside ClickHouse. Nothing streams billions of ticks into Python to look at
them one at a time.

THE DESIGN RULE
---------------
`tick_raw` is never modified. Detectors only ever INSERT into `tick_flag`. A
detector that turns out to be wrong is corrected by deleting its flags and
re-running — not by re-downloading, and never by editing the ticks.

This asymmetry is deliberate and worth stating plainly: **flags are disposable,
raw is not.** That is why `run()` deletes its own prior flags for the scope
before inserting. Re-running a detector is idempotent; re-running ingest is a
no-op. Different mechanisms, same guarantee.

WHAT IS NOT HERE, AND WHY
-------------------------
One of the nine planned flags still cannot be implemented honestly:

  feed_disagreement  needs HistData ingested as a second feed. A second
                     opinion requires a second opinion.

Shipping a guessed implementation would be worse than shipping none — the flag
table is a research deliverable, so a fabricated flag contaminates the very
distribution it is supposed to describe.

WHAT PHASE 4 UNBLOCKED
----------------------
`weekend_gap` and `holiday_thin` were blocked on the session calendar and are
now live. `rollover_window` was live but *wrong*: it hardcoded 21:00 UTC and
was an hour out for the 20 trading days a year when US and EU daylight saving
are out of step. All three now join the calendar mirrored into ClickHouse by
`fxpit.sessions.export`, which means detector runs depend on that export being
current — Postgres remains authoritative and a stale mirror is stale, not wrong
in a way that announces itself.

`session_gap` survives alongside `weekend_gap` rather than being replaced. They
answer different questions: `session_gap` reports silence without claiming to
know its cause, `weekend_gap` asserts the market was shut. A gap during trading
hours is a feed outage and only the first will catch it.
"""

from __future__ import annotations

from dataclasses import dataclass

# A run of this many identical consecutive quotes marks the later ones stale.
# 4 matches the Phase 0 fixture. It is a parameter, not a discovered constant:
# the right value is an empirical question this phase's report should inform.
STALE_RUN_LENGTH = 4

# A spread this many times the hour's median is an outlier. Multiplicative on
# the median rather than additive on the mean, because spreads are positive,
# heavy-tailed, and the mean is dragged by the very outliers being detected.
SPREAD_OUTLIER_MULTIPLE = 5.0

# Silence longer than this starts a new session-gap flag.
SESSION_GAP_MINUTES = 120


@dataclass(frozen=True)
class Detector:
    name: str
    description: str
    sql: str
    caveat: str = ""

    @property
    def blocked(self) -> bool:
        return not self.sql.strip()


# --------------------------------------------------------------------------
# Implemented detectors
# --------------------------------------------------------------------------

CROSSED = Detector(
    name="crossed",
    description="bid > ask — the book is inverted, which cannot be traded",
    sql="""
    SELECT instrument, ts, 'crossed' AS flag,
           concat('bid=', toString(bid), ' ask=', toString(ask),
                  ' spread=', toString(ask - bid)) AS detail
      FROM tick_raw
     WHERE instrument = {instrument:String}
       AND ts >= {start:DateTime64(3, 'UTC')} AND ts < {end:DateTime64(3, 'UTC')}
       AND bid > ask
    """,
)

ZERO_SPREAD = Detector(
    name="zero_spread",
    description="bid == ask — not a bargain, a defect",
    sql="""
    SELECT instrument, ts, 'zero_spread' AS flag,
           concat('price=', toString(bid)) AS detail
      FROM tick_raw
     WHERE instrument = {instrument:String}
       AND ts >= {start:DateTime64(3, 'UTC')} AND ts < {end:DateTime64(3, 'UTC')}
       AND bid = ask
    """,
)

# A repeated identical quote is the signature of a stalled feed, and is
# genuinely indistinguishable from a very quiet market without a threshold.
# The run length is therefore recorded in `detail` so the threshold can be
# re-litigated from the flags rather than by re-running the detector.
STALE = Detector(
    name="stale",
    description=f"the same (bid, ask) repeated {STALE_RUN_LENGTH}+ times consecutively",
    sql=f"""
    SELECT instrument, ts, 'stale' AS flag,
           concat('quote=', toString(bid), '/', toString(ask),
                  ' repeat_index=', toString(run_len)) AS detail
      FROM (
        SELECT instrument, ts, bid, ask,
               count() OVER (PARTITION BY instrument, grp ORDER BY ts
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS run_len
          FROM (
            SELECT instrument, ts, bid, ask,
                   sum(changed) OVER (PARTITION BY instrument ORDER BY ts
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS grp
              FROM (
                SELECT instrument, ts, bid, ask,
                       if(bid = lagInFrame(bid) OVER w AND ask = lagInFrame(ask) OVER w, 0, 1)
                         AS changed
                  FROM tick_raw
                 WHERE instrument = {{instrument:String}}
                   AND ts >= {{start:DateTime64(3, 'UTC')}}
                   AND ts <  {{end:DateTime64(3, 'UTC')}}
                WINDOW w AS (PARTITION BY instrument ORDER BY ts
                             ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
              )
          )
      )
     WHERE run_len >= {STALE_RUN_LENGTH}
    """,
)

SPREAD_OUTLIER = Detector(
    name="spread_outlier",
    description=(
        f"spread more than {SPREAD_OUTLIER_MULTIPLE}x the median for that "
        f"instrument-hour"
    ),
    sql=f"""
    SELECT t.instrument, t.ts, 'spread_outlier' AS flag,
           concat('spread=', toString(round(t.ask - t.bid, 7)),
                  ' hour_median=', toString(round(h.median_spread, 7)),
                  ' ratio=', toString(round((t.ask - t.bid) / h.median_spread, 2))) AS detail
      FROM tick_raw AS t
      INNER JOIN (
        SELECT instrument, toStartOfHour(ts) AS hr,
               quantileExact(0.5)(ask - bid) AS median_spread
          FROM tick_raw
         WHERE instrument = {{instrument:String}}
           AND ts >= {{start:DateTime64(3, 'UTC')}} AND ts < {{end:DateTime64(3, 'UTC')}}
           AND ask >= bid
         GROUP BY instrument, hr
      ) AS h ON t.instrument = h.instrument AND toStartOfHour(t.ts) = h.hr
     WHERE t.instrument = {{instrument:String}}
       AND t.ts >= {{start:DateTime64(3, 'UTC')}} AND t.ts < {{end:DateTime64(3, 'UTC')}}
       AND h.median_spread > 0
       AND (t.ask - t.bid) > {SPREAD_OUTLIER_MULTIPLE} * h.median_spread
    """,
)

ROLLOVER_WINDOW = Detector(
    name="rollover_window",
    description="17:00-18:00 New York, when swap is applied and spreads widen",
    sql="""
    SELECT t.instrument, t.ts, 'rollover_window' AS flag,
           concat('rollover_hour_utc=', toString(c.hour)) AS detail
      FROM tick_raw AS t
      INNER JOIN calendar_hour AS c ON toStartOfHour(t.ts) = c.hour
     WHERE t.instrument = {instrument:String}
       AND t.ts >= {start:DateTime64(3, 'UTC')} AND t.ts < {end:DateTime64(3, 'UTC')}
       AND c.rollover = 1
    """,
    caveat=(
        "FIXED in Phase 4. This previously hardcoded 21:00 UTC and was an hour "
        "wrong for the 20 trading days a year when US and EU daylight saving are "
        "out of step. It now joins the session calendar, which derives the window "
        "from 17:00 New York local time, so DST is handled by the conversion "
        "rather than by a constant. Requires `python -m fxpit.sessions --export`, "
        "which flattens the Postgres ranges to hour buckets because ClickHouse "
        "cannot join on inequality."
    ),
)


SESSION_GAP = Detector(
    name="session_gap",
    description=f"first tick after {SESSION_GAP_MINUTES}+ minutes of silence",
    sql=f"""
    SELECT instrument, ts, 'session_gap' AS flag,
           concat('silence_minutes=', toString(round(gap_s / 60, 1)),
                  ' previous_tick=', toString(prev_ts)) AS detail
      FROM (
        SELECT instrument, ts,
               lagInFrame(ts) OVER w AS prev_ts,
               dateDiff('second', lagInFrame(ts) OVER w, ts) AS gap_s
          FROM tick_raw
         WHERE instrument = {{instrument:String}}
           AND ts >= {{start:DateTime64(3, 'UTC')}} AND ts < {{end:DateTime64(3, 'UTC')}}
        WINDOW w AS (PARTITION BY instrument ORDER BY ts
                     ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
      )
     WHERE prev_ts > toDateTime64(0, 3, 'UTC')
       AND gap_s >= {SESSION_GAP_MINUTES * 60}
    """,
    caveat=(
        "Reports silence, not its cause. Whether a gap is the weekend, a holiday "
        "or a feed outage cannot be decided without the Phase 4 session calendar "
        "— and Dukascopy returns HTTP 200 with an empty body for closed hours, so "
        "the ingest ledger cannot settle it either."
    ),
)

# --------------------------------------------------------------------------
# Declared but not implementable yet. Present so the gap is visible in the UI
# and the report rather than silently absent.
# --------------------------------------------------------------------------

WEEKEND_GAP = Detector(
    name="weekend_gap",
    description="a tick outside the FX trading week entirely",
    sql="""
    SELECT t.instrument, t.ts, 'weekend_gap' AS flag,
           concat('market_closed dow=', toString(toDayOfWeek(t.ts))) AS detail
      FROM tick_raw AS t
      INNER JOIN calendar_hour AS c ON toStartOfHour(t.ts) = c.hour
     WHERE t.instrument = {instrument:String}
       AND t.ts >= {start:DateTime64(3, 'UTC')} AND t.ts < {end:DateTime64(3, 'UTC')}
       AND c.market_open = 0
    """,
    caveat=(
        "UNBLOCKED in Phase 4. Now asserts what `session_gap` could not: this tick "
        "falls outside every FX trading week, so the market was shut. Ticks landing "
        "here are a genuine anomaly - the feed reporting activity when there should "
        "be none - rather than merely a quiet stretch."
    ),
)


HOLIDAY_THIN = Detector(
    name="holiday_thin",
    description="either leg of the pair is on a national holiday",
    sql="""
    SELECT t.instrument, t.ts, 'holiday_thin' AS flag,
           concat(h.currency, ': ', h.name) AS detail
      FROM tick_raw AS t
      INNER JOIN calendar_pair_leg AS l ON l.instrument = t.instrument
      INNER JOIN calendar_holiday  AS h ON h.currency = l.currency
                                       AND h.holiday_date = toDate(t.ts)
     WHERE t.instrument = {instrument:String}
       AND t.ts >= {start:DateTime64(3, 'UTC')} AND t.ts < {end:DateTime64(3, 'UTC')}
    """,
    caveat=(
        "UNBLOCKED in Phase 4. Means THIN LIQUIDITY, not a closed market - FX trades "
        "through national holidays and what changes is how many participants are at "
        "their desks. National holidays are a proxy for market holidays, and EUR uses "
        "the German calendar because the euro area has no single one."
    ),
)


FEED_DISAGREEMENT = Detector(
    name="feed_disagreement",
    description="Dukascopy and HistData differ beyond tolerance on the same bar",
    sql="",
    caveat="Blocked: HistData is not ingested yet. A second opinion needs a second feed.",
)


ALL: list[Detector] = [
    CROSSED,
    ZERO_SPREAD,
    STALE,
    SPREAD_OUTLIER,
    ROLLOVER_WINDOW,
    SESSION_GAP,
    WEEKEND_GAP,
    HOLIDAY_THIN,
    FEED_DISAGREEMENT,
]

RUNNABLE: list[Detector] = [d for d in ALL if not d.blocked]
BLOCKED: list[Detector] = [d for d in ALL if d.blocked]

BY_NAME = {d.name: d for d in ALL}
