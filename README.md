# Point-in-Time FX Research Database

A research database for FX (and optionally CFD-equity) data where every query
can be answered **as of a historical timestamp**, returning only information
that was actually available at that moment.

**Status:** all seven phases built. The acceptance suite is green and the
contamination experiment runs. Two things are honestly incomplete — see
[What is not finished](#what-is-not-finished).

**Constraint:** every data source is free and requires no account, no login,
and no API key. This is not a preference — it is what defines the source set.

---

## Why

The database is not the point. The point is that after building it you can:

1. Detect look-ahead bias in someone else's backtest by asking two questions.
2. Quantify how much of a reported result comes from data contamination rather
   than signal.
3. Defend a data architecture decision for 45 minutes in an interview.

### What this is not

- **Not a trading system.** No live execution, no order management.
- **Not a profitable strategy.** Strategy code appears only in Phase 6, and only
  as a measurement instrument. Stated plainly so it isn't a disappointment later.
- **Not a general-purpose market data platform.** Scope is deliberately narrow.

---

## The core idea

Every fact carries two independent time axes:

- **`ref_period`** — the period the value describes
- **`known_at`** — the moment the value became publicly available

A point-in-time query filters `known_at <= t` and keeps the latest `known_at`
per key. You get what the world believed at time `t`, not what turned out to be
true later. Revisions fall out for free.

`as_of(t)` is the only sanctioned read path. Everything else is plumbing.

---

## Architecture

Three tiers, because the data has two incompatible shapes.

| Tier | Store | Holds | Scale |
|---|---|---|---|
| A | PostgreSQL | Instruments, calendars, sessions, macro release schedule, macro vintages, rates | single-digit millions of rows |
| B | ClickHouse | `tick_raw`, `tick_flag`, bars as materialised views | 2–5 billion rows projected |
| C | DuckDB | Research query layer; `as_of()` lives here | in-process |

ClickHouse runs single-node Docker only — no clustering, no replication, by
design. Full detail in [`docs/architecture.md`](docs/architecture.md).

---

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,web]"

Copy-Item .env.example .env    # then set both passwords
docker compose up -d
pytest                          # 100+ tests, all green
uvicorn fxpit.web.app:app --port 8000
```

Full instructions, verification steps, and troubleshooting in
[`docs/setup.md`](docs/setup.md).

### The pipeline

```powershell
python -m fxpit.ingest     --instruments EURUSD --start 2024-01-05 --end 2024-01-09
python -m fxpit.sessions   --build --start 2024-01-01 --end 2026-01-01
python -m fxpit.sessions   --export      # mirror the calendar for tick detectors
python -m fxpit.flags      --bars --scan --instruments EURUSD --start 2024-01-05 --end 2024-01-09
python -m fxpit.macro      --load        # RTDSM vintages
python -m fxpit.validation --load-ecb --drift --instrument EURUSD --start 2024-01-05 --end 2024-01-09
python -m fxpit.experiment --run --start 2022-01-01 --end 2025-01-01
```

A web dashboard covers every phase, with a research prospectus at `/` and
per-phase pages showing live data. Every panel declares whether its numbers are
measured or synthetic — see [`docs/ui-design.md`](docs/ui-design.md).

---

## Data sources

All verified by measurement, not by reading a page that claimed they were free.

| Source | Role | Status |
|---|---|---|
| Dukascopy | Primary tick feed, separate bid/ask | ✅ verified, no account |
| Philadelphia Fed RTDSM | Macro vintages, the point-in-time layer | ✅ verified, no account — 586k observations loaded |
| ECB reference rates | Independent drift anchor | ✅ verified, no account — 219,875 rates back to 1999 |
| ALFRED | Vintage dates and first-print values | ⚠️ **key required** — optional enrichment, pipeline runs without it |
| HistData.com | Second opinion for reconciliation | ❌ **not retrievable without a browser** |

Dukascopy is the primary feed for one reason: it gives **separate bid and ask**.
A feed that provides only mid price forces you to assume a spread, and assumed
spreads are how backtests lie.

---

## Known limitations

These are structural. They cannot be engineered away under the free /
no-registration constraint, and stating them explicitly is a success criterion
for the project rather than a caveat appended to it.

1. **No consolidated tape.** There is no canonical FX price. Dukascopy is one
   ECN's aggregated view. A different broker's tape will differ, especially in
   the tails and around rollover. Every result is conditional on this feed.

2. **Volume is not volume.** Dukascopy's volume field reflects their own pool.
   It is not market volume and nothing should be built on it.

3. **Small cross-section.** Roughly 8 majors and 30 liquid pairs, against 5,000+
   names in US equities. This means *less* statistical power, which
   counterintuitively makes overfitting risk *higher*. Multiple-testing
   correction matters more here, not less.

4. **No free historical consensus forecasts.** Surprise measures must be built
   from revisions or synthetic forecasts, and that choice affects results.

5. **No forward points.** Carry work must proxy with policy rate differentials.
   That proxy carries a known, non-random error: covered interest parity has
   failed persistently since 2008, so the error correlates with funding stress —
   precisely when carry strategies matter most.

6. **Equity coverage is CFD-derived.** Not exchange tape, no meaningful volume,
   no corporate action data. Adequate for index and large-cap directional work,
   inadequate for anything microstructural.

7. **Retail execution assumptions.** Even with real bid/ask, this data does not
   capture slippage, rejection, requoting, or last-look behaviour at a real
   broker. Backtest fills are optimistic by an unmeasured amount.

---

## Phases

Phase 0 comes first: write the acceptance tests against an empty schema and
watch them fail. That discipline is what separates a research database from a
scraping script.

| Phase | Exit criterion | |
|---|---|---|
| 0 — Acceptance tests | Each test's failure mode explainable in one sentence | ✅ |
| 1 — Tick ingest | A re-run produces zero new rows and zero errors | ✅ |
| 2 — Cleaning layer | For any instrument-day, list every flagged tick and why | ✅ |
| 3 — Bitemporal macro | No-clairvoyance and revision tests green | ✅ |
| 4 — Session & calendar | For any timestamp: which session, rollover?, holiday? | ✅ |
| 5 — Validation harness | Green scheduled suite plus reconciliation report | ✅ |
| 6 — Contamination experiment | Each contamination source sized in bps or Sharpe units | ✅ |

The acceptance suite was **red by design** from Phase 0 until Phase 3 implemented
`as_of()`. It is green now, and a red acceptance test means a point-in-time
guarantee has regressed.

If a phase overruns, cut the pair universe and date range — **never** the
acceptance tests, since without them the database has no claim to being
point-in-time.

Full specification, schema, and rationale in [`planning.md`](planning.md).

---

## What is not finished

Two things, both recorded rather than quietly redefined.

**Success criterion #3 is partially met.** It asks for two independent price
feeds reconciled with a documented disagreement rate. HistData turned out **not
to be retrievable programmatically** — measured 2026-08-05, every download page
returns the same 15,599-byte shell with no form or token, and `get.php` returns
HTTP 500. The form is JavaScript-rendered, so it needs a headless browser. The
ECB reference fix substitutes as a genuine independent anchor, but it is daily,
so it cannot produce the bar-by-bar disagreement *rate* an M1 feed would. One
detector of nine, `feed_disagreement`, remains blocked.

**Tick coverage is narrow.** The archive covers the days the experiment reads,
not a continuous history. Extending it is ingest time, not new work: the
pipeline is idempotent, so a re-run adds only what is missing.

### Verified source findings

Recorded because each cost time to discover and each fails silently:

| Source | Finding |
|---|---|
| Dukascopy | Works, no account. Month indices are **zero-based**; JPY pairs use a 1e3 decimal factor; **ask precedes bid** on the wire; weekends return HTTP 200 with a zero-byte body, not a 404 |
| RTDSM | Works, no account. URLs need a Sitecore `?sc_lang=en&hash=…` query string, and the hash rotates — without it you get HTTP 200 serving an HTML error page |
| ALFRED, keyless | **Accepts `vintage_date` and silently ignores it.** Four different vintage dates returned byte-identical payloads. Ask for the 2005 vintage, receive today's numbers, no warning |
| ALFRED, with key | Works. Real vintages via `realtime_start`/`realtime_end`, but **date-granularity** — it gives the vintage date, not the release time |
| ECB | Works, no account. 7,064 daily rows back to 1999 |
| HistData | **Not retrievable without a browser** |

---

## License

See [LICENSE](LICENSE).
