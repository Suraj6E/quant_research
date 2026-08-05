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
Three of the eight planned flags cannot be implemented honestly yet:

  holiday_thin      needs the per-currency holiday calendar (Phase 4)
  weekend_gap       needs session boundaries to assert a gap IS the weekend
                    rather than merely a silence (Phase 4). `session_gap`
                    below is the measurable subset: it reports silence without
                    claiming to know its cause.
  feed_disagreement needs HistData ingested as a second feed

Shipping a `holiday_thin` that guessed at holidays would be worse than not
shipping it — the flag table is a research deliverable, and a fabricated flag
contaminates the very distribution it is supposed to describe.
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
    description="21:00-22:00 UTC, when swap is applied and spreads widen structurally",
    sql="""
    SELECT instrument, ts, 'rollover_window' AS flag,
           concat('hour_utc=', toString(toHour(ts))) AS detail
      FROM tick_raw
     WHERE instrument = {instrument:String}
       AND ts >= {start:DateTime64(3, 'UTC')} AND ts < {end:DateTime64(3, 'UTC')}
       AND toHour(ts) = 21
    """,
    caveat=(
        "Fixed at 21:00 UTC. The real window moves with daylight saving, and US "
        "and EU shift on different dates — so for two to three weeks each spring "
        "and autumn this is off by an hour. Corrected in Phase 4 when the session "
        "calendar exists; the flags are deleted and re-run at that point."
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
    description="price discontinuity across the weekend close",
    sql="",
    caveat="Blocked on Phase 4: asserting a gap IS the weekend needs session boundaries.",
)

HOLIDAY_THIN = Detector(
    name="holiday_thin",
    description="legitimately sparse session on a currency holiday",
    sql="",
    caveat="Blocked on Phase 4: needs per-currency holiday calendars.",
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
