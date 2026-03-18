# Run Engine on Railway Only (No Local Engine)

To avoid **two engines** (local + Railway) fighting for the same Redis lock and shutting each other down:

---

## ✅ What to run (Railway-only setup)

| Script | When to use |
|--------|-------------|
| **morning_login.bat** | **Every morning** (or when token expires). Opens Zerodha login, you paste the redirect URL → token is stored in **Redis**. The engine on Railway picks up the new token within 2 minutes. **Does NOT start the engine.** |
| **go_live.bat** | When you want to: (1) do Zerodha login, (2) push the new token to **Railway Variables** (engine + web), (3) push code and sync trades. Use if you use `railway` CLI. **Does NOT start the engine.** |
| **sync.bat** | When you want to push code to GitHub (Vercel + Railway auto-deploy). **Does NOT start the engine.** |

---

## ❌ Do NOT run (when using Railway engine)

| Script | Why avoid |
|--------|-----------|
| **run_live_v4.bat** | This starts the **engine on your PC**. If the engine is already running on Railway, you will have **two engines** sharing the same Redis lock → one will exit (often within minutes) and Telegram may be inconsistent. |

**Rule:** If the engine is running on Railway, **do not** double-click `run_live_v4.bat`.

---

## Daily flow (Railway-only, Telegram working)

1. **Morning (before market):** Run **morning_login.bat** once. Log in on Zerodha, paste the redirect URL when prompted. Token goes to Redis; Railway engine uses it within ~2 min.
2. **Do not** run `run_live_v4.bat` — the engine is already running on Railway.
3. Open your dashboard (Vercel) and check that the engine shows **LIVE** and heartbeat is recent. Telegram alerts will work as long as the **single** Railway engine is running and token is fresh.

---

## If you want to run the engine locally instead

Then run **run_live_v4.bat** and **stop** the engine service on Railway (or use a different Redis / no Redis for local) so only one engine instance is active.
