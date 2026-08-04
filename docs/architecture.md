# Architecture

How the pieces are wired and why. `planning.md` is the specification — it holds
the rationale, phasing, and schema in full. This document covers the shape of
the system and where code goes.

---

## The problem shape

The project stores two kinds of data with incompatible characteristics, and no
single store serves both well.

| | Ticks | Reference & macro vintages |
|---|---|---|
| Volume | 2–5 billion rows projected | single-digit millions |
| Write pattern | append-only, bulk | frequent small updates |
| Needs | columnar scans, compression | referential integrity, constraints |
| Mutability | immutable after ingest | revised, corrected, extended |

Hence three tiers. The split is driven by the data, not by a preference for
distributed systems.

---

## Tiers

```
                        ┌──────────────────────────┐
                        │   Research / notebooks   │
                        └────────────┬─────────────┘
                                     │  as_of(t) — the only read path
                        ┌────────────▼─────────────┐
                        │  Tier C — DuckDB         │
                        │  Query layer, Parquet    │
                        └──────┬─────────────┬─────┘
                               │             │
              ┌────────────────▼───┐     ┌───▼────────────────┐
              │ Tier B — ClickHouse│     │ Tier A — Postgres  │
              │ Ticks & bars       │     │ Reference & vintage│
              │ Append-only, huge  │     │ Relational, small  │
              └────────▲───────────┘     └───▲────────────────┘
                       │                     │
          ┌────────────┴──────┐   ┌──────────┴────────────────┐
          │ Dukascopy .bi5    │   │ Philadelphia Fed RTDSM    │
          │ HistData CSV      │   │ ECB / BIS / OECD          │
          └───────────────────┘   └───────────────────────────┘
```

### Tier A — PostgreSQL

Instruments, calendars, sessions, macro release schedule, macro vintage
observations, rate series. Small but needs constraints and integrity.

Tables live in `infra/postgres/init/01_schema.sql`.

### Tier B — ClickHouse

`tick_raw`, `tick_flag`, and bars as materialised views. Single-node Docker
only — no clustering, no replication. That is a scope limit, not a TODO.

Tables live in `infra/clickhouse/init/01_schema.sql`.

### Tier C — DuckDB

In-process analytical engine reading Parquet directly and joining across
ClickHouse exports and Postgres extracts. **`as_of(t)` lives here.**

---

## The bitemporal model

Every fact carries two independent time axes:

- **`ref_period`** — the period the value describes
- **`known_at`** — the moment the value became publicly available

A point-in-time read filters `known_at <= t`, then keeps the row with the
latest `known_at` per key. What you get back is what the world believed at time
`t`, not what turned out to be true later. Revisions fall out automatically.

That is the entire mechanism. Everything else is plumbing.

Two properties that are easy to break and fatal when broken:

- **`known_at` is `TIMESTAMPTZ`, never `DATE`.** An 08:30 ET release and an
  08:31 ET price are different objects. A date-only column silently destroys
  that distinction.
- **Both columns, always.** A table with only `ref_period` is not point-in-time
  and cannot be made so retroactively.

---

## The read-path contract

`as_of(t)` is the only sanctioned read path, and it is the project's single
most important invariant.

Convenience accessors that query Tier A or Tier B tables directly must not be
added. That is precisely how look-ahead bias re-enters a system that had
eliminated it — someone adds a "quick" helper that skips the `known_at` filter,
and every downstream result is silently contaminated. Direct table SELECTs
outside the query layer are an audit failure (success criterion #1).

Research code talks to Tier C. Only Tier C talks to Tiers A and B.

---

## Data flow

**Ingest (Phase 1).** Fetch Dukascopy `.bi5` hourly files, decode, write to
`tick_raw`. Never transform on ingest. An ingest ledger records what was
fetched and whether it succeeded, so gaps are visible rather than silent. The
process is resumable and idempotent — a re-run produces zero new rows.

**Flag (Phase 2).** Each detector runs independently over `tick_raw` and writes
rows to `tick_flag`. Cleaning is *additive and reversible*: `tick_raw` is never
modified. A wrong detector means deleting flags and re-running, not
re-downloading 300 GB. The flag taxonomy lives in a separate table because it
grows as new pathologies are found and one tick can carry several flags.

**Aggregate (Phase 2).** Bars are materialised views over `tick_raw`, never a
destructive transform. Both bid-bars and ask-bars, **never mid-only** —
mid-price collapse is one of the four contamination sources Phase 6 measures.

**Macro (Phase 3).** RTDSM vintages land in `macro_observation`. Release
timestamps are reconstructed from BLS/BEA schedules; where the true time cannot
be sourced it is recorded as explicitly unknown, never assumed to be 08:30 ET.

**Query (Phase 3+).** DuckDB exposes `as_of(t)` over Postgres extracts and
ClickHouse exports.

---

## Rejected alternatives

| Option | Why not |
|---|---|
| TimescaleDB for Tier B | Good and simpler, but caps out lower and is a weaker CV signal |
| InfluxDB | DevOps metrics ecosystem, not finance |
| MongoDB | Wrong data shape entirely |
| kdb+/q | Strong signal at sell-side and HFT, but the free edition requires a license request — disqualified by the no-registration constraint. A separate later project |
| Postgres alone | Viable only under Path B, and only if raw ticks are never queried |
| C++ / HFT architecture | Category mismatch: this is historical batch processing with no hot path. Kernel bypass, lock-free queues, and busy-spin polling all address live-feed latency, which this system does not have. ClickHouse already provides the optimised C++ layer |

On that last row — a `.bi5` decoder as a C++ extension is defensible later as a
CPU-bound component, but only after a working Python decoder exists as a
correctness baseline. Decode time is dwarfed by network and insert time.

---

## Planned module layout

Not yet created. Recorded so the boundaries are agreed before code lands.

```
src/fxpit/
  ingest/      Dukascopy .bi5 fetch/decode, HistData CSV, ingest ledger
  flags/       Independent, re-runnable tick detectors
  macro/       RTDSM / ECB / BIS vintage loaders
  calendar/    Sessions, DST, rollover windows, holidays
  query/       as_of() and the DuckDB layer — the ONLY read path
  reports/     Coverage, data quality, cross-feed reconciliation
tests/         Phase 0 acceptance suite
infra/         Container schema bootstrap
```

The `query/` boundary is the one that matters. If another module grows a
function returning market data without a `known_at` filter, the invariant is
already broken.

---

## Where the open decision bites

The Path A / B / C choice (`planning.md` §2) is **unresolved** and changes
emphasis, though not the tier structure:

- **Path A (intraday)** — Tier B is central, macro needs timestamp precision,
  effort splits ~70/30 toward ticks.
- **Path B (multi-day)** — ticks aggregate to daily immediately and become an
  archive; Tier B could arguably be dropped. Forward points become the critical
  gap, and they have no clean free source.
- **Path C (both, narrow scope)** — the documented working assumption
  throughout, and what this architecture is drawn for. It is not yet a decision.
