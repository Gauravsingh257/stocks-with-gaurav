# PRE-REGISTRATION — does a candidate ranking beat the current ranker on realised return?

**STATUS: LIVE (pre-registration — binding)** · written 2026-10-19 · **not yet run**

> **This document is written before the test data exists.** Everything below — the candidate
> ranking, the comparators, the window, the metric, the thresholds and the decision rule — is fixed
> now so that none of it can be chosen after seeing the outcome. Any change after this commit is an
> **amendment**, must be committed with a timestamp *before* the run, and must say what changed and
> why.
>
> Written against repo state `447e932` (research branch), `a06518c` (main).

## The question, and the refinement that defines it

Earlier work ([`ranking-inputs-2026-10-19.md`](ranking-inputs-2026-10-19.md)) found that three
features order stocks by NIFTY-relative 20-day return while
`score_breakdown.composite` — the number the ranker actually ranks on — does not. Those windows
had **overlapping test sets** and **71 features were tested**, so the result is promising and not
defensible.

**The question here is deliberately harder than "do these features correlate with returns".** It
is:

> Does a basket selected by a pre-defined candidate ranking realise **higher NIFTY-relative
> 20-day returns than the basket the current ranker would have selected on the same days**?

Ingredients looking good in isolation is not evidence that a better ranking exists. The test must
be **incremental over the incumbent**, paired day by day, on data never used.

---

## 1. Candidate ranking — fixed, with no fitted parameters

Three features, from `signals_log.layer_details`:

| Role | Path |
|---|---|
| F1 | `discovery.momentum_50d_pct` |
| F2 | `discovery.breakout_score` |
| F3 | `discovery.discovery_score` |

**Combination rule — `CAND`:** within each scan day, rank each candidate on each feature
separately (ascending, **average ranks for ties**), convert each to a percentile in `[0, 1]` as
`(rank − 1) / (n − 1)`, then take the **unweighted mean of the three percentiles**. Higher is
better.

Equal weights are chosen deliberately: **no weight is estimated from data, so there is nothing to
overfit.** A fitted blend would need its own held-out set and is out of scope.

**Missing features:** a candidate must carry **all three** features to receive a `CAND` score.
Candidates missing any are excluded from *both* baskets (so A and B always choose from an identical
pool) and the exclusion count is reported.

## 2. Comparators

| ID | Ranking | Purpose |
|---|---|---|
| **A** | `score_breakdown.composite`, descending | the incumbent |
| **B** | `CAND`, descending | the candidate |
| **C** | uniform random within day, seed 7 | **control** — validates the harness |
| **D** | `discovery.momentum_50d_pct` alone | does blending three beat the best one? |

**C is the disabled-rule control.** A random ranking carries no information, so `C − A` must be
statistically indistinguishable from zero. **If it is not, the harness is broken and the entire
result is void** — this check is run and reported *before* the primary is read.

## 3. Outcome metric

- **Base** = close of the scan date. Scans publish pre-open, so that close is tradable after
  publication. No look-ahead.
- **Horizon** = **20 trading days** after the base bar.
- **Return** = close-to-close percent from base to D+20.
- **NIFTY-relative** = that return minus NIFTY 50's own return over the identical bar span.
- **Basket return for one day** = the *equal-weighted mean* NIFTY-relative return of that day's
  selected names.
- **Costs:** a flat **0.30% round-trip** (STT both sides, exchange charges, GST, stamp duty, SEBI
  fee; Zerodha delivery brokerage is zero) is subtracted from every basket. It is applied
  identically to A, B, C and D, so **it cancels exactly from every paired difference** and cannot
  affect the primary result. It is included only so the absolute levels are not overstated.
  **Slippage is deliberately not modelled** — it cannot be measured reliably from daily bars, and
  inventing a number would be worse than omitting it. The absolute levels are therefore
  optimistic; the differences are not affected.

## 4. Basket size

**N = 20**, matching the live book: `PORTFOLIO_MAX_SWING` default 20
([`services/risk_engine.py:88`](../../services/risk_engine.py#L88)) and
`EXCEPTIONALISM_SOFT_CEILING` default 20
([`services/validation_engine.py:1159`](../../services/validation_engine.py#L1159)).

N = 10 and N = 40 are reported as **robustness only** and carry no decision weight.

## 5. Validation window — a rule, not a date

- Scan dates **strictly after 2026-09-17**, so there is **zero overlap** with any window used in
  prior work (which ended 2026-09-17).
- A scan date enters only once its D+20 bar exists in the universe OHLC snapshot.
- The window **opens for analysis** when **≥ 15 matured scan dates** are available, and the run
  uses every matured date from 2026-09-18 up to that point.
- The earlier estimate of **2026-11-13 is provisional.** It assumes the NSE calendar and
  uninterrupted snapshot availability; holidays, an engine or scanner outage, or a lapsed OHLC
  shard will move it. **The gate is the 15-matured-day condition, never the calendar date.**
- A 20-trading-day embargo is unnecessary *within* this window because no training happens here —
  the candidate ranking is fully specified in advance. The embargo that matters is the separation
  from prior work, which the 2026-09-17 cutoff provides.

## 6. Minimum sample and coverage — all four required

| Requirement | Threshold | If unmet |
|---|---|---|
| matured scan dates | **≥ 15** | INSUFFICIENT EVIDENCE |
| candidates per scan day | **≥ 30**, else the day is dropped | dropped days reported |
| priceable observations | **≥ 75%** of candidates | INSUFFICIENT EVIDENCE |
| per-feature coverage | each of F1–F3 on **≥ 80%** of candidates per day | INSUFFICIENT EVIDENCE |

Prior runs achieved 78–84% price coverage, so 75% is a realistic floor rather than a generous one.

## 7. Primary test

One test, and only one.

- **Statistic:** per scan day `d`, `Δ(d) = basketReturn_B(d) − basketReturn_A(d)`. The estimate is
  `mean(Δ)` over matured days.
- **Uncertainty:** 95% CI by **day-block bootstrap** — resampling scan **days**, 5,000 iterations,
  **seed 7**. Days are the independent unit because overlapping 20-day windows make individual
  observations dependent.
- **Pairing within day** is what makes this benchmark-neutral and regime-neutral: both baskets face
  the same market on the same day.

**Minimum effect: `mean(Δ) ≥ +1.00pp` over 20 days.** Chosen in advance and justified: on a
20-slot book with roughly a 20-day holding period, 1pp per position per rotation is the smallest
difference worth the operational risk of changing a live ranker. Anything smaller is not worth
acting on even if real.

## 8. Secondary tests — labelled, corrected, non-decisive

Exactly three, fixed now. **Holm correction across these three**; none can produce a PASS on its
own.

1. **Incremental information:** per-day Spearman correlation between NIFTY-relative return and
   `CAND` percentile, **partialled on the `composite` percentile**. Tests whether `CAND` carries
   ordering information *the incumbent does not already have*.
2. **Blend vs best ingredient:** `mean(basketReturn_B − basketReturn_D)`. If this is ≤ 0, the
   three-feature blend adds nothing over 50-day momentum alone, and the simpler rule should be
   preferred in any later proposal.
3. **Overlap:** the mean number of names shared by basket A and basket B per day. A high overlap
   with a positive primary would mean the gain comes from a small number of substitutions — which
   changes how fragile the result is.

**Total pre-registered tests: 1 primary + 3 secondaries.** No others will be reported as findings.
Anything else computed is exploratory, will be labelled exploratory, and cannot support a decision.

## 9. Decision rule

| Verdict | Condition |
|---|---|
| **PASS** | all four coverage requirements met **and** control C indistinguishable from zero **and** `mean(Δ) ≥ +1.00pp` **and** the 95% CI excludes zero |
| **FAIL** | coverage met and control valid, but `mean(Δ) < +1.00pp` **or** the CI includes zero |
| **INSUFFICIENT EVIDENCE** | any coverage requirement unmet, **or** control C differs from zero (harness invalid) |

**A PASS authorises nothing by itself.** It authorises *writing a proposal* for a flag-gated,
reversible ranking change with a disabled-rule control, defaulting to current behaviour. **No
weight, threshold or flag changes without Gaurav's explicit approval**, and no deploy inside
09:15–16:10 IST.

**A FAIL is a real outcome and will be published as one.** The three features would then be
recorded as not having survived honest out-of-sample confirmation, and the ranker work would stop
rather than continue under a new split.

## 10. Protections against fishing

- **Run once.** No peeking before the 15-day condition is met.
- **The analysis script is frozen before the run.** It will be committed and **its commit hash
  recorded in this section**, with no edits afterwards. If it errors, only the error is fixed, and
  the fix is committed with an explanation before re-running.
- **Seed 7** everywhere.
- **No parameter sweeps.** N, the horizon, the thresholds, the combination rule and the feature set
  are all fixed above.
- Any deviation is an **amendment** appended below, timestamped, committed before the run.

> **Frozen analysis script:** _to be committed before the window opens; hash recorded here._

## 11. Amendments

_None._
