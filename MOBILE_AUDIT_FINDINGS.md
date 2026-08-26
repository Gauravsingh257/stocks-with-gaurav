# 📱 Responsive Audit — Coverage Matrix & Findings

> **STATUS: LIVE** · workstream: `ui-ux` · last substantive update: 2026-07-05
> Responsive coverage matrix with items still marked not-yet-audited. Open.
> Current project state lives in [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md).

**Method:** live inspection via headless browser (Playwright) at **390 / 768 / 1024 / 1440 / 1920px**, using an overflow detector (`scrollWidth > clientWidth`) and an essential-info visibility check. Info-hierarchy principle: on mobile, keep only high-value trading info inline (LIVE, Regime, P&L, Signals); tuck diagnostics behind a control.

**Status legend:** ✅ verified pass · 🟡 fixed, re-verify pending · ⬜ not yet audited · ⚠️ known debt

---

## Global baseline (good news)
At 390px there is **no page-level horizontal scroll** on the pages checked — the body is already contained. The real problems are **content clipped inside horizontal-scroll containers** (info hidden off-screen) and dense chrome burying trading info. Those are what this audit targets.

## Shared components

| Component | 390 | 768 | 1024 | 1440 | 1920 | Status / notes |
|---|---|---|---|---|---|---|
| **TopBar** | ✅ | 🟡 | 🟡 | 🟡 | 🟡 | Info-hierarchy done (PR #70/#71): LIVE/Regime/PnL/Signals inline; diagnostics (health, version, timestamp, Kite, layout toggle) behind a 44px settings dropdown on <lg, inline on desktop. 390px verified: no page scroll, essentials visible, 44×44 targets. Re-verify 768–1920 after deploy. |
| **SystemFlowDiagram** | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 | Fixed (PR #69): header wraps, fluid clamp() title (no mid-word break), funnel scrolls horizontally on mobile. Re-verify pending. |
| **MarketCommandBar** (ticker) | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 | Usability fix (PR #72): status/freshness now **sticky-right** (was ~500px into scroll, unreachable); "Indices" label hidden <sm so NIFTY/BANKNIFTY sit nearer view. Re-verify reachability across widths. |
| **OpportunityCard** | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 | Clipping fixed (PR #73): 4-col Entry/Stop/Target/RR grid overflowed ~14–18px from wide prices → `minWidth:0` + clamp() value font (desktop identical). |
| **data-table** (shared tables) | 🟡 | ✅ | ✅ | ✅ | ✅ | Sticky headers + tighter cells + momentum scroll (PR #73). **Frozen first column** shipped (PR #75, opt-in `--freeze`): Track Record (Symbol), Compare (Metric labels). Desktop unchanged. |
| **Charts — candlestick** (lightweight-charts) | 🟡 | 🟡 | 🟡 | ✅ | ✅ | Responsive via ResizeObserver; **dvh full-height fix** (PR #74) resolves Safari/Chrome URL-bar clipping. Touch (pinch/drag/long-press) + crosshair native to LWC v5. ⏳ live touch-on-real-candles verify pending Monday (weekend Kite token). |
| **Charts — recharts** (analytics) | ✅ | ✅ | ✅ | ✅ | ✅ | `ResponsiveContainer width="100%"` + fixed heights — responsive. Verify tap-tooltips on touch. |
| **Charts — MiniChart** | ✅ | ✅ | ✅ | ✅ | ✅ | ResizeObserver, width 100% — responsive. |
| **BackendStatusNotice** | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Full-width banner; check wrap/size on mobile. |
| **MobileNav** (bottom bar) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Verify no content overlap; main bottom padding; safe-area inset. |
| **Sidebar** | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Off-canvas on mobile — verify open/close + overlay + safe-area. |
| **PortfolioSection** (card) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Grid `repeat(auto-fit, minmax(100px,1fr))` — check on 390; CSV/badge header row wrap. |
| **RunningTradesMonitor** (card) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | 4-col stat grids — verify they stack. |
| **Charts** (research/stock) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `max-width:100%`, responsive height, tooltip/crosshair/legend/axis + pinch/drag on touch. |
| **Buttons / button rows** | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Header button rows wrap; targets ≥44px on mobile. |

## Public pages (priority order)

| Page | Status | Notes |
|---|---|---|
| **/dashboard** | ⬜ | priority 2 |
| **/research** (+ chart/compare/track-record) | ⬜ | priority 3 — portfolio cards, tables, sections |
| **/terminal** | 🟡 | flow diagram fixed; panel header ("TERMINAL · Leaderboard…", 416px hidden) + opp-card clip (14–18px) remain |
| **/analytics** | ⬜ | priority 5 — charts + tables |
| **/screeners** | ⬜ | table + teaser |
| **/oi-intelligence** | ⬜ | panels |
| **/market-intelligence** | ⬜ | |
| **/watchlist** | ⬜ | |
| **/journal** | ⬜ | table |
| **/agents** | ⬜ | |
| **/stock/[symbol]** | ⬜ | chart-heavy |
| **/**, **/login**, **/register** | ⬜ | |

## Remaining responsive debt (known, from inspection)
- **Terminal panel header** — "TERMINAL · POLLING · Leaderboard opens after scoring…" clips ~416px on mobile (needs wrap/scroll).
- **opp-cards** — every opportunity card clips 14–18px (inner row/progress bar slightly too wide).
- **MarketCommandBar** — far-right status dot sits at the end of the scroll on mobile (minor).

## Verification checklist (applied per component/page)
- [ ] No clipped content · [ ] No horizontal page scroll · [ ] No hidden trading info
- [ ] Touch targets ≥44px · [ ] Tooltips/chart interactions usable
- [ ] Typography via clamp()/responsive utilities · [ ] Desktop appearance unchanged
- [ ] **Landscape orientation** · [ ] **Dynamic text scaling 120/150/200%**
- [ ] **Safari on iPhone + Chrome on Android** · [ ] **Safe-area insets + on-screen keyboard**

**Responsive audit is NOT complete** until every shared component + every page row above is ✅ at all five breakpoints, AND the two post-sweep audits + journey validation below pass.

---

## Phase 2 — Post-sweep audits (after responsive sweep)

### Accessibility (WCAG 2.1 AA) — ⬜ not started
- [ ] Keyboard navigation (all interactive elements reachable + operable)
- [ ] Visible focus states
- [ ] ARIA labels / roles on custom controls (dropdowns, toggles, cards)
- [ ] Color contrast ≥ 4.5:1 (text), 3:1 (UI) — both themes
- [ ] Screen-reader pass (landmarks, headings order)
- [ ] **prefers-reduced-motion** honored (ticker flashes, framer-motion, pulses)
- [ ] **High-contrast / forced-colors mode**
- [ ] **Live-region announcements** for live market updates (aria-live on price/PnL/regime)

### Mobile performance — ⬜ not started
- [ ] LCP < 2.5s · [ ] CLS < 0.1 · [ ] FCP < 1.8s (mobile, throttled)
- [ ] Bundle size / code-split heavy libs (framer-motion, chart libs)
- [ ] Hydration cost + interaction latency (INP)
- [ ] **30-min session memory watch** — detect polling / WebSocket / chart leaks

## Phase 3 — User-journey validation — ⬜ not started
- [ ] Anonymous visitor (landing → research teaser → login prompt)
- [ ] Logged-in free user (research → screeners → watchlist → portfolio)
- [ ] Premium user (full screener rows + gated features)
- [ ] Trader workflow (terminal → opportunity → take entry → track → journal)
- [ ] **Market-open workflow** (pre-open → open → live ticks flowing)
- [ ] **Session persistence** (refresh, browser restart, reconnect after network loss)

Each journey validated end-to-end at 390 / 768 / 1024 / 1440 / 1920.

## Phase 4 — Final production-readiness sweep — ⬜ not started
Every shared component × every page × every breakpoint × every journey × every
browser target × every a11y requirement × every perf metric. **Only then mark
the frontend Production Ready.**
