# PROJECT_STATE.md

> **STATUS: LIVE** · the authoritative answer to *"where are we?"* · last checkpoint: **2026-08-27**
>
> **Keep this file short.** It answers five questions — where are we, what are we doing, where did
> we stop, what's next, what's blocked — and then gets out of the way. Everything else is a link.
>
> | Question | Source of truth |
> |---|---|
> | How does the system work? | [`../CLAUDE.md`](../CLAUDE.md) |
> | *Why* did we decide X? | Claude Code project memory (the double-bracketed refs below) |
> | What changed, exactly? | `git log`, `gh pr list` — **never duplicated here** |
> | Where are we **now**? | **this file** |
>
> Refresh with `/checkpoint`. If the header date is more than ~2 weeks old, trust `git log` over
> this file and re-checkpoint.

---

## In one paragraph

The trading system is **live and trading**; the website is **publicly readable but not commercially
launched** (no payments, no legal pages). The last three weeks were spent making stock *selection*
honest and measurable (Phase 0/1 shipped, Phase 2 built but flag-OFF), and the last two days on a
brand-new **SEO** workstream that put ~2,100 stock pages into Google. The immediate next move is
finishing SEO Phase 2 (internal linking, Core Web Vitals, GSC monitoring). The largest *unstarted*
body of work is the commercial launch: payments, legal pages, monitoring.

## Live system check — 2026-08-27

Verified against the running system, not from docs:

| | |
|---|---|
| Engine | **live**, `v4.2.1`, mode `AGGRESSIVE`, scheduler running, Kite token fresh |
| Books | Swing **20/20 (FULL)** · Long-term 19/20 · Momentum 16 active + 4 pending |
| SEO | sitemap **2,117 URLs**; `/stock/*` returns `X-Nextjs-Prerender: 1` → ISR confirmed working |
| Backend | Railway `web-production-2781a` healthy. **`api.stockswithgaurav.com` does not resolve (NXDOMAIN)** — frontend talks to the Railway URL directly, so nothing is broken |

---

## Workstreams at a glance

| Workstream | State | One line |
|---|---|---|
| `seo` | 🔴 **ACTIVE** | Phase 1 shipped + live; Phase 2 (linking, CWV, GSC) not started |
| `selection` | 🟡 recently active | Phase 0/1 live; Phase 2 built, flag OFF, awaiting calibration evidence |
| `portfolio` | 🟢 steady | All three books running; audit decisions D1–D6 answered |
| `engine` | 🟢 steady | No open work. FVG-Tap in alert-mode soak |
| `ui-ux` | 🟡 paused | Affordance pass + Universe tab shipped; a11y and responsive matrix still open |
| `platform` | 🔵 **largest unstarted** | Commercial launch gates: payments, legal, monitoring, backups |

---

## `seo` — ACTIVE

**NOW** — nothing in flight. Phase 1 is merged and verified live.

**STOPPED AT** — PR #182 merged 2026-08-26 ~23:10 IST, ISR fix confirmed in production.
Priority items 1–5 of the original SEO plan are **done**: SSR stock pages, per-page metadata,
sitemap, JSON-LD, GSC domain property verified via Hostinger DNS.

**NEXT** (ordered)
1. **Internal linking** — item 6 of the plan, the last unstarted Phase-1 item.
2. **Core Web Vitals** — baseline is already passing (perf 95, LCP 2.1s, CLS 0.054, TBT 110ms).
   Two known items: ~52KiB unused JS, and a WebSocket on public pages that blocks bfcache.
3. **Re-add Vercel Speed Insights** — PR #2 was closed 2026-08-27 (4-month-old `pnpm-lock.yaml`
   would have conflicted). Reinstall fresh against current deps; the feature is still wanted.
4. **Monitor GSC** — indexation coverage of the 2,113 equity URLs; watch for soft-404s.
5. Later: sector pages, screener landing pages, `/learn` content, freshness signals, backlinks.

**BLOCKED** — nothing hard-blocked. Note `vercel` CLI is installed but **logged out**; `vercel login`
needs browser OAuth from Gaurav if any CLI-side work comes up.

**Why it's built this way** → `[[seo-programmatic-stock-pages]]` (two-tier render + the four traps
that would deindex the long tail). Deliberately deferred: `/stock/*` still renders dashboard chrome
— that's a CWV decision, not a bug. No `generateStaticParams` list by explicit call; ISR only.

## `selection` — recently active

**NOW** — idle, in the **validation phase**. No new engine features until the current ones prove out.

**STOPPED AT** — PR #180 (2026-08-23) scoped SMC-as-score to the horizon it was validated on.
Phase 0 + Phase 1 are deployed with flags ON. Phase 2 is merged but **`PHASE2_SMC_AS_SCORE=0`
(OFF)**, `PHASE2_HORIZONS=SWING`.

**NEXT**
1. Accrue live shadow data on Phase 2 — do **not** flip the flag without it.
2. Read the calibration report before any tuning. Bootstrap says the weight variants are
   statistically **tied** — do not re-optimise on 43 days of data.
3. Keep the honest funnel reporting from PR #169 in view; LT hit rate is honestly ~7%.

**BLOCKED** — nothing. Gated on *time and data*, not on a decision.

**Why** → `[[phase2-smc-as-score]]`, `[[selection-engine-teardown-phase0]]`,
`[[calibration-validation-phase]]`, `[[explainability-principles]]`.

## `portfolio` — steady

**NOW** — running. Swing book is **at cap (20/20)**, so new ideas cannot enter until something exits.

**STOPPED AT** — PR #161 (2026-08-19) shipped the upstream `admission_gate` in **shadow** mode.
Shadow data only accrues on position creation, so it fills slowly.

**NEXT**
1. Let admission-gate shadow data accumulate, then compare against the enforcing `entry_gate`.
2. Watch the swing cap — at 20/20 the book is inert to new signals.

**BLOCKED** — nothing.

**Why** → `[[two-portfolio-gates]]` (the two gates mean *opposite* things by "admission" — the single
most confusable thing in this codebase), `[[giveback-rule-nogo]]` (D6: give-back rule was backtested
and **rejected**), `[[portfolio-selection-audit-2026-08]]` (D1–D5 answered: hold, hold, new-rules-
apply-to-new-entries-only, no price floor, no turnover change).

## `engine` — steady

**NOW** — no open work. The SMC engine is production-proven and deliberately left alone.

**STOPPED AT** — FVG-Tap has been in **alert mode** (not auto-traded) since 2026-05-26, a
validation soak.

**NEXT** — decide FVG-Tap's fate on soak evidence. No new engine features during validation phase.

**BLOCKED** — nothing.

**Why** → `[[fvg-tap-live-alert-mode]]`, `[[risk-engine]]`, `[[regime-governor-phase1]]`,
`[[component-failure-not-system-failure]]`.

## `ui-ux` — paused

**NOW** — nothing in flight.

**STOPPED AT** — PRs #177/#178 (2026-08-23): Stock Universe promoted to its own tab, and a
site-wide affordance pass making interactive things look interactive.

**NEXT**
1. Full responsive matrix — [`../MOBILE_AUDIT_FINDINGS.md`](../MOBILE_AUDIT_FINDINGS.md) still has
   unaudited rows (iPhone/Android/tablet, portrait + landscape, all pages).
2. Open a11y items in [`../ACCESSIBILITY_REPORT.md`](../ACCESSIBILITY_REPORT.md) (C1–C3 resolved in
   PR #81; several remain, some are design calls for Gaurav).
3. Cross-browser check; error/empty-state audit.
4. Refresh [`MODULE_STATUS.md`](MODULE_STATUS.md) — not updated since 2026-07-12, predates Universe
   and the SEO surface.

**BLOCKED** — nothing hard. Some a11y items need Gaurav's design decision.

## `platform` — the largest unstarted body of work

**NOW** — nothing in flight. This is the gap between "site is public" and "site is a business".

**STOPPED AT** — [`../LAUNCH_CHECKLIST.md`](../LAUNCH_CHECKLIST.md) is the master tracker and is
still **live**; its `🔒` gates are unmet.

**NEXT** (roughly ordered; all are launch gates)
1. **Legal pages** — Privacy, Terms, Refund, standalone Disclaimer. Cheapest gate to close.
2. **Payments** — Razorpay Subscriptions, lifecycle, webhooks + idempotency, GST invoices, trial.
3. **Secrets audit** — full sweep, rotate anything exposed.
4. **Monitoring + backups** — uptime/error alerting; DB backup + restore drill.
5. Optional: point `api.stockswithgaurav.com` at the Railway backend (currently NXDOMAIN; DNS is on
   **Hostinger**, not Vercel).

**BLOCKED**
- Payments and legal need **Gaurav's** business decisions and accounts — not engineering blockers.
- Positioning is **LOCKED as Option A** (analytics, not advice; not SEBI-registered). Every copy,
  legal and schema decision must stay in that lane.

**Why** → `[[launch-prep-arc-2026-07]]`, `[[prod-deploys-from-main]]`,
`[[railway-startup-healthcheck-rule]]`, `[[dashboard-db-volume-growth]]`.

---

## Recently shipped

Deliberately not a changelog — `gh pr list --state merged --limit 30` is the real record. This is
only enough to orient, mapping recent PR ranges to workstreams:

| PRs | Workstream | Arc |
|---|---|---|
| #181–#182 | `seo` | SSR stock pages, sitemap, JSON-LD, ISR fix |
| #179–#180 | `selection` | SMC as a ranking factor (flag OFF) |
| #174–#178 | `selection` `ui-ux` | Stock Universe page + tab + affordance pass |
| #167–#171 | `selection` | Phase 0/1 — honest, measurable selection funnel |
| #160–#161 | `portfolio` | the two admission gates |

## Housekeeping

- Branch `chore/project-continuity` holds the continuity system itself (docs only) and is
  **not yet pushed**.
- Three stale PRs closed 2026-08-27 (#1 obsolete Copilot draft, #2 Speed Insights → carried to
  `seo` NEXT, #4 FVG-Tap research → REJECTED verdict recorded). All branches retained on origin.
