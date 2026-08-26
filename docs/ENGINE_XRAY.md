# PHASE F-ENGINE X-RAY — Full Strategy Logic & Execution Breakdown

> **STATUS: HISTORICAL** · workstream: `engine` · last substantive update: 2026-05-16
> Research snapshot of the scorer as it stood in May 2026. Superseded by the Phase 0/1 selection-engine teardown (2026-08-23).
> Current project state lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

Research only. No production change. Every number from the committed
260-trade real DB (`docs/forensics/trade_database.csv`) + exact code
paths (file:line). This phase **corrects** a misattribution in F-Robust /
F-FORENSICS.

---

## 0. THE HEADLINE — we were measuring the wrong engine

`run_validation_scan` (the backtest's scan path) builds levels in this order:

```
validation_engine.py:596  build_swing_trade_levels()      ← REAL gated scorer
validation_engine.py:611  build_longterm_trade_levels()   ← REAL gated scorer
validation_engine.py:626  _scored_smc_levels()  FALLBACK  ← UNGATED
```

- **REAL path** → `engine/swing.py:score_swing_candidate` → gate
  `score < 7 OR rr < 2.5 → reject` (swing.py:423) + freshness (research_
  levels.py:249, >30% progress) + 8% min upside (research_levels.py:275).
- **FALLBACK path** `_scored_smc_levels` (validation_engine.py:329-375):
  only checks `stop < entry`. **No rr gate. No score gate.** Ships
  whatever has a cheap SMC "confirmation band". Setup label pattern
  `SMC_<H>_SCORE_<n>_<TIER>_<STRUCTURE>` (validation_engine.py:368).

Decoding the real trade DB by setup-string path:

| Trade source | n | % of all | win% | expectancy |
|---|---|---|---|---|
| **FALLBACK `_scored_smc_levels` (UNGATED)** | **240** | **92%** | 45% | **−0.39%** |
| REAL `build_*_trade_levels` (gated) | 20 | 8% | 60% | **+1.76%** |
| SWING only — REAL gated | 8 | — | — | **+3.13%** |
| SWING only — FALLBACK ungated | 217 | — | — | **−0.00%** |

**92% of every backtested trade — and every "no alpha" conclusion in
F-Robust and F-FORENSICS — came from the UNGATED fallback, not the live
SMC scorer.** The real gated scorer fires on only 8% of trades (20; 8 for
SWING). Where it fires it is *positive* (+1.76% all, +3.13% SWING) — but
n=8 is statistically meaningless. The correct statement is **not "SMC has
no edge"; it is "the real SMC scorer is so strict it almost never fires,
so the backtest (and production) is dominated by an ungated junk
fallback that has −0.39% expectancy."**

This reframes everything below.

---

## 1. ARCHITECTURE MAP (code-cited)

```
load_nse_universe ─► [F2 quality filter, F3, ALPHA_V2=OFF] ─► scan_technical
   ranking_engine.generate_rankings:751
        │  technical = sha256 hash (default) OR real OHLC (F1-fixed, off by default)
        │  fundamental = REAL yfinance | sentiment = SYNTHETIC hash
        ▼
   validation_engine.run_validation_scan:506
        ├─596 build_swing_trade_levels ─► research_levels.py:196
        │       └─► engine/swing.py:244 score_swing_candidate
        │             gate swing.py:423  score<7 or rr<2.5 → None   (RARELY passes)
        ├─611 build_longterm_trade_levels ─► swing.py:716 score_longterm_candidate
        └─626 _scored_smc_levels  (UNGATED FALLBACK — 92% of trades)
        ▼
   backtest_engine._simulate_long_trade:88   entry = NEXT bar OPEN (L108)
        stop-first L124 / target L129 / else TIME_EXIT L113
        ▼
   _simulate_portfolio:229   risk-sized, capped (F-Risk)
```

## 2. ENTRY LOGIC (engine/swing.py score_swing_candidate)

| # | Rule | Code |
|---|---|---|
| TF | Daily candles + weekly aggregate | swing.py:246 |
| HTF confirm | `detect_weekly_trend` must be BULL/STRONG_BULL else **None** | swing.py:261-271 |
| Structure | `detect_daily_structure` must be BULLISH_BOS/CHOCH else **None** | swing.py:274-280 |
| Entry zone | FVG-mid > OB-top > BOS-level pullback > swing-low+0.2ATR | swing.py:312-351 |
| Entry type | almost always **LIMIT pullback BELOW price** | swing.py:320-345 |
| Score | 0-12: weekly2 + daily2 + OB/FVG2 + RS2 + vol1 + RR1 + zone1 + spike1 | swing.py:261-420 |
| Gate | `score < 7 OR rr < 2.5 → None` | swing.py:423 |

**Entry flow:**
```
weekly BULL? ──no──► REJECT
   │yes
daily BOS/CHOCH? ──no──► REJECT
   │yes
build LIMIT pullback entry (FVG/OB/BOS zone, below price)
   │
SL = OB.low−0.3ATR, capped 5% (swing.py:361-364)
target = entry + 3·risk  (HARD 3R, swing.py:365)
   │
score≥7 AND rr≥2.5 ? ──no──► REJECT ──► validation falls to UNGATED _scored_smc_levels
   │yes (rare)
EMIT  (only 8% of backtest trades)
```

**Backtest fidelity gap:** entries are planned LIMIT pullbacks *below*
price, but `_simulate_long_trade:108` fills at the **next bar's open**
and never checks the limit would have filled. Planned-pullback entries
get market-filled higher → instant stop/target ambiguity. Real DB proof:
SWING/TARGET_HIT median hold = **0 days**, and 31 of 71 "TARGET_HIT" are
**losers** at −3.93%.

## 3. EXIT LOGIC

| Mechanism | Reality | Code |
|---|---|---|
| Target | Hardcoded **3R** (real) / 1.5R+3R (fallback) — fixed, never dynamic | swing.py:365, validation:365-367 |
| Stop | OB/swing-low − 0.3ATR, capped 5% — but **gaps through on daily bars** | swing.py:353-364 |
| Trailing | **none** | — |
| Partial | **none** | — |
| Structure exit | **none** | — |
| Time exit | close of bar `start+hold_days` | backtest:113-115 |
| Same-bar both | **stop counted first** (conservative, correct) | backtest:124-129 |

Real DB: TARGET_HIT 32% · **TIME_EXIT 45%** · STOP_LOSS 23% (avg
**−10.57%** vs 5% planned cap → daily gap-through is the loss engine).
**Exits are the single biggest fixable defect.** 3R is simultaneously
too far (good trades time-out before it) and irrelevant (winners run
past it); the ATR/5% stop does not survive daily gaps.

## 4. HOLDING-PERIOD ANALYTICS (real DB — the decisive cut)

| Cohort | n | avg hold | winner hold | loser hold | exp |
|---|---|---|---|---|---|
| SWING all | 225 | 13.2d (med 18) | 15.0d | 11.6d | +0.11% |
| SWING TARGET_HIT | 71 | 4.3d (med **0d**) | 7.5d | 0.0d | — |
| SWING TIME_EXIT | 111 | 20.7d | 20.8d | 20.5d | — |
| SWING STOP_LOSS | 43 | 8.5d | — | 9.2d | −10.21% |
| LONGTERM | 35 | 60.2d | 59.3d | 61.0d | −2.36% |

**SWING return by hold bucket — the proof there is no setup edge:**

| Hold | n | expectancy | win% |
|---|---|---|---|
| 0–5d | 67 | **−2.02%** | 34% |
| 6–10d | 18 | −1.72% | 44% |
| 11–15d | 14 | −2.29% | 36% |
| **15d+** | 126 | **+1.78%** | 53% |

Only trades that survive to the ~15-day time-exit are positive. The
setup contributes nothing in the first two weeks (all negative); the
single positive bucket is **passive drift in benign regimes**, not
signal. This is beta with extra steps.

## 5. SETUP CLASSIFICATION (real DB)

| Bucket | n | win% | expectancy |
|---|---|---|---|
| "other" (mostly ungated fallback) | 103 | 49.5% | +0.07% |
| BOS_continuation | 98 | 42.9% | **−0.57%** |
| CHOCH_reversal | 55 | 43.6% | −0.16% |
| BOS_other | 4 | 50.0% | +0.06% |

No bucket has edge above noise — **but caveat §0**: these are 92%
fallback-path trades. The gated BOS-continuation (n≈small) is not
isolated here; that is the open question, not a closed one.

## 6. REGIME MATRIX (real DB)

| Regime | n | win% | expectancy | verdict |
|---|---|---|---|---|
| bull_validation 2026Q1 | 45 | 53% | **+2.51%** | works |
| choppy_recovery 2025Q1 | 45 | 56% | +0.78% | marginal |
| sideways_down 2024Q4 | 45 | 36% | −0.60% | fails |
| deteriorating 2025Q4 | 45 | 44% | −0.69% | fails |
| bear_down 2024Q3 | 45 | 40% | **−1.45%** | fails badly |
| LONGTERM early-2025 | 15 | 33% | **−6.48%** | catastrophic |

**Regime gating is mandatory.** The engine is long-beta capture: positive
only in up/benign tapes, negative in every down/choppy regime. There is
**no regime filter in the code** (confirmed — no market-regime gate in
score_swing_candidate or generate_rankings entry path).

## 7. LONGTERM AUTOPSY

`score_longterm_candidate` (swing.py:716) = daily/weekly SMC on a 90d
hold. Real DB: −2.36% exp, losers avg −13.64% over 61d. Mechanism of
failure: (a) NEUTRAL weekly trend is *accepted* (swing.py:760-763) so it
enters non-trending names; (b) no macro, no fundamentals in the levels
path, no sector cycle; (c) a daily BOS decays long before 90 days; (d)
stops gap to −26% (AEROFLEX, ABDL). **Verdict: delete LONGTERM-SMC.**
It is structurally wrong, not undertuned. Fundamentals exist
(`fundamental_analysis.py`, real yfinance) but the *levels/exit* engine
ignores them entirely — long-term selection on technical structure is a
category error.

## 8. VISUAL REPLAYS — status (honest)

`docs/forensics/replay.py` runs the exact detectors on real
winners/losers but `DataIngestion` requires the cloud Kite/parquet env;
it cannot run locally (documented in F-FORENSICS). Substitute delivered:
every top-10 winner/loser decoded from its real setup-string into the
exact rule chain + planned RR/risk/reward (see `analyze.py` output in
this commit's session log). Key visual-equivalent facts:
- Losers: 9/10 are UNGATED `_SCORE_..._HIGH_CONVICTION_...` fallback;
  plannedRR 0.17–0.97 (risked more than reward — impossible under the
  real rr≥2.5 gate, proving fallback origin).
- Winners: mostly TIME_EXIT "NEUTRAL" no-structure names that drifted
  (63MOONS +42% NEUTRAL; ACUTAAS +23% TIME_EXIT) = beta, plus one
  absurd plannedRR=10.27 on a 1.83% stop (variance, not edge).
Rendered chart PNGs remain a scoped server-side follow-up; they will not
change these conclusions.

## 9. RISK ENGINE (why it succeeded while alpha failed)

`_simulate_portfolio` (backtest_engine.py:229): weight =
`min(20%, 1% / risk_dist%)` (L264), ≤8 concurrent (L269), sector cap,
account-equity drawdown (L304) vs the broken naive `_max_drawdown`
(L171, "whole account every trade"). It succeeded because it is **pure
arithmetic on position sizing — no prediction required.** It contained
DD to 1.8–7.6% across all 7 windows including losing ones. Alpha failed
because it *requires prediction* and the dominant (fallback) path has
none. Risk engineering is deterministic; alpha is not — that asymmetry
is the whole story.

## 10. FINAL ENGINE TRUTH

1. **What actually works:** the risk engine (deterministic sizing) and
   the OS/workflow layer. Confirmed across every phase.
2. **What is fake alpha:** the ungated `_scored_smc_levels` fallback —
   92% of trades, −0.39% expectancy. This produced every "no edge"
   verdict. It should be **deleted or gated**.
3. **What is real edge:** *unknown* — the gated `score_swing_candidate`
   is statistically untested (n=8 SWING, +3.13% but meaningless n). Not
   proven good, not proven bad. Prior "SMC has no edge" was a
   misattribution to the fallback.
4. **What is market beta:** the only positive hold-bucket (15d+,
   +1.78%) and the big TIME_EXIT winners — passive drift in up tapes.
5. **Useless setups:** the entire ungated fallback path; LONGTERM-SMC.
6. **Deserves R&D:** isolating the gated scorer and forcing the backtest
   to run ONLY it (no fallback) to finally measure the real strategy
   with adequate n.
7. **Are exits the biggest problem?** Co-equal with the fallback
   architecture. Fixed-3R + gap-prone 5% stop is structurally wrong;
   45% time-out, stops realise −10%.
8. **Is regime gating mandatory?** Yes — unambiguously. Long-beta
   capture must be switched off in non-bull regimes.
9. **Remove LONGTERM?** Yes — structurally unsuited.
10. **Intraday more promise?** No evidence; same follow-through failure
    likely at higher cost. Not an escape hatch.
11. **OS-first product?** Yes. Reconfirmed. The durable value is the
    risk + workflow engineering, not the signal.

## 11. CORRECTED NEXT RESEARCH DIRECTION

The prior recommendation ("regime overlay / exit redesign") still holds
but is now **reordered** by this finding:

1. **Isolate the real scorer** — re-run the backtest with the
   `_scored_smc_levels` fallback DISABLED so only gated
   `build_*_trade_levels` trades count. Until this is done we have *zero*
   valid measurement of the actual SMC strategy (n=8). This is the
   single highest-value next research step. Read-only backtest flag.
2. **Then** regime overlay + exit redesign studies on the isolated
   gated path (only meaningful once n is adequate).
3. **Delete** the ungated fallback and LONGTERM-SMC regardless.
4. Hold OS-first posture for the product throughout.

ALPHA_V2 remains OFF. This phase strengthens that and exposes that three
prior phases over-claimed "no alpha" while measuring a fallback that is
not the strategy. Intellectual honesty requires that correction be on
the record.

---

# ADDENDUM — ISOLATED GATED SCORER RESULT (the definitive answer)

`gated_only=true` flag added (validation_engine.py:disable_fallback_levels,
default off, backtest-only, commit 1048b62). Re-ran the full 7-window
matrix with the ungated `_scored_smc_levels` fallback DISABLED, so ONLY
strict `build_*_trade_levels` (rr≥2.5 / score≥7) trades survive.
Universe = 300, Good+ quality filter, point-in-time.

| Window | Horizon | Gated trades (n) | win% | expectancy |
|---|---|---|---|---|
| bull 2026Q1 | SWING | 4 | 25% | +0.06% |
| choppy 2025Q1 | SWING | 3 | 67% | +2.55% |
| bear 2024Q3 | SWING | **0** | — | — |
| sideways 2024Q4 | SWING | **0** | — | — |
| deteriorating 2025Q4 | SWING | 6 | 50% | −0.15% |
| lt_early 2025Q1 | LONGTERM | **0** | — | — |
| lt_mid 2025Q2 | LONGTERM | 12 | 58% | +0.22% |
| **TOTAL** | — | **25** | 52% | **+0.39%** |

## The definitive verdict — the alpha question is now CLOSED

**25 trades across 7 quarters and a 300-stock universe** (~2,100
symbol-quarters of opportunity) ≈ **0.012 trades per symbol-quarter**.
**4 of 7 windows produced ZERO gated trades.** n=25 expectancy +0.39%
is statistically meaningless.

The real gated SMC scorer is **not "broken alpha" — it is not a
deployable strategy by volume.** A system that fires 25 times in 7
quarters across 300 stocks cannot be traded, validated, or backtested
regardless of its per-trade numbers. The two engines are now fully
characterised:

| Engine | n (real DB) | expectancy | verdict |
|---|---|---|---|
| Ungated `_scored_smc_levels` fallback | 240 (92%) | **−0.39%** | junk — DELETE |
| Real gated `score_swing_candidate` | 25 in 7 quarters | +0.39% (n→noise) | **inert — not a strategy by volume** |

There is no third option. Loosening the gate to get volume *reproduces
the −0.39% fallback* (the strictness is the only thing separating it
from junk). The strictness that makes it not-junk also makes it inert.
**This is a structural dead end for SMC swing alpha on NSE daily.**

One genuine positive: the gate requires bullish weekly structure, so it
self-regime-gates (0 trades in bear/sideways/lt_early). That *instinct*
— "do nothing in non-bull regimes" — is correct and reusable as a
regime overlay on a *different* engine; it is the only salvageable idea.

## FINAL POSITION (6 research phases, now conclusive)

1. Selection alpha does not exist in deployable form. Proven, not asserted.
2. The risk engine is robust, real, deterministic (DD 1.8–7.6% all windows).
3. The OS / workflow / trust layer is the moat. Reconfirmed every phase.
4. **Recommended: STOP alpha R&D on this SMC approach.** The evidence is
   complete; "try harder" is not warranted. Delete the ungated fallback
   (it is −0.39% junk serving 92% of production trades — actively
   harmful). Adopt OS-first definitively. Keep the F-Risk engine. Reuse
   only the "off in non-bull regimes" instinct if a future, different
   signal is ever explored — never the single-window gate again.

ALPHA_V2: OFF, permanently, for this engine. The honest answer to "does
this engine have alpha?" is: **no — and we now know that conclusively,
from the backtest, having never risked a rupee of user capital. That is
the entire value of this discipline.**
