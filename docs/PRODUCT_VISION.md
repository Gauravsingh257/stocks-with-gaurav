# PRODUCT VISION — Stocks With Gaurav (CANONICAL NORTH-STAR)

> This document is the **single source of truth for what the platform is
> for**. Every phase, design doc, and code change is judged against it.
> It is product intent, not an implementation plan — the implementation
> plan that serves it is [CANONICAL_ARCHITECTURE.md](CANONICAL_ARCHITECTURE.md)
> (TASK 10 roadmap) and the per-phase G2 docs. Authored by Gaurav,
> folded into the repo 2026-05-17 as the canonical reference.

## What the platform IS

**A PLANNED-EXECUTION TRADING OPERATING SYSTEM.**

It is **not**: a random stock screener · a noisy AI recommendation
system · a fixed-slot recommendation engine · an instant-entry signal
generator · an "AI stock predictor".

It **is**: *"Professional Trading Operating System."*

## Core philosophy

```
FIND → PLAN → WAIT → ACTIVATE → TRACK → ANALYSE
```
NOT: instantly recommend random trades.

The edge comes from **proper market selection · proper sector selection
· proper stock selection · proper planned execution · proper risk
management · proper lifecycle management** — NOT indicator noise or
forced stock generation.

> **The core edge = PLANNED EXECUTION + RISK DISCIPLINE.**
> Not constant stock prediction.

## Final system flow (canonical)

```
MARKET REGIME
   ↓   bullish / bearish / sideways · breadth · momentum env
SECTOR STRENGTH
   ↓   strongest vs weak · rotation · institutional · macro/news
BEST STOCKS
   ↓   strongest stocks in strongest sectors
QUALITY FILTER
   ↓   liquidity · quality · relative strength · volatility/momentum quality
PLANNED ZONE GENERATION
   ↓   support/resistance · OB · FVG · liquidity · demand/supply
   ↓   → planned entry · SL · target · invalidation · RR   (NO instant entry)
MONITORING            ("Monitoring / Possible Entry" — WAIT for the zone)
   ↓
ENTRY ACTIVATION      (only at planned entry ± tolerance AND confirmation)
   ↓
LIVE TRACKING         (RR · lifecycle · structure · position tracking)
   ↓
TRADE COMPLETION      (SL | target | invalidation)
   ↓
ANALYTICS / HISTORY   (entry/exit/RR/lifecycle/screenshots/setup/regime/
   ↓                    sector/execution quality → research+backtest DB)
RECYCLE               (engine continuously scans again)
```

Worked example (the defining behaviour): *SBI CMP = 100, planned entry =
95 → the system **waits for 95**, it does **not** mark the trade active
immediately.*

## The three engines

| Engine | Holding | Selection brain | Execution |
|---|---|---|---|
| **SWING** | 1 week → 6 months | strong market → strong sector → strongest stock | planned zones + RR + state machine (NOT instant scanner entries) |
| **INTRADAY / MTF** | intraday → 1–2 weeks | strong sectors → strongest stocks | ORB · VWAP reclaim · liquidity sweeps · momentum continuation · volume expansion — planned entries only |
| **LONGTERM** | 6 months → 2–3 years | macro trends · sector cycles · institutional accumulation · earnings quality · business strength · leadership sectors | **NOT daily SMC stretched longer** |

## Risk engine (canonical, central)

The **F-Risk engine is canonical**: position sizing · sector exposure
limits · max-drawdown protection · max concurrent positions · capital
allocation rules. Risk engineering stays central — it is the validated,
robust component (proven in [ALPHA_BASELINE.md](ALPHA_BASELINE.md)).

## Dynamic inventory (hard principle)

There must **NEVER** be fixed 10/20/25 stock slots. Opportunity count is
**dynamic** — could be 0 / 2 / 7 / 15. The only rule: **high quality.**
(Implication: the slot machine — `MAX_*_SLOTS` / `MIN_*_SCAN_K` — must be
removed. It can only be removed *after* the dynamic state-machine output
is live and proven — see CANONICAL_ARCHITECTURE.md TASK 9 / G2-8.)

## Non-negotiable development rules

1. NO fake recommendations
2. NO forced stock slots
3. NO instant entries
4. NO ungated fallback systems
5. NO over-engineered AI clutter
6. Quality > quantity
7. Planned execution first
8. State-machine architecture is canonical
9. Risk-first architecture
10. Full lifecycle tracking mandatory

## Final product goal

> Build a **clean, high-trust, planned-execution, risk-aware,
> lifecycle-driven, professional trading operating system.**

## How this is enforced (auditability)

- Implementation roadmap & per-phase reversibility: see
  [CANONICAL_ARCHITECTURE.md](CANONICAL_ARCHITECTURE.md) TASK 10.
- Vision element → delivering phase: see the **Vision → Phase
  Traceability Map** appended to CANONICAL_ARCHITECTURE.md.
- Edge claims must be backtest-proven against
  [ALPHA_BASELINE.md](ALPHA_BASELINE.md) before any live flip.
- Every behavioural change: flag-gated, default-OFF, shadow-first,
  reversible, no synthetic data, cloud-only, live index engine untouched.

**This document does not change behaviour. It is the bar everything else
is measured against.**
