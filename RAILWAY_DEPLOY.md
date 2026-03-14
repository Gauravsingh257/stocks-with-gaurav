# Deploy Backend to Railway

Follow these steps to deploy your FastAPI backend so the live site shows real data.

---

## Step 1: Push latest code to GitHub

```powershell
cd "c:\Users\g6666\Trading Algo"
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
5. Choose **`Gauravsingh257/stocks-with-gaurav`**
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

1. Go to [vercel.com](https://vercel.com) → your **stocks-with-gaurav** project
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

**Build fails on ta-lib?**  
We use `requirements-railway.txt` (via nixpacks.toml) to avoid this. If it still fails, check Railway build logs.

**"Connecting to engine" forever?**  
The dashboard shows live data when your **trading engine** runs locally and pushes state. The backend API runs 24/7 on Railway; the engine runs on your PC during market hours. For now, the site will load with empty/placeholder data until you run the engine.

**WebSocket not connecting?**  
Vercel rewrites `/ws` to your Railway backend. Ensure `BACKEND_URL` in Vercel points to the Railway URL (with `https://`, no trailing slash).
