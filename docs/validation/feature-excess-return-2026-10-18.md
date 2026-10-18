# Can any feature we already have pick stocks that end up higher than the market?

**STATUS: LIVE** · 2026-10-18 · `scripts/feature_excess_return_study.py` · read-only

## Verdict first

**No — and the reason is that we were not testing the real features.** Of the seven features the
public export exposes, three collapse into one number and one has no history to validate against.
On the five genuinely distinct features that remain, **every one flips its rank-correlation sign
between train and test**, in both books. Nothing met the pre-registered bar, and nothing was tuned
to manufacture a pass.

But the finding that matters is structural, not statistical: **the ranker's actual features are
already logged and are simply not retrievable.**

## Pre-registered before any test-set result was seen

Features (all knowable at scan time): `confidence`, `exc_score`, `exc_threshold`,
`required_move`, `stop_distance`, `planned_rr`, `entry_gap`. Target: realised return over 20
trading days from the scan-date close, benchmark-relative.

A feature counts as a CANDIDATE only if **all four** hold: same rank-correlation sign in train and
test; test-set top-vs-bottom tercile CI excluding zero; both halves clear 8 days; and
`|effect| ≥ 1.0pp`. Seven features were tested, so one or two will look good by chance — the
train/test sign agreement is the guard.

### Method

Base = scan-date close (scans publish pre-open, so no look-ahead). Matured observations only. One
per (scan_date, symbol). Population = every candidate the ranker scored, selected and rejected,
because restricting to the top-20 throws away the range a feature is supposed to order. Terciles
computed **within each scan day**, since the ranker is cross-sectional. Day-block bootstrap
(resampling scan days, because overlapping forward windows make observations dependent).

**Time-separated with an embargo:** train is the earliest days, test the latest, and a gap of 20
trading days — the horizon length — sits between them, so no train observation's outcome window
reaches into the test period. Without that embargo the split leaks.

**Why raw return is already benchmark-relative here:** the benchmark's forward return is constant
within a scan day, and the bootstrap differences two buckets *inside the same day*, so the
benchmark term cancels exactly.

## Results

**SWING** — 53 matured days; train 16 (2026-06-29 → 07-22), embargo 20, test 17 (08-25 → 09-17):

| Feature | ρ train | ρ test | Sign | HI−LO test | 95% CI | Verdict |
|---|---:|---:|:--:|---:|---|---|
| confidence | −0.016 | +0.002 | **flips** | +0.33pp | [−0.19, +0.86] | not established |
| exc_score | n/a | n/a | — | — | — | no train history |
| exc_threshold | n/a | n/a | — | — | — | no train history |
| required_move | −0.077 | +0.107 | **flips** | +2.67pp | [+2.22, +3.13] | not established |
| stop_distance | −0.077 | +0.107 | **flips** | +2.67pp | [+2.22, +3.13] | not established |
| planned_rr | −0.007 | +0.011 | **flips** | +0.66pp | [−0.00, +1.32] | not established |
| entry_gap | +0.047 | −0.030 | **flips** | −0.90pp | [−1.49, −0.27] | not established |

**LONGTERM** — test 32 days (2026-08-03 → 09-17): same verdict throughout. `confidence` is the
only sign-stable result (+0.015 → +0.018, HI−LO **+0.58pp**, CI [+0.04, +1.16] excluding zero) but
it is **below the 1.0pp minimum effect** fixed in advance. Had that floor not been pre-registered
this is exactly where a trivial effect would have been written up as a win. It is not actionable.

Note `required_move` and `stop_distance` produce *identical* numbers in swing. That is not a
coincidence — see below.

## Three findings behind the null result

### 1. The target is arithmetic, not a forecast

Planned R-multiple, `((target − entry)/entry) / ((entry − stop)/entry)`, measured over the export:

| Book | n | min | median | max | stdev | distinct values |
|---|---:|---:|---:|---:|---:|---:|
| **SWING** | 5,959 | **3.000** | **3.000** | **3.000** | **0.0000** | **1** |
| LONGTERM | 5,967 | 3.000 | 3.500 | 3.512 | 0.2335 | 14 |

In swing the target is **exactly** `stop_width × 3` for every single candidate. In long-term it
spans only 3.00–3.51. So `required_move ≡ 3 × stop_distance`, `planned_rr` is a constant, and three
of the seven "features" are one number plus a constant — which is why `planned_rr`'s correlation is
indistinguishable from zero and why the two share identical statistics.

This independently explains the earlier MFE result
([`mfe-target-reach-2026-10-18.md`](mfe-target-reach-2026-10-18.md)): the median required move of
**+21.7%** against a median best-case excursion of **+7.68%** is not a judgment about the stock. It
is a multiple of the stop. **The target was never a prediction about where the stock could go.**

### 2. `exc_score` cannot be validated out-of-sample yet

Coverage of `exc_score` in the export, by month: 2026-06 **0%**, 07 **25%**, 08 **100%**, 09 100%,
10 100%. A train/test design with a 20-day horizon and a 20-day embargo needs roughly two months
per half; there are about two and a half months of exceptionalism history in total. **Document and
wait** — there is no way to out-of-sample validate this score today.

### 3. The real features exist and are not retrievable — this is the blocker

`signals_log.layer_details` already stores roughly thirty per-candidate features. From a live row:

```
discovery.momentum_5d_pct / _20d_pct / _50d_pct, momentum_score,
          volume_spike_pct, volume_score, pct_below_52w_high,
          is_at_52w_high, is_50d_breakout, breakout_score,
          discovery_score, avg_turnover_cr
quality.  score, data_authenticity, technical_score,
          fundamental_score, sentiment_score
smc.      confirmation_score, tier, partial_hits, structure, score,
          near_setup, entry_type, anchored, stop_basis
score_breakdown.  trend, volume, momentum, smc, composite
exceptionalism.   exceptionalism, threshold, qualifies, reason
```

**`/api/research/signals-log-export` does not return the `layer_details` column.** The study above
therefore tested the ranker's *output* (one composite score) plus some geometry derived from the
stop — never its *inputs*. A null result on that set says little about whether the ranker's features
carry signal; it says the export cannot see them.

Two caveats before anyone invests in fixing the export:

- The local database copy has `layer_details` populated for 2,200 October rows and **NULL for all
  100,000 August rows**. If that reflects production, the usable history is weeks, none of it
  matured at 20 days — so exposing the column would unblock the study *in about two months*, not
  today. **Confirm production's `layer_details` history before spending on it.**
- `quality.data_authenticity = 'synthetic'` appears on live rows. Worth a separate look given the
  standing rule that user-visible levels must be anchored to real OHLC.

## Power limitation — stated plainly

16 train / 17 test days (swing). A null result on that is **"not established on this window"**, not
"no edge exists". The sign instability is consistent across every feature and both books, which is
what noise looks like rather than a weak signal — but it is not proof of absence.

## What this does and does not license

- It does **not** license a target change. Narrowing the target re-slices a distribution with no
  established directional edge in it.
- It does **not** license a weight or threshold change. No feature earned one.
- It **does** locate the next decision precisely: either (a) confirm production's `layer_details`
  history and expose the column so the ranker's real inputs can be tested, or (b) accept that
  ranker improvement is gated on roughly two more months of logged history and spend the interval
  elsewhere.

Nothing in the live path was touched. No thresholds, weights, flags or targets changed.
