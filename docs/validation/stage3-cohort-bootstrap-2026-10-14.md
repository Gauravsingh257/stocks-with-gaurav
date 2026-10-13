# Stage 3 — do our selection mechanisms pick better than what they reject?

> **STATUS: LIVE** · workstream: `selection` · 2026-10-14 · measurement only
> No threshold, weight, flag, exit rule or selection logic changed by this document.
>
> **Revised after independent review.** The first version of this report claimed the
> ceiling result rested on 120 days and ~24,800 observations. That was wrong, and the
> corrections are recorded in "What the review found" below rather than quietly dropped.

## The question

Does the *existing* funnel beat the candidates it rejects? Two mechanisms sit at the end:

| Cohort | Definition (after review) |
|---|---|
| `SELECTED` | `final_selected=1`, **scan date ≥ 2026-08-23 only** |
| `CEILING_CUT` | L1∧L2∧entry and **`rejection_reason` empty** — eligible, un-rejected, so the exceptionalism ceiling is what kept it out |
| `PHASE2_CUT` | eligible but `below_rank_budget` |

`SELECTED` vs `CEILING_CUT` isolates **the ceiling**; `CEILING_CUT` vs `PHASE2_CUT`
isolates **the rank budget**.

## What the review found (and this version fixes)

1. **`SELECTED` changed meaning mid-window.** Before Phase 0/1 went live, `final_selected=1`
   marked *thousands* of rows a day — 2026-07-03 carries **3,863**, 07-06 carries **8,088** —
   against **20/day** afterwards. A 120-day window pooled two different things. Fixed with an
   era guard at **2026-08-23** (`--selected-since`).
2. **The export truncated silently.** Both kinds return **exactly 50,000 rows** at 120 days,
   and the endpoint returns an **empty list** above its own cap, so a truncated page was
   indistinguishable from a complete one. Now a `TruncatedExportError` — verified to fire at
   `--days 120`.
3. **`CEILING_CUT` was a residual bucket.** It was "eligible and not `below_rank_budget`",
   which swept in ~27% of rows carrying SMC rejects (`no_BOS`, `no_liquidity_sweep`,
   `weak_trend`, `no_order_block`). Those were cut for structure, not by the ceiling. Now it
   requires an empty `rejection_reason`.
   **Honest nuance:** in the valid 52-day window this excluded **zero** rows — the
   contamination lived entirely in the pre-era data. The fix corrects the label and protects
   future runs; it does not move the numbers below.

## Run — `--days 52 --horizon SWING --by-regime`, 2026-10-14

2,166 price series, last bar 2026-10-13. Deduped: `SELECTED` 714 · `CEILING_CUT` 7,521 ·
`PHASE2_CUT` 4,495. Priced 9,843; **23% unpriceable — survivorship caveat**.

| horizon | cohort | n | win | mean | median |
|---|---|---|---|---|---|
| 5d | SELECTED | 505 | 43.8% | +0.18% | −0.73% |
| | CEILING_CUT | 5,838 | 43.1% | −0.30% | −0.91% |
| | PHASE2_CUT | 3,500 | 38.8% | −0.51% | −1.10% |
| 10d | SELECTED | 429 | 42.2% | −0.03% | −1.94% |
| | CEILING_CUT | 5,257 | 41.1% | −0.54% | −1.50% |
| | PHASE2_CUT | 2,954 | 35.4% | −1.04% | −1.90% |
| 20d | SELECTED | **247** | 40.9% | −0.95% | −2.16% |
| | CEILING_CUT | 3,441 | 40.6% | −0.83% | −2.49% |
| | PHASE2_CUT | 2,071 | **31.0%** | **−2.12%** | **−3.75%** |

### Date-block bootstrap (`diff` = B − A; negative means B is worse)

| pair | 5d | 10d | 20d |
|---|---|---|---|
| SELECTED vs CEILING_CUT | −0.47pp [−1.17,+0.24] · 30d | −0.59pp [−1.87,+0.69] · 25d | +0.31pp [−1.08,+1.64] · 15d |
| SELECTED vs PHASE2_CUT | −0.65pp [−1.41,+0.14] · 30d | −1.11pp [−2.44,+0.26] · 25d | −0.95pp [−2.12,+0.17] · 15d |
| CEILING_CUT vs PHASE2_CUT | −0.18pp [−0.51,+0.15] · 30d | **−0.52pp [−0.92,−0.14]** · 25d | **−1.26pp [−1.91,−0.62]** · 15d |

## Findings

**1. The ceiling comparison is inconclusive — underpowered, not disproven.** Every CI
straddles zero, but the honest reason is **power**: 505 / 429 / **247** matured `SELECTED`
observations over 30 / 25 / **15** comparable days. The 20d interval spans ±1.4pp, so any
effect smaller than that is undetectable here. This neither supports nor refutes the ceiling.
It does *not* add weight to the 2026-10-11 finding, as the earlier version of this report
claimed.

**2. The rank budget is the one positive signal — and it is provisional.**
`CEILING_CUT` vs `PHASE2_CUT` is significant at **10d (−0.52pp [−0.92,−0.14])** and
**20d (−1.26pp [−1.91,−0.62])**, `PHASE2_CUT` lower on 3 of 15 days at 20d, effect growing
monotonically with horizon (win 38.8% → 35.4% → 31.0%). It is **identical** at `--days 52`
and the old `--days 120`, and stable across seeds 7 and 101 — it never depended on the broken
`SELECTED` cohort.

**3. `SELECTED` vs `PHASE2_CUT` is not significant** despite (2). Fewer comparable days and
noisier day-means widen the interval past zero. A power result, not a contradiction.

## Why finding 2 stays provisional

* **Time- and regime-confounded, and they are the same confound.** `PHASE2_CUT` exists only
  from **2026-08-23** (`below_rank_budget` was not logged before Phase 0/1), and that window
  is overwhelmingly RISK_OFF — **9,011 observations vs 832**, and only **2 RISK_ON scan days**,
  so every RISK_ON comparison is withheld. The regime split cannot corroborate the finding;
  it can only show that it is inseparable from RISK_OFF.
* **13–15 comparable days** at 20d. The bootstrap handles overlap; it cannot invent
  independent days.
* **23% unpriceable**, mostly symbols gone from the universe snapshot.
* **Scanned candidates, not executed trades** — no entry trigger, stop or sizing. These are
  not book returns.
* Every cohort median is negative. That is the tape in this window.

## What the evidence licenses

* On the **rank budget**: keep collecting. Real in this window, one regime, few days.
  **No flag moves.**
* On the **ceiling**: nothing. The comparison needs more post-era days before it can speak.
* **No strategy change is proposed**, and nothing here is proof of consistent outperformance.

```bash
python -m scripts.ceiling_cohort_study --days 52 --horizon SWING --by-regime
# --days 120 now fails loudly: the export cap makes that window incomplete.
```
