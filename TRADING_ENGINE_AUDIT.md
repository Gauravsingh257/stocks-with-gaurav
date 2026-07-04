# 🔬 Trading Engine Validation Audit — stockswithgaurav.com

**Auditor role:** Senior Quant Researcher + Trading-Systems Auditor + Production QA
**Date:** 2026-07-05 (IST) · **Scope:** scanner, indicators, promotion, journal, analytics
**Method:** source review (file:line), live API capture, clean-room recomputation, unit-test execution.
**Constraint honored:** no production code was modified.

> **Data snapshot** used throughout (captured 2026-07-05 ~00:05 IST):
> screener `as_of=2026-07-03` (last complete session); SWING journal = 26 closed trades;
> LONGTERM journal = 1 closed trade; research track-record = 200 picks.
> The journal is **live and changing** (the stats API briefly returned 27 trades mid-audit as a new stop closed) — all figures below are pinned to the captured snapshot.

---

## 1. Executive Summary

**Verdict: CONDITIONALLY READY — internally correct and consistent where verifiable, with 3 honest gaps that must be disclosed or closed before relying on it at scale.**

The engine is **not** producing mathematically wrong numbers. Every calculation I could verify without live market data reconciled **exactly**. The concerns are not bugs — they are (a) verification I could not complete this session because the Kite token is expired (weekend), (b) a statistical honesty issue (confidence does not predict outcomes), and (c) a tiny, churn-distorted sample.

| # | Finding | Severity |
|---|---|---|
| C1 | **Confidence score does not predict outcomes** — in-sample, losers had *higher* average confidence (65.6) than winners (63.8); the largest bucket (60–69) is the worst performer | 🔴 High (honesty) |
| C2 | **Live-OHLC numeric re-verification not executed** — Kite token expired; indicator values could not be recomputed against fresh Zerodha candles this session (math proven separately via unit tests) | 🟠 Medium (coverage gap) |
| C3 | **Sample is tiny and churn-distorted** — 26 closed trades, 13 of them the APTUS/KALYAN churn; headline stats are not yet statistically meaningful | 🟠 Medium |
| M1 | **Intraday scanner refresh can repaint** — forming-candle flips shown between post-close runs | 🟡 Medium |
| M2 | **Targets are mechanical 3R, not structural** — target = entry + 3×risk, not resistance-derived | 🟡 Minor (disclosure) |
| M3 | **EMA20/EMA50 use truncated seed windows** (`closes[-80:]`/`[-160:]`) vs full-series EMA10 — sub-0.2% deviation | 🟢 Minor |
| M4 | **One research pick had a 0.1% stop** (min in 200-pick set) — probable outlier to investigate | 🟡 Minor |

**Nothing mathematically incorrect was found in any verifiable calculation.** Proof follows.

---

## 2. What was PROVEN correct (with evidence)

### 2.1 Scanner scoring, filter, and tier — 100% consistent (Parts 1, 2, 5)
Clean-room reimplementation of the scoring weights (NOT importing the engine's code) recomputed `quality_score`, `tier`, the `close>EMA10` filter, and the `stack` flag for **every live screener row**:

- **Daily (11/11 rows):** every recomputed `quality_score` matched published to ±0.1; every `tier` matched `tier_of()`; `close>EMA10` held on all; `stack == (close>EMA20>EMA50)` held on all.
- **Weekly (9/9 rows):** same — all consistent.

Example (COSMOFIRST, 1D): published `qs=50.7`, recomputed `50.7`; `close 853.1 > ema10 800.69` ✓; `stack=True` and `853.1 > 782.74 (ema20) > 752.66 (ema50)` ✓.
Evidence: [scoring.py:53-90](services/scanners/scoring.py#L53-L90), [registry.py:36-113](services/scanners/registry.py#L36-L113), live `/api/screeners/supertrend_flip/{1D,1W}`.

### 2.2 Indicator math — canonical + unit-tested (Part 2)
The indicator library is a textbook-correct implementation:
- **Wilder ATR** ([indicators.py:47-64](services/scanners/indicators.py#L47-L64)) — correct TR + Wilder smoothing.
- **Supertrend(10,3)** ([indicators.py:67-126](services/scanners/indicators.py#L67-L126)) — correct hl2±mult·ATR bands, canonical final-band carry logic, correct flip detection. Matches TradingView's definition.
- **EMA** ([indicators.py:17-29](services/scanners/indicators.py#L17-L29)) — standard recursive EMA.
- **`python -m pytest tests/test_scanner_indicators.py` → 11 passed** (reference-value tests).

⚠️ Two methodology notes (not bugs): EMA10 uses the full series but EMA20/EMA50 use truncated windows `closes[-80:]`/`[-160:]` (registry.py:70-71) → seed-convergence deviation <0.2% (M3). Supertrend seeds direction=+1 at warm-up (indicators.py:106), washed out after ~250 daily bars.

### 2.3 Risk-Reward and stops — fixed 1:3 by construction (Part 7)
Recomputed RR from published entry/stop/target for every closed trade: **every trade is exactly RR = 1:3.00**, risk capped at ~5% (`MAX_SWING_SL_PCT`), e.g. AEGISVOPAK entry 217.85 / sl 206.96 (5.0%) / tgt 250.52 (15.0%) → 1:3.00. No RR calculation bug. Target = entry + 3×risk (mechanical, not resistance-based — M2).

### 2.4 Historical statistics — reconcile EXACTLY (Parts 8, 13)
Independent recomputation from the raw journal vs the stats API:

| Metric | Recomputed (journal) | Stats API | Match |
|---|---|---|---|
| trades | 26 | 26 | ✅ |
| hit rate | 23.1% | 23.1% | ✅ |
| avg P&L | +0.89% | +0.89% | ✅ |
| total P&L | +23.13% | +23.13% | ✅ |
| target/stop/structure | 6 / 6 / 14 | 6 / 6 / 14 | ✅ |

Derived (recomputed): profit factor **1.34**, expectancy **+0.89%/trade**, avg winner **+15.13%**, avg loser **−3.38%**, avg hold 13.4d. No mismatch between journal, stats API, and website summary. **Analytics integrity: verified.**

> Caveat: a naive sum-of-% "equity curve" shows −65% max drawdown — an artifact of the 14 concentrated structure-losses (APTUS/KALYAN churn), *not* a position-sized portfolio drawdown. Treat total/avg as robust; treat this DD as directional only.

### 2.5 Promotion picks the BEST-ranked candidate, not the first (Part 6)
Both selectors rank before choosing: `select_swing_ideas` scores all candidates, sorts by `selection_score` desc, then takes the top after a risk check ([idea_selector.py:311-322](services/idea_selector.py#L311-L322)); `select_from_final_ideas` sorts by confidence desc ([idea_selector.py:435](services/idea_selector.py#L435)). It does **not** promote first-arrival. (Note: the two sources rank by *different* keys — confidence vs selection_score — a consistency wrinkle, not a bug.)

### 2.6 Confidence stays in range (Part 5)
Across 200 research picks, confidence ∈ **[49.1, 90.16]** — zero values outside [0,100]. No duplicate-bonus overflow.

---

## 3. 🔴 C1 — Confidence does NOT predict outcomes (the key honesty finding)

Bucketed the 26 closed SWING trades by confidence vs actual result:

| Confidence | n | Win rate | Avg return |
|---|---|---|---|
| 50–59 | 4 | **50.0%** | +5.11% |
| 60–69 | 17 | **11.8%** | −0.45% |
| 70–79 | 4 | 50.0% | +3.87% |
| 90+ | 1 | 0.0% | −5.06% |

And winner-vs-loser characteristics:

| Group | n | Avg confidence | Avg days held | Avg P&L |
|---|---|---|---|---|
| Winners | 6 | **63.84** | 20.3 | +15.13% |
| Losers | 20 | **65.61** | 11.3 | −3.38% |

**Losers had *higher* average confidence than winners, and the largest bucket (60–69) is the worst performer.** In this sample, confidence is non-monotonic and non-predictive — what actually separated winners was **holding time** (winners ran ~20 days, losers cut at ~11). Much of this is the APTUS/KALYAN churn (13 trades) polluting the 60–69 bucket, but the honest conclusion stands: **the confidence score cannot currently be presented as a reliability signal to the public.** Either recalibrate it against outcomes or stop displaying it as if higher = better.

---

## 4. 🟠 Coverage gaps I could NOT close this session (stated honestly)

Per your "never assume, never estimate" instruction, I am **not** fabricating these:

- **C2 — Live indicator recompute vs Zerodha (Part 2 numeric, Parts 3, 12):** the Kite access token is expired (last login 2026-07-03; 24h TTL; today is a weekend). Local Kite fetch → `InputException: Invalid api_key or access_token`; backend `/api/ohlc/*` → empty (same expired token, no Redis candles for equities). So I could not re-pull OHLC to numerically reproduce Supertrend/EMA per symbol, verify split/holiday adjustments, or compute MFE/MAE. **The math is proven (§2.2); the live re-pull is pending.** A one-command reproduction script is ready — run after the next Kite login:
  ```
  # after morning Kite login refreshes kite:access_token in Redis:
  python -m pytest tests/test_scanner_indicators.py     # math (already green)
  # then fetch OHLC for each screener symbol and diff supertrend/ema vs the published row
  ```
- **Part 11 — Missed opportunities:** requires scanning full market history against the strategy — needs the same OHLC access.
- **Part 3 — 50-trade OHLC/candle-alignment/split audit:** pending same access.

These are **coverage gaps, not defects.** I found no evidence of error in them; I simply could not complete the numeric proof today.

---

## 5. 🟡 Medium / Minor findings

- **M1 — Intraday repaint:** the cron runs a canonical **post-close** scan (~15:45 IST, [scanner_cron.py:105](scripts/scanner_cron.py#L105)) but also optional intraday refreshes on a forming candle. A flip shown intraday can vanish by close. The code itself documents "the canonical, confirmed-at-close result is the post-close run." For public display, label intraday results as provisional or serve only the post-close snapshot.
- **M2 — Targets are 3R, not structural:** every target = entry + 3×risk (§2.3). Honest, but should be disclosed — "target" is a fixed reward multiple, not a resistance projection.
- **M4 — Stop outlier:** across 200 research picks, stop distance ranged 0.1%–8.0% (median 8.0%). A **0.1% stop** is almost certainly a bad row (entry≈SL) — investigate that symbol; such a stop is untradeable.
- **Survivorship/selection context (Part 9):** of 200 research picks, **120 EXPIRED** (never triggered) and 14 cancelled; only 17 resolved to target/stop. The "track record" is dominated by unfilled ideas — the resolved-only stats are a small, self-selected subset. Disclose the denominator.
- **Look-ahead (Part 9):** promotion sets entry/stop/target at signal time and tracks forward; structure-exit uses a live 200-DMA ([position_tracking_service.py:134](services/position_tracking_service.py#L134)). No future-candle leakage found in the promotion→journal path. Post-close scan uses the last *complete* bar.

---

## 6. Final Scorecard (evidence-based, /10)

| Dimension | Score | Basis |
|---|---|---|
| Scanner Accuracy | 8 | pipeline clean; 20/20 live rows score/filter/tier consistent; repaint risk (M1) |
| Indicator Accuracy | 8 | canonical math + 11/11 unit tests; live recompute pending (C2); EMA-window note (M3) |
| Promotion Logic | 8 | best-ranked not first; re-entry guard live; dual ranking keys |
| Target Accuracy | 8 | RR 1:3 exact, verified; mechanical not structural (M2) |
| Stop Accuracy | 7 | 5% cap verified; one 0.1% outlier (M4) |
| Confidence Accuracy | 4 | in-range, but **non-predictive in sample** (C1) |
| Portfolio Accuracy | 8 | stats reconcile exactly; churn fixed; cap draining |
| Analytics Accuracy | 9 | journal ↔ stats API exact reconciliation |
| Market Data Integrity | 6 | code correct (Wilder ATR, post-close bar); **not re-verified vs Zerodha this session** (C2) |
| Bias Risk | 6 | post-close good; repaint + survivorship + tiny sample |
| Production Readiness | 7 | correct & consistent where checkable; gaps C1–C3 to disclose/close |

---

## 7. Final Launch Verdict

**Technically correct: YES, where verifiable.** Every calculation I could independently reproduce — scanner scoring, filters, tiers, RR, and all historical statistics — reconciled **exactly**, and the indicator math is canonical and unit-tested. I found **zero mathematically incorrect recommendations** in the verifiable set.

**Statistically honest: NOT YET.** The confidence score does not predict outcomes (C1), the sample is tiny and churn-distorted (C3), and the public "track record" hides a 120/200 expiry denominator. These are disclosure/credibility issues, not code bugs.

**Fully verified: NO.** The live-OHLC numeric re-verification (C2) is pending an active Kite session and must be run before claiming end-to-end indicator correctness against Zerodha.

**Recommendation:** safe to launch the *reporting/UI* as-is (already hardened). Before promoting the engine's *predictive* claims to the public: (1) stop presenting confidence as reliability until recalibrated, (2) disclose sample size + expiry denominator + "3R target" + intraday-provisional labeling, (3) complete the C2 live-OHLC re-verification on the next trading day. None require strategy changes — only honesty in presentation and one verification pass.

---

## 8. Point-in-Time Integrity — "would an independent trader at that timestamp agree?"

This is the institutional "is the engine cheating with future information?" test. It splits into a **structural (code) proof** — completed now — and a **numeric reproduction** — staged for the live-OHLC pass.

### 8.1 Structural proof (completed, no token needed) — NO future-data path found
Traced every data read in scanner → recommendation → promotion → journal:

- **Scanner** ([runner.py:84-99](services/scanners/runner.py#L84-L99)): computes on `candles[-1]` as the signal bar; candles are historical bars up to the run time — future bars do not exist. In the **post-close** canonical run the last bar is the completed session. ✅ no future candle.
- **Feedback/confidence bonus** ([feedback_analyzer.py:501](services/feedback_analyzer.py#L501), query [feedback_analyzer.py:268-275](services/feedback_analyzer.py#L268-L275)): reads `portfolio_journal` **at decision time**. In live operation it can only see trades that closed *before* the current signal, so the loop is causally correct (past → present). The **displayed** `confidence_score` is persisted at generation, not recomputed on read — so no future outcome leaks into a historical recommendation's shown score. ✅
- **Entry/stop/target** are fixed at signal time; the **outcome** (target/stop/structure) is determined purely by *forward* price via the tracker ([position_tracking_service.py:128-138](services/position_tracking_service.py#L128-L138)) — that is the trade playing out, not look-ahead. ✅

**One caveat (already logged as M1):** the *intraday* scanner refresh recomputes on a forming bar, so a within-session conclusion can change before close. That is same-day repaint, not future-day leakage — but it means an intraday screener view is not yet "confirmed." The **post-close** snapshot is point-in-time honest.

**Structural conclusion:** in live operation the engine is **not** structurally capable of using future information; the only reproducibility caveat is intraday repaint (serve/label the post-close snapshot to eliminate it).

### 8.2 Numeric reproduction (STAGED — runs on next trading day)
[scripts/audit_live_ohlc_verification.py](scripts/audit_live_ohlc_verification.py) (read-only, compiles clean) will, once the Kite token is live:
1. Re-fetch Zerodha OHLC for every screener hit and **clean-room recompute** Supertrend/EMA/stop/flip, asserting an exact match to the published row (Step 1 / Parts 2, 3).
2. For sampled recommendations, fetch candles **only up to each pick's `created_at`** and assert (a) the signal was computable then and (b) **no candle dated after the recommendation is used** — the direct numeric proof of §8.1.

Run: `python -m scripts.audit_live_ohlc_verification --pit 10`

---
*Read-only audit. No production code modified. All figures pinned to the 2026-07-05 00:05 IST snapshot; the journal is live and will drift. §2.2 math proven; §4/§8.2 live-OHLC numeric reproduction pending the next Kite session.*
