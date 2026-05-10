# Platform stabilization audit — stockswithgaurav.com

**Scope:** Railway backend, Redis, Vercel frontend (code review + static validation).  
**Phase:** Stabilization / hardening — no new product features.  
**Date:** 2026-05-09  

---

## Executive summary

The stack is **architecturally sound**: snapshot-first APIs, Redis-backed watchlist OS, unified `global_state_version` on the main engine WebSocket path, reconciliation helpers in `stateEngine/reconcile.ts`, and intentional degraded UX (`BackendStatusNotice`, polling fallback in `useWebSocket.ts`).  

**Primary risks** cluster around **multiple independent WebSocket clients**, **JWT vs DB role drift for premium**, **auth token storage**, and **OI page WS stall** (addressed in code this pass). Production correctness still depends on **env**: `NEXT_PUBLIC_BACKEND_URL` / `NEXT_PUBLIC_WS_URL` on Vercel so browsers never rely on same-origin WS.

---

## 1. Root-cause report

| Symptom | Likely root cause | Evidence |
|--------|-------------------|----------|
| “Realtime stuck” on OI Intelligence | WS reconnection **stopped permanently** after 3 close events (`failCount >= 3` short-circuited `connect()`). REST polling continued, so data moved but UI showed **WifiOff** forever until full refresh. | `app/oi-intelligence/page.tsx` (pre-fix). |
| Duplicate React state updates / list glitches | **Non-unique keys** when brief sections shared titles (`key={s.title}`). | `app/dashboard/page.tsx` narrative + sections. |
| Multiple WS connections to Railway | **Separate hooks** open `/ws`: `useEngineSocket`, terminal `useChartData` singleton, `useLiveTrades`, OI page — multiplies connections per user session. | `lib/useWebSocket.ts`, `terminal/_lib/useChartData.ts`, `oi-intelligence/page.tsx`. |
| Premium gating mismatch | Command center uses **`role` from JWT** (`get_optional_user`). If role is updated in DB without re-login, client still sees **FREE** limits. | `routes/command_center.py`, `command_center_service._tier_limit`. |
| Session persistence loss | **Single JWT in `localStorage`**; invalid/expired token cleared on `/api/auth/me` failure only at bootstrap. | `lib/auth.tsx`. |
| Stale UI while WS reconnects | By design: **last snapshot retained** during reconnect; banner suppressed when prior snapshot exists (`BackendStatusNotice`). | `components/BackendStatusNotice.tsx`. |
| Chart LTP vs index | Terminal charts use **shared WS** + REST; engine ribbon uses **merged snapshot** — divergence possible if one path stale. | Architectural (two consumers of same stream). |

---

## 2. Stability report

**Strengths**

- Main dashboard socket: **sequential queue** (`enqueueSequential`) reduces race processing on rapid frames.
- **Stale envelope rejection** + `requestResync` when version moves backward (`rejectStaleUpdate`).
- **Tab visibility** resets `failCount` and retries WS before polling (`useWebSocket.ts`).
- Watchlist OS: **live → LKG → build** path avoids empty shell when DB has symbols (`watchlist_os.py`).
- Ops: **`/api/system/debug/platform`** aggregates Redis, snapshot consistency, WS telemetry, `retention_engine`.

**Weaknesses**

- OI page had **hard stop** on WS retries (fixed: capped exponential backoff, no permanent stall; visibility recovery).
- No automated E2E in repo for WS reconnect under packet loss (manual / synthetic test recommended).

---

## 3. UX inconsistency report

| Area | Issue |
|------|--------|
| Connection status | OI page showed WS “off” while REST still refreshed — **confusing** until WS fix. |
| Banners | `BackendStatusNotice` intentionally **hides** yellow bar during brief disconnect if snapshot present — consistent with “trust-first,” but power users may not see “reconnecting” at all. |
| Premium copy | FREE tier note on command center vs JWT role — **consistent only if token matches DB**. |
| Duplicate content | Daily brief **narrative** vs **sections** are separate blocks; titles could theoretically repeat — keys fixed to use index. |

---

## 4. Mobile UX report

- **Command Center / Watchlist**: Flex/grid layouts use `clamp` and `minmax` — generally mobile-safe.
- **Bottom `MobileNav`**: Fixed bar — verify safe-area (`env(safe-area-inset-bottom)`) already on `MobileNav.tsx`.
- **WS + sleep**: `visibilitychange` recovery on main engine hook helps mobile backgrounding; OI page now aligns with visibility retry.
- **Risk:** Long pages + fixed nav — scroll-to-section not standardized (minor).

---

## 5. Realtime consistency report

| Path | Consistency mechanism |
|------|------------------------|
| Engine snapshot | WS snapshot + digest patches + merge in `mergeSnapshot` / `mergeDigestPatch`. |
| Version | `extractEnvelopeVersion` + monotonic `lastGvRef`. |
| Watchlist | **Hint-only WS** (`watchlist_delta`) → client **refetches** `GET /api/watchlist/operating` — avoids merging partial WL state in browser. |
| LTP | `ltp` frames patch `index_ltp` on snapshot object. |
| OI | Dedicated WS type `oi_intelligence`; REST fallback `/api/agents/oi-intelligence`. |

**Residual risk:** Multiple WS clients may receive frames in different order; main hook serializes its queue — **secondary clients do not**, so chart/LTP duplicate paths are slightly more exposed to ordering quirks.

---

## 6. Performance bottleneck report

| Bottleneck | Notes |
|------------|--------|
| Multiple `/ws` connections | Extra Railway CPU + Redis broadcast fan-out per connection. |
| Redis SCAN on debug platform | Bounded loops — OK; avoid calling debug endpoints at high frequency in prod. |
| Watchlist OS rebuild | Background refresh + digest — tuned via TTL envs; large symbol lists cost CPU on Railway. |
| Frontend polling | After 3 WS failures, main hook uses **5s REST polling** — acceptable fallback; watch quota on Railway. |

---

## 7. Production hardening recommendations

1. **Env (critical)**  
   - Set `NEXT_PUBLIC_BACKEND_URL` and preferably **`NEXT_PUBLIC_WS_URL`** explicitly on Vercel so production **never** depends on localhost fallbacks in dev-only branches.

2. **WebSocket consolidation (medium)**  
   - Long-term: single shared WS subscription multiplexing `ltp`, OI, trades — reduces duplicates (requires careful refactor).

3. **Premium role (medium)**  
   - On role change: force re-login or issue new JWT from `/api/auth/me` refresh endpoint so command center tier matches DB.

4. **Auth (medium)**  
   - Consider httpOnly cookie session for token (reduces XSS impact); migration effort non-trivial.

5. **Monitoring**  
   - Alert on `stale_systems` from `/api/system/debug/platform`, `watchlist_health.max_snapshot_age_sec_approx`, engine heartbeat age.

6. **Testing**  
   - Contract tests for `mergeSnapshot` / `rejectStaleUpdate`; smoke test watchlist refetch after injected `watchlist_delta`.

7. **Telegram / signals**  
   - Not validated in this code audit — verify `services/signal_delivery` + worker processes in deployment separately.

---

## Changes applied in this stabilization pass

1. **`dashboard/frontend/app/oi-intelligence/page.tsx`**  
   - Removed permanent WS stall after 3 failures; **exponential backoff with cap**, infinite retry cycle.  
   - **`visibilitychange`**: reset backoff and reconnect when tab visible (aligned with `useEngineSocket`).  
   - Cleanup for retry timers on unmount.

2. **`dashboard/frontend/app/dashboard/page.tsx`**  
   - **Stable list keys** for daily brief narrative and sections (`index + title`) to avoid React reconciliation bugs when titles collide.

---

## Checklist mapping (20 items)

| # | Topic | Assessment |
|---|--------|--------------|
| 1 | Auth/session persistence | JWT in localStorage; cleared on bad `/me` — **OK with XSS caveat**. |
| 2 | Watchlist persistence | SQLite + Redis snapshots — **OK**. |
| 3 | Realtime sync | Main hook strong; **multi-WS** is the main drift risk. |
| 4 | WS reconnect | Main hook + **OI fixed**; terminal/OI still separate sockets. |
| 5 | Redis snapshot consistency | Server-side keys + TTLs — **OK**; verify Railway Redis latency. |
| 6 | State version | Client reconcile + resync — **OK** on main path. |
| 7 | Command center freshness | Snapshot meta + Redis WL — **OK**; depends on engine snapshot age. |
| 8 | Retention/revisit | visit-mark on unmount + digest compare — **OK**; first session has no prior digest. |
| 9 | Evolution timeline | Redis list on feed diff — **OK** when WL refresh runs. |
| 10 | Mobile responsiveness | Generally **OK**; spot-check long tables. |
| 11 | Chart stability | Separate WS path — **moderate** consistency risk. |
| 12 | LTP sync | Patched on snapshot in main hook — **OK** for ribbon; charts separate. |
| 13 | Signal delivery | **Not audited** (backend worker path). |
| 14 | Telegram | **Not audited** (external bot). |
| 15 | Empty-state handling | Watchlist OS empty shells mitigated — **OK**. |
| 16 | Degraded mode | Polling + banners + LKG — **OK**. |
| 17 | Premium gating | **JWT-only** — document / refresh token recommended. |
| 18 | UI duplication | Brief keys — **fixed**; narrative vs sections still two blocks (intentional). |
| 19 | Reconnect flashing | Suppressed when snapshot exists — **by design**. |
| 20 | Stale snapshot handling | `rejectStaleSnapshotAge`, `snapshotLikelyStale` — **OK**. |

---

*This document is a static architecture/code audit; live production validation requires runtime probes against Railway + Vercel with real sessions.*
