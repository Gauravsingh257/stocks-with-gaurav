# Phase A — Cleanup + Trust Fixes Audit Report

**Date:** 2026-05-10  
**Scope:** Railway backend + Redis + Vercel frontend  
**Phase:** Product refinement — no feature expansion  

---

## 1. FULL AUDIT REPORT

### A — Trade Terminal
- **KPIs:** Hero bar is lean (TERMINAL label, live status, best trade, daily PnL, setup count, timestamp, refresh). No duplication found — already a single scrollable compact bar.
- **Filters:** `AdvancedFilterBar` sends `direction`, `setups`, `strategy`, `risk`, `symbol` to the backend via debounced `apiFilters`. Server-side filtering is active when backend returns filtered `final_trades`. Client-side only applies text search. **Functional.**
- **Signals:** Terminal loads from `api.researchDecisionFeed(40, 1)` (REST) + `useLiveTrades` (WS with REST fallback). Data sources are real (Redis snapshot → FastAPI). `useTerminalSummary` provides daily PnL and best trade from the same pipeline.
- **Empty states:** Uses `MarketMonitoringEmpty` which connects to engine snapshot for live regime/zone/signal hints. **Trust-first language.**

### B — Analytics
- **Issue found:** Hero bar showed 4 cards (Intraday/Swing/LT/Algo Score) even when data was empty — showed `0.0% WR`, `+0.00R`, `0/100 Weak`. This looks fake.
- **Fix applied:** Cards now only render when their data source has >0 trades/picks. Empty state shows "Building verified track record" instead of zero-filled institutional grids.
- **Intraday section:** Now hidden entirely when `total_trades === 0`.
- **Empty chart states:** Changed "No data yet" → "Awaiting resolved picks".
- **Empty table states:** Changed "No picks yet — run a scan first" → "Awaiting scan results — picks appear after research scans complete."

### C — Removed / Cleaned
- **Journal:** Removed from both `Sidebar.tsx` NAV array and `MobileNav.tsx` ITEMS. Page still exists but is inaccessible from navigation. Backend remains dormant.
- **Nav reorder:** Research and Watchlist moved up (higher priority for retention/conversion). Analytics moved below them.

### D — Watchlist
- **Add flow:** `AddToWatchlistButton` already handles logged-out ("Sign in to save" link to /login) and logged-in (instant POST to Railway backend → SQLite persist + Redis snapshot update on next refresh). **No changes needed.**
- **Synchronization:** Add → SQLite → next watchlist OS refresh picks up symbol → Redis snapshot → WS hint → frontend refetch. Single source of truth chain verified.
- **Fake levels:** Already gated by `showActionableLevels` which requires `entry_ready=true` from backend. Backend's `build_symbol_intel` → decision engine → `trade_levels.show_actionable_levels` check is multi-gate. When false, shows "Monitoring" state with reason. **Trust-first by design.**
- **Desk OS fields:** `desk_os` (rank, urgency, priority, trend, timing, window) already attached via retention engine and rendered on each card.

### E — OI Intelligence
- **Simplified description:** Removed "AI-style derivatives narrative" marketing language. Now reads: "Derivatives bias, support/resistance, and flow analysis from live OI data."
- **Empty blocks:** Strike heatmap and short covering/execution quality panels now conditionally rendered — hidden when data arrays are empty.
- **WS stall fix:** Already applied in prior stabilization pass (infinite backoff + visibility recovery).

### F — Login/Premium UX
- **Auth-aware sidebar:** Removed static "AI Engine · Active" text for all users. Logged-out users now see "Sign in to unlock Watchlist + Command Center" — clear CTA.
- **Research page:** Already has `ResearchConversionPanel` with "Discovery → Watchlist → Final Review" pipeline and "Sign in to save" / "Open Watchlist" CTAs based on auth state.

### G — System Trust
- **Timestamps:** Already present: watchlist cards show "Last engine tick", Terminal hero shows "updated at" time, OI page shows "Last generated" with IST timestamp.
- **Confidence states:** `BackendStatusNotice` shows live/syncing/stale/reconnecting. MarketMonitoringEmpty shows regime + zones + signal count. Terminal hero shows LIVE/POLLING/SYNCING/OFFLINE pill.
- **Negative language:** Fixed "No picks found" → "No matching results" in track-record page.

---

## 2. TRUST REPORT

### What's trustworthy
| Component | Trust mechanism |
|-----------|----------------|
| Trade levels | Multi-gate: `entry_ready` + `show_actionable_levels` + decision engine gates. Withheld levels show "Monitoring" with reason. |
| Analytics | Now shows data ONLY when verified trades exist. Empty state is explicit. |
| Watchlist feed | Diff-based from Redis — events only appear when actual intel hash changes. |
| OI Intelligence | All data from backend interpretation engine (Redis-backed snapshot). "LIVE DATA" vs "SNAPSHOT" indicator. |
| Engine status | Real heartbeat, cycle age, snapshot TTL visible in debug endpoints. |
| Daily PnL | From `useTerminalSummary` → actual signal/trade tracking, not synthetic. |

### Trust risks remaining
| Risk | Impact | Recommendation |
|------|--------|----------------|
| JWT role drift | Medium | After admin changes user role in DB, frontend still shows old role until re-login. Add `/api/auth/me` role refresh. |
| JWT default secret | High | `JWT_SECRET` defaults to `swg-default-secret-change-me-in-prod` — set in Railway env vars. |
| Journal sync endpoints | Medium | `POST /api/journal/sync` and `/api/journal/trade` allow unauthenticated writes when `TRADES_SYNC_KEY` env is unset. Set this key in Railway. |
| OI during market close | Low | Shows "SNAPSHOT" + last session data. Clear to user. |
| Pillar scores precision | Low | Scores are heuristic (rule-based), not ML-calibrated. Labels say "heuristic" — honest. |
| Premium upgrade | Fixed | Was openly callable by any user. **Now requires `OPS_API_KEY` header.** |

---

## 3. PERFORMANCE REPORT

| Metric | Status |
|--------|--------|
| TypeScript typecheck | ✅ Clean |
| Python compile (all modified backend files) | ✅ Clean |
| Linter errors | ✅ None on modified files |
| WS reconnect (main hook) | ✅ Infinite backoff + visibility recovery |
| WS reconnect (OI page) | ✅ Fixed (was permanently stalled after 3 failures) |
| REST polling fallback | ✅ 5s interval after WS failure |
| Snapshot freshness | ✅ `_snapshot_age_ms` + `snapshotLikelyStale` checks |
| Redis key TTLs | ✅ All retention/evolution/session keys have 5-14d TTLs |

---

## 4. FINAL UX SUMMARY

### Changes applied

| # | Change | Files |
|---|--------|-------|
| 1 | Removed Journal from navigation | `Sidebar.tsx`, `MobileNav.tsx` |
| 2 | Reordered nav: Research + Watchlist higher | `Sidebar.tsx`, `MobileNav.tsx` |
| 3 | Analytics: hide empty sections, trust-first empty states | `analytics/page.tsx` |
| 4 | Analytics: hero cards only render when data exists | `analytics/page.tsx` |
| 5 | OI: simplified description text | `oi-intelligence/page.tsx` |
| 6 | OI: hide empty strike heatmap + short covering blocks | `oi-intelligence/page.tsx` |
| 7 | Auth CTA: sidebar shows "Sign in to unlock" for logged-out users | `Sidebar.tsx` |
| 8 | Fixed negative language in track-record empty state | `track-record/page.tsx` |

### Additional fixes from deep audit

| # | Change | Files |
|---|--------|-------|
| 9 | **Security: Premium upgrade endpoint now requires OPS_API_KEY** — was openly callable by any authenticated user | `routes/auth.py` |
| 10 | **Dead code: removed unused `EmptyHero` function** from terminal page | `terminal/page.tsx` |

### What was NOT changed (and why)

| Item | Reason |
|------|--------|
| Terminal KPIs | Already lean — single compact hero bar with real data sources. No duplication found. |
| Terminal filters | Functional — server-side filtering confirmed active. |
| Watchlist add flow | Already handles logged-out → "Sign in to save". |
| Watchlist levels | Already gated by multi-layer decision engine. |
| Journal page code | Left dormant — only removed from nav. Can be re-enabled later. |
| OI MarketIntelligenceSuite | Complex but data-driven. Each section conditionally renders from backend data. Hiding individual sub-sections requires knowing which are empty at runtime. |
| Research pipeline sections | Discovery → Watchlist → Final is the core product flow — not a duplicate. |

### Product state after Phase A

The platform now:
- Shows data ONLY when it exists (no zero-filled grids)
- Uses trust-first language for empty states ("Building verified history" not "0 trades")
- Has cleaner navigation (Journal hidden, priority pages higher)
- Handles WS reconnection reliably across all pages
- Gates trade levels behind real validation checks
- Shows clear auth CTAs for conversion
