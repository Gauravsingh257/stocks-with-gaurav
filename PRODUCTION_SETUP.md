# Production Setup — stockswithgaurav.com

## 1. One-Click Sync (Code → GitHub)

Run **`sync.bat`** after any code change. It will:
- Stage all files
- Commit with your message
- Push to GitHub
- Trigger auto-deploy on Vercel + Railway

---

## 2. Trade Data (Journal + Analytics)

**Problem:** Railway DB resets on deploy (ephemeral storage).

**Fix: Persistent storage**

1. In **Railway** → your backend service → **Settings** → **Volumes**
2. Click **Add Volume** → mount at `/data`
3. Add env var: `DATA_DIR=/data`
4. Redeploy

**Sync trades to cloud:**

Run **`sync_trades_to_cloud.ps1`** to push your local `trade_ledger_2026.csv` to production.

```powershell
# Set your Railway backend URL (from Railway → your service → Settings → Domain)
$env:BACKEND_URL = "https://YOUR-RAILWAY-URL.up.railway.app"
.\sync_trades_to_cloud.ps1
```

Optional: Add `TRADES_SYNC_KEY` in Railway env vars, and set it locally before sync:
```powershell
$env:TRADES_SYNC_KEY = "your-secret-key"
```

---

## 3. Kite Token (SMC Charts + Live Data)

**One-time setup:**

1. Railway → your service → **Variables**
2. Add:
   - `KITE_API_KEY` = your Zerodha API key
   - `KITE_ACCESS_TOKEN` = (update daily — see below)

**Daily (token expires ~24h):**

1. Run **`zerodha_login.py`** on your PC
2. Copy the token from `access_token.txt` or console
3. Railway → Variables → edit `KITE_ACCESS_TOKEN` → paste new token
4. Redeploy (or wait — token is read at request time, restart refreshes)

**Alternative:** Keep `access_token.txt` locally. Charts work when you run the dashboard locally. For production charts, you must update Railway’s `KITE_ACCESS_TOKEN` daily.

---

## 4. Live Signals + OI Intelligence (Engine on Railway)

To run the trading engine in the cloud (no local PC):

1. Create a **second Railway service** in the same project
2. Connect the same GitHub repo
3. **Root directory:** `./`
4. **Start command:**  
   `python smc_mtf_engine_v4.py`  
   (or your main engine script)
5. **Variables:** Same as backend + `KITE_API_KEY`, `KITE_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, etc.
6. **Volumes:** Mount `/data` so engine state persists

**Note:** The engine has heavier deps (ta-lib, etc.). You may need a custom `Dockerfile` or `requirements-full.txt` for this service. The dashboard backend stays on the slim `requirements.txt`.

---

## Summary

| Task | Action |
|------|--------|
| Code changes | Run `sync.bat` |
| Push trades to site | Run `sync_trades_to_cloud.ps1` |
| Persistent DB | Add Railway Volume at `/data`, set `DATA_DIR=/data` |
| Charts work | Add `KITE_API_KEY` + `KITE_ACCESS_TOKEN` to Railway |
| Token refresh | Update `KITE_ACCESS_TOKEN` in Railway daily |
| Engine live | Deploy engine as second Railway service (see above) |
