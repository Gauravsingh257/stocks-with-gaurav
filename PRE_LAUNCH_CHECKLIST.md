# 🚀 Pre-Launch Checklist — stockswithgaurav.com

> **STATUS: HISTORICAL** · workstream: `platform` · last substantive update: 2026-07-05
> SUPERSEDED BY LAUNCH_CHECKLIST.md (2026-07-12), which declares itself the single source of truth for launch. Kept for its infrastructure detail; do not track launch status here.
> Current project state lives in [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md).

Operational readiness for public launch. Check every box before flipping to public. Owner-driven; items marked 🤖 can be assisted/verified by tooling, 👤 require the owner.

---

## 1. Infrastructure
- [ ] 👤 **Vercel** frontend: production branch = `main`, latest deploy green, custom domain + SSL valid (`stockswithgaurav.com`).
- [ ] 👤 **Railway backend** (web) `web-production-2781a`: healthy, autoscaling/instance sized for expected traffic.
- [ ] 👤 **Railway engine** (worker): running, `Dockerfile.engine`, restart policy set.
- [ ] 👤 **Redis** (Railway): reachable from both engine + backend; memory headroom; eviction policy sane; `auth:jwt_secret` present (64-char random).
- [ ] 👤 **Env vars** set on the correct services (not mixed): `DASHBOARD_URL`, `REDIS_URL`, `JWT_SECRET`(optional), `PORTFOLIO_*` flags, `KITE_*`. No secrets in the repo.
- [ ] 🤖 **CORS**: only `https://stockswithgaurav.com` allowed on authed endpoints (verified — no wildcard-with-credentials).
- [ ] 👤 **Rate limiting** enabled on public endpoints; DDoS/edge protection (Cloudflare/Vercel) considered.

## 2. Trading engine health
- [ ] 👤 **Daily Kite login** completed (`CLICK ONCE to START/RUN_ENGINE_ON_RAILWAY.bat`) → `kite:access_token` fresh (24h TTL), `kite:token_ts` current.
- [ ] 🤖 **engine_heartbeat** present (<120s) and `engine:snapshot` fresh (<600s) during market hours.
- [ ] 🤖 **Scanner cron** producing snapshots (screener `snapshot_stale=false`, `as_of` = today) after the post-close run.
- [ ] 🤖 **Portfolio guards** active as intended: re-entry guard (`PORTFOLIO_REENTRY_GUARD`), stale cull (`PORTFOLIO_STALE_EXIT`), structure exit (`PORTFOLIO_STRUCTURE_EXIT`), cap = 20.
- [ ] 👤 **Trade sync** engine→dashboard working (closed trade → `POST /api/journal/trade`; retry queue draining).

## 3. APIs
- [ ] 🤖 `/health` → 200; `/api/portfolio/summary`, `/api/research/track-record`, `/api/screeners` → 200 with fresh data.
- [ ] 🤖 **Chart endpoint** `/api/chart/{sym}` returns candles during market hours (was 502 on weekend — confirm Monday).
- [ ] 🤖 **No 502/CORS storms** in the browser console during a normal session (market hours).
- [ ] 🤖 **WebSocket** `/ws` + `/ws/trades` connect (not 502) and stream; reconnect + polling fallback verified.
- [ ] 🤖 **Data integrity**: journal ↔ stats API ↔ website reconcile (verified in TRADING_ENGINE_AUDIT.md).

## 4. Authentication & security
- [ ] ✅ **JWT forgery fixed** (no hardcoded fallback secret) — verified live (forged token → 401, legit → 200).
- [ ] 👤 **Rotate `auth:jwt_secret`** once more right before launch (invalidates any pre-launch tokens); confirm login still issues valid tokens.
- [ ] 🤖 **Anonymous gating**: screeners/premium content locked for anon; no private data (watchlist/positions) leaks without auth.
- [ ] 👤 **Password policy** + rate-limit on `/api/auth/login` (brute-force protection).
- [ ] 👤 **Compliance**: "educational only / not SEBI-registered / not advice" disclaimer visible site-wide; track-record labeled "algorithmic signals / hypothetical".

## 5. Monitoring & alerting
- [ ] 👤 **Uptime monitor** on `/health` + the frontend (e.g. UptimeRobot/BetterStack) with alerts.
- [ ] 👤 **Error tracking** (Sentry or similar) on frontend + backend; source maps uploaded.
- [ ] 👤 **Engine watchdog** alerts (heartbeat miss, token expiry, snapshot stale) → Telegram.
- [ ] 👤 **Log retention** + a way to inspect Railway logs during launch.
- [ ] 🤖 **Web Vitals** (LCP/CLS/FCP/INP) monitored under live traffic (Vercel Analytics/Speed Insights).

## 6. Rollback & safety
- [ ] 👤 **Rollback plan**: know how to revert a Vercel deploy (instant) and a Railway deploy (redeploy previous image).
- [ ] 👤 **Feature kill-switches** documented (env flags: `PORTFOLIO_STALE_EXIT=0`, `PORTFOLIO_REENTRY_GUARD=off`, etc.) for fast mitigation without a deploy.
- [ ] 👤 **DB backup** of `smc_engine_state.db` / journal + a restore test.
- [ ] 👤 **"Maintenance mode"** or graceful degraded state if the engine/backend goes down (frontend already degrades to LKG + banner).

## 7. Launch-day verification (Monday, market hours)
- [ ] 🤖 Chart loading works with **live candles** (crosshair/tooltip/pinch/drag) on desktop.
- [ ] 👤 **Real-device pass**: Safari-iPhone + Chrome-Android — chart gestures, keyboard-above-input, no clipping (see RELEASE_BLOCKERS Critical #1).
- [ ] 🤖 **WebSocket stable** through a full session; no reconnect storms; live ticks flowing into ticker + charts.
- [ ] 🤖 **30-min memory soak** under live traffic — no leak (heap stable, listeners/canvases bounded).
- [ ] 🤖 **Web Vitals** within budget on throttled mobile (LCP<2.5s, CLS<0.1, INP<200ms).
- [ ] 👤 **Market-open workflow** + **session persistence** (refresh, restart, reconnect after network loss) validated.
- [ ] 🤖 Final regression at 360/390/768/1024/1440/1920 — no returned responsive defects.

---

## Go / No-Go
**GO only when:** all boxes above checked · RELEASE_BLOCKERS.md has **zero Critical** · ACCESSIBILITY_REPORT C1–C3 resolved · MOBILE_AUDIT_FINDINGS matrix all ✅ · Monday live validation passed.
