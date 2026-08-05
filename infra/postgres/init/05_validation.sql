-- Phase 5 — validation harness.
--
-- ECB euro foreign exchange reference rates: one official fix per currency per
-- day, 1999 to present, free CSV, no account.
--
-- Useless for trading — a single daily point cannot support any strategy. Its
-- value is as an INDEPENDENT ANCHOR. If the reconstructed Dukascopy price at
-- the ECB concertation instant drifts systematically away from the published
-- fix over months, the cause is a timezone or session-boundary bug, not the
-- market.
--
-- planning.md §10 rates silent timezone bugs as the highest-likelihood risk in
-- the project and names this anchor as the mitigation. Phase 4 then produced
-- one for real — a naive datetime read as machine-local shifted every calendar
-- hour by 5h45m and made a detector return zero flags instead of raising — so
-- this table exists to catch the next one rather than as a precaution against
-- a hypothetical.

CREATE TABLE IF NOT EXISTS ecb_reference_rate (
  fix_date  DATE NOT NULL,
  currency  TEXT NOT NULL,
  -- Units of `currency` per ONE euro. EUR/USD 1.1515 means 1 EUR = 1.1515 USD,
  -- which is the same orientation as a Dukascopy EURUSD quote. Anything not
  -- EUR-based has to be crossed, and the cross introduces its own error.
  rate      DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (fix_date, currency)
);

CREATE INDEX IF NOT EXISTS idx_ecb_currency ON ecb_reference_rate (currency, fix_date);

-- --------------------------------------------------------------------------
-- Drift-anchor observations: one row per comparison actually made.
--
-- Stored rather than recomputed so the series can be watched for TREND. A
-- single day's difference means nothing — spreads and timing noise dominate.
-- A month of differences with a consistent sign is a bug.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drift_observation (
  fix_date        DATE NOT NULL,
  instrument      TEXT NOT NULL,
  anchor_ts       TIMESTAMPTZ NOT NULL,   -- the concertation instant, in UTC
  feed_mid        DOUBLE PRECISION NOT NULL,
  ecb_rate        DOUBLE PRECISION NOT NULL,
  diff_pips       DOUBLE PRECISION NOT NULL,
  ticks_in_window INTEGER NOT NULL,
  PRIMARY KEY (fix_date, instrument)
);
