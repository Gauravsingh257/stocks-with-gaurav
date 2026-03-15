# Production Setup — stockswithgaurav.com

> **Deployment flow:** GitHub → Railway (backend) + Vercel (frontend)  
> Push to `main` → both platforms auto-deploy.

---

## Quick Reference — What Causes Each Error

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| Vercel build fails | No `vercel.json` / wrong root directory | Already fixed — `vercel.json` now in repo root |
| HTTP 502 on all pages | `BACKEND_URL` not set in Vercel | Set env vars (Step 2) |
| Kite Offline / OHLC 502 | `KITE_ACCESS_TOKEN` missing or expired in Railway | Set/refresh token (Step 3) |
| ENGINE STALE badge | Trading engine not running on Railway | Expected — engine runs locally (Step 4) |
| OI Intelligence empty | Engine JSON files not on Railway | Expected without live engine |
| Railway deploy fails | Wrong Dockerfile / missing packages | Fixed — `railway.toml` now in repo root |

---

## Step 1 — Deploy Backend to Railway

### First-time setup
1. Go to [Railway](https://railway.app) → your project → your web service
2. **Settings → Source** → confirm it is connected to your GitHub repo `main` branch
3. **Settings → Config File** → leave blank (Railway will auto-detect `railway.toml`)
4. Click **Deploy**

### Environment Variables (Railway)
Go to **Railway → your service → Variables** and add:

```
KITE_API_KEY          = your_zerodha_api_key
KITE_ACCESS_TOKEN     = (from zerodha_login.py — update daily!)
OPENAI_API_KEY        = sk-...
TELEGRAM_BOT_TOKEN    = (optional)
TELEGRAM_CHAT_ID      = (optional)
DATABASE_URL          = sqlite:///data/dashboard.db
DATA_DIR              = /data
ENGINE_MODE           = AGGRESSIVE
PAPER_TRADING         = 1
LOG_LEVEL             = INFO
PORT                  = 8080
REDIS_URL             = (optional) redis://default:password@host:port — for market data cache
```

- **Rate limit:** 60 requests/minute per IP (health endpoints excluded).
- **Cache:** If `REDIS_URL` is set, OHLC and OI snapshots are cached for 5s; API reads from cache so Kite is not hit repeatedly.

> **Important:** After setting variables, click **Deploy** to restart with new env.

### Add Persistent Volume (prevents DB loss on redeploy)
1. Railway → your service → **Settings → Volumes**
2. Click **Add Volume** → Mount path: `/data`
3. Add env var: `DATA_DIR=/data`
4. Redeploy

### Verify Railway is healthy
Visit: `https://<your-railway-url>.up.railway.app/health`  
Should return: `{"status": "ok", "service": "smc-dashboard"}`

---

## Step 2 — Deploy Frontend to Vercel

### First-time setup
1. Go to [Vercel](https://vercel.com) → your project
2. **Settings → General → Root Directory** → set to `dashboard/frontend`
   *(Or Vercel will auto-read from `vercel.json` in the repo root — already set)*
3. **Framework Preset** → Next.js (auto-detected)

### Environment Variables (Vercel)
Go to **Vercel → Project → Settings → Environment Variables** and add:

```
BACKEND_URL              = https://<your-railway-url>.up.railway.app
NEXT_PUBLIC_BACKEND_URL  = https://<your-railway-url>.up.railway.app
NEXT_PUBLIC_WS_URL       = wss://<your-railway-url>.up.railway.app/ws
```

> Replace `<your-railway-url>` with the actual Railway domain from  
> Railway → your service → Settings → Domains.

> **After setting variables → click Redeploy (clear cache).**

---

## Step 3 — Kite Token (Daily Refresh)

The Kite access token expires every day at ~05:00 IST.

### Daily refresh workflow
1. Run `zerodha_login.py` on your local PC (or `go_live.bat`)
2. Copy the access token from `access_token.txt` or the console output
3. Railway → your service → Variables → edit `KITE_ACCESS_TOKEN` → paste new token
4. **Optionally** click **Deploy** — token is picked up on next request without restart

### Verify Kite connection
Visit: `https://<your-railway-url>.up.railway.app/api/system/kite-status`  
Should return: `{"kite_ready": true, "token_valid": true, ...}`

---

## Step 4 — Sync Trade Data to Cloud

Trade data lives in `trade_ledger_2026.csv` locally. Push it to production:

```powershell
$env:BACKEND_URL = "https://<your-railway-url>.up.railway.app"
.\sync_trades_to_cloud.ps1
```

---

## Step 5 — Code Changes

After any code change:
```bat
sync.bat
```
This stages, commits, and pushes to GitHub → triggers Railway + Vercel auto-deploy.

---

## Step 6 — Market Data Worker (Optional — Redis + second service)

To avoid hitting Kite on every request, run a **market engine worker** that fills Redis every 5s:

1. **Add Redis** to your project (Railway → New → Database → Redis, or use Upstash).
2. Set **REDIS_URL** on both the **API service** and the **worker service**.
3. Create a **second Railway service** (same repo):
   - Start command: `python scripts/market_engine.py`
   - Variables: `REDIS_URL`, `KITE_API_KEY`, `KITE_ACCESS_TOKEN` (same as API)
4. API will read OHLC and OI from cache; worker keeps cache warm.

Without Redis, the API falls back to in-memory cache and Kite on demand (higher latency, more Kite usage).

## Step 7 — Live Engine (Optional — Railway Worker)

The trading engine (`smc_mtf_engine_v4.py`) runs locally by default.  
"ENGINE STALE" in the TopBar is **normal** for the cloud-only setup.

To run the engine in the cloud:
1. Create a **second Railway service** in the same project
2. Connect the same GitHub repo
3. Start command: `python smc_mtf_engine_v4.py`
4. Add same env vars + `KITE_API_KEY`, `KITE_ACCESS_TOKEN`
5. Add Volume at `/data`

---

## Architecture Summary

```
stockswithgaurav.com (Vercel — Next.js)
      │
      │  NEXT_PUBLIC_BACKEND_URL (direct browser → Railway)
      ▼
Railway Web Service (FastAPI — dashboard backend)
      │
      ├── /health            → health check
      ├── /api/ohlc/*        → Kite OHLC data (needs KITE_ACCESS_TOKEN)
      ├── /api/system/*      → system health / kite status
      ├── /api/analytics/*   → trade analytics
      ├── /api/journal/*     → trade journal
      ├── /ws                → WebSocket broadcast (engine snapshots)
      └── /docs              → Swagger UI
```

---

## Troubleshooting

### "OHLC fetch failed" / "Kite Offline"
→ `KITE_ACCESS_TOKEN` is missing, expired, or wrong in Railway Variables  
→ Run `zerodha_login.py`, copy token, paste into Railway, redeploy

### HTTP 502 / 503 on all API calls
→ `BACKEND_URL` and `NEXT_PUBLIC_BACKEND_URL` not set in Vercel  
→ Follow Step 2 above

### Vercel build fails
→ Ensure `vercel.json` is in the repo root (already committed)  
→ Check Vercel build logs for npm install errors

### Railway deploy fails
→ Check Railway build logs for pip install errors  
→ `railway.toml` in repo root now points to `Dockerfile` which uses `requirements-railway.txt`

### ENGINE STALE
→ Normal when engine runs locally, not on Railway  
→ REST polling fallback activates automatically — all pages still load data
