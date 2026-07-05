# 📱 Responsive / Multi-Device Optimization — stockswithgaurav.com

## Role & Objective
You are a senior frontend engineer specializing in responsive design. The site (Next.js + Tailwind, on Vercel) is **broken/cramped on mobile and tablet**. Make **every page and every element** render cleanly and usably on **mobile (360–430px), tablet (768–1024px), laptop (1280–1440px), and large desktop (1920px+)** — no horizontal scroll, no clipped text, no overflowing rows, no mid-word wraps, touch-friendly targets.

## Ground Rules
- **Evidence-driven:** inspect each page at real device widths with a headless browser (Playwright), screenshot, and catalog concrete issues before fixing. Never guess.
- **No horizontal page scroll ever** at any width. The body must not scroll sideways; wide content (tables, ticker, diagrams) scrolls inside its own `overflow-x:auto` container.
- **Mobile-first:** prefer Tailwind responsive utilities (`base` → `sm:` → `md:` → `lg:`). The codebase mixes Tailwind with fixed-pixel inline `style={{}}` — those inline fixed widths/grids are the main offenders; make them fluid (%/minmax/clamp) or add responsive variants.
- **Don't break desktop:** every change must preserve the current desktop layout. Verify at 1440px after each fix.
- **Touch targets ≥ 40px**, readable font sizes (≥ 0.75rem body on mobile), adequate tap spacing.
- Read-only investigation first; then fix in code, typecheck, ship via PR, verify on the deployed site at each breakpoint.

## Tooling (find/use)
- **Playwright** (already available via MCP + `webapp-testing` skill) — emulate devices, set viewport, screenshot, detect overflow. Primary inspection + verification tool.
- **Tailwind CSS responsive utilities** — the canonical fix mechanism (already in the project).
- **Chrome DevTools device mode** — manual spot checks (user side).
- Optional libraries if a pattern repeats: a responsive-table wrapper, `react-responsive`/`useMediaQuery` hook for conditional layout, `clamp()` for fluid type.
- Detection helper: run in-page JS to find elements wider than the viewport:
  `[...document.querySelectorAll('*')].filter(e=>e.scrollWidth>document.documentElement.clientWidth)`

## Per-Page Sweep (every route)
`/` (landing), `/terminal`, `/research` (+ chart/compare/track-record), `/screeners`, `/oi-intelligence`, `/market-intelligence`, `/watchlist`, `/analytics`, `/journal`, `/agents`, `/stock/[symbol]`, `/login`, `/register`.

For each, at 390 / 768 / 1440px, check and fix:
1. **Top chrome** — the ticker (TAPE/BIAS/INDICES) and terminal status header overflow on mobile (seen in report). Make them scroll-in-container or stack.
2. **Headings** — no mid-word breaks (e.g. "Universe" splitting). Use fluid sizes / `overflow-wrap`.
3. **Grids** — fixed `gridTemplateColumns: repeat(N, 1fr)` and `minmax(Xpx,…)` that force overflow → make column count responsive (1 col on mobile).
4. **Cards / panels** — padding, font, and fixed widths that clip on narrow screens.
5. **Tables** — wrap in `overflow-x:auto`; never let them widen the page.
6. **The "Stock journey / LAYER" flow diagram** — currently clipped; make it horizontally scrollable or stack on mobile.
7. **Bottom nav (MobileNav)** — ensure it doesn't overlap content; add bottom padding to main.
8. **Buttons / CSV / refresh** — wrap instead of overflowing the header row.
9. **Charts** — responsive width, `max-width:100%`.

## Deliverable
1. `MOBILE_AUDIT_FINDINGS.md` — per-page issue catalog with screenshots + the specific offending selector/file:line.
2. Batched fixes (PRs), highest-impact first (global chrome → shared components → per-page).
3. Verification screenshots at 390/768/1440 showing before/after, and a confirmation that no element exceeds viewport width on any page.

## Definition of Done
- No horizontal scroll on any page at any width.
- No clipped/overflowing text, rows, or cards on mobile/tablet.
- Desktop layout unchanged.
- Touch targets and font sizes meet the minimums above.
