# PULLBACK_3 — frozen test protocol (NOT activated)

> **STATUS: REFERENCE** · workstream: `selection` · frozen 2026-10-17 · specification only
> Nothing here is implemented, enabled or deployed. No threshold, flag, stop, target or
> trading rule changes. **Recommendation at the bottom is PARK** — the protocol is frozen so
> that if the sample ever arrives it can be run without re-deriving anything.

## 1. The frozen rule

Exactly as replayed in `scripts/entry_timing_counterfactual.py`. Any deviation invalidates
the test and requires a new protocol document.

| Element | Frozen value |
|---|---|
| Trigger | A SWING signal that today would be entered at `entry_price` |
| Order | Resting limit at **`entry_price × 0.97`** (3% below signal price) |
| Validity | **5 sessions** from the signal day, inclusive; expires unfilled |
| Fill | At the limit, **except** when the session opens below it → fill **at the open** (gap-through, the worse outcome) |
| No fill | **No trade.** Counts as a skipped signal, scored **0R**, never excluded |
| Stop | **5%** below the achieved fill |
| Target | **15%** above the achieved fill |
| Target fill | At the target, except a gap above → fill at the open |
| Horizon | **30 sessions measured from the SIGNAL day**, not from the fill, so lateness buys fewer remaining bars and never extra future data |
| Horizon exit | Mark to the last close in the window |
| Costs | **30bps round-trip** |
| Slippage | **10bps per fill** |
| Ambiguity | A session whose low ≤ stop *and* high ≥ target is **AMBIGUOUS**: excluded from return means, never resolved favourably |
| Direction | LONG only |
| Book | SMC / SWING only. **Momentum and LT are out of scope** |

## 2. The key risk — and it does not resolve

PULLBACK_3 skipped **62 of 147** signals; those skips earned **+38.18R** under BASELINE
(mean +0.606R, 52.4% win). Its entire gain comes from converting losers it still takes: on
the 85 it took, BASELINE was −57.33R and PULLBACK_3 was +20.14R.

So the question is whether a **pre-defined, hindsight-free filter** can tell a skipped winner
from a skipped loser. Six features computable from bars **on or before the signal day** were
measured (medians):

| group | n | 5d ret | 20d ret | % below 20d high | 20d vol | ext. vs 50MA | confidence |
|---|---|---|---|---|---|---|---|
| **skipped WINNER** | 33 | 6.45 | 10.46 | −3.50 | 2.50 | 11.44 | 72.48 |
| **skipped LOSER** | 29 | 3.80 | 10.38 | −3.39 | 2.18 | 10.32 | 74.57 |
| taken | 85 | 5.45 | 8.63 | −6.05 | 2.93 | 13.15 | 80.21 |

**Nothing separates the winners from the losers.** 20d return, distance below the 20-day high
and extension are effectively identical. The engine's own `confidence` is *lower* on the
winners (72.5 vs 74.6), so it cannot be used to rescue them. The only visible gap is 5-day
return (6.45 vs 3.80) — **one of six features scanned on n=33 vs 29**, which is what noise
looks like, and acting on it would be exactly the hindsight this protocol forbids.

**Conclusion: no filter is specified.** The +38.18R opportunity cost is accepted as an
intrinsic property of the rule, not something to be engineered away. Any future filter must
arrive with its own out-of-sample evidence before it may enter this protocol.

`setup`-level analysis is impossible: `setup` is NULL on every SMC row.

## 3. Sample size

Measured paired per-signal difference (PULLBACK_3 − BASELINE, skips scored 0R):
**mean +0.267R, SD 1.851R, n=147**.

Clustering is **negligible**, which was worth checking rather than assuming: 68 distinct
signal dates, mean 2.16 signals/date, **ICC ≈ 0.000, design effect 1.00**. No inflation is
required, so signals may be treated as independent for sizing.

Two-sided, 80% power, paired t:

| minimum effect | α = .05 | α = .017 (Bonferroni ×3) |
|---|---|---|
| **0.267R** (the observed effect) | 378 | 504 |
| 0.20R | 673 | 897 |
| **0.15R** ← the pre-specified floor | **1,196** | **1,595** |
| 0.10R | 2,690 | 3,588 |

**Powering on the observed effect is circular.** +0.267R is the best of six variants scanned,
so it carries a winner's curse and is biased upward; sizing a test to detect exactly it would
guarantee an inconclusive result if the truth is half as large.

**Minimum economically meaningful effect: +0.15R per signal.** Justification, not statistics:
at ~40 closed SWING signals a month, +0.15R per signal is ≈ +6R a month of added expectancy —
enough to change the book's trajectory and comfortably larger than the cost and slippage drag
already modelled (0.5R of the 1R risk unit is drag-free headroom at these assumptions). Below
+0.10R the rule is not worth the operational complexity of resting limits and managing 43%
unfilled signals.

**Multiple testing:** 3 comparisons are pre-declared — PULLBACK_3 vs BASELINE (primary), and
PULLBACK_2 and DELAY_2 as the two secondaries already measured. Bonferroni α = .017. No
further variant may be added; a new variant means a new protocol.

**Attrition:** ~43% of signals go unfilled. Those are *not* attrition for the primary
portfolio-level test (they score 0R and remain in the denominator), but the **traded-only**
secondary needs ~1,196 / 0.57 ≈ **2,100 signals** to reach the same power, which is why the
portfolio-level comparison is the primary.

**Feasibility:** 147 closed trades accrued over roughly 3.7 months ≈ **40/month**. So
1,196 signals is **~30 months**; even the circular 378 is ~9.5 months, and 504 with
multiplicity is ~13 months.

## 4. Validation design

- **Chronological holdout, untouched.** All signals from the protocol start date forward are
  the test set. The 147 already replayed are **exploratory and may never be re-used** as
  evidence. No shuffling, no k-fold: the data is a time series.
- **Primary metric:** portfolio-level total R over the same signal set, **skips scored 0R**.
- **Secondary, reported separately:** traded-only expectancy; win rate; max drawdown on the
  per-signal R series; **missed-winner cost** (BASELINE R on skipped signals); turnover
  (fills ÷ signals); capital utilisation (mean concurrent positions × mean sessions held);
  benchmark-relative excess vs NIFTY 50 over each trade's own window.
- **Uncertainty:** bootstrap 95% CI resampling **signals** (ICC ≈ 0 justifies this); report
  the CI, not a p-value alone.
- **Regime:** report RISK_ON / RISK_OFF split using the existing NIFTY-vs-trailing-50-bar-mean
  classifier. **Descriptive only** — the test is not powered for a regime interaction.
- **Tooling:** reuse `scripts/entry_timing_counterfactual.py` and `services/forward_eval.py`
  unchanged. **No new shadow system.**

## 5. Stopping rule — fixed in advance

| Condition | Value |
|---|---|
| Minimum sample before *any* look | **1,196 signals** (α .05, δ 0.15R) |
| Maximum duration | **24 months** from start |
| Interim looks | **None.** No peeking, no early stop, no rule change after outcomes appear |
| Data quality | ≥90% of signals priceable; if unpriceable >10%, the run is void, not adjusted |
| **GO** | portfolio-level diff ≥ **+0.15R** per signal **and** bootstrap 95% CI excludes 0 **and** the holdout sign agrees with exploratory |
| **NO-GO** | CI includes 0, or diff < +0.15R, or missed-winner cost exceeds the gain |
| **VOID** | the rule was altered, a filter was added mid-flight, or data quality fails |
| On timeout | If 24 months elapse below 1,196 signals, the test reports **underpowered** and the idea is abandoned — not extended |

## 6. Recommendation: PARK

Two independent reasons, either sufficient:

1. **The key risk does not resolve.** No hindsight-free feature distinguishes the 33 skipped
   winners from the 29 skipped losers, so the +38.18R opportunity cost is permanent and
   unmanageable.
2. **The test is not feasible on this data rate.** A credible test needs ~1,196 signals ≈ **30
   months**. Sizing to the observed +0.267R instead would be circular and would likely return
   "inconclusive" after a year.

The evidence as it stands — +0.267R with **95% CI [−0.026, +0.564]**, a still-negative holdout
half (−0.188R), and an unmitigable opportunity cost — does not justify a two-and-a-half-year
commitment or any live change.

**Un-park trigger:** if the closed-SWING ledger reaches **600+ trades** and a re-run of the
existing replay shows the paired difference holding at **≥ +0.20R with a CI excluding zero**,
reopen this document unchanged and start the holdout. Nothing else should reopen it.
