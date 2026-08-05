-- Tier B — tick storage.
-- Schema as specified in planning.md §5.4.
--
-- Runs automatically on first `docker compose up` against an empty volume.
-- To re-apply after edits: `docker compose down -v && docker compose up -d`
-- (this DESTROYS the ClickHouse volume).

-- The database name below MUST match CLICKHOUSE_DB in .env.
--
-- It is hardcoded because the ClickHouse entrypoint runs init scripts WITHOUT
-- --database set: unqualified CREATE TABLE silently lands in `default` while
-- $CLICKHOUSE_DB is created but left empty. Verified empirically — the failure
-- is silent, so qualify every table explicitly.

CREATE DATABASE IF NOT EXISTS fxpit;

-- --------------------------------------------------------------------------
-- Raw ticks.
--
-- IMMUTABLE. Never modified after ingest, never transformed on ingest.
-- All cleaning is additive and lands in tick_flag. If a detector turns out to
-- be wrong you delete flags and re-run; you do not re-download 300 GB.
--
-- Bid and ask are stored separately and stay separate. Mid-price collapse is
-- one of the four contamination sources the Phase 6 experiment measures.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fxpit.tick_raw (
  instrument   LowCardinality(String),
  ts           DateTime64(3, 'UTC'),
  bid          Float64,
  ask          Float64,
  -- Dukascopy's own pool, NOT market volume. Nothing should be built on it.
  bid_volume   Float32,
  ask_volume   Float32,
  source       LowCardinality(String)
) ENGINE = MergeTree
PARTITION BY (instrument, toYYYYMM(ts))
ORDER BY (instrument, ts);

-- --------------------------------------------------------------------------
-- Quality flags — a separate table, not columns on tick_raw, because the
-- taxonomy grows as new pathologies are found and one tick can carry several
-- flags at once.
--
-- Planned initial flag set:
--   crossed          bid > ask
--   zero_spread
--   stale            repeated identical quote beyond threshold
--   spread_outlier
--   rollover_window
--   weekend_gap
--   holiday_thin
--   feed_disagreement
--
-- The flag distribution by hour and instrument is a research deliverable in
-- its own right, not just plumbing.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fxpit.tick_flag (
  instrument   LowCardinality(String),
  ts           DateTime64(3, 'UTC'),
  flag         LowCardinality(String),
  detail       String
) ENGINE = MergeTree
PARTITION BY (instrument, toYYYYMM(ts))
ORDER BY (instrument, ts, flag);

-- --------------------------------------------------------------------------
-- Bars are materialised views over tick_raw, never a destructive transform.
-- Both bid-bars and ask-bars, never mid-only. Defined in Phase 2.
-- --------------------------------------------------------------------------
