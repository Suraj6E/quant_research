# Point-in-Time FX Research Database

A research database for FX (and optionally CFD-equity) data where every query
can be answered **as of a historical timestamp**, returning only information
that was actually available at that moment.

**Status:** early setup. Infrastructure scaffolded; no pipeline code yet.
One decision is still open and blocks ingestion — see [Open decisions](#open-decisions).

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
pip install -e ".[dev]"

Copy-Item .env.example .env    # then change both passwords
docker compose up -d
```

Full instructions, verification steps, and troubleshooting in
[`docs/setup.md`](docs/setup.md).

---

## Data sources

| Source | Role | Status |
|---|---|---|
| Dukascopy | Primary tick feed, separate bid/ask | Free, account-free (Aug 2026) — **unverified here** |
| HistData.com | Second opinion for reconciliation, not a fallback | No login |
| ECB reference rates | Daily fix as a drift anchor for timezone bugs | Free CSV |
| Philadelphia Fed RTDSM | Macro vintages, the point-in-time layer | Primary recommendation — **unverified here** |
| ALFRED | Secondary vintage source | **Conditional** — dropped if a key is required |
| ECB / BIS / OECD | Non-USD macro coverage, effective exchange rates | Free, no key |

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

| Phase | Weeks | Exit criterion |
|---|---|---|
| 0 — Acceptance tests | 1 | Each test's failure mode explainable in one sentence |
| 1 — Tick ingest | 2–3 | A re-run produces zero new rows and zero errors |
| 2 — Cleaning layer | 2 | For any instrument-day, list every flagged tick and why |
| 3 — Bitemporal macro | 2 | No-clairvoyance and revision tests green |
| 4 — Session & calendar | 1–2 | For any timestamp: which session, rollover?, holiday? |
| 5 — Validation harness | 2 | Green scheduled suite plus reconciliation report |
| 6 — Contamination experiment | 1–2 | Each contamination source sized in bps or Sharpe units |

If a phase overruns, cut the pair universe and date range — **never** the
acceptance tests, since without them the database has no claim to being
point-in-time.

Full specification, schema, and rationale in [`planning.md`](planning.md).

---

## Open decisions

**Research horizon — Path A, B, or C.** Unresolved, and it materially changes
Phases 1–4. Path A (intraday) makes the tick layer central; Path B (multi-day)
shifts ~70% of effort to macro and rates; Path C (both, narrow scope) is the
documented working assumption but is not yet a decision.

Three source-access checks also remain before ingestion code is written:
Dukascopy single-day pull, RTDSM download without registration, and whether
ALFRED CSV works without an API key.

---

## License

See [LICENSE](LICENSE).
