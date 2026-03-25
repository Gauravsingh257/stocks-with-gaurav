# Railway 24/7 Safe Architecture — Dashboard API + Engine Worker

This guide deploys the trading system to Railway so the **engine runs continuously** even when the developer’s laptop is off.

---

## Target Architecture

```
Users
  ↓
Frontend Dashboard (Vercel)
  ↓
FastAPI API (Railway — Service 1)
  ↓
Redis (Railway shared)
  ↓
Trading Engine Worker (Railway — Service 2)
  ↓
Telegram alerts
```

- **Service 1 — Dashboard API**: FastAPI for charts, WebSocket, Redis, health.
- **Service 2 — Engine Worker**: Runs `python smc_mtf_engine_v4.py` 24/7 with Redis lock, heartbeat, and safe shutdown.

---

## 1. Railway Project Setup

### 1.1 Create Redis (if not already)

1. Railway project → **+ New** → **Database** → **Redis**.
2. Note the **REDIS_URL** (or set it in Variables for both services).

### 1.2 Service 1 — Dashboard API

1. **+ New** → **GitHub Repo** → select your repo.
2. Name the service: `dashboard` (or `web`).
3. **Settings**:
   - **Build**: Dockerfile path = `Dockerfile` (default).
   - **Deploy**: Start command = `python scripts/start_web.py` (or leave default from `railway.toml`).
   - **Networking**: Generate domain → get `https://xxx.up.railway.app`.
4. **Variables** (same Redis as engine):
   - `REDIS_URL` = your Redis connection URL.
   - `KITE_API_KEY`, `KITE_ACCESS_TOKEN` (for charts/Kite).
   - `PORT` is set by Railway.

### 1.3 Service 2 — Engine Worker

1. **+ New** → **GitHub Repo** → same repo.
2. Name the service: `engine` (or `smc-engine`).
3. **Settings**:
   - **Build**: Dockerfile path = `Dockerfile.engine`.
   - **Deploy**: Start command = `python smc_mtf_engine_v4.py`.
   - **Config file path** (optional): `railway-engine.toml` for restart policy.
   - **Networking**: No public domain needed (engine does not serve HTTP).
4. **Variables** (must match dashboard + engine needs):
   - `REDIS_URL` = **same Redis URL as dashboard** (required for lock + heartbeat).
   - `KITE_API_KEY`, `KITE_API_SECRET` (or use `KITE_ACCESS_TOKEN` from login).
   - `KITE_ACCESS_TOKEN` = daily token from `zerodha_login.py`.
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
   - Optional: `SMC_PRO_CHAT_ID`, `OPENAI_API_KEY`.

---

## 2. Behaviour Implemented in Code

### 2.1 Single engine instance (Redis lock)

- On start, the engine tries to acquire Redis key `engine_lock` (NX, TTL 3600s).
- If another instance holds the lock → process exits with message: *"Another engine instance already running"*.
- Lock is refreshed every 5 minutes while running.
- On shutdown (SIGTERM/SIGINT), the lock is released so a new instance can start after restart.

### 2.2 Engine heartbeat (dashboard status)

- Every 30 seconds the engine writes `engine_heartbeat` = current timestamp to Redis.
- The Dashboard API reads this key when the engine is not in-process (e.g. on Railway).
- **ENGINE LIVE**: heartbeat age ≤ 60 seconds.
- **ENGINE STALE**: heartbeat age > 60 and ≤ 120 seconds.
- **ENGINE OFFLINE**: heartbeat missing or > 120 seconds.

### 2.3 Signal deduplication

- Before sending a Telegram signal, the engine checks Redis for `signal_id` (e.g. `ema_NIFTY_1234567890`).
- If the key exists → skip send (prevents duplicate alerts on restart/retries).
- After sending → set Redis key with 1-hour TTL.

### 2.4 Safe shutdown

- SIGTERM (e.g. Railway restart) is caught; engine saves state, persists trades, releases Redis lock, sends shutdown Telegram, then exits.
- Lock TTL ensures that if the process dies without cleanup, the lock expires and another instance can start.

### 2.5 Crash recovery

- Railway **restartPolicyType = ON_FAILURE** restarts the engine on crash.
- Redis lock has TTL; after expiry a new deployment can acquire the lock.

---

## 3. Start Commands Summary

| Service   | Start command                          |
|----------|----------------------------------------|
| Dashboard | `python scripts/start_web.py`          |
| Engine   | `python smc_mtf_engine_v4.py`          |

Both services must have **REDIS_URL** set and use the **same Redis** instance.

---

## 4. Environment Variables (both services)

| Variable             | Dashboard | Engine | Notes                          |
|----------------------|-----------|--------|--------------------------------|
| `REDIS_URL`          | ✅        | ✅     | Same Redis for both            |
| `KITE_API_KEY`       | ✅        | ✅     | Zerodha API key                |
| `KITE_ACCESS_TOKEN`  | ✅        | ✅     | Daily token from login         |
| `TELEGRAM_BOT_TOKEN` | Optional  | ✅     | Required for engine alerts     |
| `TELEGRAM_CHAT_ID`   | Optional  | ✅     | Required for engine alerts     |
| `SMC_PRO_CHAT_ID`    | —         | Optional | Pro signals chat             |
| `PORT`               | Set by Railway | —   | Dashboard only                 |

---

## 5. Final Validation Checklist

After deployment, verify:

- [ ] **Engine runs when laptop is off**  
  Stop local engine; confirm Telegram “Engine Alive” or signals from Railway engine.

- [ ] **Redis lock prevents duplicate engines**  
  Deploy a second engine service (or run locally with same REDIS_URL); second instance should exit with “Another engine instance already running”.

- [ ] **Heartbeat updates every 30 seconds**  
  In Redis (or via dashboard API), check that `engine_heartbeat` key updates roughly every 30s when engine is running.

- [ ] **Dashboard shows engine status**  
  Call `GET /api/system/health` (or open dashboard UI). When engine is running on Railway, expect `engine_status`: `"running"` or `"stale"`; when engine is stopped, `"offline"`.

- [ ] **Telegram alerts send correctly**  
  Trigger a signal (e.g. during market hours); confirm no duplicate messages on engine restart.

- [ ] **Railway restarts worker on crash**  
  In engine logs, force an exit or crash; confirm service restarts automatically (Railway restart policy).

- [ ] **SIGTERM shutdown is clean**  
  Redeploy or stop the engine service; in logs you should see “Engine shutting down safely” and lock release; no duplicate “ENGINE SHUTDOWN” Telegram if you restart quickly (dedupe/lock behaviour as designed).

---

## 6. Daily: Kite token refresh

Kite access token expires ~24h after login:

1. Run locally: `python zerodha_login.py` (or your login script).
2. Copy the new access token.
3. Railway → **engine** service → **Variables** → set `KITE_ACCESS_TOKEN` → **Update**.
4. Redeploy engine (or rely on auto-redeploy if Railway does it on variable change).

Optionally do the same for the **dashboard** service if it uses Kite for charts.

---

## 7. Files Touched for 24/7 Safe Architecture

| File | Purpose |
|------|--------|
| `engine_runtime.py` | Redis lock, heartbeat write, signal dedupe, shutdown helpers |
| `smc_mtf_engine_v4.py` | Acquire/release Redis lock, heartbeat every 30s, lock refresh, signal_id in telegram_send, shutdown releases lock |
| `dashboard/backend/cache.py` | `ENGINE_HEARTBEAT_KEY`, `get_engine_heartbeat_ts()` |
| `dashboard/backend/state_bridge.py` | When engine not in-process, derive `engine_live` / `engine_running` from Redis heartbeat |
| `requirements-engine.txt` | Added `redis>=5.0.0` |
| `railway-engine.toml` | Engine service build + start command + restart policy |
| `RAILWAY_24_7_DEPLOY.md` | This guide |

---

## 8. Testing Steps (short)

1. **Local with Redis**  
   Set `REDIS_URL` locally, run `python smc_mtf_engine_v4.py`. Check logs for “Engine started with Redis lock” and “Engine runtime: Redis connected”. Run a second terminal with same command → should exit with “Another engine instance already running”.

2. **Dashboard engine status**  
   Run dashboard only (no engine). Set `REDIS_URL`. Start engine in another terminal. Call `GET /api/system/health` → `engine_status` should become `running`; stop engine → after 60–120s should become `stale` then `offline`.

3. **Signal dedupe**  
   Send a signal (e.g. EMA) once; trigger same condition again within 1 hour → second send should be skipped (check logs for “Signal dedupe skip”).

4. **Shutdown**  
   Stop engine with Ctrl+C or kill SIGTERM; check logs for “Engine lock released (Redis)” and shutdown Telegram.

After these pass locally, deploy both services to Railway and run the validation checklist above.
