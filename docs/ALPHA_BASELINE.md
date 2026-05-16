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

---

# PHASE F3 RESULT — Filtered vs Unfiltered (THE PROOF)

Same backtest params (2026-01-15→04-15, SWING, top_n=5, univ=60, hold=15,
step=7, costs identical). F2 filter is **point-in-time** — scores each
symbol on OHLC up to 2026-01-15 only (no look-ahead into the test window).
Reproduce: add `&universe_quality_filter=true&quality_min_tier=Good` to the
backtest URL above.

| Config | Trades | Win% | Expectancy/trade | Max DD | Sharpe | Payoff |
|---|---|---|---|---|---|---|
| **Unfiltered (baseline)** | 45 | 42.2% | **+0.20%** | **71.2%** | 0.28 | 1.43 |
| **F2 filter — Good+** | 41 | 48.8% | **+2.75%** | **39.7%** | 4.46 | 2.16 |
| F2 filter — Strong+ | 31 | 54.8% | +2.94% | 47.2% | 4.29 | 1.59 |

(Good+ filter: 60→24 symbols kept, 36 rejected as junk, point-in-time.)

## Verdict — the Phase E/F thesis is empirically proven

Filtering the universe with F2 (zero change to the SMC validation logic
itself) produced a **~14× expectancy improvement** (+0.20% → +2.75%) and
**cut max drawdown by 44%** (71% → 40%). The SMC engine was always real;
**selection quality was the entire alpha gap.** This is now hard evidence,
not assertion.

## ALPHA_V2 promotion gate — scored against Good+ (best DD config)

| Gate criterion | Target | Result | |
|---|---|---|---|
| Expectancy/trade | ≥ +1.0% | **+2.75%** | ✅ PASS |
| Win rate @ payoff | ≥48% @ ≥1.5 | **48.8% @ 2.16** | ✅ PASS |
| Max drawdown | ≤ 30% | **39.7%** | ❌ FAIL |
| +ve walk-forward windows | ≥ 3/4 | **3/4** | ✅ PASS |

**3 of 4 pass decisively. ALPHA_V2 stays OFF** (gate not fully cleared).

## Critical diagnosis (why the flag is NOT flipped yet)

The single failing criterion is max drawdown (39.7% vs ≤30%). The
Strong-tier run **disproves** "tighter selection fixes drawdown" — it made
DD *worse* (47.2%), because fewer qualified names → higher concentration →
larger equity swings. Therefore the residual drawdown is a
**portfolio-construction / position-sizing problem, not a selection
problem**. More selection tuning will not close it.

## Next phase (clearly identified by the evidence)

Portfolio-level risk controls (NOT more scoring work):
- per-trade risk budget (fixed-fractional / volatility-scaled sizing)
- max concurrent open positions cap
- sector concentration cap
- correlation-aware position limits

Re-run this exact comparison after risk controls land. ALPHA_V2 flips only
when Good+ clears all four gate criteria including maxDD ≤ 30%.

---

# PHASE F-Risk RESULT — Realistic Portfolio Model

The 40-71% drawdowns were a **model artifact**: `_max_drawdown` compounded
every trade's full % return sequentially = "entire account in one trade at
a time". No portfolio trades that way. F-Risk adds a real
risk-sized, capacity-constrained portfolio sim (1% risk/trade, ≤8
concurrent, ≤3/sector, 20% max position) and measures drawdown on the
ACCOUNT equity curve.

Same params as the F3 proof, Good+ filter, point-in-time (no look-ahead).
Reproduce: F3 URL + `&risk_per_trade_pct=1.0&max_concurrent_positions=8`.

| Stage | Expectancy/trade | Drawdown (model) | Account return |
|---|---|---|---|
| Unfiltered baseline (naive DD) | +0.20% | 71.2% naive | — |
| F2 filter Good+ (naive DD) | +2.75% | 39.7% naive | — |
| **F2 Good+ + realistic portfolio** | **+2.75%** | **4.97% account** | **+10.89%** |

Account-level: 38 positions taken, avg 4.15 concurrent, equity ×1.109
over the 3-month window, **account max drawdown 4.97%**.

## ALPHA_V2 gate — FULL SCORECARD (all four)

| Criterion | Target | Result | |
|---|---|---|---|
| Expectancy/trade | ≥ +1.0% | +2.75% | ✅ |
| Win rate @ payoff | ≥48% @ ≥1.5 | 48.8% @ 2.16 | ✅ |
| Max drawdown (account) | ≤ 30% | **4.97%** | ✅ |
| +ve walk-forward windows | ≥ 3/4 | 3/4 | ✅ |

**ALL FOUR CLEARED on the validation window.** The full F1→F-Risk thesis
is empirically proven: real technicals (F1) + quality selection (F2/F3) +
realistic risk sizing (F-Risk) turns a +0.20% / 71%-DD junk system into
+2.75%/trade, +10.89% account, 4.97% DD.

## BUT — ALPHA_V2 stays OFF pending robustness validation

This is **one 3-month window on a 60-symbol slice**. Clearing the gate on
a single window is necessary, not sufficient, to flip a LIVE trading flag.
Required before flip:
- multiple non-overlapping windows (incl. a bearish regime)
- larger universe (300-500, not 60)
- explicit out-of-sample period
- LONGTERM horizon validated too (only SWING tested so far)

Discipline holds: huge proven improvement, flag still OFF until robustness
is demonstrated across regimes and a wider universe.

---

# PHASE F-Robust RESULT — multi-window / regime / horizon (THE HONEST VERDICT)

Same engine, Good+ filter, realistic portfolio model, point-in-time (no
look-ahead), universe=150, weekly scans. Seven independent windows.

## SWING — 5 non-overlapping windows

| Window | WR | Expectancy/trade | Account ret | Account maxDD | Gate? |
|---|---|---|---|---|---|
| 2026-01-15→04-15 (original "validation") | 48.8% | **+2.75%** | +10.89% | 5.0% | ✅ |
| 2025-02-01→05-01 | 55.6% | **+0.78%** | +1.52% | 7.6% | ❌ |
| 2024-06-01→09-01 | 40.0% | **−1.45%** | −4.9% | 5.3% | ❌ |
| 2024-10-01→2025-01-01 | 35.6% | **−0.60%** | −7.2% | 7.2% | ❌ |
| 2025-09-01→12-01 | 44.4% | **−0.69%** | +0.9% | 4.1% | ❌ |

SWING mean expectancy ≈ **+0.16%/trade**. **1 of 5** windows clears the
gate; **3 of 5 are negative**.

## LONGTERM — 2 windows (hold 90d)

| Window | WR | Expectancy/trade | Account ret | Account maxDD | Gate? |
|---|---|---|---|---|---|
| 2025-01-01→03-01 | 33.3% | **−6.48%** | −4.1% | 7.2% | ❌ |
| 2025-06-01→08-01 | 55.0% | **+0.72%** | +4.1% | 1.8% | ❌ |

LONGTERM: **0 of 2** clear the gate; one strongly negative.

## VERDICT — brutal honesty (as required)

1. **The alpha/selection edge is NOT robust.** Across 7 windows × 2
   horizons, only the single original validation window cleared the gate.
   The +2.75% "gate cleared" headline from F-Risk was **overfit to one
   favourable 3-month window**. Mean expectancy is breakeven-to-negative.
   The multi-window discipline correctly exposed this — exactly what it
   was designed to do.

2. **The F-Risk portfolio model IS robust and validated.** Account max
   drawdown stayed **1.8%–7.6% in every window**, including the heavily
   losing ones (vs 71% under the old naive model). The risk engineering
   is sound and genuinely valuable.

3. This empirically reconfirms the Phase E audit: **the platform's real
   moat is the operating-system / workflow / risk-engineering layer, NOT
   signal alpha.** F-Risk = real value. The SMC+F2 selection = no durable
   edge on this evidence.

## DECISION

- **ALPHA_V2 stays OFF — decisively and indefinitely.** The selection
  pipeline does not have cross-regime edge. Do not flip the flag.
- **F-Risk is promoted as the canonical drawdown metric** (the naive
  model was simply wrong; the portfolio model is correct and robust).
- We found "no durable alpha" in a *backtest*, not with real user
  capital. That is the entire point of this discipline and a major win
  despite the disappointing headline.

## Strategic fork (needs a human product decision)

A. Invest in genuinely better alpha — regime-adaptive scoring (Phase F3
   adaptive engine), better entry timing, alternative signals. High
   effort, uncertain payoff.
B. Accept Phase E's conclusion: the moat is the OS/workflow/trust +
   risk engineering. Stop chasing signal alpha; double down on the
   watchlist/lifecycle/position-tracking product that already works.

Recommendation: **B for the product, A only as scoped R&D** — never
again with a single-window gate.
