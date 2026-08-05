-- Phase 3 — how exactly is known_at known?
--
-- planning.md: "Where the exact time is unknown, record it explicitly as
-- unknown rather than assuming." This column is that record.
--
-- The problem is real and unavoidable. RTDSM publishes a vintage MONTH.
-- ALFRED publishes a vintage DATE. Neither publishes a release TIME. Only the
-- agency schedules give 08:30 ET, and only for the releases they cover.
-- Assuming 08:30 for everything else would manufacture precision that was
-- never measured — the same error as using a revised value, in different
-- clothes.
--
-- CONSERVATIVE PLACEMENT
-- When precision is coarse, known_at is set to the LATEST instant consistent
-- with what is known:
--
--   month   -> last instant of that vintage month
--   date    -> last instant of that day
--   exact   -> the release timestamp itself
--
-- This biases every point-in-time query toward WITHHOLDING rather than
-- leaking. A value whose release time is unknown is treated as not-yet-public
-- for longer than it may truly have been, never for less. Under-reporting what
-- was knowable is a conservative research error; over-reporting it is
-- look-ahead bias, which is the thing this database exists to prevent.

ALTER TABLE macro_observation
  ADD COLUMN IF NOT EXISTS known_at_precision TEXT NOT NULL DEFAULT 'exact';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'macro_observation_precision_chk'
  ) THEN
    ALTER TABLE macro_observation
      ADD CONSTRAINT macro_observation_precision_chk
      CHECK (known_at_precision IN ('exact', 'date', 'month'));
  END IF;
END $$;

-- Provenance of the release timestamp, so a later phase can improve the
-- coarse ones without re-deriving which they were.
ALTER TABLE macro_observation
  ADD COLUMN IF NOT EXISTS known_at_source TEXT NOT NULL DEFAULT 'unknown';

COMMENT ON COLUMN macro_observation.known_at_precision IS
  'exact | date | month. Coarse values are placed at the LATEST consistent '
  'instant so queries withhold rather than leak.';
COMMENT ON COLUMN macro_observation.known_at_source IS
  'Where the timestamp came from: rtdsm_vintage_month, bls_release_date, alfred, manual.';
