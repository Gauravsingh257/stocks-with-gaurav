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
- **MiniChart lazy-loading shipped** (PR #77) — fetch/poll/WS gated on IntersectionObserver; off-screen cards issue zero `/api/chart` requests. (Live before/after count pending Monday — weekend terminal has 0 opportunity cards.)
- **Accessibility partial** — global focus-visible ring + prefers-reduced-motion (PR #78) + search-input labels (PR #79) shipped. See ACCESSIBILITY_REPORT.md.

---

## 🔴 Critical (block production sign-off)

1. **Real-device gesture validation not performed** — I have **no physical device** and **no live market data this weekend** (Kite token expired). The acceptance criteria require verifying on **Safari-iPhone + Chrome-Android with live candles**: pinch-zoom, long-press, crosshair, tooltip, drag, orientation change, resize-after-rotation. *Action:* owner or a device lab (BrowserStack/LambdaTest) must run these on Monday with live data. **I cannot sign this off.**
2. **Accessibility not yet fully passing** — *partially done* (focus-visible ✅, reduced-motion ✅, input labels ✅ — PRs #78/#79). **Remaining before the a11y gate clears:** dim-text contrast fails AA (C1), icon-button touch targets (C2), scoped `aria-live` for P&L/Regime/Signals (C3), and the **manual screen-reader + keyboard-E2E pass**. See ACCESSIBILITY_REPORT.md.
3. **Performance metrics not measured** — LCP/CLS/FCP/INP on throttled mobile, bundle analysis (framer-motion, two chart libs), and the **30-minute memory soak** for polling/WS/chart leaks. Required; not done (soak needs sustained monitoring beyond this session).
4. **Backend availability during market hours unconfirmed** — intermittent **502s** on `/ws/trades`, `/api/chart/*`, `/api/snapshot` observed over the weekend. The frontend degrades gracefully, but if this persists during live hours users lose real-time data. *Action:* confirm backend stability Monday 09:15–15:30 IST. (Backend concern, but gates "production ready".)

---

## 🟠 Important (should fix before or shortly after launch)

1. ✅ ~~Opportunity-card mini-chart fetch fan-out~~ — **FIXED (PR #77)**: MiniChart now lazy-loads via IntersectionObserver; off-screen cards issue zero requests.
2. **Dim-text contrast fails AA** (ACCESSIBILITY_REPORT C1) — `--text-dim` ≈ 2.8:1; lighten to ~`#6b7fa6`. (Owner sign-off — visual change.)
3. **Icon-button touch targets** (C2) — 18 sub-40px interactive elements; pad icon-only buttons to ≥44px on mobile.
4. **Scoped aria-live for market data** (C3) — announce P&L/Regime/Signals changes to screen readers without over-announcing the per-tick ticker.
5. **On-screen-keyboard occlusion unverified** — real device needed to confirm focused inputs scroll above the keyboard.
6. **RunningTradesMonitor stat grids** — fixed `repeat(4,1fr)` grids not yet audited on 360px; likely need the `minWidth:0`+`clamp()` treatment.
7. **Landscape + dynamic text scaling (120/150/200%)** not yet verified across pages.

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
