# ♿ Accessibility Report — stockswithgaurav.com

> **STATUS: LIVE** · workstream: `ui-ux` · last substantive update: 2026-07-06
> A11y findings; some fixes shipped (PRs #78/#81), several items still open and unactioned.
> Current project state lives in [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md).

**Date:** 2026-07-05 · **Standard:** WCAG 2.1 AA (with AAA notes) · **Method:** code review + Playwright DOM/contrast scans on the deployed site (dark theme, 360–390px). Screen-reader + real-device passes are explicitly out of scope for automated tooling and flagged as manual.

**Status:** partial — safe global fixes shipped; several findings recommended (some are design decisions left to the owner).

---

## ✅ Fixed & shipped
| # | Fix | WCAG | PR |
|---|---|---|---|
| A1 | **Global keyboard focus ring** (`:focus-visible` outline) — every interactive element shows a visible focus indicator for keyboard/AT users; pointer users unaffected | 2.4.7 Focus Visible | #78 |
| A2 | **prefers-reduced-motion** — animations/transitions reduced to near-instant for users who request it (kills pulses, shimmer, ticker flashes, orb drift) | 2.3.3 Animation from Interactions | #78 |
| A3 | **Accessible names for search inputs** — terminal ticker search + 2 research inputs had only a placeholder; added `aria-label` | 1.3.1, 4.1.2 | #79 |
| C1 | **Dim-text contrast** — `--text-dim` was 2.8:1 (dark) / 2.4:1 (light); now `#6b7fa6` (~4.8:1) / `#5f6b7d` (~5.0:1), **passes AA** | 1.4.3 | #81 |
| C2 | **Touch targets** — `.tap-44` extends primary icon buttons (hamburger, alerts, refresh×2) to a 44px tap area via invisible overlay (no visual change) | 2.5.5 | #81 |
| C3 | **Live regions** — removed the over-announcing ticker `role="status"`; added a scoped visually-hidden `aria-live="polite"` for market regime + signal count (P&L excluded — too frequent) | 4.1.3 | #81 |

---

## 🔴 Findings — recommended before launch

### C1 — Low-contrast dim text (**fails AA**)
`--text-dim: #4a5a7a` on `--bg-base: #080d1a` computes to **≈ 2.8:1** — below the 4.5:1 required for normal text (WCAG 1.4.3). It's used widely for hints, timestamps, axis labels, and secondary metadata.
- **Fix:** lighten `--text-dim` to ~`#6b7fa6` (≈ 4.5:1) — a one-line palette change.
- **Note:** this is a deliberate *visual* change (dim text becomes lighter), so it's left for owner sign-off rather than applied unilaterally. `--text-primary` (15.8:1) and `--text-secondary` (6.8:1) both pass.

### C2 — Touch-target sizing
Playwright found **18 interactive elements < 40px** on the terminal at mobile width. WCAG 2.5.8 (AA) requires ≥ 24×24px (most text links pass); best practice / 2.5.5 (AAA) is 44×44px. TopBar theme/settings were already bumped to 44px.
- **Fix:** audit **icon-only** buttons (chart links, small toggles, badges-as-links) and pad to ≥ 44px on mobile. Pure text links are acceptable.

### C3 — Live-region announcements for market data
`MarketCommandBar` and `BackendStatusNotice` use `role="status"` (polite live region) ✅. But:
- The **TopBar** live trading data (Daily P&L, Regime, Signals) has **no `aria-live`** — screen readers won't announce changes.
- A per-second-updating ticker inside `role="status"` risks **over-announcing** (SR reads every tick).
- **Fix (needs design):** wrap the key numbers (P&L, Regime, Signals) in a **scoped `aria-live="polite"`** region that updates on *meaningful* change (debounced), not every tick; keep the raw ticker `aria-hidden` for SR and expose a summarized live region instead.

### C4 — Forced-colors / high-contrast mode
No `@media (forced-colors: active)` handling. In Windows High-Contrast, custom `rgba()` borders/backgrounds may disappear.
- **Fix (minor):** add a `forced-colors` block ensuring borders use `CanvasText`/system colors on key surfaces (cards, inputs, focus ring). Lower priority.

---

## ⏳ Requires manual / assistive-tech validation (cannot automate)
- **Full screen-reader pass** (VoiceOver iOS + NVDA/JAWS): landmark structure, heading order, table semantics, live-region behavior, dropdown/menu announcements.
- **Keyboard end-to-end**: tab order across TopBar dropdown, Sidebar, MobileNav, modals; no focus traps; Esc closes overlays; focus returns on close.
- **Dynamic text scaling** 120/150/200% (browser + OS) — no clipping/overlap.
- **High-contrast mode** on real Windows.

---

## Summary
| Area | State |
|---|---|
| Focus visible | ✅ fixed |
| Reduced motion | ✅ fixed |
| Input labels | ✅ fixed (search inputs) |
| Contrast | ✅ C1 fixed (both themes ≥ 4.5:1) |
| Touch targets | ✅ C2 fixed (primary icon buttons 44px; content chips meet 24px AA by design) |
| Live regions | ✅ C3 fixed (scoped aria-live; ticker no longer over-announces) |
| Forced colors | 🟡 C4 — minor, deferred |
| Screen reader / keyboard E2E | ⏳ manual (only remaining a11y item) |

**Automated + code-level a11y findings are resolved (C1–C3).** The only remaining a11y work is the **manual screen-reader + keyboard-E2E pass** (and optional C4 forced-colors), which cannot be automated.
