-- Phase 2 — bars as materialised views over tick_raw.
--
-- BID AND ASK ARE KEPT SEPARATE. There is no mid column and there will not be
-- one: a feed that forces an assumed spread is how backtests lie, and
-- mid-price collapse is one of the four contamination sources the Phase 6
-- experiment exists to measure. Collapsing here would destroy the experiment
-- before it runs.
--
-- These are materialised views, never a destructive transform. tick_raw is
-- untouched; dropping every object in this file loses nothing but compute.
--
-- The database name must match CLICKHOUSE_DB in .env — see the note in
-- 01_schema.sql about the entrypoint not setting --database.

CREATE DATABASE IF NOT EXISTS fxpit;

-- --------------------------------------------------------------------------
-- One-minute bars, both sides.
--
-- open/close need argMin/argMax over the tick timestamp, so they are stored as
-- aggregate states; high/low/count are simple aggregates that merge on their
-- own. Read through bar_1m_view, which does the merging.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fxpit.bar_1m (
  instrument  LowCardinality(String),
  source      LowCardinality(String),
  minute      DateTime('UTC'),

  bid_open    AggregateFunction(argMin, Float64, DateTime64(3, 'UTC')),
  bid_high    SimpleAggregateFunction(max, Float64),
  bid_low     SimpleAggregateFunction(min, Float64),
  bid_close   AggregateFunction(argMax, Float64, DateTime64(3, 'UTC')),

  ask_open    AggregateFunction(argMin, Float64, DateTime64(3, 'UTC')),
  ask_high    SimpleAggregateFunction(max, Float64),
  ask_low     SimpleAggregateFunction(min, Float64),
  ask_close   AggregateFunction(argMax, Float64, DateTime64(3, 'UTC')),

  tick_count  SimpleAggregateFunction(sum, UInt64)
) ENGINE = AggregatingMergeTree
PARTITION BY (instrument, toYYYYMM(minute))
ORDER BY (instrument, source, minute);

-- --------------------------------------------------------------------------
-- The view that keeps bar_1m fed.
--
-- GOTCHA: a materialised view only sees rows inserted AFTER it is created. It
-- does not backfill. Ticks already in tick_raw when this runs are invisible to
-- it until an explicit INSERT ... SELECT replays them — fxpit.flags.bars
-- does that, and does it idempotently.
-- --------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS fxpit.bar_1m_mv TO fxpit.bar_1m AS
SELECT
  instrument,
  source,
  toStartOfMinute(ts) AS minute,
  argMinState(bid, ts) AS bid_open,
  max(bid)             AS bid_high,
  min(bid)             AS bid_low,
  argMaxState(bid, ts) AS bid_close,
  argMinState(ask, ts) AS ask_open,
  max(ask)             AS ask_high,
  min(ask)             AS ask_low,
  argMaxState(ask, ts) AS ask_close,
  count()              AS tick_count
FROM fxpit.tick_raw
GROUP BY instrument, source, minute;

-- --------------------------------------------------------------------------
-- Read path. Merges the aggregate states so callers see plain numbers.
--
-- `source` is deliberately not defaulted anywhere: there is no consolidated
-- tape in FX, so "the price" is never answerable without naming a feed.
-- --------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS fxpit.bar_1m_view AS
SELECT
  instrument,
  source,
  minute,
  argMinMerge(bid_open) AS bid_open,
  max(bid_high)         AS bid_high,
  min(bid_low)          AS bid_low,
  argMaxMerge(bid_close) AS bid_close,
  argMinMerge(ask_open) AS ask_open,
  max(ask_high)         AS ask_high,
  min(ask_low)          AS ask_low,
  argMaxMerge(ask_close) AS ask_close,
  sum(tick_count)       AS tick_count
FROM fxpit.bar_1m
GROUP BY instrument, source, minute;
