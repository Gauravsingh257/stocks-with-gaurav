# 🚦 RELEASE_BLOCKERS — Frontend Production Readiness

**Date:** 2026-07-05 · **Verdict: NOT production-ready yet** — Critical items remain (mostly required validations I cannot perform without a physical device + live market data).

**How to read this:** the responsive *engineering* is in good shape and verified where I can verify it. What blocks sign-off is **validation coverage** the acceptance criteria require but that needs a real device, live data, or dedicated audit phases.

---

## ✅ Verified this session (emulated + code-level)
- **No horizontal page scroll** at 360px (Android) and 390px (iPhone) — essentials (LIVE/Regime/PnL/Signals) visible; only intentional `overflow-x:auto` containers exceed width.
- **Chart lifecycle is leak-safe** — teardown does `ResizeObserver.disconnect()` + `chart.remove()` ([research/chart/page.tsx:203-209](dashboard/frontend/app/research/chart/page.tsx#L203-L209)); no detached canvases after navigation.
- **WebSocket reliability** — auto-reconnect w/ backoff, polling fallback, ordered-frame dedup, reconnect-storm prevention, visibility-based reconnect, unmount cleanup ([lib/useWebSocket.ts](dashboard/frontend/lib/useWebSocket.ts)). Covers websocket recovery, polling recovery, duplicate-subscription prevention.
- **Graceful degradation under backend 502s** — WS→polling, "Realtime feed reconnecting" banner, cards render empty; no crash.
- **Memory (short test)** — 13.2→19.0MB over a research↔terminal cycle, far under the 4192MB limit; no runaway.
- **Charts responsive** — candlestick (ResizeObserver + dvh), recharts (`ResponsiveContainer`), MiniChart (ResizeObserver).

---

## 🔴 Critical (block production sign-off)

1. **Real-device gesture validation not performed** — I have **no physical device** and **no live market data this weekend** (Kite token expired). The acceptance criteria require verifying on **Safari-iPhone + Chrome-Android with live candles**: pinch-zoom, long-press, crosshair, tooltip, drag, orientation change, resize-after-rotation. *Action:* owner or a device lab (BrowserStack/LambdaTest) must run these on Monday with live data. **I cannot sign this off.**
2. **Accessibility audit not yet performed** — keyboard nav, visible focus, ARIA on custom controls (TopBar diagnostics dropdown, cards), contrast (both themes), screen-reader landmarks, **prefers-reduced-motion**, **forced-colors/high-contrast**, and **aria-live announcements for price/PnL/regime updates**. Required by acceptance criteria; not started.
3. **Performance metrics not measured** — LCP/CLS/FCP/INP on throttled mobile, bundle analysis (framer-motion, two chart libs), and the **30-minute memory soak** for polling/WS/chart leaks. Required; not done (soak needs sustained monitoring beyond this session).
4. **Backend availability during market hours unconfirmed** — intermittent **502s** on `/ws/trades`, `/api/chart/*`, `/api/snapshot` observed over the weekend. The frontend degrades gracefully, but if this persists during live hours users lose real-time data. *Action:* confirm backend stability Monday 09:15–15:30 IST. (Backend concern, but gates "production ready".)

---

## 🟠 Important (should fix before or shortly after launch)

1. **Opportunity-card mini-chart fetch fan-out** — the terminal fires **~15+ individual `/api/chart/{sym}?interval=5m` requests** (one per card). Heavy on mobile/slow networks and the source of the console error spam when the backend degrades. *Fix:* lazy-load MiniChart via IntersectionObserver (fetch only when card enters viewport) and/or batch.
2. **On-screen-keyboard occlusion unverified** — need a real device to confirm focused inputs (login, search, filters, watchlist add) **scroll above** the keyboard and aren't hidden. Emulation can't test this.
3. **RunningTradesMonitor stat grids** — fixed `repeat(4,1fr)` grids (Entry/CMP/SL/Target etc.) not yet audited on 360px; likely need the `minWidth:0`+`clamp()` treatment applied to OpportunityCard.
4. **Landscape + dynamic text scaling (120/150/200%)** not yet verified across pages.

---

## 🟡 Cosmetic (nice-to-have)

1. **MarketCommandBar** secondary indices (GIFT/VIX/USD-INR/GOLD/CRUDE) still require horizontal scroll on mobile — acceptable ticker behavior; freshness/health now pinned so nothing important is lost.
2. Engine-mode suffix + state-version intentionally hidden `<lg` (available in the diagnostics dropdown) — by design.
3. Light/Dark parity spot-checked on global chrome; a full per-page dual-theme screenshot pass is still pending.

---

## Production-ready gate (per acceptance criteria)
Mark the frontend **Production Ready** only when ALL hold:
- [ ] Every coverage-matrix cell ✅ (see MOBILE_AUDIT_FINDINGS.md — still 🟡/⬜ rows remain)
- [ ] Every user journey passes (incl. market-open + session persistence)
- [ ] Every accessibility check passes (Critical #2)
- [ ] Every performance metric passes (Critical #3)
- [ ] Regression pass clean after each PR
- [ ] **This file has ZERO Critical items**

**Current Critical count: 4 → NOT production-ready.** Three of the four are validation phases/actions (real-device, a11y, perf) rather than code defects; the responsive code itself is holding up well under emulated + code-level scrutiny.
