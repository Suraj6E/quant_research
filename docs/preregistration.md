# Pre-registration — the contamination experiment

**Written 2026-08-05, before any variant was run.**

This document exists so the experiment measures *contamination* rather than my own
search through the space of rules. Everything below — instrument, event set,
direction, holding period, cost model, and the metrics reported — is fixed here
and must not be changed after results are seen. If something has to change, the
change is recorded in §8 with its reason, and the original stays visible.

The rule is deliberately dull. An interesting rule would invite tuning, and
tuning would reintroduce exactly the selection effects this design exists to
isolate.

---

## 1. What is being measured

**Not** whether the rule is profitable. The estimand is the set of *differences*
between four evaluations of the same rule under four information regimes:

| Difference | Prices |
|---|---|
| A → B | revision leakage |
| B → C | timestamp coarsening |
| C → D | the mid-price assumption |

The level of any single variant is of little interest. Variant A is not expected
to be attractive.

## 2. Instrument and events

- **Instrument:** EURUSD only.
- **Events:** US nonfarm payrolls (`PAYEMS`) and US CPI (`CPIAUCSL`) releases.
- **Window:** 2022-01-01 to 2024-12-31.
- **Release instant:** 08:30 America/New_York, converted to UTC. Derived from
  local time, never written as a UTC constant.
- **Release dates:** ALFRED vintage dates for the series.

**A vintage date only counts as a release if it introduces a new reference
period.** CPI publishes ~13 vintages a year because annual seasonal-factor
revisions create a vintage that republishes old periods without adding a new
one. Those are revisions, not releases, and trading them would be trading an
event that did not happen.

## 3. The surprise measure

There is no free source of historical consensus forecasts, so a genuine
"actual vs expected" surprise cannot be constructed. The substitute:

```
headline_change  = value(M) − value(M−1)      # both as known at the release
surprise         = headline_change − mean(previous 12 headline_changes)
```

**This is a synthetic surprise and is labelled as such throughout.** It measures
deviation from recent trend, not deviation from expectation, and those are
different things.

**Why this does not invalidate the experiment.** The same surprise construction
is used in all four variants. Its imperfection is a constant across the
comparison, so it shifts every variant's level together and leaves the
*differences* — the estimand — intact. A shared flaw in a shared input cannot
manufacture a difference between arms.

Where a variant is defined to use revised data, the surprise is recomputed from
the revised series. That is the contamination being measured, not a bug.

## 4. The rule

```
at ENTRY_TIME:
    if surprise > 0:   SELL EURUSD        # stronger US data → buy USD
    elif surprise < 0: BUY  EURUSD        # weaker US data → sell USD
    else:              no trade
hold HOLD_MINUTES, then close
```

- **HOLD_MINUTES = 30**, fixed. Chosen because it is long enough to contain the
  release reaction and short enough that the mid-price assumption should bite,
  which is the regime hypothesis H2 concerns. Not tuned.
- One position at a time. No stops, no targets, no sizing rules, no filters.
- Position size is constant, so returns are comparable across events.

## 5. The four variants

| | Macro value | Entry time | Execution |
|---|---|---|---|
| **A — Honest** | first print, `as_of` the release | release instant | real bid/ask |
| **B — Revised** | final revised value | release instant | real bid/ask |
| **C — Date-only** | final revised value | 00:00 UTC on the release date | real bid/ask |
| **D — Mid-price** | final revised value | 00:00 UTC on the release date | mid, no spread |

Each variant strictly adds one advantage that was unavailable in reality.
Variant A is the control and the only regime corresponding to information a
trader could have acted upon.

**Execution detail.** A buy enters at the ask and exits at the bid; a sell enters
at the bid and exits at the ask. Variant D uses `(bid+ask)/2` on both sides, so
its advantage is exactly one full spread per round trip.

**Entry price** is the first tick at or after the entry instant, within a
5-minute tolerance. If no tick is found the event is skipped for all variants
alike, so the arms always compare the same event set.

## 6. Metrics reported

For each variant:

- number of events traded
- mean return per trade, in basis points
- standard deviation of returns, in basis points
- **Sharpe ratio**, annualised as `mean/std × sqrt(24)` — 24 being the expected
  events per year (12 payrolls + 12 CPI)
- hit rate
- cumulative return

And the three adjacent differences, in Sharpe units and basis points.

## 7. Hypotheses, fixed in advance

Restated from `planning.md` §8 so they cannot drift:

- **H1** — reported Sharpe orders D > C > B > A. *Confidence: high.*
- **H2** — the mid-price assumption is the largest single contributor to the
  A→D gap. *Confidence: moderate.* **This is the most falsifiable claim here.**
  If revision leakage dominates instead, that inverts the usual practitioner
  intuition and should be reported prominently rather than buried.
- **H3** — timestamp coarsening (B→C) exceeds revision leakage (A→B).
  *Confidence: moderate to low.*

**What would falsify H1:** any ordering other than D > C > B > A.

**A null result is a result.** If the four variants come out indistinguishable,
that is a finding about this rule and this sample, and it gets written up as
plainly as a positive one.

## 8. Deviations from this document

*(Any change made after results were seen is recorded here, with its reason.
An empty section means none were needed.)*

- None.
