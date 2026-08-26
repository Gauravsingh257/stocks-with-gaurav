# PHASE F-FORENSICS — Trade-by-Trade Alpha Autopsy

> **STATUS: HISTORICAL** · workstream: `selection` · last substantive update: 2026-05-16
> 260-trade autopsy. The raw evidence in docs/forensics/ is still valid as a dataset; the conclusions predate the risk engine, Phase 0 and SMC-as-score.
> Current project state lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

**Evidence base:** 260 *real* simulated trades (225 SWING + 35 LONGTERM)
across 7 independent, regime-labelled, point-in-time backtest windows
(no look-ahead). Raw data committed in `docs/forensics/raw_*.json`,
flattened to `docs/forensics/trade_database.csv`, analysed by
`docs/forensics/analyze.py`. Zero synthetic data; zero production change.

---

## 1. THE HEADLINE FINDING — setups don't resolve

| Exit reason | n | % | avg return | win% |
|---|---|---|---|---|
| TARGET_HIT | 83 | 32% | +3.87% | 62.7% |
| **TIME_EXIT** | **118** | **45%** | +2.07% | 54.2% |
| STOP_LOSS | 59 | 23% | **−10.57%** | 5.1% |

**45% of all trades neither hit target nor stop — they just time out.**
The engine is built on a ~3R target premise, but only **32%** of trades
ever reach target. The SMC thesis assumes follow-through that, in the
data, mostly does not happen. Setups *stall*.

## 2. THE ALPHA KILLER — stop overshoot + RR collapse

- Engine *intends* `target = entry + 3×risk` (see `score_swing_candidate`).
  **Realised planned-RR mean = 1.71**, not 3.0 — the 5% SL cap +
  freshness/min-upside guards compress the geometry before the trade
  even starts.
- STOP_LOSS trades average **−10.57%** although risk was "capped" ~5%.
  Daily-bar gap-through means real losses massively exceed the modelled
  stop (biggest losers: ADANIENT −26.8%, AEROFLEX −26.5%, ABDL −22.2%).
- SWING asymmetry: avgWin **+7.66%** vs avgLoss **−6.26%** → payoff
  **1.22**, win rate 45.8% → expectancy **+0.11%** (breakeven). Confirmed
  at trade level, not just aggregate.

## 3. THE CORE SETUP HAS NEGATIVE EXPECTANCY

| Setup bucket | n | win% | expectancy |
|---|---|---|---|
| "other" (ATR/fallback/unparsed) | 103 | 49.5% | +0.07% |
| BOS_other | 4 | 50.0% | +0.06% |
| CHOCH_reversal | 55 | 43.6% | **−0.16%** |
| **BOS_continuation** (the flagship SMC thesis) | **98** | **42.9%** | **−0.57%** |

The single setup the entire engine is premised on — bullish
break-of-structure continuation — **loses money** across 98 real trades.
No setup bucket has positive edge above noise. This is the most important
sentence in this document.

## 4. REGIME DEPENDENCE IS TOTAL

| Regime window | n | win% | expectancy |
|---|---|---|---|
| bull_validation 2026-01..04 | 45 | 53.3% | **+2.51%** |
| choppy_recovery 2025-02..05 | 45 | 55.6% | +0.78% |
| lt_mid2025 2025-06..08 (LT) | 20 | 55.0% | +0.72% |
| sideways_down 2024-10..2025-01 | 45 | 35.6% | −0.60% |
| deteriorating 2025-09..12 | 45 | 44.4% | −0.69% |
| bear_down 2024-06..09 | 45 | 40.0% | **−1.45%** |
| lt_early2025 2025-01..03 (LT) | 15 | 33.3% | **−6.48%** |

The engine **only works in benign/up markets**. Every down/choppy regime
is negative. There is **no regime filter** — it trades a bear market with
the same aggression as a bull. The "+2.75% gate-clear" from F-Risk was
the single bull window; this autopsy shows it was regime luck, not edge.

## 5. SECTOR — every classified sector is negative

Cement −1.51%, Pharma −3.0%, Energy −4.44% (0% win), Infra −4.83%,
Chemicals −5.98%. (233/260 are "Unknown" — `SECTOR_MAP` coverage is thin,
itself a finding: the sector cap in F-Risk barely binds because most
names aren't mapped.) No sector is a refuge.

## 6. LONGTERM IS STRUCTURALLY BROKEN

35 trades, expectancy **−2.36%**, −6.48% in early-2025. Daily/weekly SMC
structure on a 90-day hold does not work — momentum decays long before
the horizon, entries are too tactical for an investment timeframe, macro
and sector cycle are entirely ignored. LONGTERM should be considered
**non-viable in its current SMC form**, not "tuned".

## 7. WINNERS vs LOSERS — what actually separated them

- **Biggest winners are mostly TIME_EXIT, not TARGET_HIT** (63MOONS
  +42% time-exit, ACUTAAS +23% time-exit, AIIL +22.9% time-exit). The
  real money came from a few names that kept trending and timed out
  high — *despite* the target logic, not because of it. The 3R target is
  simultaneously too far (good trades time out before reaching it) and
  irrelevant (winners run past where a sane target would have been).
- **Biggest losers cluster on:** down regime + STOP_LOSS gap-through +
  LONGTERM horizon. 5 of the 10 worst trades are LONGTERM early-2025.
- Winners skew to the bull window + Adani-complex momentum names
  (ADANIPOWER +19.5% target, ADANIENSOL +18.6% target) — i.e. the engine
  works when it accidentally lands on a strong-momentum name in a strong
  tape. That is beta, not alpha.

## 8. VISUAL/DETECTOR REPLAY — status

`docs/forensics/replay.py` runs the *exact* engine detectors
(`detect_daily_structure/ob/fvg`, RS, volume) on real winners/losers
sliced to scan date. It could not execute locally — `DataIngestion`
requires the cloud Kite/parquet data environment (the backtest itself
only runs server-side). This is a tooling limit, not an evidence gap:
the trade-level autopsy above is conclusive without it. Rendering the
detector/chart layer server-side is a scoped follow-up if the visual
overlay is wanted — it will not change the conclusions.

---

## 9. BRUTALLY HONEST CONCLUSIONS

1. **What works:** the F-Risk portfolio/risk engine (drawdown contained
   1.8–7.6% across all 7 windows incl. losing ones). The operating-system
   / workflow / trust layer. Nothing about *signal selection*.
2. **What fails:** the alpha itself. BOS-continuation (the thesis) is
   −0.57% over 98 trades. 45% of trades time out. Stops gap through to
   −10.6% avg. LONGTERM is −2.36%.
3. **Setup with real promise:** none, as-is. The least-bad is "other"
   (~breakeven). CHOCH and BOS-continuation are both negative.
4. **Delete:** LONGTERM SMC (structurally unsuited), and the implicit
   "3R fixed target" exit model (it neither protects nor captures).
5. **Dangerous regimes:** every non-bull regime. The absence of a regime
   gate is the single largest structural defect.
6. **Best sectors:** none — and sector data is too sparse to lean on.
7. **Does SMC have edge here?** On this evidence, **no durable standalone
   edge** on the NSE universe at daily granularity. It captures momentum
   beta in strong tapes and gives it back in weak ones.
8. **Entry timing:** weak — planned RR collapses 3.0→1.71 before entry;
   winners run past target (entries/targets mis-scaled to actual move).
9. **Exits:** the biggest single fixable defect. Fixed 3R target +
   ATR-capped stop is mis-matched to how these names actually move
   (gap-through losses, time-out winners). MFE/MAE capture is not yet
   instrumented — that is the highest-value *research* instrumentation
   to add next (read-only).
10. **Does alpha exist anywhere?** Only conditionally: long bias in a
    confirmed strong-tape regime. That is a *regime overlay*
    opportunity, not a setup-tuning one.
11. **Intraday vs swing:** untested here, but the swing failure mode
    (no follow-through, time-outs, gap stops) suggests intraday would
    face the *same* follow-through problem at higher cost/noise. Not a
    clear escape hatch on this evidence.
12. **OS-first?** Yes. The data says the moat is the risk + workflow
    engineering, exactly as Phase E concluded and F-Robust confirmed.

## 10. RECOMMENDED NEXT RESEARCH DIRECTION (not implementation)

Priority order, all *research*, no blind optimisation:

1. **Regime overlay study** — the data screams this. Re-run the matrix
   gating entries to a confirmed-bull market filter (NIFTY above
   200DMA / breadth). Hypothesis: the engine is a leveraged-beta
   momentum capture that must be switched OFF in non-bull regimes.
2. **Exit redesign study** — instrument MFE/MAE (read-only backtest
   enhancement) to quantify profit left on table and stop slippage,
   then test ATR-trailing / structure-based exits vs the fixed 3R.
3. **Kill LONGTERM-SMC** — stop investing research there; it's
   structurally wrong, not undertuned.
4. **Accept the OS-first strategic posture** for the product while the
   above two studies run in the backtest only.

ALPHA_V2 remains OFF. Nothing here changes that — it reinforces it.
