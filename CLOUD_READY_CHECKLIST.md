# Cloud-ready checklist — Can I turn off my laptop?

**Last check:** Run audit and `/api/system/health` to confirm.

---

## Services that must be on the cloud

| Service | Where it runs | Status from health / audit |
|--------|----------------|-----------------------------|
| **Dashboard API** | Railway (web) | ✅ Live — health 200, Kite connected, DB connected |
| **Trading engine** | Railway (engine) | ⚠️ Check — engine_status was "offline"; confirm in Railway dashboard |
| **Redis** | Railway | ✅ Used by web + engine (internal URL) |
| **Frontend** | Vercel | ✅ Loads from BACKEND_URL (Railway) |

---

## What works with laptop off

- **Website** (stockswithgaurav.com or your frontend URL) — works; API is on Railway.
- **Charts / OHLC** — work; Kite token is on Railway web.
- **Dashboard UI** — works; no local process needed.

---

## What needs the engine on Railway

- **Live trading loop** (scans, signals, Telegram alerts) — only if the **engine** service is running on Railway.
- If **engine_status** is `offline`, the engine is not writing heartbeat to Redis (engine not running or not connected to same Redis).

---

## Verdict

- **If engine service is running on Railway** (and you see "Engine lock acquired", "Heartbeat thread started" in engine logs):  
  **✅ All services are cloud-hosted — you can switch off your laptop.**

- **If engine service is not running or keeps crashing:**  
  **⚠️ Website/API are cloud-hosted, but the trading engine will not run until the engine service is fixed on Railway.**  
  Then you can switch off the laptop for the site; for 24/7 signals, fix and start the engine on Railway.

---

## Quick self-check

1. Railway → **engine** service → **Deployments** → latest deployment **Running**?
2. Railway → **engine** service → **Logs** → see `Starting SMC trading engine...` and `Engine lock acquired (Redis)`?
3. After a few minutes, call `GET /api/system/health` → is `engine_status` **running** or **stale** (and `engine_live`: true)?

If 1–3 are yes → you can turn off the laptop.
