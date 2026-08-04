# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

**Infrastructure is scaffolded and verified; no pipeline code exists yet.** `src/fxpit/` is a bare package skeleton and `tests/` is empty — Phase 0 has not started.

`planning.md` is the specification. Read it before writing code in this repo; it is the source of truth for schema, phasing, and rationale. `docs/architecture.md` covers how the tiers are wired, `docs/setup.md` covers environment setup. This file records only the rules that are easy to violate accidentally.

## Stack and commands

Python 3.12+ (3.13 tested), venv + pip, pytest, ruff, Docker Compose. Python was chosen because the storage tiers all have first-class Python clients and the workload is network- and ClickHouse-bound, not CPU-bound — see the rejected-alternatives table in `docs/architecture.md` for why C++/HFT architecture was considered and rejected.

```powershell
.\.venv\Scripts\Activate.ps1     # venv already created at .venv
pip install -e ".[dev]"

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

## Blocking decisions (planning.md §2, §12)

Do not write ingestion code until these are resolved:

1. **Path A / B / C is undecided** and materially changes Phases 1–4. Path A (intraday) makes the tick layer central and demands timestamp-precise macro; Path B (multi-day) shifts ~70% of effort to macro/rates and hits the forward-points gap; Path C (both, narrow scope) is the document's working assumption but is *not* a decision. Everything in `planning.md` assumes C unless annotated.
2. Dukascopy feed access verified with a single-day EURUSD pull.
3. Philadelphia Fed RTDSM download verified to work without registration.
4. ALFRED CSV download tested without an API key — if a key is required it is **out of scope** (see constraint below) and ALFRED gets dropped.

## Hard constraint: free, no registration

Every data source must be free and require **no account, no login, no API key**. This is not a preference — it is what defines the project's source set. An API key requirement disqualifies a source (this is why ALFRED is conditional, and why kdb+ is excluded from the storage options).

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

Phase 0 (write the acceptance tests against an empty schema, watch them fail) comes **before** any pipeline code. The four test families are: no-clairvoyance, revision, tick sanity, cross-feed. Later phases: 1 tick ingest → 2 cleaning/flags → 3 bitemporal macro → 4 session & calendar → 5 validation harness → 6 contamination experiment. Each phase in `planning.md` has an explicit exit criterion; treat it as the definition of done.

## Ingest gotchas (Phase 1)

Recorded because each produces plausible-looking wrong data rather than an error:

- Dukascopy month indices in the URL path are **zero-based**.
- Prices are integer-encoded with a **per-instrument** decimal factor; JPY pairs differ from EUR pairs.
- Weekends are *absent*, not empty — distinguish "no file" from "empty file".
- Some hours legitimately return valid-but-empty payloads (thin holiday sessions). Not an error.
- Ingest must be resumable and idempotent, with an ingest ledger recording what was fetched and whether it succeeded, so coverage gaps are visible rather than silent. Exit criterion for Phase 1 is that a re-run produces zero new rows and zero errors.

## Known limitations to preserve in writing

`planning.md` §9 lists seven structural limitations (no consolidated tape, Dukascopy volume is not market volume, small cross-section raising overfitting risk, no free consensus forecasts, no forward points with a CIP-deviation-correlated proxy error, CFD-derived equity coverage, optimistic retail fills). These belong in the README and must not be quietly dropped or softened — stating them is success criterion #5.
