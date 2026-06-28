# Scanner Suite — Architecture & Implementation Plan

**Goal:** Add a "Screeners" section to stockswithgaurav.com hosting multiple technical scanners
(starting with Supertrend(10,3) flip + EMA10), that:
1. **Never hangs, lags, or slows the site** — even with many concurrent users.
2. Returns **identical, reproducible** results for every user at a given point in time.
3. Surfaces **high-probability** setups (confluence-scored & ranked), not raw signal noise.
4. Scales to **N scanners** with near-zero marginal infra cost.

---

## 1. The One Idea That Makes This Safe: Producer / Consumer Split

The website must **never** call Kite or run a scan on a user click. Kite is hard-capped at
**3 requests/sec per API key**; a full-universe scan = ~2,000 calls = ~13 min. If users
triggered that live, the site would melt and the token would get rate-limited.

So we split into two completely decoupled halves:

```
┌────────────────────── PRODUCER (writes) ──────────────────────┐
│  Runs in the ENGINE container (already has Kite auth + a loop) │
│                                                               │
│  Scheduler tick ─► fetch FULL universe OHLC  ONCE             │
│        (daily + weekly, shared across ALL scanners)          │
│            └─► run every scanner on the shared dataset        │
│                  └─► confluence-score + rank                  │
│                        └─► write result → Redis snapshot key  │
└───────────────────────────────┬───────────────────────────────┘
                                 │  Redis (already shared)
┌───────────────────────────────┴── CONSUMER (reads) ───────────┐
│  FastAPI route  GET /api/screeners/{name}                     │
│     └─► read Redis snapshot key  (NO Kite, NO compute)        │
│           └─► return JSON  (sub-millisecond, cached)          │
│                 └─► Next.js /screeners page renders table     │
└───────────────────────────────────────────────────────────────┘
```

**Consequence:** a user click is a single Redis GET (~0.2 ms). 1 user or 10,000 users —
identical, instant, and the load on Kite is **constant** (it never changes with traffic).
This is the same pattern your `engine:snapshot` / `discovery` snapshots already use.

---

## 2. Why Results Are Identical For Everyone

- Scans are **pure deterministic math** (Supertrend/EMA on OHLC) — same candles in → same output out.
- Everyone reads the **same Redis snapshot**, written once per scheduler tick.
- Each snapshot is **stamped** with `as_of` (the candle close timestamp) and `computed_at`.
- The UI shows *"Signals confirmed at close — 26 Jun 2026"* so the meaning is unambiguous.
- Fixed lookback window (200 daily / 150 weekly bars) → Supertrend's recursive seed is stable.

**Intraday behavior (chosen policy):** signals are **confirmed-at-last-close** by default. An
optional "live (forming candle)" toggle can be added later, clearly labeled as provisional.

---

## 3. The Accuracy Layer (High-Probability, Not Raw Signal)

A raw Supertrend flip is a coin-flip-plus edge. We stack confluence to lift the hit rate,
then **rank**. Every scanner output row carries a transparent `quality_score` (0–100):

| Factor | Why it raises hit probability | Weight |
|---|---|---|
| **Trend stack** (close > EMA20 > EMA50) | aligns signal with higher-TF trend | high |
| **Volume confirmation** (flip-bar vol vs avg) | institutional participation, not drift | high |
| **52-week positioning / proximity to high** | breakouts continue; basing has runway | high |
| **Momentum** (1m / 3m return) | already-moving names follow through | med |
| **Liquidity floor** (min avg volume) | tradeable, low manipulation/slippage | gate |
| **ATR-based risk-to-stop** | filters over-extended entries | med |

- **Hard gates** (reject outright): below liquidity floor, price < ₹X, illiquid/SME junk.
- **Soft score**: composite used to **rank** and to tier rows into 🟢 Quality / 🟠 Momentum / 🔴 Speculative.
- Output is **always sorted best-first**; UI can default to top N.

> **Honest framing (important for trust & liability):** No scanner "guarantees" a target hit.
> What we *can* do is maximize the probability stack and present it transparently. We back this
> with a **validation harness** (§7) so any "hit-rate" claim on the site is data-backed, not marketing.

---

## 4. Component Breakdown (real file paths)

### Producer (engine side)
| File | Role |
|---|---|
| `services/scanners/data_layer.py` | Fetch full NSE universe OHLC **once** (daily+weekly), cache in Redis `ohlc:bulk:{tf}` (short TTL). Rate-limit-aware (3/sec, threaded, retry). |
| `services/scanners/indicators.py` | Pure functions: `supertrend()`, `ema()`, `atr()` — unit-tested, deterministic. |
| `services/scanners/registry.py` | A `SCANNERS` list of scanner definitions (name, timeframe, signal fn, gates, score fn). **Add a scanner = add one entry here.** |
| `services/scanners/runner.py` | Orchestrator: load shared OHLC → run every scanner → score+rank → write snapshot. Called by scheduler. |
| `services/scanners/scoring.py` | The confluence `quality_score` model (§3). |

### Scheduler (where the producer runs)
- Reuse the **engine `while True` loop** in `smc_mtf_engine_v4.py` (already authed + market-hours aware),
  adding a cadence-gated `run_scanners()` call — **OR** a dedicated lightweight Railway cron worker
  (`scripts/scanner_cron.py`) if we want it decoupled from the trading engine. *(Decision in §11.)*

### Consumer (web side)
| File | Role |
|---|---|
| `dashboard/backend/routes/screeners.py` | `GET /api/screeners` (list available), `GET /api/screeners/{name}` (read snapshot). Pure Redis read; reuses `redis_endpoint_cache.serve_cached_endpoint` + LKG fallback. |
| register in `dashboard/backend/main.py` | `app.include_router(screeners_router)` |
| `dashboard/backend/rate_limit.py` | already exists — apply per-IP limit to the endpoint as defense-in-depth. |

### Frontend
| File | Role |
|---|---|
| `dashboard/frontend/src/app/screeners/page.tsx` | New section: scanner picker + sortable/filterable results table, "as of close" stamp, score tiers, CSV export. |
| `dashboard/frontend/src/app/api/...` | thin proxy to backend (matches existing sections). |
| nav entry | add "Screeners" to the dashboard nav. |

---

## 5. Redis Key Design

| Key | Contents | TTL |
|---|---|---|
| `ohlc:bulk:daily` / `ohlc:bulk:weekly` | shared universe OHLC for a tick (producer-internal) | 1–2h |
| `scanner:{name}:{tf}` | **live** ranked result snapshot (JSON: rows + meta) | 25h |
| `scanner:{name}:{tf}:lkg` | last-known-good fallback | 7d |
| `scanner:index` | list of available scanners + last `computed_at` | 25h |

Snapshot JSON shape:
```json
{
  "scanner": "supertrend_flip", "timeframe": "1D",
  "as_of": "2026-06-26T15:30:00+05:30", "computed_at": "...",
  "universe_size": 2079, "hits": 31,
  "rows": [ { "symbol": "...", "close": ..., "quality_score": 81.6,
             "tier": "quality", "stop": ..., "metrics": {...} } ]
}
```
Non-empty **write gate** (never overwrite a good snapshot with an empty/errored one) +
**last-known-good** fallback — both already implemented in `redis_endpoint_cache.py`. Reuse them.

---

## 6. Scheduling Cadence

| Window (IST) | Action | Rationale |
|---|---|---|
| **15:45 (after close)** | full daily scan; weekly scan on Fridays | confirmed candles = the canonical result |
| **09:30 → 15:30, every 15 min** *(optional)* | refresh daily as "provisional/live" | for users who want intraday; clearly labeled |
| **Weekend** | one weekly recompute | weekly candle finalized Friday |

One scheduled run fetches OHLC **once** and feeds **all** scanners → cost is independent of scanner count.

---

## 7. Accuracy Validation Harness (so claims are real)

Before a scanner goes live, run it through `scripts/scanner_backtest.py`:
- Replay the signal historically (e.g. last 2 years of weekly/daily flips).
- Measure **forward returns** at +1w/+1m/+2m, **hit-rate to a target** (e.g. % reaching +10% before stop),
  win/loss, avg R, max drawdown — **per score tier**.
- Output a one-page report. We only market hit-rates that the backtest supports.
- This directly honors the "no curve-fitting / validate before trust" principle in the engine.

This also lets us **tune the score weights** on evidence, and prove that the 🟢 tier really does
out-hit the 🔴 tier (otherwise the tiering is theater).

---

## 8. Scaling To Many Scanners (the registry pattern)

Adding scanner #2…#N is **one entry** in `registry.py`:
```python
SCANNERS = [
  Scanner(name="supertrend_flip", tf="1D",
          signal=supertrend_flip_signal, gates=[liquidity_floor, min_price],
          score=confluence_score, lookback=200),
  Scanner(name="supertrend_flip", tf="1W", ... lookback=150),
  # e.g. EMA crossover, 52w-high breakout, volume spike, RSI reversal, OB/FVG tap...
]
```
All scanners share the **one** OHLC fetch per tick. 10 scanners ≈ same data budget as 1.
Compute is cheap (pure NumPy/list math on already-fetched data).

---

## 9. Performance Guarantees & Load Behavior

| Concern | Guarantee | Mechanism |
|---|---|---|
| Site hangs under load | **No** | user click = 1 Redis GET; no compute, no Kite |
| Slow first paint | **No** | snapshot pre-computed; served from cache/LKG |
| Kite rate-limit blowups | **No** | only the producer hits Kite, on a fixed schedule |
| Concurrent users collide | **No** | reads are shared & idempotent |
| Scan errors blank the page | **No** | non-empty write gate + last-known-good fallback |
| Backend CPU spike | **No** | heavy work runs in engine/cron container, not web |
| Cost grows with scanners | **Minimal** | shared fetch; compute is in-memory math |

The web service stays a thin, fast read layer — exactly like your existing snapshot endpoints.

---

## 10. Failure Modes & Safeguards

- **Stale token / Kite down** → producer skips write, LKG keeps serving last good list with an
  `as_of` stamp so users see it's not fresh. (Mirrors engine's existing token-freshness handling.)
- **Partial fetch** (some symbols error) → scan still completes on what it has; per-symbol errors counted, not fatal.
- **Empty result** → write gate refuses to overwrite a non-empty snapshot.
- **Redis down** → endpoint returns graceful 503 with cached/in-memory fallback (cache.py already does this).
- **Schema versioning** → snapshot carries a `schema_version` so frontend never breaks on changes.

---

## 11. Decisions (LOCKED — 2026-06-28)

1. **Producer host:** ✅ **Separate Railway cron worker** (`scripts/scanner_cron.py`). Fully isolated
   from the live trading engine — scanner load can never slow or risk execution. Uses `config/kite_auth`
   for token (reads same Redis `kite:access_token`).
2. **Intraday "live" mode:** ✅ **Confirmed-at-close only for v1.** Live/forming-candle toggle deferred.
3. **Universe:** ✅ **Curated liquid subset** (F&O + top liquid NSE, ~500 names) as default. Full-universe
   as an opt-in filter later. Need `services/scanners/universe.py` to build/maintain this list
   (e.g. from F&O list + avg-volume floor), refreshed periodically.
4. **Access:** ✅ **Paid tier.** Endpoint + frontend section gated behind entitlement. Reuse existing
   `routes/auth.py` + `user_product.py` / `user_store.py` for the paywall check; unauthorized users get
   a teaser/locked state. **New work item:** entitlement gate on `/api/screeners/*`.
5. **Scanner #1 scope:** ✅ **Supertrend(10,3) flip + EMA10, 1D + 1W only.** Prove the full pipeline,
   then expand via the registry.

---

## 12. Rollout Phases (milestones)

- **Phase 0 — Foundation:** `indicators.py` (+ unit tests), `data_layer.py` shared fetch, `registry.py` skeleton.
- **Phase 1 — Producer:** `runner.py` + `scoring.py`; write `scanner:supertrend_flip:{1D,1W}` snapshots; wire scheduler.
- **Phase 2 — Consumer API:** `routes/screeners.py` + register; Redis read + LKG; rate-limit; contract test.
- **Phase 3 — Frontend:** `/screeners` section, table UI, tiers, "as of close" stamp, CSV export, nav entry.
- **Phase 4 — Validation:** `scanner_backtest.py`; tune score weights; publish honest hit-rate per tier.
- **Phase 5 — Scale:** add scanners #2–#N via registry; optional intraday live mode.

Each phase is independently shippable and reversible; nothing touches live trade logic.

---

## 13. Implementation Kickoff Brief (the "prompt")

> Build the Scanner Suite per `docs/SCANNER_SUITE_PLAN.md`, Phase 0→3.
> - Producer runs in a **separate Railway cron worker** (`scripts/scanner_cron.py`), reusing
>   `config/kite_auth` for token and writing snapshots via the existing
>   `dashboard/backend/redis_endpoint_cache` conventions (non-empty write gate + last-known-good).
> - Scanner #1 = Supertrend(10,3) red→green flip **AND** close > EMA10, on **1D and 1W**, over a
>   **curated liquid NSE universe** (default) with confluence `quality_score` and tiering.
> - Web `GET /api/screeners/{name}` is a **pure Redis read** — no Kite, no compute — with per-IP
>   rate limiting and LKG fallback.
> - Frontend `/screeners` section: scanner picker, sortable/filterable ranked table, score tiers,
>   "confirmed at close" stamp, CSV export.
> - Add unit tests for indicators, a contract test for the API, and `scripts/scanner_backtest.py`
>   to validate hit-rate per tier before any accuracy claim ships.
> - Do not modify live trade-execution logic. Each phase independently shippable.
