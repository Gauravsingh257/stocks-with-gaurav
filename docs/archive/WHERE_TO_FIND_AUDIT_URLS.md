# Where to Find BACKEND_URL and REDIS_URL for the Audit

These values are **not stored in the repo** (they’re in Railway and your local config). Use the steps below to get them.

---

## BACKEND_URL (Dashboard API)

This is the **public URL of your FastAPI backend** on Railway.

### Option A — Railway dashboard

1. Open **[railway.app](https://railway.app)** and sign in.
2. Open your **project**.
3. Click the **Dashboard API** (or **web**) service — the one that runs the FastAPI app.
4. Go to **Settings** → **Networking** (or **Domains**).
5. If there’s no domain yet, click **Generate Domain**.
6. Copy the URL, e.g. `https://trading-algo-production.up.railway.app` or `https://web-production-xxxx.up.railway.app`.

That URL is your **BACKEND_URL**.

### Option B — You already use .go_live_config

If you have a `.go_live_config` file in the project root with a line like:

```text
BACKEND_URL=https://something.up.railway.app
```

then that `https://...` value is your BACKEND_URL.

To print it (PowerShell):

```powershell
.\scripts\show_audit_env.ps1
```

### Option C — Vercel (if the frontend is on Vercel)

1. Open **[vercel.com](https://vercel.com)** → your project.
2. **Settings** → **Environment Variables**.
3. Find **BACKEND_URL** or **NEXT_PUBLIC_BACKEND_URL**.
4. The value is your backend URL (e.g. `https://xxx.up.railway.app`).

---

## REDIS_URL

This is the **Redis connection string** used by your Railway services.

### Option A — Railway dashboard (recommended)

1. Open **Railway** → your **project**.
2. If you added **Redis** as a service:
   - Click the **Redis** service.
   - Open **Connect** or **Variables**.
   - Copy the **REDIS_URL** or **Connection URL** (e.g. `redis://default:password@host:port`).
3. If Redis is provided via a **plugin** or **shared variables**:
   - Open the **Dashboard API** or **Engine** service.
   - Go to **Variables**.
   - Copy **REDIS_URL** (you may need to reveal it).

### Option B — .go_live_config

If you’ve put Redis in `.go_live_config` (e.g. for local scripts):

```text
REDIS_URL=redis://default:xxxxx@host:port
```

that value is your REDIS_URL. Run:

```powershell
.\scripts\show_audit_env.ps1
```

to print it (and BACKEND_URL) for the audit.

---

## Using the values for the audit

**PowerShell (Windows):**

```powershell
# Replace with your actual URLs from above
$env:BACKEND_URL = "https://YOUR-RAILWAY-URL.up.railway.app"
$env:REDIS_URL = "redis://default:password@host:port"

python scripts/audit_cloud_deployment.py
```

**Bash / Linux / macOS:**

```bash
export BACKEND_URL="https://YOUR-RAILWAY-URL.up.railway.app"
export REDIS_URL="redis://default:password@host:port"
python scripts/audit_cloud_deployment.py
```

If you use `.go_live_config`, run `.\scripts\show_audit_env.ps1` first; it will print the exact `$env:BACKEND_URL` and `$env:REDIS_URL` lines to use (if they’re set in that file).
