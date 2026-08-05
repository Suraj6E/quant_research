# Findings and conclusion

**Written 2026-08-05, after the run. Pre-registration: `docs/preregistration.md`,
sha256 `a96c0c7287807cfb`.**

---

## 1. Verdict

**The apparatus succeeded. The measurement is one-third complete.**

Those are different claims and collapsing them into "the experiment worked" or
"the experiment failed" would be exactly the kind of imprecision this project
was built to detect.

- **One of the three contamination channels was measured** at high significance
  (t = 7.6) and agrees with an independent prediction of its size.
- **Two channels are unresolvable** at the sample obtained, and the analysis
  below shows they were never going to be resolvable at this sample — not
  because of a flaw, but because their effect is delivered through a mechanism
  with far higher variance.
- **No hypothesis is judged.** H1 and H2 are recorded as not assessed.

The most useful output is not a Sharpe ratio. It is a **power calculation**
that says how much data the remaining channels actually need, and a
**methodological finding** about why they differ so sharply.

---

## 2. What was obtained

72 genuine macro releases were constructed for 2022–2024 (36 payrolls, 36 CPI),
filtered from 75 ALFRED vintage dates — three were annual seasonal-factor
revisions that republish old periods without adding a new one, and trading those
would be trading events that did not happen.

**Only 8 of the 72 could be priced.** Dukascopy IP-blocked the ingest after
roughly 360 requests in a short window: TCP connect timeouts, not HTTP 429s.
377,418 ticks landed with zero recorded errors before the block; the pipeline
behaved correctly and the source refused.

---

## 3. Results

### Per-arm returns (n = 8)

| Arm | Mean bps | SD | SE | t | 95% CI on the mean |
|---|---|---|---|---|---|
| A — Honest | 8.82 | 19.40 | 6.86 | 1.29 | [−4.62, 22.26] |
| B — Revised | −0.55 | 21.89 | 7.74 | −0.07 | [−15.71, 14.62] |
| C — Date-only | 5.22 | 13.44 | 4.75 | 1.10 | [−4.10, 14.54] |
| D — Mid-price | 5.58 | 13.51 | 4.78 | 1.17 | [−3.79, 14.94] |

**No arm's mean return is distinguishable from zero.** Every confidence
interval spans it. The rule is not profitable in any regime at this sample, which
is the expected and pre-registered outcome — profitability was never the point.

### The estimand: paired channel differences

The arms trade the *same* events, so the differences are paired. This matters:
pairing removes the common market movement and is far more powerful than
comparing four independent Sharpe ratios.

| Channel | Mean Δ bps | SE | t | Resolved? |
|---|---|---|---|---|
| A → B  revision leakage | −9.369 | 9.231 | −1.01 | no |
| B → C  timestamp coarsening | +5.766 | 6.198 | 0.93 | no |
| **C → D  mid-price assumption** | **+0.357** | **0.047** | **7.56** | **YES** |
| A → D  all three combined | −3.246 | 6.919 | −0.47 | no |

---

## 4. Finding 1 — the mid-price channel is measured, and it is small

**+0.357 bps per round trip, SE 0.047, t = 7.56.**

This survives n = 8 because the paired difference is nearly deterministic: the
mid-price arm saves exactly one spread on every trade, so the difference series
has almost no variance. It is the one number in this project that is a genuine
measurement rather than an estimate.

It is also **independently corroborated**. The measured median EURUSD spread in
the archive is 0.271 bps. The channel should equal one spread per round trip, and
it comes out slightly higher because the date-only arms enter at 00:00 UTC in the
thin Asian session, where spreads are wider. Two independent routes to the same
number.

**Interpretation.** For a 30-minute EURUSD trade around a macro release, assuming
a mid price is worth roughly **0.36 basis points per trade** — about a third of a
pip. That is real but modest. A practitioner who assumes the mid-price assumption
alone explains a large Sharpe gap is, for this instrument and horizon, wrong: the
spread is simply too tight. The picture would differ sharply in a wider-spread
instrument or a shorter holding period.

---

## 5. Finding 2 — the channels have radically different measurability

This was not anticipated in the pre-registration and is the more useful result.

| Channel | Mechanism | SD of paired difference |
|---|---|---|
| Mid-price | a fixed cost on every trade | **0.13 bps** |
| Revision leakage | occasional **signal flips** | **26.11 bps** |

The revision channel does not shift returns by a small amount on every event. It
changes the *direction of the trade* on a minority of events — 2 of 8 here — and
on those events the entire return distribution swings. A rare, large effect has
vastly more variance than a small, constant one.

### What that costs in data

Using the observed 26.11 bps paired-difference SD, to resolve a revision effect
at t = 2:

| Effect to detect | Events needed | Years of payrolls + CPI |
|---|---|---|
| 10 bps | 28 | 1.2 |
| 5 bps | 110 | 4.6 |
| 2 bps | 682 | **28.4** |

**This is the practical finding of the whole project.** The spread channel can be
measured in a week of data. The revision channel needs years, and a small
revision effect needs decades. Any study claiming to have isolated revision
leakage from a short sample should be asked how it beat this variance.

---

## 6. Finding 3 — H1's premise is questionable, independent of the sample

H1 predicted the ordering D > C > B > A on the grounds that *"each variant
strictly adds an information or cost advantage that was unavailable in reality."*

For the mid-price and timestamp channels that is plainly true — a free spread and
thirteen hours of foresight are advantages in any design.

**For the revision channel it is not obviously true at all**, and this is an
argument about mechanism rather than a claim from the data.

The market moved on the **first print**. That is the number traders saw and acted
on at 08:30. A simulation that conditions on the *revised* value is conditioning
on a figure that was never on any screen at the moment the price moved. For a
strategy predicting the **economy**, the revised value is better information. For
an **event study predicting the market's reaction to a print**, it is not better
information — it is a different, partly uncorrelated signal.

The observed sign is consistent with this (A → B is negative: revised data made
the rule worse), but at n = 8 the sign is not evidence. The argument stands on
its own logic.

**The consequence is that H1 should be reformulated before it is tested.** As
written it treats "more accurate data" and "more advantageous data" as the same
thing, and in an event-study design they come apart.

---

## 7. Hypothesis status

| | Status | Why |
|---|---|---|
| **H1** — D > C > B > A | **Not assessed** | n = 8. The observed order is A > D > C > B, but at this sample that is a coin toss. §6 also argues the hypothesis needs reformulating |
| **H2** — mid-price is the largest source | **Not assessed** | The point estimate says revision leakage dominates by ~26×, contradicting H2 — but the revision estimate has SE 9.2 and is indistinguishable from zero |
| **H4** — cross-feed disagreement | **Blocked** | HistData is not retrievable without a headless browser |
| **H5** — flagged-tick share | **Partially observed** | 1.98% of ticks carry a flag; 97% of spread outliers cluster at the Friday close and Sunday reopen |
| **H6** — DST anomalies | **Confirmed to exist** | The London–New York overlap is 5h instead of 4h for ~20 trading days a year, computed from local-time rules. Economic significance untested |

---

## 8. What would finish this

Nothing here needs new code. The gap is data.

1. **Ingest the remaining 64 release days, paced.** Dukascopy blocks at roughly
   360 requests in a burst. Spread across hours or days it is fine. The ingest
   ledger makes this resumable — a re-run fetches only what is missing.
2. **Extend the window to 2015–2024** for ~240 events. Per §5 that resolves a
   5 bps revision effect but still not a 2 bps one.
3. **Reformulate H1** along the lines of §6 before testing it.
4. **A second price feed** for H4. HistData needs a browser; another
   account-free tick source would be a better answer.

---

## 9. Conclusion

The project's stated goal was never a profitable strategy. It was to make
contamination **measurable**, and to be able to say how much of a reported result
comes from data rather than signal.

On that goal:

**What was achieved.** A point-in-time database where the guarantee is enforced
by 116 tests rather than by care, where every read goes through one
implementation of `known_at <= t`, and where a mutation check confirms the
acceptance suite catches five distinct ways of breaking it. An experiment harness
whose pre-registration is hash-verified, whose arms provably differ in exactly
one dimension each, and which refuses to render a verdict it cannot support. One
contamination channel measured at t = 7.6 and corroborated independently.

**What was not achieved.** Two of three channels remain unmeasured, and the
honest reason is that the sample is 8 events rather than 72 — a free data source
rate-limited the ingest, not a design failure.

**The most valuable output is the one that was not planned.** The power
calculation in §5 says the revision channel needs 110 events to resolve a 5 bps
effect and 682 for a 2 bps one. That reframes the original question. "How much is
revision leakage worth?" turns out to be less useful than "how much data does
anyone need before they can honestly claim to know?" — and the answer, for the
smaller effects, is *more than most published studies have*.

That is a more transferable result than a Sharpe ratio would have been, and it
came from the experiment failing to reach significance rather than from it
succeeding. A null result that explains its own null is not a failed experiment.

**A closing note on the project's own standard.** Three times during this build
the system produced a plausible-looking wrong number — a 6,919-point GDP
"revision" that was a base-year change, a rollover detector silently returning
zero flags after a timezone bug, and a keyless ALFRED endpoint serving revised
data under a historical vintage date. Each was caught by a check that existed
because the project assumed such things happen. That is the strongest evidence
the discipline was worth the effort, and it is worth more than the experiment's
headline number.
