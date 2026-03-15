# Deploy Backend to Railway

Follow these steps to deploy your FastAPI backend so the live site shows real data.

> **Production:** Backend at Railway + Frontend at Vercel → stockswithgaurav.com

**Website down / 502 / Kite Offline?** → See **[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)** for step-by-step fixes.

---

# Daily: One Command

Run **`go_live.bat`**. Paste the Zerodha `request_token` when prompted. Done.

See **GO_LIVE_README.md** for one-time setup (Railway CLI, config file, engine service).

---

# Backend Deployment (Reference)

---

## Step 1: Push latest code to GitHub

```powershell
cd "path/to/your/trading-algo"   # Your project root
git add .
git commit -m "Add Railway deployment config"
git push origin main
```

---

## Step 2: Create Railway account & project

1. Go to [railway.app](https://railway.app)
2. Sign up with **GitHub**
3. Click **"New Project"**
4. Select **"Deploy from GitHub repo"**
5. Choose **`YOUR_GITHUB_USERNAME/your-repo-name`**
6. Railway will auto-detect Python and start building

---

## Step 3: Configure the deployment

1. Click the new **service** (your backend)
2. Go to **Settings** tab
3. Under **Deploy**, set **Root Directory** to: `./` (leave blank or `.` — we use repo root)
4. **Start Command** is already set via Procfile — no change needed
5. Under **Networking** → **Generate Domain** — click it to get your public URL (e.g. `https://xxx.up.railway.app`)

---

## Step 4: Add environment variables (optional)

In Railway → your service → **Variables** tab:

| Variable | Value | Required? |
|----------|-------|-----------|
| `OPENAI_API_KEY` | Your key (for AI features) | Optional |
| `DATABASE_URL` | (leave default) | No |

No Kite/Telegram keys needed for the dashboard to load — it will show "Connecting to engine" until you run the engine locally or add them later.

---

## Step 5: Connect Vercel frontend to Railway backend

1. Go to [vercel.com](https://vercel.com) → your project
2. **Settings** → **Environment Variables**
3. Add:

| Name | Value |
|------|-------|
| `BACKEND_URL` | `https://YOUR-RAILWAY-URL.up.railway.app` |

*(Use the exact URL Railway gave you — e.g. `https://stocks-with-gaurav-production.up.railway.app`)*

4. **Redeploy** the frontend: Deployments → ⋮ on latest → **Redeploy**

---

## Step 6: Verify

1. Visit **https://www.stockswithgaurav.com**
2. The dashboard should load and show data (or "Connecting to engine" if engine isn't running)
3. Check **https://YOUR-RAILWAY-URL/health** — should return `{"status":"ok"}`

---

## Troubleshooting

**"Invalid value for '--port': '$PORT'" or "uvicorn" not found?**  
- **web** service must use the root `Dockerfile` (not `Dockerfile.engine`).  
- Set **Config file path** to `railway-web.toml` (web service → Settings → Deploy) so the start command runs `python scripts/start_web.py` and reads PORT from env.  
- Clear any custom **Start Command** that contains `$PORT` (Railway runs it without a shell, so $PORT is not expanded).

**Build fails on ta-lib?**  
We use `requirements-railway.txt` (via Dockerfile) to avoid this. If it still fails, check Railway build logs.

**"Kite Offline" on Charts page?**  
The web service needs both `KITE_API_KEY` and `KITE_ACCESS_TOKEN`. Add `KITE_API_KEY` to `.go_live_config` — `go_live.bat` will push it to both engine and web. Or add manually: Railway → web → Variables → `KITE_API_KEY`, `KITE_ACCESS_TOKEN`. Redeploy after adding.

**"Connecting to engine" forever?**  
The dashboard shows live data when your **trading engine** runs locally and pushes state. The backend API runs 24/7 on Railway; the engine runs on your PC during market hours. For now, the site will load with empty/placeholder data until you run the engine.

**WebSocket not connecting?**  
Vercel rewrites `/ws` to your Railway backend. Ensure `BACKEND_URL` in Vercel points to the Railway URL (with `https://`, no trailing slash).

---

# Deploy Engine to Railway (Optional)

Run the SMC engine in the cloud so you don't need to run anything locally. You only need to refresh the Kite token once per day.

## Engine vs Backend

| Service | Role | Runs |
|---------|------|------|
| **Backend** | FastAPI API for the website | 24/7 |
| **Engine** | SMC analysis, signals, Telegram alerts, OI monitor | During market hours (9:15–15:30 IST) |

The engine and backend are **separate services**. The engine sends signals via Telegram; the backend serves the website. For live data on the site, the engine would need to push state to the backend (future enhancement).

---

## Step 1: Add engine as a second service

1. In your Railway project, click **+ New** → **GitHub Repo**
2. Select the **same repo** as your backend
3. Name the service: `engine` (or `smc-engine`)

---

## Step 2: Use Docker build for the engine

1. Click the new **engine** service
2. Go to **Settings** tab
3. Under **Build**:
   - **Builder**: Dockerfile
   - **Dockerfile Path**: `Dockerfile.engine`
4. Under **Deploy**:
   - **Root Directory**: leave blank
5. Under **Networking**: no public domain needed (engine does not serve HTTP)

---

## Step 3: Set environment variables

Go to **Variables** tab and add:

| Variable | Value | Required |
|----------|-------|----------|
| `KITE_API_KEY` | Your Zerodha API key | Yes |
| `KITE_ACCESS_TOKEN` | Your daily token (from zerodha_login) | Yes |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token | Yes |
| `TELEGRAM_CHAT_ID` | Your main chat ID | Yes |
| `SMC_PRO_CHAT_ID` | Pro signals chat (optional) | No |
| `OPENAI_API_KEY` | For AI features (optional) | No |

---

## Step 4: Refresh Kite token daily

The Kite access token expires ~24 hours after login. Each morning before market open:

1. Run locally: `python zerodha_login.py`
2. Copy the new token from the output
3. Railway → engine service → **Variables** → edit `KITE_ACCESS_TOKEN` → paste → **Update**
4. Redeploy: **Deployments** → ⋮ → **Redeploy** (or it may auto-redeploy on variable change)

---

## Step 5: Verify

1. Check **Deployments** → logs for the engine service
2. You should see: `Zerodha Kite Connected` and `✅ Token from KITE_ACCESS_TOKEN env`
3. During market hours, you should receive Telegram signals

---

## Engine Troubleshooting

**Build fails?**  
- Ensure `Dockerfile.engine` and `requirements-engine.txt` exist in the repo
- Check build logs for missing modules

**"Connection Failed" in logs?**  
- Verify `KITE_API_KEY` and `KITE_ACCESS_TOKEN` are set
- Token may be expired — refresh it (Step 4)

**No signals?**  
- Engine runs a 1-minute loop; wait for the first candle close after 9:15 IST
- Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are correct
