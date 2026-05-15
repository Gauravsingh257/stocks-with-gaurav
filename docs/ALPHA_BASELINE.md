# Alpha Baseline — Phase F8 (the gate ALPHA_V2 must beat)

This is the **first real walk-forward backtest** of the strategy, run *after*
the Phase F1 fix that un-broke the real OHLC technical path
(`technical_scanner._snapshot_from_ohlc`). Before F1, `snapshot_from_ohlc`
returned `None` for every symbol, so the validation/backtest engine scored
every name with no technical snapshot. These are the first numbers that
reflect the real SMC + real-OHLC pipeline.

## How to reproduce (cloud, read-only)

```
GET https://web-production-2781a.up.railway.app/api/research/backtest
    ?start_date=2026-01-15&end_date=2026-04-15
    &horizon=SWING&top_n=5&target_universe=60&hold_days=15&scan_step_days=7
```

Walk-forward, OHLC sliced to each scan date (no look-ahead), entry at next
candle open + 0.05% slippage, exit on stop/target/time, 0.10% cost/side,
conservative same-day ordering (stop counted before target).

## BASELINE RESULT — SWING (unfiltered 60-symbol alphabetical universe)

| Metric | Value | Verdict |
|---|---|---|
| Total trades | 45 | adequate sample |
| Win rate | **42.2%** | below breakeven for the payoff |
| Avg return / trade (net) | **+0.20%** | statistically zero edge |
| Avg gross return | +0.50% | edge disappears after costs |
| Payoff (avgWin/avgLoss) | 1.43 (11.19% / −7.83%) | mediocre |
| Expectancy / trade | **+0.20%** | no exploitable edge |
| Max drawdown | **71.18%** | account-destroying |
| Sharpe | 0.28 | very poor |
| Exits | 14 target / 13 stop / 18 time | |
| Walk-forward by month | Jan −2.24% · Feb −2.91% · Mar −0.12% · Apr +15.38% (n=5) | no consistency |

## Honest interpretation

1. **The SMC validation logic is real, but applied to a junk universe it
   produces no edge.** The 60-symbol set here is an alphabetical slice
   (AAATECH, AARVI, AAREYDRUGS, 5PAISA…) — exactly the illiquid / operator
   / microcap names the Phase F2 Quality Universe Engine is designed to
   remove. F2 does not exist yet, so this backtest runs on garbage.
2. **This validates the entire Phase F thesis:** selection quality is the
   missing alpha, not validation quality. The real SMC filter is sound;
   the inputs to it are not curated.
3. **71% max drawdown** confirms position/portfolio risk controls are
   inadequate for an unfiltered universe.
4. Short window (3 months) — directional, not conclusive, but the absence
   of edge is consistent across Jan/Feb/Mar.

## The ALPHA_V2 gate

No new scoring/selection path (ALPHA_V2) ships to production trust until a
backtest over a comparable window shows **all** of:

- Expectancy / trade ≥ **+1.0%** net (vs +0.20% baseline)
- Win rate ≥ **48%** at payoff ≥ 1.5 (vs 42.2% / 1.43)
- Max drawdown ≤ **30%** (vs 71%)
- Positive in ≥ 3 of 4 monthly walk-forward windows (vs 1 of 4)

ALPHA_V2 runs in shadow mode (computed + logged, not served) and is graded
against this baseline on the same date range before any flag flip.

## Next dependency

Phase F2 (Quality Universe Engine) must land before a fair ALPHA_V2
comparison — both baseline and candidate must be re-run on the *filtered*
universe so the test isolates scoring quality, not universe junk.
