# Deployment Audit — Why the Website May Not Be Live

This document lists the most likely causes and fixes for **stockswithgaurav.com** not going live (Railway backend + Vercel frontend).

---

## 1. Railway (Backend) — Most Likely Issues

### A. ModuleNotFoundError: No module named 'dashboard'

**Cause:** When Railway runs `python scripts/start_web.py`, Python adds the **script’s directory** to `sys.path`, not the repo root, so `dashboard` is not found.

**Fix (already in code):** `scripts/start_web.py` adds the repo root to `sys.path` before importing:

```python
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, repo_root)
```

**You must ensure:**

- Railway uses the **Dockerfile** (not Nixpacks) so that `WORKDIR /app` and `ENV PYTHONPATH=/app` are set.
- In Railway → your **web** service → **Settings** → **Build**:
  - **Builder:** Docker (or set **Config file** to `railway.toml`, which has `dockerfilePath = "Dockerfile"`).
- **Start command** is either left default (Dockerfile `CMD`) or explicitly: `python scripts/start_web.py`.

If the service is still using Nixpacks, switch to Docker so the container layout is `/app` and PYTHONPATH is set.

---

### B. Backend crashes after startup (e.g. health check fails)

**Possible causes:**

- **Missing env vars:** `KITE_API_KEY` / `KITE_ACCESS_TOKEN` are optional for startup but some routes may fail if required later. No env should be required for `/health` and `/api/system/health`.
- **PORT:** Railway sets `PORT` automatically; the app uses `int(os.environ.get("PORT", 8000))`. No change needed unless you override PORT incorrectly.

**Check:** Railway → **Logs**. Look for the line after “Dashboard backend ready”. If you see a traceback, fix that specific error (e.g. missing Redis, DB path).

---

### C. Health check URL wrong

**Railway** pings the service to decide if it’s “running”. Your `railway.toml` has:

- `healthcheckPath = "/health"`

So Railway will call `https://<your-app>.up.railway.app/health`. The app must respond with HTTP 200. Our `main.py` defines `@app.get("/health")` and returns `{"status": "ok", "service": "smc-dashboard"}`.

If the service is slow to start, the health check can timeout. `healthcheckTimeout = 60` is already set. If startup is slower than 60s (e.g. heavy DB/CSV work), increase this in `railway.toml` or in Railway dashboard.

---

## 2. Vercel (Frontend) — Most Likely Issues

### A. BACKEND_URL not set (most common)

**Symptom:** Site loads but all API calls fail (502/504 or “Failed to fetch”). Browser may show requests to `https://stockswithgaurav.com/api/...` returning errors.

**Cause:** Next.js **rewrites** in `next.config.ts` send `/api/*` to `process.env.BACKEND_URL`. If `BACKEND_URL` is **not set** on Vercel, it defaults to `http://localhost:8000`. The Vercel serverless function then tries to reach localhost (not your Railway backend), so the request fails.

**Fix:**

1. Vercel → your project → **Settings** → **Environment Variables**.
2. Add (for **Production**, and optionally Preview):
   - **BACKEND_URL** = `https://<your-railway-app>.up.railway.app`  
     (get the exact URL from Railway → your web service → **Settings** → **Domains**.)
3. **Redeploy** (e.g. **Deployments** → … → **Redeploy**).

---

### B. NEXT_PUBLIC_BACKEND_URL and WebSocket

- **NEXT_PUBLIC_BACKEND_URL:** If the frontend sometimes calls the backend directly (e.g. for WebSocket or polling), set this to the same Railway URL so the browser can reach Railway. If you only use relative `/api/...` and rewrites, you can leave it empty.
- **NEXT_PUBLIC_WS_URL:** For WebSocket (e.g. live updates), set to `wss://<your-railway-app>.up.railway.app/ws`. Otherwise the client may try to connect to the Vercel host, which does not proxy WebSockets.

---

### C. Vercel build / root directory

- **Root directory:** `vercel.json` has `"rootDirectory": "dashboard/frontend"`. So Vercel builds from `dashboard/frontend`. No change needed unless your repo layout is different.
- If the **build fails**, check the build logs (Vercel → **Deployments** → failed deployment → **Building**). Fix any TypeScript, lint, or dependency errors reported there.

---

## 3. GitHub

- **Branch:** Railway and Vercel should deploy from the **same branch** (usually `main`). Confirm in both:
  - Railway: **Settings** → **Source** → branch.
  - Vercel: **Settings** → **Git** → Production Branch.
- **dashboard/** is not in `.gitignore`, so `dashboard/backend/main.py`, `dashboard/__init__.py`, `scripts/start_web.py`, etc. are in the repo and will be in the Docker build context and in Vercel’s clone.

---

## 4. Integration (CORS, domains)

- **CORS:** `main.py` already allows `https://stockswithgaurav.com`, `https://www.stockswithgaurav.com`, and `https://*.vercel.app`. No change needed unless you use another domain.
- **Custom domain:** If you use a custom domain on Vercel (e.g. `stockswithgaurav.com`), add it in Vercel **Settings** → **Domains** and ensure DNS is pointed as instructed.

---

## 5. Quick Checklist

| Check | Where | What to do |
|-------|--------|------------|
| Backend uses Docker | Railway → web service → Build | Use Dockerfile; ensure `railway.toml` has `dockerfilePath = "Dockerfile"` if using config file. |
| Backend starts | Railway → Logs | No `ModuleNotFoundError`; see “Dashboard backend ready”. |
| /health returns 200 | Browser or curl | `curl https://<railway-url>/health` → `{"status":"ok",...}` |
| BACKEND_URL set | Vercel → Settings → Env Vars | `BACKEND_URL` = `https://<railway-url>.up.railway.app` |
| Redeploy after env change | Vercel | Redeploy production after adding/changing BACKEND_URL. |
| Frontend build passes | Vercel → Deployments | Last deployment “Ready”; no build errors. |

---

## 6. Final “go live” verification

1. **Backend:**  
   `curl https://<your-railway-url>.up.railway.app/health`  
   → `{"status":"ok","service":"smc-dashboard"}`

2. **Backend system health:**  
   `curl https://<your-railway-url>.up.railway.app/api/system/health`  
   → JSON with `engine_status`, `kite_connected`, etc.

3. **Frontend:**  
   Open `https://stockswithgaurav.com` (or your Vercel URL). No blank page or “Failed to fetch” for API calls.

4. **API from site:**  
   Open DevTools → Network; reload the site. Requests to `/api/...` should go to your domain and return 200 (or expected errors), not 502/504.

If all of the above pass, the site is live. If not, use the section that matches the symptom (backend crash, 502 on API, build failure) and apply the fixes above.
