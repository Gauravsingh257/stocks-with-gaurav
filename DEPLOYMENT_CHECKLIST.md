# Deployment Checklist — Website Live + Kite Live

Use this when the website shows "ENGINE STALE", "Kite Offline", or 502 errors.

---

## 1. Railway Web Service Must Be Running (Fixes 502 + Site Down)

The **web** service serves the API. If it fails, you get 502 and the site won’t work.

### 1.1 Use the Correct Dockerfile and Start Command

- Railway → your project → **web** service → **Settings** → **Build**.
- Set **Dockerfile Path** to: `Dockerfile`  
  (not `Dockerfile.engine`).
- **Fix PORT / start command (pick one):**
  - **Option A:** Set **Config file path** to `railway-web.toml` (Deploy / General). Then clear **Start Command** if set.
  - **Option B:** Set **Start Command** to exactly:  
    `/bin/sh -c "exec python scripts/start_web.py"`  
    (The shell runs our script, which reads PORT from the environment.)
- Do **not** use `uvicorn ... --port $PORT` as Start Command — Railway runs in exec form so `$PORT` is not expanded and you get "Invalid value for '--port': '$PORT'".
- **Save**. Trigger a **Redeploy**.

### 1.2 Confirm Deployment Succeeded

- **Deployments** tab → latest deployment must be **Successful**.
- If you see **"The executable 'uvicorn' could not be found"** → Dockerfile path is wrong (see 1.1).

### 1.3 Find Your Backend URL

**Where to get it:**

1. Open **https://railway.app/dashboard** and select your project.
2. Click the **web** service.
3. Go to **Settings** → **Networking** (or **Domains**).
4. Copy the generated URL (e.g. `https://web-production-xxxxx.up.railway.app`).
5. Put it in `.go_live_config` as: `BACKEND_URL=https://that-url`

Or run: `.\scripts\get_railway_web_url.ps1` (if Railway CLI is linked).

### 1.4 Test Backend

Open in browser (use **your** URL from step 1.3):

```
https://YOUR-WEB-URL/health
```

You should see: `{"status":"ok","service":"smc-dashboard"}`.

Then open `https://YOUR-WEB-URL/health/kite` — you should see `kite_api_key_set: true`, `kite_access_token_set: true`, `kite_ready: true`.

---

## 2. Kite on the Website (Charts + Live Data)

Charts and Kite status come from the **web** service. It needs both env vars.

### 2.1 Set Variables on Web Service

- Railway → **web** service → **Variables**.
- Add or update:
  - `KITE_API_KEY` = your Zerodha API key.
  - `KITE_ACCESS_TOKEN` = current token (from `access_token.txt` or after running `zerodha_login.py`).

### 2.2 Update Token Daily

Run **`go_live.bat`** each morning. It updates `KITE_ACCESS_TOKEN` on both **engine** and **web**.

### 2.3 Add KITE_API_KEY to .go_live_config (One-Time)

So `go_live.bat` can push the API key to both services:

- Edit `.go_live_config`.
- Add: `KITE_API_KEY=your_zerodha_api_key`
- Next time you run `go_live.bat`, it will set `KITE_API_KEY` on engine and web.

---

## 3. Vercel (Frontend) Must Point to Your Backend

The site (stockswithgaurav.com) is the frontend on Vercel. It must know your backend URL.

### 3.1 Set BACKEND_URL on Vercel

- Vercel → your project → **Settings** → **Environment Variables**.
- Add:
  - **Name:** `BACKEND_URL`
  - **Value:** `https://web-production-1eabc.up.railway.app`  
    (replace with your Railway web URL from **Settings → Domains**).
- Redeploy: **Deployments** → ⋮ on latest → **Redeploy**.

### 3.2 Optional: Root Directory

- **Settings** → **General** → **Root Directory**: `dashboard/frontend`  
  (if your Next.js app is in that folder).

---

## 4. Why "ENGINE STALE" Shows

- **Engine** and **web** are two separate services on Railway.
- The **web** service does not run the trading engine; it only serves the API.
- So the dashboard cannot see the engine’s heartbeat and always shows **ENGINE STALE** unless you run the engine in the same process (e.g. locally with `run_dashboard.bat`).

**What works:**

- **Charts** and **Kite** status depend on the **web** service and its `KITE_*` variables. Fix steps 1 and 2.
- **Engine** on Railway runs the strategy and Telegram; it does not change the "ENGINE STALE" label on the site unless you later add a heartbeat from engine → backend.

---

## 5. Quick Verification

Run (PowerShell, from project root):

```powershell
.\scripts\check_backend_health.ps1
```

Or manually:

1. Open `https://YOUR-RAILWAY-WEB-URL/health` → expect `{"status":"ok"}`.
2. Open `https://YOUR-RAILWAY-WEB-URL/health/kite` → expect `kite_ready: true`.
3. Visit stockswithgaurav.com → Charts page should load and show "Kite" as online when the above are OK.

---

## 6. Summary

| Issue | What to check |
|-------|----------------|
| 502 / site down | Web service uses `Dockerfile`, deployment successful, `/health` returns OK. |
| Kite Offline | Web service has `KITE_API_KEY` and `KITE_ACCESS_TOKEN`, `/health/kite` shows `kite_ready: true`. |
| ENGINE STALE | Expected when engine and web are separate; fix 502 and Kite first. |
| Trade sync 502 | Backend was down; retry `sync_trades_to_cloud.ps1` after step 1 is fixed. |
