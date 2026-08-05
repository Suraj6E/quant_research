# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

**Phases 0–3 are complete; Phase 4 is next.**

**The whole suite is green: 70 passed, 0 failed.** The acceptance suite was red by design from Phase 0 until Phase 3 implemented `as_of()`. It must stay green now — a red acceptance test means a point-in-time guarantee regressed, not that the suite is "still in its expected state". Do not fix one by weakening its assertion.

Phase 0's test bodies have never been edited. `conftest.py` supplies the backing store they always assumed. Keep it that way.

## Phase 1 ingest (src/fxpit/ingest/)

```powershell
python -m fxpit.ingest --instruments EURUSD --start 2024-01-08 --end 2024-01-09
python -m fxpit.ingest --majors --start 2024-01-08 --end 2024-01-09
python -m fxpit.ingest --report                # coverage + ledger/store reconciliation
python -m fxpit.ingest --verify-idempotent ... # asserts the exit criterion
```

- **Idempotency comes from the ledger, not from dedup.** A settled hour is never re-fetched. Dedup after the fact would need a mutable tick table (forbidden) or a scan of billions of rows.
- **Never reorder `claim → insert → settle`.** A crash between insert and settle leaves an `in_progress` claim, which startup treats as suspect: it deletes that hour's ticks and re-fetches. Settling first would mark an hour permanently complete with no data in it.
- **`store.delete_hour()` is the only write path that removes ticks** and exists solely to undo a crashed claim. Never use it to clean or correct data that landed successfully.
- **Decoding is not transformation.** Integer→float via the decimal factor and ms-offset→UTC timestamp are required to have a price at all. Everything else the feed sent — crossed quotes, zero spreads, duplicate stamps, out-of-order rows — is preserved untouched and flagged additively in Phase 2.
- **Ask precedes bid in the 20-byte wire record** (`>IIIff` = ms, ask, bid, ask_vol, bid_vol). Swapping them yields a uniformly negative spread that reads as a broken feed rather than a decode bug.
- **Concurrency above 2 workers draws HTTP 503** (measured: 4 workers at 0.15s pause produced 10 throttled hours). Defaults are 2 workers / 0.25s with jittered backoff on 429/503 so retrying workers don't resynchronise.

## Phase 2 cleaning layer (src/fxpit/flags/)

```powershell
python -m fxpit.flags --bars --instruments EURUSD --start 2024-01-05 --end 2024-01-09
python -m fxpit.flags --scan --instruments EURUSD --start 2024-01-05 --end 2024-01-09
python -m fxpit.flags --report
python -m fxpit.flags --explain EURUSD 2024-01-08   # the exit criterion
```

- **Idempotency here is the mirror of Phase 1's, on purpose.** Ingest never re-fetches a settled hour (raw is expensive and immutable); detectors always recompute their own scope, deleting their prior flags first (flags are cheap and disposable). Keep it that way — it makes "delete the flags and re-run" the only correction path rather than an exceptional one.
- **Detectors run as SQL inside ClickHouse**, never by streaming ticks into Python. They may only read `tick_raw`; a detector that read `tick_flag` would make results order-dependent. Tests enforce both.
- **A blocked detector stays in the catalogue with its reason.** `weekend_gap` and `holiday_thin` need Phase 4; `feed_disagreement` needs HistData. Do not ship a guessed implementation — the flag distribution is a deliverable, so a fabricated flag contaminates the thing it describes. `session_gap` is the honest subset of `weekend_gap`: it reports silence without claiming to know the cause.
- **A ClickHouse materialised view does not backfill.** It only sees rows inserted after it was created, raises no error, and silently covers only the future. `--bars` replays existing ticks; `bars_reconcile()` checks bars account for every tick.
- **Bars carry no mid column and must not gain one.** Mid-price collapse is one of the four contamination sources Phase 6 measures.

## Phase 3 query layer + macro (src/fxpit/query/, src/fxpit/macro/)

```powershell
python -m fxpit.macro --load        # download and load RTDSM vintages
python -m fxpit.macro --report      # coverage and timestamp precision
python -m fxpit.macro --revisions   # largest first revisions
```

- **`as_of()` has exactly one implementation of the `known_at <= t` filter.** Sources materialise into DuckDB relations with fixed shapes; every `as_of` function is SQL over those relations. Adding a second filter path — even a "quick" one for a different backend — destroys the guarantee, because two copies can drift.
- **`open_production_session()` is scopeable and should be scoped.** `series=[...]` for macro; ticks and bars are always windowed. Loading is bulk-via-Arrow, not `executemany` — the row-by-row path took 339s for the payrolls archive versus 1.9s.
- **Coarse timestamps are placed at the LATEST consistent instant**, never the earliest or the middle. A month-precision vintage sits at month end so queries withhold rather than leak. Never "improve" this by centring it.
- **Do not compare rebased series by level across vintages.** `loader.REBASED` lists them (ROUTPUT, CPI); `loader.UNITS_STABLE` lists the ones where a level difference is genuinely new information (EMPLOY). A new series must be classified into one or the other — a test enforces it. Ignoring this produced a 6,919-point "revision" that was purely a 1982→2017 base-year change.
- **Passing a bare `date` to `as_of()` raises.** `datetime` subclasses `date`, so the isinstance order matters; never relax it.

`planning.md` is the specification. Read it before writing code in this repo; it is the source of truth for schema, phasing, and rationale. `docs/architecture.md` covers how the tiers are wired, `docs/setup.md` covers environment setup, `docs/ui-design.md` covers the dashboard. This file records only the rules that are easy to violate accidentally.

## Dashboard rules (docs/ui-design.md)

A FastAPI dashboard in `src/fxpit/web/` covers all seven phases. **Do not migrate it to Django** — the ORM and admin generate direct table access, which contradicts success criterion #1. FastAPI was chosen precisely because it has no ORM, so the UI can only consume `fxpit.query`.

- **Every panel declares provenance** — `live` / `recorded` / `demo`. `Panel` has no default and raises if a DEMO panel does not name the phase replacing it. Demo cards render dashed and hatched so they cannot be mistaken for results at a glance.
- **When a demo panel becomes real:** add the accessor to `live.py`, flip the provenance, then **delete the generator from `demo.py`**. A leftover generator is what the next person reaches for by mistake.
- **Every chart returns a `TableView`.** It is a required field, not an option — light-mode aqua sits at 2.74:1 against the surface, which obligates relief under the contrast rule.
- **Charts are server-rendered SVG, max 3 series.** No CDN, no chart library, no web fonts — the no-external-requests constraint applies to the UI too. Past three series the palette stops clearing the all-pairs CVD floor.
- Re-run the palette validator if colours change; never eyeball CVD safety.

## Stack and commands

Python 3.12+ (3.13 tested), venv + pip, pytest, ruff, Docker Compose. Python was chosen because the storage tiers all have first-class Python clients and the workload is network- and ClickHouse-bound, not CPU-bound — see the rejected-alternatives table in `docs/architecture.md` for why C++/HFT architecture was considered and rejected.

```powershell
.\.venv\Scripts\Activate.ps1     # venv already created at .venv
pip install -e ".[dev,web]"

uvicorn fxpit.web.app:app --reload --port 8000   # dashboard; /api/docs for OpenAPI

docker compose up -d             # Postgres + ClickHouse; needs Docker Desktop running
docker compose down              # stop, keep data
docker compose down -v           # stop, DESTROY volumes

ruff check .                     # lint
ruff format .                    # format
pytest                           # exits 5 (no tests collected) until Phase 0
pytest tests/test_x.py::test_y   # single test
pytest -m acceptance             # point-in-time suite only
pytest -m "not integration"      # skip tests needing a live stack
```

Schema bootstrap in `infra/*/init/*.sql` runs **only against an empty volume**. Editing those files and restarting does nothing — either apply changes by hand or `docker compose down -v && docker compose up -d`, which destroys all ingested data.

## Resolved decisions (planning.md §2, §12) — all four blockers cleared 2026-08-04

1. **Path C decided.** Tick layer built properly, scoped to 7 majors from 2015; carry construction deferred to a follow-on project. Ingestion is unblocked.
2. **Dukascopy verified.** Zero-based month indices confirmed empirically; JPY factor 1e3 vs 1e5 confirmed.
3. **RTDSM verified.** Files need a Sitecore `?sc_lang=en&hash=…` query string — without it you get a soft-404 (HTTP 200 serving HTML). The hash rotates, so scrape the series page rather than hardcoding URLs.
4. **ALFRED reclassified as optional, key-gated.** The keyless CSV accepts `vintage_date` and *silently ignores it*, returning current revised data — verified byte-identical across four vintage dates.

## Hard constraint: free, no registration

Every data source must be free and require **no account, no login, no API key**. This is what defines the project's source set, not a preference (it is also why kdb+ is excluded from the storage options).

**The one carve-out:** ALFRED is wired in as an *optional enrichment source* behind `FRED_API_KEY`. The pipeline must run to completion with that variable unset, and Philadelphia Fed RTDSM stays primary. Never make ALFRED a hard dependency — the point of the constraint is that anyone can clone the repo and reproduce the work without an account. Note ALFRED's real-time axis is date-granularity, so it does not supply release *times*.

## Architecture

Three tiers, because the data has two incompatible shapes:

| Tier | Store | Holds | Scale |
|---|---|---|---|
| A | PostgreSQL | Instruments, calendars, sessions, macro release schedule, macro vintages, rate series | single-digit millions of rows; needs referential integrity |
| B | ClickHouse | `tick_raw`, `tick_flag`, bars as materialised views | 2–5 billion rows projected; append-only |
| C | DuckDB | Research query layer over Parquet / ClickHouse exports / Postgres extracts | in-process |

ClickHouse runs **single-node Docker only** — no clustering, no replication. That is a deliberate scope limit, not a TODO.

## Invariants — violating these breaks the project's premise

These exist because the whole point of the database is that it cannot leak future information. Code that breaks one of them is wrong even if its tests pass.

- **`as_of(t)` is the only sanctioned read path.** It lives in the DuckDB layer (Tier C). Do not add convenience accessors that query Tier A/B tables directly — that is exactly how look-ahead bias re-enters after being eliminated. Direct table SELECTs outside the query layer are an audit failure (success criterion #1).
- **Bitemporality is two columns, always both:** `ref_period` (the period a value describes) and `known_at` (when it became public). A point-in-time query filters `known_at <= t` and keeps the latest `known_at` per key. Revisions then fall out for free.
- **`known_at` is `TIMESTAMPTZ`, never `DATE`.** An 08:30 ET release and an 08:31 ET price are different objects. A date-only column silently destroys that distinction and destroys the project entirely under Path A.
- **`tick_raw` is immutable.** Never modified after ingest, never transformed on ingest. All cleaning is *additive* — detectors write rows to the separate `tick_flag` table. A wrong detector means deleting flags and re-running, not re-downloading 300 GB.
- **Bid and ask are always kept separate.** Bars are materialised as both bid-bars and ask-bars; never mid-only. A feed that forces an assumed spread is how backtests lie, and mid-price collapse is one of the four contamination sources Phase 6 is built to measure.
- **Disagreements are logged, never suppressed.** HistData is a second opinion, not a fallback; the Dukascopy/HistData disagreement rate is itself a deliverable.
- **Unknown means unknown.** Where a macro release time can't be sourced, record it as explicitly unknown rather than assuming 08:30 ET.

## Scope guardrails

- Not a trading system: no live execution, no order management.
- Not a profitable strategy. Strategy code is permitted **only** in Phase 6, and only as a measurement instrument for the contamination experiment.
- Scope creep into strategy building is flagged as a high-likelihood risk. If Phase 1 overruns, cut the pair universe and date range — **never** the acceptance tests, since without them the database has no claim to being point-in-time.

## Phase order

Phase 0 is **done** (red suite, `tests/SPEC.md`). Remaining: 1 tick ingest → 2 cleaning/flags → 3 bitemporal macro → 4 session & calendar → 5 validation harness → 6 contamination experiment. Each phase in `planning.md` has an explicit exit criterion; treat it as the definition of done.

Do not "fix" a red acceptance test by weakening its assertion. The suite goes green when the pipeline provides the guarantee, not before — Phase 3's exit criterion is precisely that the no-clairvoyance and revision families pass. Raise `MIN_REVISED_PERIODS_FIXTURE` (currently 8) to the 50 that `planning.md` requires once real RTDSM vintages are loaded.

## Ingest gotchas (Phase 1)

Recorded because each produces plausible-looking wrong data rather than an error:

- Dukascopy month indices in the URL path are **zero-based**.
- Prices are integer-encoded with a **per-instrument** decimal factor; JPY pairs differ from EUR pairs.
- Weekends return **HTTP 200 with a 0-byte body**, not a 404 (measured; `planning.md`'s original "absent, not empty" claim was wrong). HTTP status therefore cannot distinguish closed-market from feed-gap — the ingest ledger must consult the session calendar, making Phase 4 a dependency of Phase 1's coverage report.
- Some hours legitimately return valid-but-empty payloads (thin holiday sessions). Not an error.
- Ingest must be resumable and idempotent, with an ingest ledger recording what was fetched and whether it succeeded, so coverage gaps are visible rather than silent. Exit criterion for Phase 1 is that a re-run produces zero new rows and zero errors.

## Known limitations to preserve in writing

`planning.md` §9 lists seven structural limitations (no consolidated tape, Dukascopy volume is not market volume, small cross-section raising overfitting risk, no free consensus forecasts, no forward points with a CIP-deviation-correlated proxy error, CFD-derived equity coverage, optimistic retail fills). These belong in the README and must not be quietly dropped or softened — stating them is success criterion #5.
