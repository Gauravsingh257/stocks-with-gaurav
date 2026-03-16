# Everything on Cloud — Full Checklist

Get every service running on the cloud so you can **switch off your laptop**.

---

## 1. Architecture (all cloud)

```
[Vercel]          Frontend (Next.js)
    │
    └─────────────► [Railway – web]   Dashboard API (FastAPI + WebSocket)
                            │
                            ├────────► [Railway – Redis]   Lock, heartbeat, cache, Kite token
                            │
                            └────────► [Railway – engine]  Trading engine (smc_mtf_engine_v4.py)
                                            │
                                            └────────► Telegram alerts
```

| # | Service | Where | Purpose |
|---|---------|--------|---------|
| 1 | **Frontend** | Vercel | Website, UI, calls backend |
| 2 | **Dashboard API** | Railway (web) | Charts, health, snapshot, Kite, WebSocket |
| 3 | **Redis** | Railway | Lock, heartbeat, engine_version, cache, Kite token |
| 4 | **Trading engine** | Railway (engine) | Scans, signals, Telegram, 24/7 loop |

---

## 2. Step-by-step: take everything online

### 2.1 Railway project (one project, multiple services)

1. Go to [railway.app](https://railway.app) → your project.
2. You should have **at least 3 services** (or 2 if Redis is a plugin):
   - **web** (or “Dashboard API”) — FastAPI
   - **engine** (or “Engine worker”) — smc_mtf_engine_v4.py
   - **Redis** — database (or add via + New → Database → Redis)

---

### 2.2 Service 1 — Web (Dashboard API)

| Setting | Value |
|--------|--------|
| **Source** | Same GitHub repo, `main` branch |
| **Config file** | Leave blank (uses root `railway.toml`) |
| **Build** | Dockerfile = `Dockerfile` (default) |
| **Start command** | `python scripts/start_web.py` |
| **Networking** | Generate domain → e.g. `https://web-production-xxxx.up.railway.app` |

**Variables (required):**

| Variable | Value |
|----------|--------|
| `REDIS_URL` | Same Redis URL (from Redis service → Connect) |
| `KITE_API_KEY` | Your Zerodha API key |
| `KITE_ACCESS_TOKEN` | From zerodha_login.py (refresh daily) |
| `PORT` | Set by Railway (do not override unless needed) |

**Optional:** `KITE_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`, `DATA_DIR`, etc. (see PRODUCTION_SETUP.md).

**Verify:**  
`curl https://<your-web-url>/health` → `{"status":"ok","service":"smc-dashboard"}`

---

### 2.3 Service 2 — Engine (trading engine)

| Setting | Value |
|--------|--------|
| **Source** | Same GitHub repo, `main` branch |
| **Config file** | `railway-engine.toml` |
| **Build** | Dockerfile = `Dockerfile.engine` |
| **Start command** | `python smc_mtf_engine_v4.py` |
| **Networking** | No public domain needed |

**Variables (required):**

| Variable | Value |
|----------|--------|
| `REDIS_URL` | **Same as web** (same Redis) |
| `KITE_API_KEY` | Same as web |
| `KITE_ACCESS_TOKEN` | Same as web (refresh daily) |
| `TELEGRAM_BOT_TOKEN` | For alerts |
| `TELEGRAM_CHAT_ID` | For alerts |

**Verify:**  
Railway → engine → **Logs** → you should see:  
`Starting SMC trading engine...` → `Engine lock acquired (Redis)` → `Heartbeat thread started`

---

### 2.4 Redis

- If you added Redis via Railway: **Variables** or **Connect** tab gives you **REDIS_URL**.
- Use that **exact** URL in both **web** and **engine** services.
- Engine uses it for: lock, heartbeat, engine_version, engine_last_cycle, signal dedupe.  
  Web uses it for: cache, optional Kite token storage.

---

### 2.5 Frontend (Vercel)

| Setting | Value |
|--------|--------|
| **Repository** | Same GitHub repo |
| **Root directory** | `dashboard/frontend` (or as in vercel.json) |
| **Framework** | Next.js |

**Variables:**

| Variable | Value |
|----------|--------|
| `BACKEND_URL` | `https://<your-railway-web-url>` (no trailing slash) |
| `NEXT_PUBLIC_BACKEND_URL` | Same as BACKEND_URL |
| `NEXT_PUBLIC_WS_URL` | `wss://<your-railway-web-url>/ws` |

**Verify:**  
Open your site (e.g. stockswithgaurav.com) → dashboard loads, no 502.

---

## 3. Daily: Kite token refresh

Token expires ~daily. Either:

- **URL method:** Open `https://<your-site>/api/kite/login` → log in → token stored in Redis (needs REDIS_URL + KITE_API_SECRET + callback whitelist).
- **Env method:** Run `python zerodha_login.py` locally → copy access_token → Railway → **web** and **engine** → Variables → set `KITE_ACCESS_TOKEN` → Save. Redeploy engine (and web if you want) or wait for next request.

---

## 4. Final checklist (all on cloud)

- [ ] **Web** — Railway service running, `/health` returns 200, same REDIS_URL as engine.
- [ ] **Engine** — Railway service running, config = `railway-engine.toml`, logs show “Engine lock acquired”, “Heartbeat thread started”.
- [ ] **Redis** — One instance, REDIS_URL set on both web and engine.
- [ ] **Frontend** — Vercel deployed, BACKEND_URL and NEXT_PUBLIC_WS_URL set, site loads.
- [ ] **Kite** — KITE_API_KEY and KITE_ACCESS_TOKEN set on both web and engine; refresh token daily.
- [ ] **Telegram** — TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID set on engine (and optionally web if needed).

When all are done → **everything is on the cloud; you can switch off your laptop.**

---

## 5. Repo files used for cloud

| File | Used by |
|------|--------|
| `railway.toml` | Web service (default config at repo root) |
| `railway-engine.toml` | Engine service (set “Config file path” to this) |
| `Dockerfile` | Web build |
| `Dockerfile.engine` | Engine build |
| `scripts/start_web.py` | Web start command |
| `smc_mtf_engine_v4.py` | Engine start command |
| `requirements-railway.txt` | Web deps |
| `requirements-engine.txt` | Engine deps |

No local scripts or localhost are required for production; all of the above run on Railway/Vercel.
