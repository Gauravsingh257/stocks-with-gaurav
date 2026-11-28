# Which ranking inputs pick stocks that beat the benchmark?

**STATUS: LIVE** · 2026-10-19 · `scripts/ranking_inputs_study.py` · read-only

## First, a correction

An earlier conclusion that the ranker's real features were unavailable and we would have to wait
~2 months for history **was wrong**. It was drawn from a stale partial local copy of `signals_log`
that had `layer_details` for 2,200 October rows and NULL for 100,000 August rows.

Production, queried directly over `railway ssh` (read-only, no new endpoint):

| | |
|---|---|
| `signals_log` rows | **1,270,705** |
| rows with `layer_details` | **1,270,705 — 100%** |
| span | **2026-04-25 → 2026-10-16** |
| distinct scan dates | **129** |
| numeric features per row | **71** |

The data was there the whole time. 2026-04 is excluded because
`quality.data_authenticity` is `synthetic` for ~78% of that month's rows against ~95% `real` from
May onward, leaving **124 scan dates** and, after 20-day maturity, **87 usable days**
(2026-05-05 → 2026-09-17).

## The result

**Four of 71 features clear the pre-registered bar in both windows.** The bar, fixed before any
test-set result was seen: same per-day rank-correlation sign in train and test; test-set
top-vs-bottom tercile 95% CI excluding zero; ≥8 scan days per half; and |effect| ≥ 1.0pp so a
statistically real triviality cannot pass.

| Feature | full HI−LO | pre-era HI−LO | HI vs NIFTY | LO vs NIFTY |
|---|---|---|---|---|
| **`discovery.momentum_50d_pct`** | **+2.28pp** [1.70, 2.89] | **+2.41pp** [1.29, 3.52] | **+0.71%**, beat 53.3% | −0.43%, beat 47.5% |
| **`discovery.breakout_score`** | +1.90pp [1.50, 2.30] | +1.36pp [0.81, 2.00] | **+0.81%**, beat 54.0% | −0.35%, beat 48.0% |
| **`quality.fundamental_score`** | +1.57pp [1.43, 1.72] | +1.12pp [0.91, 1.34] | −0.07%, beat 49.7% | −0.70%, beat 46.2% |
| **`discovery.discovery_score`** | +1.41pp [0.96, 1.84] | +1.09pp [0.43, 1.76] | **+0.55%**, beat 52.5% | −0.38%, beat 47.8% |

Three of the four have a **top tercile that genuinely beats the benchmark** (+0.55% to +0.81%
median, 52.5–54.0% beat rate) and a **bottom tercile that loses to it**.
`quality.fundamental_score` is weaker and must be described precisely: its top tercile still
*underperforms* the market (−0.07%); it only beats its own bottom tercile. That is a different,
smaller claim.

## The finding that matters most

**The composite score the ranker actually ranks on fails the bar in both windows** — +0.97pp
(full) and +0.43pp (pre-era) — **while three of its own upstream inputs clear it.** And the SMC
component **flips sign in both windows**:

| | full | pre-era |
|---|---|---|
| `score_breakdown.composite` | +0.97pp — below bar | +0.43pp — below bar |
| `score_breakdown.smc` | sign flips | sign flips |
| `smc.confirmation_score` | sign flips | — |
| `discovery.momentum_5d_pct` | sign flips | sign flips |

So the blend is **weaker than its best ingredient**. Signal exists in the discovery features —
most of all 50-day momentum — and the composite dilutes it, with the SMC component contributing
nothing stable to the *ordering*.

Note also that **short-horizon momentum (5d) flips sign while 50-day momentum is the strongest
single feature.** That is medium-term momentum, and it is the opposite of what a volatility- or
excursion-seeking selector would surface. It sits consistently with the earlier finding that the
engine currently has an excursion edge but no return edge
([`mfe-target-reach-2026-10-18.md`](mfe-target-reach-2026-10-18.md)).

## Method

Base = close of the scan date (scans publish pre-open, so that close is tradable after publication
— no look-ahead). Matured observations only; a missing bar at D+20 is dropped and counted, never
zero-filled. One observation per (scan_date, symbol). Terciles computed **within each scan day**,
because the ranker is cross-sectional. Statistical claims use `forward_eval.block_bootstrap`,
resampling scan **days**, since overlapping forward windows make observations dependent.

Raw return is already benchmark-relative for the tercile test: the benchmark's forward move is
constant inside a scan day and the bootstrap differences two buckets within that same day, so the
benchmark term cancels exactly. The absolute excess level is reported separately because "beats its
peers" and "beats the market" are different claims.

Windows: `full` = 2026-05-01 onward (train 33 days 05-05→07-02, embargo 20, test 34 days
07-31→09-17); `pre_era` = up to the 2026-08-23 Phase 0/1/2 selection change (train 24 days
05-05→06-17, embargo 20, test 25 days 07-20→08-21). The embargo exists because a 20-day horizon
without one leaks train outcomes into the test period.

## Limitations — and one is a flaw in this design, not in the data

1. **The two windows' test sets OVERLAP.** `full` tests 07-31→09-17 and `pre_era` tests
   07-20→08-21, sharing roughly 16 days. **"Survived both windows" is therefore not two
   independent confirmations** — the agreement is partly mechanical. This is the single biggest
   reason not to act on these four yet.
2. **71 features were tested.** At a 95% CI you would expect ~3.5 to clear the CI criterion by
   chance alone. The sign-agreement and effect-size filters are much stricter than that, and four
   survivors with consistent magnitudes across windows is more than chance would comfortably
   produce — but combined with (1) it is not conclusive.
3. **The effects are small.** A 1–2pp tercile spread over 20 days, and top-tercile excess of
   +0.55% to +0.81%. Real, but not transformative, and before costs.
4. **Coverage differs by feature** — `momentum_50d_pct` n=23,523 against
   `fundamental_score` n=52,035 — so the survivors are not measured on identical populations.
5. Survivorship: symbols absent from the universe snapshot are counted, not silently dropped.
6. Features surviving only one window are recorded and **not** promoted:
   `discovery.momentum_score` and `discovery.pct_below_52w_high` and `score_breakdown.trend`
   (full only), `smc.structure_info.level` (pre-era only).

## Minimum additional history for a defensible second test

A clean confirmation needs a test window that does **not** overlap 07-31→09-17. The next available
is scans from **2026-09-18** onward, matured at 20 trading days. The latest price bar is
2026-10-16, so the 09-18→10-16 scans (~20 scan days) finish maturing around **2026-11-13**.

**≈4 weeks, available mid-November, for ~20 genuinely held-out test days.** No further logging is
required — the data accrues on its own.

## What this does and does not license

- It does **not** license a weight change. The overlap flaw in (1) must be cleared first.
- It **does** give a concrete, pre-registerable hypothesis, which is more than this project had
  yesterday: *50-day momentum, breakout score and discovery score order stocks by realised
  benchmark-relative return, and the composite blend dilutes them.*
- The confirmation test should be registered **now**, before the November data exists, with its
  features, horizon, window and bar all fixed in writing — so the second test cannot be chosen
  after seeing the outcome.

Nothing in the live path was touched. No thresholds, weights, flags or targets changed, and no new
endpoint was added.
