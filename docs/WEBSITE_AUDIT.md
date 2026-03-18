# stockswithgaurav.com — Deep Website Audit

**Scope:** Full stack (Vercel frontend → Railway web backend → Redis / Engine).  
**Date:** 2026-03-18  
**No code was modified; analysis only.**

---

## 1. Architecture Overview

```
Browser (stockswithgaurav.com)
    │
    ├── REST:  /api/*  ──[Next.js rewrite]──► BACKEND_URL (Railway Web)
    │
    └── WS:    /ws     ──[must be direct]──► NEXT_PUBLIC_WS_URL or NEXT_PUBLIC_BACKEND_URL/ws
                                              (Vercel does NOT support WebSocket upgrade)

Railway Web (dashboard.backend.main)
    ├── state_bridge.get_engine_snapshot()
    │   ├── LIVE:  smc_mtf_engine_v4 in-process (only when engine+web same process)
    │   └── STANDALONE: Redis heartbeat + state_db/JSON fallback (engine on separate Railway service)
    ├── /api/snapshot, /api/system/health, /api/agents/oi-intelligence, etc.
    └── WebSocket /ws → broadcast loop (get_engine_snapshot every 5s)

Railway Engine (run_engine_railway.py)
    ├── Writes: engine_heartbeat, engine_started_at, engine_version, engine_last_cycle (Redis)
    └── Does NOT write: full snapshot, index_ltp, active_trades to Redis

Redis (shared)
    ├── engine_heartbeat, engine_started_at, engine_version, engine_last_cycle ← Engine
    ├── kite:access_token, kite:token_ts ← Login / Engine
    └── ltp:NIFTY, ltp:BANKNIFTY ← Only if dashboard realtime service runs (Kite tick stream)
```

**Critical point:** The **Engine** and **Web** are separate Railway services. The Web backend never imports the engine, so it runs in **STANDALONE** mode. Live trade/signal data comes only from Redis heartbeat + fallback DB, not from the engine’s live state.

---

## 2. What Is Working

| Component | Status | Notes |
|-----------|--------|--------|
| **Site load** | OK | Home redirects to /live; pages render. |
| **REST rewrites** | OK | next.config.ts rewrites /api/* to BACKEND_URL (must be set in Vercel). |
| **Engine status (ON/OFF)** | OK | From Redis `engine_heartbeat`; state_bridge uses it in standalone mode. |
| **Market status (OPEN/CLOSED)** | OK | Client-side IST in MarketCommandBar; backend also has IST market_status. |
| **Signals 0/5** | OK | From snapshot (standalone: 0 from fallback; live would show real count). |
| **Analytics page** | OK | Uses /api/analytics/* and forceSync; data from CSV/DB. |
| **OI Intelligence** | OK | /api/agents/oi-intelligence; can show "Loading" if agent/cache not ready. |
| **CORS** | OK | Backend allows stockswithgaurav.com, www, vercel.app, railway.app. |
| **Health** | OK | /api/system/health returns engine_status, kite_connected, etc. |

---

## 3. Flaws and Bugs

### 3.1 WebSocket Never Connects on Vercel (High)

- **What:** Frontend uses `useEngineSocket()`; if `NEXT_PUBLIC_WS_URL` and `NEXT_PUBLIC_BACKEND_URL` are unset, `getWsUrl()` returns `wss://stockswithgaurav.com/ws`.
- **Why it fails:** Vercel does not support WebSocket upgrade. The request never reaches Railway.
- **Observed:** "Connecting to engine…" and "WebSocket reconnecting — switching to REST polling soon".
- **Fix:** In Vercel, set `NEXT_PUBLIC_WS_URL=https://<railway-web-host>/ws` (or set `NEXT_PUBLIC_BACKEND_URL` so WS URL is derived). Both must use the **Railway Web** host, not the Engine host (Engine only exposes /health, not /ws).
- **Doc:** next.config.ts already states: "Set to https://... on Vercel for direct browser → Railway (required because Vercel does not proxy WebSocket)."

### 3.2 REST Polling Uses Empty BASE When Env Missing (Medium)

- **What:** In `useWebSocket.ts`, `BASE = process.env.NEXT_PUBLIC_BACKEND_URL || ""`. Polling does `fetch(\`${BASE}/api/snapshot\`)`.
- **When BASE is "":** Browser requests `fetch("/api/snapshot")` (same-origin). Next.js rewrites then send it to `BACKEND_URL/api/snapshot`. So polling works **only if** Vercel has `BACKEND_URL` set (server-side rewrite).
- **Risk:** If `BACKEND_URL` is unset, rewrites default to `http://localhost:8000` (next.config), so production would proxy to localhost and fail.
- **Fix:** Ensure Vercel env has `BACKEND_URL=https://<railway-web-url>`. Optional: set `NEXT_PUBLIC_BACKEND_URL` to the same so client-side polling and health use the same base.

### 3.3 NIFTY / BANKNIFTY Show "—" (Medium)

- **What:** Index LTP in the command bar is "—" when no data.
- **Why:** In standalone mode, `_get_index_ltp_from_cache()` reads Redis keys `ltp:NIFTY`, `ltp:BANKNIFTY`. Those are written only by `dashboard/backend/realtime.py` (Kite tick stream). The **Engine** does not write LTP to Redis; it only updates in-memory state. So when Web runs without the engine in-process, LTP stays empty unless the realtime service runs and has Kite credentials.
- **Fix options:**  
  (1) Run realtime service on the Web service and give it Kite token (Redis or env), or  
  (2) Have the Engine publish NIFTY/BANKNIFTY LTP to Redis (e.g. same keys) whenever it fetches LTP.

### 3.4 Stale / Empty Live Data When Engine Is Separate (Medium)

- **What:** In standalone mode, `get_engine_snapshot()` uses:
  - `active_trades` from state_db (SQLite / engine_state)
  - `zone_state` = {}
  - `daily_pnl_r`, `signals_today`, etc. = 0 or defaults
- **Why:** The Engine service does not push snapshot or trades to Redis; it only writes heartbeat/version/last_cycle. The Web service’s state_db is not updated by the Engine.
- **Result:** Dashboard shows "Engine ON" (from heartbeat) but "Signals 0/5", no active trades, zero PnL.
- **Fix (architectural):** Either:  
  (A) Engine periodically writes a minimal snapshot to Redis (e.g. active_trades, signals_today, daily_pnl_r, index_ltp) and state_bridge reads it in standalone mode, or  
  (B) Web service calls Engine’s HTTP API (e.g. /api/status, /api/trades) and merges into snapshot. Today Engine only exposes /health and /api/status (minimal) on its own server.

### 3.5 Kite "OFF" on Dashboard (Medium)

- **What:** Command bar shows "Kite OFF".
- **Why:** `/api/system/health` sets `kite_connected` by loading token (Redis or env) and calling `kite.profile()` on the **Web** service. The Web service uses its own Kite client and token. If the token lives only in Redis and was set by the Engine (or login flow), Web can read it, but if Web has no Redis or different Redis, or token is only on Engine, Web will show Kite OFF.
- **Fix:** Ensure Railway **Web** service has `REDIS_URL` same as Engine, and that login/engine stores token in that Redis. Then Web’s `config.kite_auth` / `dashboard.backend.kite_auth` will see the token and `kite.profile()` can succeed.

### 3.6 OI Intelligence "Loading" or Stale (Low)

- **What:** /oi-intelligence can stay on "Loading OI Intelligence..." or show stale data.
- **Why:** Data comes from `agents.oi_intelligence_agent.generate_snapshot()` or cache. If the agent or market data pipeline fails or is slow, the UI waits or shows old cache.
- **Check:** Ensure backend has Redis and (if needed) Kite/API access for OI; check `/api/agents/oi-intelligence` in Network tab for 503/5xx or slow response.

### 3.7 Analytics "DISCONNECTED" / "ENGINE STALE" (Low)

- **What:** Analytics page can show "DISCONNECTED", "ENGINE STALE", "LIVE · —".
- **Why:** These come from the same snapshot/health as the command bar. If WebSocket is disconnected and REST is failing (wrong BACKEND_URL) or snapshot is standalone (no engine data), status will look disconnected or stale.
- **Fix:** Same as 3.1 and 3.2 (WS + BACKEND_URL); then 3.4 if you want real engine metrics.

### 3.8 Hardcoded Backend URL in RUN_ENGINE_ON_RAILWAY.bat (Low)

- **What:** `CLICK ONCE to START/RUN_ENGINE_ON_RAILWAY.bat` sets `BACKEND=https://web-production-2781a.up.railway.app`.
- **Risk:** If the Railway Web URL changes, the bat file must be updated manually.
- **Fix:** Use an env or config file for the backend URL, or document that this URL must be updated when the service URL changes.

### 3.9 system/health Overwrites engine_status When Worker Stale (Low)

- **What:** In `system.py`, if `worker_status` is "stale" (market_engine last update > 15s), code sets `engine_status = "stale"`.
- **Why:** `MARKET_ENGINE_LAST_UPDATE_KEY` is written by `market_engine.py` (a worker). If you don’t run that worker on Railway Web, this key is never set, so worker_status is treated as stale and can force engine_status to "stale" even when the real engine heartbeat is fresh.
- **Fix:** Only set engine_status from worker_status when the worker is actually in use; or don’t overwrite engine_status when engine_heartbeat is fresh (e.g. prefer Redis engine_heartbeat over worker heartbeat when engine is the source of truth).

---

## 4. Configuration Checklist (Vercel + Railway)

| Variable | Where | Purpose |
|----------|--------|--------|
| **BACKEND_URL** | Vercel (server) | Next.js rewrite target for /api/* and /ws. Must be Railway **Web** URL. |
| **NEXT_PUBLIC_BACKEND_URL** | Vercel (build) | Client-side API base; used for polling and health. Should match BACKEND_URL. |
| **NEXT_PUBLIC_WS_URL** | Vercel (build) | WebSocket URL. Must be `wss://<railway-web-host>/ws`. Required for WS to work. |
| **REDIS_URL** | Railway Web + Engine | Same Redis for token, heartbeat, and (if added) snapshot. |
| **KITE_* / TELEGRAM_* ** | Railway Web (for dashboard Kite/health) and Engine | As per existing setup. |

**Minimal for "Engine ON" and polling to work:**  
Vercel: `BACKEND_URL` = Railway Web URL.  
Optional but recommended: `NEXT_PUBLIC_BACKEND_URL` = same; `NEXT_PUBLIC_WS_URL` = `wss://<same-host>/ws`.

---

## 5. Page-by-Page Summary

| Page | Data source | Working | Issues |
|------|-------------|--------|--------|
| **/** | Redirect | Yes | Redirects to /live. |
| **/live** | useEngineSocket → snapshot | Partial | No snapshot until REST/WS works; then snapshot is standalone (zeros) unless 3.4 fixed. |
| **/analytics** | api.summary(), equityCurve(), etc. | Yes | Depends on BACKEND_URL; shows "DISCONNECTED" if snapshot/health wrong. |
| **/oi-intelligence** | /api/agents/oi-intelligence + WS | Partial | Loading/error if agent or cache fails; WS same as 3.1. |
| **/charts** | state_bridge + Kite OHLC | Partial | Needs Kite on Web service; same as 3.5. |
| **/journal** | /api/journal | Yes | Backend DB; independent of engine. |
| **/research** | /api/research/* | Yes | Backend routes. |
| **/agents** | /api/agents | Yes | Backend. |

---

## 6. Security and Best Practices

- **Secrets:** No secrets in frontend; BACKEND_URL/WS_URL are public (build-time). Token only on backend/Redis.
- **CORS:** Backend allows specific origins; no wildcard for credentials.
- **Rate limit:** RateLimitMiddleware (60 req/min per IP) on backend.
- **Kite hint:** Health endpoint returns a hint for token/redirect URL; no token value is exposed.

---

## 7. Summary Table

| Item | Status | Action |
|------|--------|--------|
| WebSocket on production | Broken | Set NEXT_PUBLIC_WS_URL (and optionally NEXT_PUBLIC_BACKEND_URL) in Vercel to Railway Web. |
| REST API | Works if BACKEND_URL set | Set BACKEND_URL in Vercel to Railway Web URL. |
| Engine ON/OFF | Works | From Redis heartbeat in standalone mode. |
| NIFTY/BANKNIFTY LTP | Missing in standalone | Realtime service or Engine writing LTP to Redis. |
| Live trades / PnL / signals | Stale/zero in standalone | Engine → Redis snapshot or Web → Engine API. |
| Kite ON/OFF | Depends on Web token | Ensure Web has Redis + same token as login/engine. |
| OI Intelligence | Can hang or be stale | Check agent and cache; ensure Redis/Kite for Web if needed. |

---

## 8. Fixes Applied (2026-03-18)

All code fixes below are committed. Remaining action: **set Vercel env vars and redeploy**.

### Code changes:
1. **useWebSocket.ts** — WS never uses same-domain on production; exponential backoff; debug logs.
2. **next.config.ts** — build-time warning when BACKEND_URL missing on Vercel.
3. **api.ts** — clear error log when NEXT_PUBLIC_BACKEND_URL missing.
4. **MarketCommandBar.tsx** — removed `if (!base) return` guard so health/snapshot polls work through rewrites even when NEXT_PUBLIC_BACKEND_URL is unset.
5. **oi-intelligence/page.tsx** — WS only uses localhost, not same-domain; backoff and retry limit added.
6. **engine_runtime.py** — new `write_engine_snapshot()` and `set_index_ltp()` functions; removed duplicate `write_last_cycle()`.
7. **cache.py** — new `ENGINE_SNAPSHOT_KEY` and `get_engine_snapshot_from_redis()`.
8. **state_bridge.py** — standalone mode merges Redis snapshot (trades, PnL, signals, regime, engine_mode, index_ltp).
9. **smc_mtf_engine_v4.py** — writes full snapshot to Redis after each cycle; pushes index LTP on fetch.
10. **system.py** — worker_status no longer overwrites engine_status when worker key is missing (standalone mode).

### Remaining manual action:
Set these in **Vercel** → Environment Variables → redeploy:
```
BACKEND_URL = https://web-production-2781a.up.railway.app
NEXT_PUBLIC_BACKEND_URL = https://web-production-2781a.up.railway.app
NEXT_PUBLIC_WS_URL = wss://web-production-2781a.up.railway.app/ws
```

This document is sufficient for an engineer or AI to reproduce the setup, find the same flaws, and apply the fixes without guessing.
