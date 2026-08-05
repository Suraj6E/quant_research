-- Ingest ledger (planning.md Phase 1).
--
-- Records what was fetched, when, and whether it succeeded, so that a partial
-- run is recoverable and coverage gaps are VISIBLE rather than silent. Without
-- it, a run that died halfway looks identical to a complete one.
--
-- Lives in Postgres rather than ClickHouse because it is small (7 pairs x 10
-- years x 24h is under a million rows) and because it is UPDATE-heavy - each
-- hour transitions in_progress -> ok, which ClickHouse handles badly.
--
-- This file is also executed by fxpit.ingest.ledger.ensure_schema() so the
-- table appears on an already-running database, not only on a fresh volume.

CREATE TABLE IF NOT EXISTS ingest_ledger (
  instrument      TEXT        NOT NULL,
  hour            TIMESTAMPTZ NOT NULL,
  source          TEXT        NOT NULL DEFAULT 'dukascopy',

  -- in_progress : claimed, outcome unknown. A row left in this state means the
  --               process died mid-hour; the runner deletes any rows it may
  --               have written and re-fetches.
  -- ok          : rows fetched, decoded and committed.
  -- empty       : HTTP 200 with no ticks. NOT an error - closed session or a
  --               thin holiday hour. Distinguishing the two needs the Phase 4
  --               session calendar.
  -- missing     : HTTP 404. The file genuinely is not published.
  -- error       : network or decode failure after retries. Retryable.
  status          TEXT        NOT NULL
                  CHECK (status IN ('in_progress','ok','empty','missing','error')),

  tick_count      INTEGER     NOT NULL DEFAULT 0,
  bytes_downloaded INTEGER    NOT NULL DEFAULT 0,
  attempts        INTEGER     NOT NULL DEFAULT 1,
  detail          TEXT        NOT NULL DEFAULT '',

  first_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at     TIMESTAMPTZ,

  PRIMARY KEY (instrument, hour, source)
);

-- The resumability query: "which hours in this range still need work?"
CREATE INDEX IF NOT EXISTS idx_ledger_status
  ON ingest_ledger (status, instrument, hour);

-- The coverage report groups by instrument-month.
CREATE INDEX IF NOT EXISTS idx_ledger_hour
  ON ingest_ledger (instrument, hour);
