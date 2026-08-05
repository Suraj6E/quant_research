# Phase 0 — Acceptance test specification

**Status: RED, by design.** 17 failing, 11 passing.

Every failure is `NotImplementedError` from `fxpit.query.as_of` — the guarantee
is not yet provided because Phase 3 has not been written. The 11 passing tests
validate the *fixture* itself, not the system: they prove the test data still
contains the pathologies the detectors are supposed to find. If someone
"tidies up" a fixture, those tests go red and the suite tells you it has lost
its power.

The exit criterion for Phase 0 is that each test's failure mode is explainable
in one sentence. That is what this document is.

## Why the tests come first

A test written after the pipeline tends to encode what the pipeline does. A
test written before it encodes what the pipeline *must* do. The distinction
matters most for look-ahead bias, because a contaminated pipeline produces no
errors — only better-looking results.

## Running

```powershell
pytest                      # whole suite
pytest -m acceptance        # these four families only
pytest tests/test_revision.py::test_returns_first_print_between_publication_and_revision
```

---

## Family 1 — No-clairvoyance (`test_no_clairvoyance.py`)

**Guarantee:** a query at time `t` returns only what was public at `t`.

**Failure mode:** `as_of(t)` returned information that did not exist at `t`, so
every backtest built on it can see the future.

| Test | What it pins down |
|---|---|
| `test_macro_fact_invisible_one_second_before_release` | The core assertion — one second before `known_at`, the fact is not there |
| `test_macro_fact_visible_at_exact_release_instant` | The boundary is inclusive (`known_at <= t`); an off-by-one here hides an entire vintage |
| `test_nothing_visible_before_the_earliest_vintage` | Before any vintage exists the answer is empty — not a zero, not a placeholder, not a later vintage |
| `test_ticks_never_returned_from_the_future` | Prices are known when they print; `ticks_as_of(t)` must not return a tick stamped after `t` |
| `test_a_date_only_query_is_not_accepted` | Passing a bare `date` must raise rather than be coerced to midnight — silent coercion is how timestamp precision dies |

That last one is the cheapest test in the suite and guards the most expensive
mistake. `known_at` is `TIMESTAMPTZ` precisely because an 08:30 ET release and
an 08:31 ET price are different objects.

## Family 2 — Revisions (`test_revision.py`)

**Guarantee:** asked about a moment between the first print and a later
revision, `as_of` returns the first print.

**Failure mode:** the query returned a revised value that was not published
until later, so the backtest traded on a number nobody had.

| Test | What it pins down |
|---|---|
| `test_fixture_contains_enough_revised_periods` | A revision suite running on unrevised data proves nothing — an implementation that always returns the latest value would pass it |
| `test_returns_first_print_between_publication_and_revision` | The central assertion |
| `test_returns_latest_vintage_when_asked_after_all_revisions` | The mirror case; without it, an implementation that always returns the *first* print would pass |
| `test_each_intermediate_vintage_is_reachable` | A store keeping only first-and-final discards the middle of the revision path |
| `test_vintage_seq_increases_with_known_at` | `vintage_seq` is only useful if it agrees with chronology |
| `test_a_null_value_is_a_real_vintage` | A release published as missing is a fact with a `known_at`, not an absence |

The threshold is currently `MIN_REVISED_PERIODS_FIXTURE = 8`. `planning.md`
requires **50** against real RTDSM data; raise it in Phase 3 when real vintages
land.

## Family 3 — Tick sanity (`test_tick_sanity.py`)

**Guarantee:** ticks reaching research code are internally coherent, or are
announced as not being so.

**Failure mode:** a corrupt quote reached research code without announcing
itself, so a spread or return computed from it is silently wrong.

This family is structured differently from the others. `tick_raw` is immutable
and pathological ticks are *not* deleted — they are flagged additively in
`tick_flag`. So the fixture tests assert the pathologies still exist, and the
query-layer tests assert they never arrive unflagged.

| Test | What it pins down |
|---|---|
| `test_fixture_contains_a_crossed_quote` | `bid > ask` is present to be caught |
| `test_fixture_contains_a_zero_spread_quote` | A zero spread is not a bargain, it is a defect |
| `test_fixture_contains_a_duplicate_key` | Duplicate `(instrument, ts, source)` present |
| `test_fixture_contains_a_backwards_timestamp` | Out-of-order stamp means a decode bug or a feed defect |
| `test_fixture_contains_a_stale_run` | A repeated identical quote is a stalled feed, indistinguishable from a quiet market without a threshold |
| `test_query_layer_never_returns_a_crossed_quote` | |
| `test_query_layer_never_returns_a_negative_spread` | |
| `test_query_layer_returns_monotonic_timestamps` | |
| `test_query_layer_deduplicates_on_instrument_ts_source` | |
| `test_bid_and_ask_are_never_collapsed_to_mid` | A feed that forces an assumed spread is how backtests lie |

## Family 4 — Cross-feed (`test_cross_feed.py`)

**Guarantee:** where Dukascopy and HistData disagree materially, the
disagreement is reported.

**Failure mode:** the feeds disagreed and the system picked a winner without
telling anyone, so a data-quality problem was laundered into a
confident-looking number.

HistData is a **second opinion, not a fallback**. The disagreement rate is a
deliverable (success criterion #3), so anything that quietly reconciles the two
feeds destroys a project output rather than merely hiding a bug.

| Test | What it pins down |
|---|---|
| `test_fixture_contains_material_disagreements` | Two bars differ beyond tolerance by construction |
| `test_fixture_contains_a_within_tolerance_difference` | A detector flagging every non-identical bar would flag rounding and be useless |
| `test_fixture_contains_a_coverage_gap` | A bar in one feed and absent from the other is a gap, not agreement |
| `test_feeds_are_addressable_separately` | `source` is required so "the price" is never answerable without naming a feed — there is no consolidated tape in FX |
| `test_disagreements_are_not_silently_reconciled` | Values must not be averaged, snapped, or overwritten from a preferred feed |
| `test_a_gap_in_one_feed_is_not_filled_from_the_other` | Backfilling turns a coverage gap into fabricated agreement |

Tolerance is **0.00005** (half a pip on a 5-decimal pair), exposed as the
`tolerance` fixture.

---

## Fixture provenance

Honesty about which numbers are real matters, because a fixture that looks real
but isn't will eventually be mistaken for evidence.

| File | Provenance |
|---|---|
| `fixtures/macro_vintages.csv` | **PAYEMS rows are real** — pulled from ALFRED 2026-08-04, verified by `scripts/check_fred_key.py`. The 08:30 ET clock time is supplied from the BLS schedule, since ALFRED's real-time axis is date-granularity. CPIAUCSL and GDPC1 rows are **synthetic**, built to exercise an unrevised series, a NULL vintage, and two vintages on one day |
| `fixtures/ticks.csv` | **Clean EURUSD and USDJPY rows are real** — decoded from the Dukascopy `.bi5` for 2024-01-09 10:00 UTC. Pathological rows are **synthetic**, each violating exactly one rule so a failing detector names itself |
| `fixtures/bars_cross_feed.csv` | **Synthetic**, modelled on the real 2024-01-09 EURUSD level |

## What turns this suite green

| Family | Unblocked by |
|---|---|
| No-clairvoyance, Revision | Phase 3 — bitemporal macro store and `as_of()` |
| Tick sanity | Phase 1 ingest, then Phase 2 detectors |
| Cross-feed | Phase 1 (both feeds ingested) and Phase 2 (bars) |

Phase 3's exit criterion is that families 1 and 2 are green.
