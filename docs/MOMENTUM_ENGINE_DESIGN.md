# Momentum Continuation Engine — Design & Implementation

> **STATUS: LIVE** · workstream: `portfolio` · last substantive update: 2026-07-10
> Design doc for a subsystem that is now LIVE and automated. The 'INERT by default' safety contract in the text is no longer true — the flags are ON.
> Current project state lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

A first-class, **independent** subsystem that complements (never replaces) the
production **SMC Pullback Swing Engine**. It harvests the one gap proven in the
Phase-1 study: strong leaders that keep moving without offering a pullback
(e.g. **LODHA**) — the SMC structural-reject stream (`no_BOS` / `no_liquidity_sweep`
/ `no_order_block`), which is **+10pp richer in fast movers** than the market.

> **Safety contract:** the SMC engine is production-proven and is **not modified**.
> The Momentum Engine is INERT by default (`MOMENTUM_ENGINE_ENABLED=0`,
> `MOMENTUM_SHADOW_ONLY=1`) and nothing in the live trade path calls it until the
> controlled rollout (Phase 12). Every threshold is env-configurable; every
> component is independently reversible.

## Decision flow
```
Universe → Discovery (L1) → Quality (L2) → SMC materializer (L3)
   ├── reachable pullback setup? ──YES──► SMC PULLBACK ENGINE  (unchanged)
   └── NO (structural reject) ──► MOMENTUM ENGINE
          candidate_feed (only layer1_pass=1 & final_selected=0 & structural)
            → router: regime allowed? (TRENDING_UP/SIDEWAYS; never DOWN)
            → metrics (RS, 200DMA, ATR, trend-quality, extension, base…)
            → eligibility (leadership + liquidity + anti-chase gates)
            → entry model (VCP ▸ breakout ▸ shallow-pullback)  [pluggable registry]
            → ranking (Momentum Quality Score, explainable)
            → arm-on-tap PENDING → tap → ACTIVE            [Phase 7+]
            → risk_engine sizing + ATR/structure trail      [Phase 8]
```
**SMC-first is structural:** the engine only ever sees candidates SMC already
rejected, so a stock SMC can trade never reaches it.

## Module map (`services/momentum_engine/`)
| Module | Phase | Responsibility |
|---|---|---|
| `config.py` | 1 | All flags/thresholds, read live from env (defaults = INERT). |
| `models.py` | 1 | Immutable dataclasses (`MomentumCandidate` → `MomentumDecision`). |
| `candidate_feed.py` | 2 | Consume the SMC structural-reject stream (read-only). |
| `metrics.py` | 3 | Pure technical metrics from OHLCV (+NIFTY for RS). |
| `eligibility.py` | 3 | Hard leadership + anti-chase gates. |
| `entry_models.py` | 4 | VCP / breakout / shallow-pullback via an open/closed **registry**. |
| `ranking.py` | 5 | Momentum Quality Score (weighted, penalised, explainable). |
| `router.py` | 6 | Deterministic regime gate. |
| `engine.py` | 6 | Orchestrator + shadow-safe batch `run()` + Redis audit log. |

## Entry philosophy
- **Include:** Volatility-Contraction Breakout (core), volume-confirmed pivot Breakout (core), Shallow-Pullback continuation (the too-shallow-for-SMC case).
- **Reject (by design):** earnings continuation (event/gap risk), trend-acceleration (= late/climax), standalone RS or volume (no timing).
- **Never chase:** every entry is an **arm-on-tap trigger above a base**; base-proximity + extension gates block FOMO; new models register without editing existing code.

## Risk philosophy (Phase 8 — extends `risk_engine`, does not replace it)
Structure-based stop (below the base) capped by ATR-min and a momentum stop-%;
**no fixed target** — trail with the higher of a Chandelier/ATR trail and a rising
20-EMA/higher-low; breakeven after +1R; **fail-fast** on a close back inside the
base; plus the existing `risk_engine` trend-break + max-loss backstop. The SMC
risk profile is untouched.

## Ranking (Momentum Quality Score, 0–100)
Weighted, normalised, **all weights configurable** (`MOM_W_*`): Relative Strength
(primary) · Breakout quality · Volume expansion · Trend quality · VCP tightness ·
Sector leadership — minus an **extension penalty** (anti-chase). Every decision
carries the full component + penalty breakdown.

## Portfolio integration (Phase 7)
One shared Swing book; a **hard momentum sub-allocation cap**
(`MOMENTUM_MAX_POSITIONS`) so it can never crowd out SMC; both engines compete
for slots on a **risk-normalised** rank; positions tagged by `engine` for
independent analytics. Same arm-on-tap lifecycle + `risk_engine`.

## Configuration reference (all env, live-reload)
| Var | Default | Meaning |
|---|---|---|
| `MOMENTUM_ENGINE_ENABLED` | `0` | master flag (OFF) |
| `MOMENTUM_SHADOW_ONLY` | `1` | generate signals only; never promote |
| `MOMENTUM_REGIME_GATE_ENABLED` | `1` | apply the regime gate |
| `MOM_REGIMES_ALLOWED` | `TRENDING_UP,SIDEWAYS` | permitted regimes |
| `MOM_MIN_RS_20D` | `5.0` | min relative strength vs NIFTY (%) |
| `MOM_REQUIRE_ABOVE_200DMA` | `1` | require primary uptrend |
| `MOM_MIN_TURNOVER_CR` | `5.0` | liquidity floor (₹Cr/day) |
| `MOM_MIN_TREND_QUALITY` | `0.55` | min advance linearity (0..1) |
| `MOM_MIN_VOLUME_RATIO` | `1.3` | breakout-day vol / 20d avg |
| `MOM_MAX_EXTENSION_ATR` / `_PCT` | `4.0` / `12.0` | anti-chase extension caps |
| `MOM_CLIMAX_MAX_UP_DAYS` / `_ATR_PCTL` | `6` / `0.90` | exhaustion caps |
| `MOM_MAX_GAP_PCT` | `6.0` | gap-trap cap |
| `MOM_MAX_BASE_PROXIMITY_PCT` | `6.0` | entry must be near the trigger |
| `MOM_ENTRY_MODELS` | `vcp,breakout,shallow_pullback` | enabled models (priority order) |
| `MOM_W_*` | see `config.py` | ranking weights |
| `MOMENTUM_MAX_POSITIONS` | `6` | shared-book sub-allocation cap |
| `MOMENTUM_ALLOCATION_PCT` | `0.0` | live allocation (0 until Phase 12) |

## Implementation status
- ✅ **Phases 1–6** (this PR): package, config, models, candidate feed, eligibility, entry models, ranking, router, orchestrator — **isolated, unit-tested (13 tests), inert, deployable.** Nothing in the live path imports it.
- ⏳ **Phase 7** portfolio integration · **8** risk extension · **9** analytics · **10** backtest harness · **11** shadow deployment · **12** controlled rollout — subsequent reviewed PRs, each gated on the prior. No live trades until shadow (11) validates against the combined benchmark.

## Research & backtesting framework (`services/momentum_engine/research/`)
Production integration is **frozen** until statistical evidence proves the
combined book beats SMC-alone without degrading its expectancy. The framework
proves/disproves/optimises every component, isolated from the live path:

| Module | Role |
|---|---|
| `models.py` | `SimConfig` (the swept knobs), `SimTrade`, `BacktestResult`, **`ExperimentRecord`** |
| `stops.py` | initial-stop methodologies — structural / atr_multiple / pct_cap / hybrid (registry) |
| `trailing.py` | trailing methodologies — none / atr_chandelier / ema / structure (registry) |
| `simulator.py` | deterministic forward sim: arm→tap→breakeven→trail→failed-breakout→stop→time; returns R + MFE/MAE |
| `metrics.py` | hit-rate, expectancy(R), profit factor, avg-R, DD, MFE/MAE + regime/sector/model **attribution** |
| `experiment_store.py` | durable `momentum_experiments` table — the labelled dataset |
| `backtest.py` | config-driven `run_backtest`, `compare_configs`, `time_split` (OOS), `walk_forward_folds`, `sensitivity` |

**Experiment platform (the compounding advantage).** Every simulated signal is
persisted as an `ExperimentRecord` capturing **why it qualified, why it ranked,
why it entered, why it exited**, plus regime, sector, RS, ATR, extension, VCP
tightness, breakout score, quality score, and the realised R/MFE/MAE outcome.
After thousands of experiments this becomes a labelled dataset the engine is
**re-fit** on — continuous, evidence-based improvement instead of intuition.

**How it answers each research question:** multiple entry models (engine
registry) · stop/trail methodologies (registries) · ranking-weight optimisation
(`SimConfig.ranking_weights` + `compare_configs`) · allocation optimisation
(portfolio-combination configs) · regime/sector analysis (attribution) ·
walk-forward + out-of-sample (`walk_forward_folds`, `time_split`) · parameter
sensitivity (`sensitivity`). **Success gate:** combined > SMC-alone on
risk-adjusted return AND SMC expectancy preserved AND edge regime-consistent.

## Rollback
Instant, no redeploy: `MOMENTUM_ENGINE_ENABLED=0` disables everything; individual
gates (`MOMENTUM_REGIME_GATE_ENABLED`, entry-model list, `MOMENTUM_ALLOCATION_PCT=0`)
each revert independently.
