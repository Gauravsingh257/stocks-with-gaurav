# Stage 3 — do our selection mechanisms pick better than what they reject?

> **STATUS: LIVE** · workstream: `selection` · 2026-10-14 · measurement only
> No threshold, weight, flag, exit rule or selection logic changed by this document.

## The question

Stage 3 asks whether the *existing* selection mechanisms actually beat the candidates
they reject. Two mechanisms sit at the end of the funnel:

| Cohort | Meaning |
|---|---|
| `SELECTED` | `final_selected=1` — the served top-20 |
| `CEILING_CUT` | passed L1∧L2∧entry **and** the Phase-2 rank budget, then lost at the exceptionalism ceiling (`EXCEPTIONALISM_SOFT_CEILING`, default 20) |
| `PHASE2_CUT` | eligible but `below_rank_budget` — cut by the Phase-2 ranker |

So `SELECTED` vs `CEILING_CUT` isolates **the ceiling**, and `CEILING_CUT` vs
`PHASE2_CUT` isolates **the rank budget**.

## What changed in the instrument

`scripts/ceiling_cohort_study.py` already had the right method — entry at the scan-date
close, maturity gating, one observation per `(date, symbol)`, survivorship reported, and
a **date-block bootstrap** that resamples scan days rather than observations. Two gaps:

1. It bootstrapped **only** `SELECTED` vs `CEILING_CUT`. The `PHASE2_CUT` gap — the large
   one — was printed as raw means with **no confidence interval**.
2. No regime check.

Both are now closed: all three pairs are bootstrapped, and `--by-regime` repeats every
pair inside RISK_ON / RISK_OFF. Regime is classified from the **NIFTY 50 benchmark** on
the scan date (`RISK_ON` = close ≥ its own trailing 50-bar mean), deliberately
**independent of `signals_log`** so it cannot inherit a selection-side bias.

## Run

`--days 120 --horizon SWING --by-regime`, 2026-10-14. 2,166 price series, last bar
2026-10-13. 30,570 deduped observations (`SELECTED` 12,707 · `CEILING_CUT` 13,368 ·
`PHASE2_CUT` 4,495); 24,815 priced, **19% unpriceable — survivorship caveat**.

### Raw distributions (matured only)

| horizon | cohort | n | win | mean | median |
|---|---|---|---|---|---|
| 5d | SELECTED | 10,271 | 45.3% | +0.08% | −0.45% |
| | CEILING_CUT | 11,044 | 46.3% | +0.12% | −0.43% |
| | PHASE2_CUT | 3,500 | **38.8%** | **−0.51%** | −1.10% |
| 10d | SELECTED | 10,195 | 43.9% | −0.03% | −0.83% |
| | CEILING_CUT | 10,463 | 45.5% | +0.29% | −0.69% |
| | PHASE2_CUT | 2,954 | **35.4%** | **−1.04%** | −1.90% |
| 20d | SELECTED | 10,013 | 47.4% | +0.74% | −0.51% |
| | CEILING_CUT | 8,647 | 44.7% | +0.35% | −1.14% |
| | PHASE2_CUT | 2,071 | **31.0%** | **−2.12%** | **−3.75%** |

### Date-block bootstrap, all days (`diff` = B − A; negative means B is worse)

| pair | 5d | 10d | 20d |
|---|---|---|---|
| SELECTED vs CEILING_CUT | −0.10pp [−0.61,+0.45] | +0.08pp [−0.79,+0.91] | +0.52pp [−0.83,+1.84] |
| SELECTED vs PHASE2_CUT | −0.65pp [−1.41,+0.14] | −1.11pp [−2.44,+0.26] | −0.95pp [−2.12,+0.17] |
| CEILING_CUT vs PHASE2_CUT | −0.18pp [−0.51,+0.15] | **−0.52pp [−0.92,−0.14]** | **−1.26pp [−1.91,−0.62]** |

## Findings

**1. The top-20 exceptionalism ceiling does not discriminate.** `SELECTED` vs
`CEILING_CUT` shows no measurable difference at any horizon, every CI straddles zero, and
the cut cohort wins on roughly half the days (30/63, 32/58, 28/48). This holds in **both**
regimes. On 120 days this is a much larger sample than the 2026-10-11 finding and points
the same way — the ceiling is not selecting better stocks, it is just selecting fewer.

**2. The Phase-2 rank budget does discriminate.** `CEILING_CUT` vs `PHASE2_CUT` is
significant at **10d (−0.52pp, CI [−0.92,−0.14])** and **20d (−1.26pp, CI [−1.91,−0.62])**,
with `PHASE2_CUT` lower on 3 of 15 days at 20d. The raw distributions agree and the effect
**grows monotonically with horizon** (win 38.8% → 35.4% → 31.0%). This is the one
mechanism in the funnel with evidence that it earns its place.

**3. `SELECTED` vs `PHASE2_CUT` is *not* significant**, even though
`CEILING_CUT` vs `PHASE2_CUT` is. That is not a contradiction: the comparison rests on
fewer comparable days (30/25/15) and `SELECTED`'s day-means are noisier, so the interval
widens past zero. It is a sample-size result, not evidence that the ranker fails.

## Limitations — none of this is proof of consistent outperformance

* **The `PHASE2_CUT` result is effectively RISK_OFF-only.** The regime split is what
  revealed it: of 30 comparable days, **28 are RISK_OFF and 2 are RISK_ON**, so every
  `PHASE2_CUT` pair is withheld in RISK_ON. The finding survives RISK_OFF (10d
  [−0.81,−0.05], 20d [−1.68,−0.48]) but is **regime-unproven**, not regime-robust.
* **Thin day counts at the long horizon** — 15 comparable days at 20d for the significant
  pair. The bootstrap accounts for overlap but cannot manufacture independent days.
* **19% of observations are unpriceable**, mostly symbols no longer in the universe
  snapshot. Reported, not dropped, but it is a survivorship caveat on every number here.
* **These are scanned candidates, not executed trades** — no entry trigger, stop or sizing.
  They do not describe book performance.
* Every cohort's median is negative. That is the tape over the window.

## What this does and does not license

It licenses a **Stage 4 conversation** about the ceiling, with no change yet:
two independent studies now agree it does not discriminate, and the honest options are
retire it or re-derive it — not tune it. Per `ceiling-20-not-discriminating`, raising the
cap is the wrong lever; the ranker is where the signal is.

It licenses **nothing** on the rank budget except "keep collecting". The effect is real in
this window but concentrated in one regime, and the rule is that no flag moves without
measurable improvement that survives a regime check.

**No change is proposed here.** Reproduce with:

```bash
python -m scripts.ceiling_cohort_study --days 120 --horizon SWING --by-regime
```
