-- Tier A — reference data and bitemporal macro vintages.
-- Schema as specified in planning.md §5.2 and §5.3.
--
-- Runs automatically on first `docker compose up` against an empty volume.
-- To re-apply after edits: `docker compose down -v && docker compose up -d`
-- (this DESTROYS the Postgres volume).

BEGIN;

-- --------------------------------------------------------------------------
-- Macro series catalogue
-- --------------------------------------------------------------------------
CREATE TABLE macro_series (
  series_id     TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  country       TEXT NOT NULL,
  frequency     TEXT NOT NULL,
  unit          TEXT NOT NULL,
  seasonal_adj  BOOLEAN NOT NULL
);

-- --------------------------------------------------------------------------
-- Macro observations — the bitemporal core
--
--   ref_period : the period the value describes
--   known_at   : the moment the value became publicly available
--
-- A point-in-time read filters `known_at <= t` and keeps the latest known_at
-- per (series_id, ref_period). Revisions then fall out automatically.
--
-- known_at is TIMESTAMPTZ, never DATE. An 08:30 ET release and an 08:31 ET
-- price are different objects; a date-only column silently destroys that
-- distinction. See planning.md §5.2.
-- --------------------------------------------------------------------------
CREATE TABLE macro_observation (
  series_id     TEXT NOT NULL REFERENCES macro_series(series_id),
  ref_period    DATE NOT NULL,
  known_at      TIMESTAMPTZ NOT NULL,
  value         DOUBLE PRECISION,
  -- 1 = first print, 2 = first revision, ... Makes "first print vs final"
  -- queries trivial without a self-join.
  vintage_seq   INTEGER NOT NULL,
  PRIMARY KEY (series_id, ref_period, known_at)
);

CREATE INDEX idx_macro_obs_pit
  ON macro_observation (series_id, known_at DESC, ref_period);

-- --------------------------------------------------------------------------
-- Release calendar
--
-- `consensus` is a known gap: free historical consensus forecasts do not
-- exist without registration. The honest default is to leave it NULL and use
-- revision-based surprise measures. If a synthetic forecast is ever stored
-- here, consensus_src MUST label it as synthetic. See planning.md §5.3.
--
-- actual_at is nullable and stays NULL when the true release time is unknown.
-- Unknown is recorded as unknown, never assumed to be 08:30 ET.
-- --------------------------------------------------------------------------
CREATE TABLE macro_release (
  release_id      BIGSERIAL PRIMARY KEY,
  series_id       TEXT NOT NULL REFERENCES macro_series(series_id),
  ref_period      DATE NOT NULL,
  scheduled_at    TIMESTAMPTZ NOT NULL,
  actual_at       TIMESTAMPTZ,
  consensus       DOUBLE PRECISION,
  consensus_src   TEXT
);

CREATE INDEX idx_macro_release_series
  ON macro_release (series_id, ref_period);

COMMIT;

-- --------------------------------------------------------------------------
-- Not yet specified in planning.md — added in later phases:
--   * instrument / calendar / session tables  (Phase 4)
--   * policy rate series                      (Phase 3)
--   * ingest ledger                           (Phase 1, may live in ClickHouse)
-- --------------------------------------------------------------------------
