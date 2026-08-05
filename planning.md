# Project 1 — Point-in-Time FX Research Database

**Status:** Planning
**Owner:** (you)
**Estimated duration:** 11–13 weeks part-time (evenings + weekends)
**Constraint:** All data sources must be free and require no registration or login.

---

## 1. Goal

Build a research database for FX (and optionally CFD-equity) data where every query can be answered **as of a historical timestamp**, returning only information that was actually available at that moment.

The database is not the point. The point is that after building it you can:

1. Detect look-ahead bias in someone else's backtest by asking two questions.
2. Quantify how much of a reported result comes from data contamination rather than signal.
3. Defend a data architecture decision for 45 minutes in an interview.

### Non-goals

- This is **not** a trading system. No live execution, no order management.
- This is **not** a profitable strategy. Any strategy built on top is a validation harness, not a product.
- This is **not** a general-purpose market data platform. Scope is deliberately narrow.

### Success criteria

The project is done when all of the following are true:

| # | Criterion | How verified |
|---|---|---|
| 1 | Every read path goes through `as_of(t)` | Code audit; no direct table SELECTs outside the query layer |
| 2 | All Phase 0 acceptance tests pass | Automated test suite, green |
| 3 | Two independent price feeds reconciled with a documented disagreement rate | Reconciliation report artifact |
| 4 | The contamination experiment (Phase 6) produces a measured number | Chart + written findings |
| 5 | README states every known limitation explicitly | Written, reviewed |

---

## 2. Open decision — resolve before Phase 1

**The research horizon determines where the engineering effort goes.** This has not been decided yet and it materially changes Phases 1–4.

### Path A — Intraday / session horizon

Holding periods of minutes to hours. Research questions around spread dynamics, session effects, macro release reactions, intraday mean reversion.

- Tick layer is central; must go back at least 8–10 years across 7+ pairs.
- Macro layer needs **timestamps**, not dates — an 08:30 ET print and an 08:31 ET price are different objects.
- Storage: billions of rows. ClickHouse is required, not optional.
- Effort split: ~70% tick layer, ~30% macro layer.

### Path B — Multi-day / carry-momentum horizon

Holding periods of days to weeks. Research questions around carry, momentum, PPP/value, volatility regimes.

- Tick layer can be aggregated to hourly or daily bars immediately; raw ticks become an archive, not a working set.
- Interest rate differentials and forward points become the critical missing input, and forward points have **no clean free source**. This is a real gap that must be worked around, not designed away.
- Storage: manageable in Postgres alone if you never query raw ticks.
- Effort split: ~30% tick layer, ~70% macro and rates layer.

### Path C — Both (default assumed in this document)

Build the tick layer properly, but scope the initial pair universe and date range narrowly enough to stay tractable. Defer Path-B-only components (forward points, carry construction) to a follow-on project.

**DECIDED 2026-08-04: Path C.**

Rationale, recorded so it can be argued with later:

- Path B's critical input (forward points) has no free source, and the policy-rate proxy carries an error correlated with funding stress — wrong precisely when carry matters. That is a missing ingredient, not an engineering problem.
- Path A's hard part is data volume, which is tractable work.
- The Phase 6 contamination experiment ("EURUSD held N minutes after a CPI/payrolls release") is intraday by construction. Path B would require redesigning the project's flagship deliverable.

C is therefore Path A with guardrails: build the tick layer properly, start at 7 majors from 2015, defer carry construction to a follow-on project. Extend backward only once the pipeline is stable.

---

## 3. Data sources

All sources below are free and require no account. Verification status is stated per source — **verify each yourself before committing to it**, since availability changes.

### 3.1 Prices — primary

**Dukascopy historical feed**

- Tick-level bid/ask with a per-side volume field.
- Majors go back to roughly 2003; coverage varies by instrument.
- Raw files are hourly `.bi5` (LZMA-compressed), directly addressable by URL. No account.
- Open-source downloaders: `dukascopy-node` (Node/CLI), `duka` (Python).
- Also covers ~1600 instruments including CFD equities, indices, commodities.

*Status: verified as free and account-free (Aug 2026).*

**Why this source:** separate bid and ask. This is the single most important property. A feed that gives you only mid price forces you to assume a spread, and assumed spreads are how backtests lie.

### 3.2 Prices — reconciliation

**HistData.com**

- M1 bars and tick data, ~66 pairs, organised by pair/year/month, CSV.
- No login required.

**Purpose:** this is *not* a fallback. It is a second opinion. Where two independent feeds disagree materially on the same bar, at least one is wrong, and the disagreement rate is a data-quality finding in its own right.

**ECB euro foreign exchange reference rates**

- One official daily fix, 1999–present, free CSV, no login.
- Single point per day. Useless for trading, useful as a drift anchor: if your reconstructed daily close diverges systematically from the ECB fix over months, you have a timezone or session-boundary bug.

### 3.3 Macro vintages — the point-in-time layer

**Philadelphia Fed Real-Time Data Set for Macroeconomists (RTDSM)**

- Free, no registration, direct spreadsheet downloads.
- Vintage history for GDP, payrolls, CPI, industrial production, and more, back to 1965.
- This is the academic standard for real-time US macro research.

*Status: primary recommendation. Build the macro layer on this.*

**ALFRED (Archival FRED)**

- Each observation carries `realtime_start` and `realtime_end` alongside `date`, which is exactly the bitemporal structure needed.
- **Caveat:** the FRED/ALFRED API requires a free API key, which counts as registration under your constraint.

*Status (verified 2026-08-04): **OPTIONAL, key required.***

The keyless CSV endpoint does not work and fails **silently**, which is worse than failing loudly. `fredgraph.csv?id=PAYEMS&vintage_date=...` returns HTTP 200 and accepts any `vintage_date`, then ignores it. Four different vintage dates (2005, 2015, 2024, none) returned byte-identical payloads — sha256 `177efe81de346565` — all serving current revised data. Ask for the January 2005 vintage, receive today's numbers, no warning. Keep this as a worked example of the exact contamination the project exists to detect.

With a key the API does serve genuine vintages via `realtime_start`/`realtime_end`, which map 1:1 onto `known_at`. Verified: 857 vintage dates for PAYEMS, and January 2024 payrolls revising 157700 → 157533 → 157560 → 157049 → 157032 across five vintages.

ALFRED is therefore wired in as an **optional enrichment source**: the pipeline must run to completion with `FRED_API_KEY` unset, and RTDSM stays primary. This preserves the constraint's real purpose — anyone can clone the repo and reproduce the work without an account.

**Limitation:** ALFRED's real-time axis is **date-granularity**. It says a value became known on 2024-02-02, not at 08:30:00 ET. It broadens coverage; it does not solve release timing.

**ECB Data Portal / BIS / OECD**

- Free REST or bulk CSV, no key required.
- BIS is the source for effective exchange rates and cross-currency basis work.
- Non-US macro coverage, which matters for any pair that isn't USD-crossed.

### 3.4 Rates

Central bank policy rates from ECB, BIS, and national central bank sites — free, no login.

**Known gap:** forward points have no clean free source. The workaround is to proxy carry with policy rate differentials. This proxy has a **known, non-random error**: covered interest parity has failed persistently since 2008 (Du, Tepper & Verdelhan, 2018 — established empirical fact, widely replicated). The proxy error is therefore correlated with funding stress, which is precisely when carry strategies matter most. Document this; do not pretend the proxy is clean.

---

## 4. Architecture

Three tiers, chosen because the data has two incompatible shapes and one store cannot serve both well.

```
                        ┌─────────────────────────┐
                        │   Research / notebooks   │
                        └────────────┬─────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │  Tier C — DuckDB          │
                        │  Query layer, Parquet     │
                        │  as_of() lives here       │
                        └──────┬─────────────┬──────┘
                               │             │
              ┌────────────────▼───┐     ┌───▼────────────────┐
              │ Tier B — ClickHouse│     │ Tier A — Postgres  │
              │ Ticks & bars       │     │ Reference & vintage│
              │ Append-only, huge  │     │ Relational, small  │
              └────────▲───────────┘     └───▲────────────────┘
                       │                     │
          ┌────────────┴──────┐   ┌──────────┴────────────────┐
          │ Dukascopy .bi5    │   │ Philadelphia Fed RTDSM     │
          │ HistData CSV      │   │ ECB / BIS / OECD           │
          └───────────────────┘   └────────────────────────────┘
```

### Tier A — PostgreSQL: reference and vintages

Instruments, calendars, sessions, macro release schedules, macro vintage observations, rate series.

**Rationale (career):** Postgres is the default relational store across fintech. It is not differentiating on a CV, but its absence is disqualifying. Bitemporal modelling in Postgres — range types, exclusion constraints, `tstzrange` — is a directly transferable skill that appears in regulatory reporting and reconciliation work.

**Rationale (technical):** this tier is small (single-digit millions of rows) but needs referential integrity, constraints, and frequent updates. That is exactly what a relational database is for.

### Tier B — ClickHouse: tick and bar storage

Raw ticks, quality flags, aggregated bars as materialised views.

**Volume estimate:** EURUSD alone runs tens of millions of ticks per year on the Dukascopy feed. Ten pairs over fifteen years plausibly lands at 2–5 billion rows. That is past comfortable Postgres territory even with TimescaleDB.

**Rationale (career):** open source, no registration, runs single-node on a laptop. Increasingly used for market data at crypto exchanges, fintechs, and a growing number of funds. This is where the market is moving.

**Rejected alternatives, with reasons:**

| Option | Why not |
|---|---|
| TimescaleDB | Good and simpler, but caps out lower and is a weaker CV signal |
| InfluxDB | DevOps metrics ecosystem, not finance. Weak signal |
| MongoDB | Wrong data shape entirely |
| kdb+/q | Strong CV signal at sell-side and HFT, but alien syntax, thin docs, and the free edition requires a license request. Treat as a **separate later project**, not part of this build |

### Tier C — DuckDB: research query layer

In-process analytical engine. Reads Parquet directly, joins across ClickHouse exports and Postgres extracts.

**This is where `as_of()` lives.** It is the only sanctioned read path. Convenience accessors that bypass it must not be added — that is how look-ahead bias re-enters after being eliminated.

---

## 5. Core data model

### 5.1 The bitemporal principle

Every fact carries two independent time axes:

- **`ref_period`** — the period the value describes
- **`known_at`** — the moment the value became publicly available

A point-in-time query filters `known_at <= t`, then keeps the row with the latest `known_at` per key. Revisions fall out automatically: you get what the world believed at time `t`, not what turned out to be true later.

That is the entire mechanism. Everything else is plumbing.

### 5.2 Postgres — macro vintages

```sql
CREATE TABLE macro_series (
  series_id     TEXT PRIMARY KEY,
  source        TEXT NOT NULL,
  country       TEXT NOT NULL,
  frequency     TEXT NOT NULL,
  unit          TEXT NOT NULL,
  seasonal_adj  BOOLEAN NOT NULL
);

CREATE TABLE macro_observation (
  series_id     TEXT NOT NULL REFERENCES macro_series(series_id),
  ref_period    DATE NOT NULL,
  known_at      TIMESTAMPTZ NOT NULL,
  value         DOUBLE PRECISION,
  vintage_seq   INTEGER NOT NULL,
  PRIMARY KEY (series_id, ref_period, known_at)
);

CREATE INDEX idx_macro_obs_pit
  ON macro_observation (series_id, known_at DESC, ref_period);
```

`known_at` is `TIMESTAMPTZ`, not `DATE`. This is deliberate and it matters: an 08:30 ET payrolls release and the 08:31 ET price are separate events, and a date-only column silently destroys that distinction. Under Path A this destroys the project.

`vintage_seq` is 1 for the first print, 2 for the first revision, and so on. It makes "first print vs final" queries trivial without a self-join.

### 5.3 Postgres — release calendar

```sql
CREATE TABLE macro_release (
  release_id      BIGSERIAL PRIMARY KEY,
  series_id       TEXT NOT NULL REFERENCES macro_series(series_id),
  ref_period      DATE NOT NULL,
  scheduled_at    TIMESTAMPTZ NOT NULL,
  actual_at       TIMESTAMPTZ,
  consensus       DOUBLE PRECISION,
  consensus_src   TEXT
);
```

The `consensus` column is a **known problem**. Free historical consensus forecasts essentially do not exist without registration. Options, in order of preference:

1. Leave it NULL and use revision-based surprise measures instead (actual vs previous print).
2. Use a Philadelphia Fed Survey of Professional Forecasters series where one exists — free, no login, but quarterly and low frequency.
3. Build a naive statistical forecast (AR model on the vintage series) as a synthetic consensus, and label it clearly as synthetic.

Option 1 is the honest default. Option 3 is defensible only if labelled.

### 5.4 ClickHouse — ticks

```sql
CREATE TABLE tick_raw (
  instrument   LowCardinality(String),
  ts           DateTime64(3, 'UTC'),
  bid          Float64,
  ask          Float64,
  bid_volume   Float32,
  ask_volume   Float32,
  source       LowCardinality(String)
) ENGINE = MergeTree
PARTITION BY (instrument, toYYYYMM(ts))
ORDER BY (instrument, ts);
```

`tick_raw` is immutable and never modified after ingest. All cleaning is additive.

```sql
CREATE TABLE tick_flag (
  instrument   LowCardinality(String),
  ts           DateTime64(3, 'UTC'),
  flag         LowCardinality(String),
  detail       String
) ENGINE = MergeTree
PARTITION BY (instrument, toYYYYMM(ts))
ORDER BY (instrument, ts, flag);
```

Flags are a separate table rather than columns on `tick_raw` because the flag taxonomy will grow as you discover new pathologies, and because a tick can carry several flags at once. Planned initial flag set: `crossed` (bid > ask), `zero_spread`, `stale` (repeated identical quote beyond threshold), `spread_outlier`, `rollover_window`, `weekend_gap`, `holiday_thin`, `feed_disagreement`.

The flag table is a research deliverable, not just plumbing. The distribution of flags by hour and instrument is one of the more interesting outputs of the whole project.

Bars are materialised views over `tick_raw`, never a destructive transform. Both bid-bars and ask-bars, never mid-only.

---

## 6. Phases

### Phase 0 — Acceptance tests first (1 week)

Write the tests before the pipeline. This is the discipline that separates a research database from a scraping script.

**Tasks**

- Set up repo, CI, test runner, and a small hand-built fixture dataset.
- Write the four test families below against an empty schema. They should fail. That is correct.

**Tests**

| Test | Assertion |
|---|---|
| No-clairvoyance | For N random macro facts, `as_of(known_at − 1s)` returns nothing for that fact |
| Revision | For ≥50 series-periods where first print ≠ final, `as_of` at a date between them returns the first print |
| Tick sanity | Timestamps non-decreasing within instrument; no `bid > ask`; no negative spread; no duplicate `(instrument, ts, source)` |
| Cross-feed | Dukascopy and HistData M1 bars agree within tolerance; every disagreement is logged, not suppressed |

**Deliverable:** a red test suite and a written spec of what each test means.

**Exit criterion:** you can explain each test's failure mode in one sentence.

---

### Phase 1 — Tick ingest (2–3 weeks)

**Tasks**

- Implement the Dukascopy `.bi5` fetch and decode path. Resumable, idempotent, rate-limited.
- Load into `tick_raw` in ClickHouse. Never transform on ingest.
- Start narrow: 7 majors, 2015–present. Extend backward only after the pipeline is stable.
- Build an ingest ledger table recording what was fetched, when, and whether it succeeded — so a partial run is recoverable and coverage gaps are visible rather than silent.

**Gotchas to expect**

- Dukascopy month indices are zero-based in the URL path. Off-by-one here produces plausible-looking wrong data, which is the worst kind.
- Prices are integer-encoded with a per-instrument decimal factor. JPY pairs differ from EUR pairs.
- ~~Weekends are absent, not empty. Distinguish "no file" from "empty file".~~ **Corrected 2026-08-04 by measurement.** Sunday 03:00 UTC returned **HTTP 200 with a 0-byte body**, not a 404. Dukascopy serves an empty file for closed sessions, so HTTP status cannot distinguish closed-market from feed-gap — both look identical. The ingest ledger must consult the session calendar to make that call, which makes Phase 4 a dependency of Phase 1's coverage report rather than a later addition.
- Some hours return valid but empty payloads legitimately (thin holiday sessions). Do not treat these as errors.

**Deliverable:** populated `tick_raw` with a coverage report by instrument and month.

**Exit criterion:** re-running the ingest produces zero new rows and zero errors.

---

**COMPLETE 2026-08-05.** Implemented in `src/fxpit/ingest/`; exit criterion verified by
`python -m fxpit.ingest --verify-idempotent`, which ingests a range, ingests it again, and
fails unless the second pass fetches nothing and inserts nothing.

Idempotency is achieved by **never re-fetching a settled hour**, not by de-duplicating
after the fact — dedup would require either a mutable tick table (forbidden) or a scan
across billions of rows. The ledger lives in Postgres because it is small and
UPDATE-heavy, which ClickHouse handles badly.

Crash safety rests on the ordering `claim → insert → settle`. A crash between insert and
settle leaves an `in_progress` claim; startup treats every such row as suspect, deletes
that hour's ticks, and re-fetches. The reverse ordering would be far worse — a crash would
mark an hour permanently complete with no data in it, and the ledger would report coverage
that does not exist.

**Two findings from the first live run**, neither of which was in the gotcha list above:

1. **Ask precedes bid in the 20-byte wire record** (`>IIIff` = ms, ask, bid, ask_vol,
   bid_vol). Reversing them produces a uniformly *negative* spread, which reads as a broken
   feed rather than a decode bug — so the failure gets attributed to the wrong component.
2. **Concurrency draws throttling.** Four workers at a 0.15 s pause earned a burst of
   HTTP 503s and 10 failed hours on the first run. Defaults are now 2 workers at 0.25 s
   with jittered backoff on 429/503, so retrying workers do not resynchronise and recreate
   the burst. The ledger handled this correctly without intervention: the following run
   skipped all 86 settled hours and retried exactly the 10 failures.

---

### Phase 2 — Cleaning as a reversible layer (2 weeks)

**Tasks**

- Implement each flag as an independent, re-runnable detector writing to `tick_flag`.
- Build the bid/ask bar materialised views.
- Produce a data-quality report: flag counts by instrument, by hour-of-day, by year.

**Design rule**

Raw is never modified. Cleaning is additive and reversible. If a detector turns out to be wrong, you delete flags and re-run — you do not re-download 300 GB.

**Deliverable:** flag tables plus a written data-quality report.

**Exit criterion:** you can produce, for any instrument-day, a list of every tick that was flagged and why.

---

**COMPLETE 2026-08-05.** Implemented in `src/fxpit/flags/`. Exit criterion is executable:
`python -m fxpit.flags --explain EURUSD 2024-01-08` lists every flagged tick with its
reasons.

**Idempotency is the mirror image of Phase 1's, deliberately.** Ingest never re-fetches a
settled hour because raw data is expensive and immutable. Detectors always recompute their
own scope, deleting their prior flags before inserting, because flags are cheap and
disposable. That makes "delete the flags and re-run" the *only* correction path rather than
an exceptional one.

**Six of eight detectors are running; three are blocked and say so.** `weekend_gap` and
`holiday_thin` need the Phase 4 session and holiday calendars; `feed_disagreement` needs
HistData ingested as a second feed. `session_gap` is the honest measurable subset of
`weekend_gap` — it reports silence without claiming to know its cause. Shipping a guessed
`holiday_thin` would be worse than not shipping it, since the flag distribution is itself a
deliverable and a fabricated flag contaminates it.

**First measured results over 453,143 real ticks** (EURUSD/GBPUSD/USDJPY, 2024-01-05 to
2024-01-09):

| Detector | Flags | Note |
|---|---|---|
| `crossed` | 0 | No inverted quotes in this sample |
| `zero_spread` | 0 | |
| `stale` | 106 | Runs of ≥4 identical quotes |
| `spread_outlier` | 358 | >5× the hour's median spread |
| `rollover_window` | 8,755 | Time-window marker, not a defect |
| `session_gap` | 1 | The weekend — EURUSD is the only pair spanning it |

**1.98% of ticks carry at least one flag.** The most interesting result is the
concentration: **97% of spread outliers (348 of 358) fall at the Friday close and Sunday
reopen** — 255 at Friday 21:00 UTC and 93 across Sunday 22:00–23:00. Session-boundary
spread widening is textbook microstructure, but this is it measured rather than assumed,
and it is directly relevant to hypothesis H2: a strategy backtested on an average spread
pays neither the wide one nor the tight one.

**Gotcha found:** a ClickHouse materialised view only sees rows inserted *after* it is
created — it does not backfill. Every tick from Phase 1 was invisible to `bar_1m_mv` until
replayed with an explicit `INSERT ... SELECT`. The view raises no error while covering only
the future, so `bars_reconcile()` checks that bars account for every tick.

---

### Phase 3 — Bitemporal macro store (2 weeks)

**Tasks**

- Ingest Philadelphia Fed RTDSM vintages into `macro_observation`.
- Reconstruct release timestamps. This is the hard part — RTDSM gives vintage dates, not release times. Release times must be sourced from BLS/BEA schedules and are largely stable (08:30 ET for most US releases) but have changed historically. Where the exact time is unknown, record it explicitly as unknown rather than assuming.
- Add ECB and BIS series for non-USD coverage.
- Implement `as_of(t)` in the DuckDB layer over the Postgres extract.

**Deliverable:** working `as_of()` with Phase 0 revision tests passing.

**Exit criterion:** the no-clairvoyance and revision tests are green.

---

**COMPLETE 2026-08-05.** Implemented in `src/fxpit/query/` and `src/fxpit/macro/`.
**All four acceptance families now pass, not just the two required — 70 tests, 0 failures.**
The suite has been red since Phase 0 by design; this is the phase that turns it green.

**586,494 real vintage observations loaded** from RTDSM: EMPLOY 442,176 (1,050 periods ×
740 vintages, back to 1964), CPI 96,913, ROUTPUT 47,405.

**`as_of()` has exactly one implementation of the filter.** Macro from Postgres and
ticks/bars from ClickHouse both materialise into DuckDB relations with fixed shapes, and
every `as_of` function is SQL over those relations. Where rows come from changes the
loader, never the filter — the guarantee is worth nothing if there are two copies of it
that can drift.

**The tests were not adjusted to fit the implementation.** Phase 0 test bodies are
untouched; `conftest.py` gained a fixture that supplies the backing store they always
assumed. A five-mutation check confirms the suite has teeth — dropping the `known_at`
filter, inverting the vintage ordering, dropping the tick filter, admitting crossed quotes,
and accepting a bare `date` are each caught by 1–5 tests.

### Timestamp honesty

RTDSM publishes a vintage **month**; ALFRED a vintage **date**; neither a release **time**.
So **100% of the loaded archive carries `known_at_precision = 'month'`**, recorded
explicitly rather than assumed away. Coarse timestamps are placed at the **latest** instant
consistent with what is known, which biases every query toward withholding rather than
leaking. Under-reporting what was knowable costs signal; over-reporting it is look-ahead
bias, which is the failure the database exists to prevent.

**Remaining task, not done:** joining BLS/BEA release schedules to upgrade EMPLOY vintages
from month to date precision. The source exists — `Release_-Dates-Employment_Situation-BLS.xls`
was found during Phase 0 verification — but it is legacy `.xls` and needs a different
reader. Phase 6 wants intraday alignment, so this should land before then. ECB/BIS non-USD
coverage is likewise deferred.

### A trap caught in this phase

The first revision ranking reported 1985Q3 real GNP revised from 1,684.8 to 8,604.2 — a
6,919-point move. **It is not a revision.** Real GDP is published in chained dollars of a
base year, the base moved from 1982 to 2017, and a rebasing shifts every historical period
at once. Narrowing to a 400-day window still showed 113%; a single-vintage step still showed
113%. No window is tight enough, so ROUTPUT and CPI are now **excluded** from level-based
revision rankings with the reason stated, rather than quietly included.

This is worth recording because it is the project's own failure mode appearing in its own
output: a plausible number, no error, nothing to alert you. The only defence was knowing
what the units meant.

With rebased series excluded, the genuine result is striking: **December 2009 payrolls —
the crisis trough — had 1,363,000 jobs removed at the first revision** (130,910 → 129,547,
−1.04%).

---

### Phase 4 — Session and calendar layer (1–2 weeks)

Underrated and disproportionately interview-relevant.

**Tasks**

- FX week boundaries (Sunday open / Friday close, which move with DST).
- DST handling. **US and EU shift on different dates**, producing a two-to-three-week window each spring and autumn where the London–New York overlap is an hour different from normal. This is a recurring, genuine source of bugs and a good thing to have handled correctly.
- Rollover window identification (approximately 21:00–22:00 UTC, varying with DST).
- Holiday calendars per currency.

**Deliverable:** a session table joinable to any timestamp.

**Exit criterion:** for any timestamp you can answer: which session, is it rollover, is it a holiday for either leg of the pair.

---

**COMPLETE 2026-08-05.** Implemented in `src/fxpit/sessions/`. Exit criterion is
executable: `python -m fxpit.sessions --describe 2024-01-08T22:30:00Z --pair EURUSD`.

**Nothing in this phase contains a UTC constant.** Every window is defined in local
wall-clock time with an IANA zone and converted, so daylight saving falls out of the
conversion instead of being special-cased. That is the only way the awkward cases come out
right without a table of exceptions: US and EU shifting on different dates, Sydney shifting
the opposite way, Tokyo not shifting at all.

Windows are stored as `TSTZRANGE` with GiST **exclusion constraints**, so overlapping
windows for the same session are impossible rather than merely unlikely — a DST bug that
emitted two overlapping London sessions fails at write time instead of double-counting at
read time.

### The DST anomaly, measured

| Year | Window | Trading days | Overlap | Normal | Delta |
|---|---|---|---|---|---|
| 2024 | Mar 11 – Mar 29 | 15 | 5.0 h | 4.0 h | +1.0 |
| 2024 | Oct 28 – Nov 1 | 5 | 5.0 h | 4.0 h | +1.0 |
| 2025 | Mar 10 – Mar 28 | 15 | 5.0 h | 4.0 h | +1.0 |
| 2025 | Oct 27 – Oct 31 | 5 | 5.0 h | 4.0 h | +1.0 |

For roughly **20 trading days a year the most liquid window of the FX day is an hour longer
than normal**. Hypothesis H6's artefact now exists as a measurement; whether it is
economically meaningful remains open.

### What this unblocked, and what it corrected

`weekend_gap` and `holiday_thin` were blocked on this phase and are now live — eight of
nine detectors run, with only `feed_disagreement` still waiting on HistData.

**`rollover_window` was live but wrong.** It hardcoded 21:00 UTC, which is 17:00 New York
only under EDT. Every winter tick — including the entire January sample — was flagged an
hour early. Corrected results: 4,051 rollover flags at 22:00 UTC, and `holiday_thin` fires
on 100% of USDJPY ticks for 2024-01-08, which is Coming of Age Day in Japan.

`session_gap` survives alongside `weekend_gap` rather than being replaced: one reports
silence, the other asserts the market was shut. A gap during trading hours is a feed outage
and only the first catches it.

### A silent timezone bug, caught here

The first ClickHouse export stripped `tzinfo` before inserting, so the driver read each
naive datetime as machine-local and converted it. On a machine at **UTC+5:45** every
calendar hour landed at `:15` past, the detector's hour join matched nothing, and
`rollover_window` returned **zero flags instead of raising**.

This is precisely the risk §10 rates as high-likelihood — a silent timezone bug producing
plausible-looking wrong data — appearing in the phase built to prevent it. It has a
regression test rather than just a fix, and it is the strongest argument yet for the ECB
drift anchor planned in Phase 5.

### Known approximation

Currency holidays come from the `holidays` package (rules, not a downloaded dataset, so the
no-registration constraint holds). Two caveats are recorded in the data rather than only in
a docstring: **national holidays are a proxy for market holidays**, and **the euro area has
no single calendar** so Germany stands in for EUR. A holiday means thin liquidity, not a
closed market — which is why the flag is `holiday_thin` and not `market_closed`.

---

### Phase 5 — Validation harness (2 weeks)

**Tasks**

- Wire all tests into CI, running against the real database on a schedule.
- Spread distribution monitoring by hour and instrument.
- Tick-rate anomaly detection (a sudden drop usually means a feed gap, not a quiet market).
- Cross-feed reconciliation report with a tracked disagreement rate.

**Deliverable:** a green, scheduled test suite and a reconciliation dashboard or report.

---

**COMPLETE 2026-08-05.** Implemented in `src/fxpit/validation/` and
`.github/workflows/ci.yml`.

**CI runs two jobs.** `fast` is unit tests with no services, seconds on every push.
`stack` runs the whole suite against real Postgres and ClickHouse containers, plus daily on
a schedule — because a point-in-time guarantee proved against fixtures is a weaker claim
than the same guarantee proved against the real stores, and because a scheduled run is what
catches an external source changing shape underneath the pipeline. That has now happened
twice in this project.

### The drift anchor

**219,875 ECB reference rates loaded**, 41 currencies × 7,063 days, 1999-01-04 to
2026-08-04. The comparison instant is the daily concertation procedure at **14:15
Frankfurt** — 13:15 UTC in winter, 12:15 in summer — derived through the Phase 4 local-time
machinery rather than written as a UTC constant. An anchor built to catch timezone bugs,
itself containing one, would be the worst possible outcome.

Against the ingested January 2024 sample the feed agrees with the fix to **−0.54 pips
mean, 1.12 pips worst case**.

**The anchor was then deliberately broken to check it has power:**

| Offset applied | Mean diff | Verdict |
|---|---|---|
| none (correct) | −0.54 pips | within noise |
| +1 h | 8.18 pips | **detected** |
| −1 h | −1.40 pips | **missed** |
| +5 h 45 m (the Phase 4 bug exactly) | 21.95 pips | **detected** |

The −1 h miss is real and is reported rather than hidden: with only two overlapping
observations the anchor lacks power in that direction. That is why it reports a trend
rather than a verdict — a single day's difference means nothing, and what matters is
whether the mean stays near zero as the sample grows. A test asserts the +1 h and +5 h 45 m
cases and deliberately does **not** assert −1 h, because asserting it would claim a
sensitivity the data does not support.

The 5 h 45 m row is not hypothetical. It is exactly the Phase 4 export bug, on this
machine's UTC+5:45 offset. This anchor would have caught it.

**One bug found while building the anchor:** `Europe/Frankfurt` is not an IANA zone and
raises `ZoneInfoNotFoundError`. The canonical CET zone is `Europe/Berlin`. A loud failure
for once, and now pinned by a test.

### Monitors

Spread distribution by instrument-hour, reported as **median / p95 / max rather than a
mean** — spreads are positive and heavy-tailed, so a mean is dragged by exactly the
rollover and news spikes that matter. Tick rate as a **ratio to each instrument's median
hour** rather than an absolute floor, because rates differ by an order of magnitude between
instruments and sessions and an absolute threshold would flag every Asian hour while
missing a real outage in London.

### Success criterion #3 — PARTIALLY met, and said so

> Two independent price feeds reconciled with a documented disagreement rate.

**HistData is not retrievable programmatically.** Measured 2026-08-05: every download page
returns the same 15,599-byte shell with no form and no token, and `get.php` returns HTTP
500. The form is JavaScript-rendered, so retrieving it needs a headless browser — a
materially larger dependency than the rest of the pipeline carries. §3.2's "no login
required" was true about accounts and wrong about accessibility.

**The ECB fix substitutes partially.** It is a genuine independent second opinion — it
comes from somebody else's process and therefore cannot share a bug with this pipeline —
and §3.2 already lists it under reconciliation. But it is daily, so it cannot produce the
bar-by-bar disagreement *rate* an M1 feed would.

`feed_disagreement` therefore remains the one detector of nine still blocked. The criterion
is recorded as partially met rather than quietly redefined to fit what was achievable.

---

### Phase 6 — The contamination experiment (1–2 weeks)

This is the deliverable that makes the project worth putting on a CV.

**Design**

Take one simple, pre-registered macro-reactive rule. Pre-registration matters: write the rule down before you look at results, so the experiment measures contamination rather than your own search.

Suggested rule: directional trade in EURUSD held for N minutes following a US CPI or payrolls release, conditioned on the sign of the surprise.

Evaluate under four data regimes:

| Variant | Macro data | Timestamp | Costs |
|---|---|---|---|
| A — Honest | First print, `as_of` release | Exact release timestamp | Actual bid/ask from feed |
| B — Revised values | Final revised value | Exact release timestamp | Actual bid/ask |
| C — Revised + date-only | Final revised value | Date only, entry at day open | Actual bid/ask |
| D — C plus mid-price | Final revised value | Date only | Mid price, no spread |

**Deliverable:** four Sharpe ratios, a chart, and a written interpretation.

**Exit criterion:** you can state the size of each contamination source in basis points or Sharpe units.

---

**HARNESS COMPLETE 2026-08-05; the run is UNDERPOWERED and says so.**
Implemented in `src/fxpit/experiment/`.

### Pre-registration

`docs/preregistration.md` was written **before any variant ran**, and its sha256
(`a96c0c7287807cfb`) is printed with every result so the claim is checkable rather than
asserted. A test enforces that the document and the code agree on the holding period, the
event count per year, and the variant set — a pre-registration that disagrees with the code
is worse than none, because it documents a rule nobody ran.

### Event construction

Release dates come from ALFRED vintage dates, which also **closes the task Phase 3 left
open**: RTDSM publishes a vintage month and cannot say which day a value became public.
Combined with the standing 08:30 ET release time — derived from local time, so 13:30 UTC in
winter and 12:30 in summer — that gives an instant precise enough for an intraday
experiment.

**A vintage date only counts as a release if it introduces a new reference period.** CPI
produces ~13 vintages a year because the annual seasonal-factor revision republishes old
periods without adding a new one; trading those would be trading an event that did not
happen. Filtering gives **72 genuine releases** in 2022-2024 from 75 vintage dates.

### The result, and why it cannot be read as one

| Variant | n | Mean bps | Sharpe |
|---|---|---|---|
| A — Honest | 8 | 8.82 | 2.228 |
| B — Revised values | 8 | −0.55 | −0.122 |
| C — Revised + date-only | 8 | 5.22 | 1.902 |
| D — C plus mid-price | 8 | 5.58 | 2.022 |

**8 of 72 events. The sample is too small for these numbers to mean anything**, and that is
enforced structurally rather than left to prose: `Experiment.underpowered` is a property of
the result object, the CLI prints a banner, and the hypothesis section refuses to render a
verdict. H1's ordering would read "does not hold", but at this N that is a coin toss.

The differences between arms are small quantities measured against the variance of
30-minute FX returns. At eight events that variance swamps them completely.

### Why only 8 of 72

**Dukascopy IP-blocked the ingest.** After roughly 360 requests in a short window the feed
stopped returning HTTP responses at all — TCP connect timeouts, not 429s. 377,418 ticks
landed with zero recorded errors before it cut off; the pipeline behaved correctly and the
source refused.

This is a real operational constraint and belongs in the risk register alongside §10's
"Dukascopy changes or restricts the feed": **a large historical ingest must be paced over
hours or days, not minutes.** The ledger makes the remaining work resumable — a re-run
fetches only what is missing — so completing the experiment is ingest time, not new code.

### One channel that IS validated

C → D measures the mid-price assumption, which should equal exactly one spread per round
trip. Observed: **+0.36 bps against a measured median EURUSD spread of 0.271 bps** —
slightly higher because the date-only arms enter at 00:00 UTC in the thin Asian session,
where spreads are wider. The channel measures what it claims to, independent of whether the
sample is large enough to interpret the strategy result.

---

## 7. Expected end result

**Artifacts**

1. A running three-tier database with tick and macro data under a single `as_of()` interface.
2. A test suite that fails loudly when look-ahead bias is introduced.
3. A data-quality report on FX tick pathologies — flag distributions, cross-feed disagreement rates, spread behaviour by session.
4. A contamination study quantifying four distinct sources of backtest inflation.
5. A README documenting every limitation, unflinchingly.

**Capabilities you should have afterwards**

- Reading someone's backtest and identifying, in a few questions, whether they handled release timing, revisions, and spread realistically.
- Explaining why a two-tier storage architecture is correct for market data, with volume numbers to back it.
- Discussing FX microstructure specifics — rollover, session effects, the absence of a consolidated tape — from measurement rather than reading.

**What you will not have**

A profitable strategy. Stated plainly so it isn't a disappointment later.

---

## 8. Expected findings

These are **hypotheses, not predictions**. Several may be wrong, and being wrong is a legitimate result to write up. They are recorded now so that confirmation bias is harder later.

### H1 — Contamination ordering
D > C > B > A in reported Sharpe.
*Confidence: high.* Each variant strictly adds an information or cost advantage that was unavailable in reality.

### H2 — Spread is the largest single contamination source
The A→D gap will be dominated by the mid-price assumption rather than by the revision effect, because the strategy is short-horizon and trades in a window where spreads widen.
*Confidence: moderate.* This is a genuine prediction that could fail. If the revision effect dominates instead, that is more interesting and worth writing up prominently.

### H3 — Timestamp precision matters more than revision magnitude
The B→C gap (exact release time vs date-only) will exceed the A→B gap (first print vs revised).
*Confidence: moderate to low.* Depends heavily on holding period. Almost certainly true under Path A; possibly reversed under Path B.

### H4 — Cross-feed disagreement is non-trivial and non-random
Dukascopy and HistData will disagree on a measurable fraction of M1 bars, concentrated around rollover, news releases, and thin holiday sessions.
*Confidence: high on the existence of disagreement; low on the magnitude.* The magnitude is genuinely unknown to me and is one of the more interesting numbers this project will produce.

### H5 — Flagged-tick share is higher than expected
Stale and crossed quotes will appear at a rate that surprises you, and will cluster in specific hours.
*Confidence: moderate.* Widely reported anecdotally by practitioners; not something I can cite a specific figure for.

### H6 — DST transition weeks show structural anomalies
The US/EU DST offset windows will show measurably different session-overlap behaviour.
*Confidence: high that the artefact exists; unknown whether it is economically meaningful.*

---

## 9. Known limitations

These are structural. They cannot be engineered away under the free/no-login constraint, and they belong in the README rather than being discovered by a reviewer.

1. **No consolidated tape.** There is no canonical FX price. Dukascopy is one ECN's aggregated view. A different broker's tape will differ, especially in the tails and around rollover. Every result is conditional on this feed.

2. **Volume is not volume.** Dukascopy's volume field reflects their own pool. It is not market volume and nothing should be built on it.

3. **Small cross-section.** Roughly 8 majors and 30 liquid pairs, against 5,000+ names in US equities. This means **less** statistical power, which counterintuitively makes overfitting risk **higher**. Multiple-testing correction matters more here, not less.

4. **No free historical consensus forecasts.** Surprise measures must be constructed from revisions or synthetic forecasts, and the choice affects results.

5. **No forward points.** Carry work must proxy with policy rate differentials, and that proxy carries a known error that is correlated with funding stress (persistent CIP deviations post-2008).

6. **Equity coverage is CFD-derived.** If you extend to stocks via Dukascopy, note that this is not exchange tape, has no meaningful volume, and carries no corporate action data. It is adequate for index and large-cap directional work and inadequate for anything microstructural.

7. **Retail execution assumptions.** Even with real bid/ask, this data does not capture slippage, rejection, requoting, or last-look behaviour at a real broker. Backtest fills are optimistic by an unmeasured amount.

---

## 10. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Dukascopy changes or restricts the feed | Low–moderate | HistData already ingested as second source; ingest ledger makes a re-pull tractable |
| Tick volume exceeds local disk | Moderate | Start with 7 pairs / 10 years; partition by instrument-month; compress aggressively; extend only after measuring |
| Phase 1 overruns | High | Explicitly budgeted at 2–3 weeks. If it hits 5, cut the pair universe rather than the quality checks |
| ClickHouse operational learning curve | Moderate | Single-node Docker only. No clustering, no replication. Resist scope creep here |
| Scope creep into strategy building | High | Phase 6 is the only place strategy code is permitted, and only as a measurement instrument |
| Silent timezone bugs | High | The ECB daily fix anchor in Phase 5 is specifically designed to catch these |

---

## 11. Timeline

| Phase | Weeks | Cumulative |
|---|---|---|
| 0 — Acceptance tests | 1 | 1 |
| 1 — Tick ingest | 2–3 | 3–4 |
| 2 — Cleaning layer | 2 | 5–6 |
| 3 — Macro bitemporal store | 2 | 7–8 |
| 4 — Session & calendar | 1–2 | 8–10 |
| 5 — Validation harness | 2 | 10–12 |
| 6 — Contamination experiment | 1–2 | 11–13 |

Phases 1 and 3 are the ones that historically overrun. If forced to cut, cut the pair universe and the date range — never the acceptance tests, because without them the database has no claim to being point-in-time.

---

## 12. Immediate next actions

1. ~~Decide Path A, B, or C (Section 2).~~ **DONE 2026-08-04 — Path C.**
2. ~~Verify Dukascopy feed access with a single-day EURUSD pull.~~ **DONE.** 4,508 EURUSD ticks for 2024-01-09 10:00 UTC; zero crossed quotes, monotonic stamps, spreads 0.1–0.5 pip. Zero-based months confirmed empirically (month `00` → mean bid 1.09409 ≈ January; month `01` → 1.07665 ≈ February). JPY decimal factor 1e3 confirmed against USDJPY.
3. ~~Verify Philadelphia Fed RTDSM download works without registration.~~ **DONE.** Eight files across the payrolls/CPI/GDP pages. `employMvMd.xlsx` (2.2 MB) has columns `EMPLOY64M12`…`EMPLOY26Mxx` (one per vintage) and rows `1943:11`…`2026:06` (ref periods) — the bitemporal grid in spreadsheet form. **Gotcha:** file URLs require a Sitecore query string (`?sc_lang=en&hash=…`); without it you get a soft-404 (HTTP 200 serving an HTML error page). The hash can rotate, so scrape the series page for links rather than hardcoding URLs. Also found: `Release_-Dates-Employment_Situation-BLS.xls`, actual BLS release dates, which covers much of Phase 3's "hard part" — note it is legacy `.xls`, not `.xlsx`.
4. ~~Test whether ALFRED CSV download works without an API key.~~ **DONE — it does not, silently.** See §3.3. Reclassified as an optional key-gated enrichment source.
5. ~~Set up the repo and write the Phase 0 tests.~~ **DONE 2026-08-04.** Red suite: 17 failing (all `NotImplementedError` from the unimplemented `as_of()`), 11 passing (fixture-validation). See `tests/SPEC.md`.

**Next:** Phase 1 — Dukascopy tick ingest. Items 1–4 are resolved, so ingestion code is unblocked.