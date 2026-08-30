# PROJECT_STATE.md

> **STATUS: LIVE** · the authoritative answer to *"where are we?"* · last checkpoint: **2026-08-31**
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

## Live system check — 2026-08-31

Verified against the running system, not from docs:

| | |
|---|---|
| Engine | **live**, `v4.2.1`, mode `AGGRESSIVE`, scheduler running, Kite token fresh |
| Books | Swing **14/20** (12 active + 2 pending) · Long-term **16/20** (13+3) · Momentum 17 active — room in both after the stale-exit cull |
| SEO | sitemap **2,117 URLs**; `/stock/*` returns `X-Nextjs-Prerender: 1` → ISR confirmed working |
| Backend | Railway `web-production-2781a` healthy. **`api.stockswithgaurav.com` does not resolve (NXDOMAIN)** — frontend talks to the Railway URL directly, so nothing is broken |

---

## Workstreams at a glance

| Workstream | State | One line |
|---|---|---|
| `seo` | 🔴 **ACTIVE** | Phase 1 shipped + live; Phase 2 (linking, CWV, GSC) not started |
| `selection` | 🟡 in validation | Phase 0/1/2 **all live** (Phase 2 = SWING only); measuring, not tuning |
| `portfolio` | 🔴 **ACTIVE** | Stale-exit outage fixed + live; per-book patience Swing 20d / LT 45d |
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

**NOW** — Phase 0 + 1 + 2 are **all live on the `web` service**, in validation. No new engine
features until the current ones prove out.

**STOPPED AT** — PR #180 (2026-08-23) scoped SMC-as-score to the horizon it was validated on.
Verified in Railway env on 2026-08-29, **not** from code defaults:
`PHASE0_KITE_OHLC`, `PHASE0_NO_SYNTHETIC`, `PHASE0_REAL_SECTORS`, `PHASE1_STRICT_FUNNEL`,
`PHASE1_UNIFIED_FEED`, `PHASE1_SECTOR_UNKNOWN_STRICT`, `EXCEPTIONALISM_ENABLED`,
`REGIME_GOVERNOR_ENABLED`, `SECTOR_LEADERSHIP_SCORING_ENABLED`,
`SECTOR_DIVERSIFICATION_ENABLED` — all `=1`. **`PHASE2_SMC_AS_SCORE=1`**, with
`PHASE2_HORIZONS` unset so it defaults to `SWING` only; Long-Term is untouched by design.
Live ideas carry `smc_evidence` (confirmation_score, tier), so Phase 2 is demonstrably scoring.

> **Correction:** this file previously said `PHASE2_SMC_AS_SCORE=0`. That was the *code default*,
> read from `services/phase2_ranking.py` instead of the deployed environment. Check Railway env
> for flag state, never the code default — the whole design is that env overrides the default.

**NEXT** — two findings from 2026-08-31 outrank everything else here, both **unverified**:

1. **Confidence score is inversely related to outcome.** On 93 closed trades: highest-conf
   quartile mean **−1.05%**, win **17.4%**; lowest-conf quartile **+3.90%**, win **58.3%**;
   correlation **−0.142**. Hypothesis worth testing first: `swing_alpha_agent` *downgrades*
   confidence when CMP is far from entry (−30% if gap >5%), so high conf ≈ chasing and low conf ≈
   pullback setups. If real, this outranks every exit-rule and slot change. **Verify before acting.**
2. **`/api/research/swing` and `/api/research/longterm` return identical candidates** — same three
   symbols, same confidence scores, differing only in the `setup` label. Odd for 1–8 week vs
   6–24 month horizons, and it makes the books less independent than assumed. Also only **3**
   candidates are served where ranking runs report `selected_count: 20`.
3. Measure Phase 2's effect — but see `docs/validation/phase2-validation-report.md`: the effective
   sample is **2 positions, not 7** (only KRISHANA and ANTHEM came through the Phase-2 door).
4. Bootstrap said the weight variants are statistically **tied** — do not re-optimise on 43 days.

**BLOCKED** — nothing. Gated on *time and data*, not on a decision.

**Why** → `[[phase2-smc-as-score]]`, `[[selection-engine-teardown-phase0]]`,
`[[calibration-validation-phase]]`, `[[explainability-principles]]`.

## `portfolio` — ACTIVE (exit discipline restored)

**NOW** — nothing in flight. Both books have room again: Swing 14/20 used, LT 16/20.

**STOPPED AT** — 2026-08-31, the stale-exit outage found and fixed:

- **The bug:** the stale/dead-money cull sat inside the `else` of the trend-break branch, so the
  risk engine (2026-07-09, flags default-ON) made it **unreachable**. `PORTFOLIO_STALE_EXIT` read
  `"1"` the whole time. Dead ~7 weeks → positions sat 38–87 days going nowhere. → `[[flag-on-but-unreachable]]`
- **Fixed and LIVE:** `PORTFOLIO_STALE_EXIT_INDEPENDENT=1` set on `web`. **Verified fired** — 8
  positions closed and journaled 2026-08-31 00:31 (4 SWING, 4 LT; combined −2.30%, avg −0.29%).
- **Per-book patience, live:** Swing **20d**, LT **45d** (`PORTFOLIO_STALE_EXIT_MIN_DAYS_SWING`
  / `_LONGTERM`). GRINDWELL at 39d was correctly KEPT — a flat 20-day rule would have culled it.
- **`source_door` on positions** (PR-less, `535aa87`) — deployed, column verified in prod, but
  **still 0 non-null**: no position has been created since. Unproven until one is.
- 2 positions closed manually on the 8-week Swing horizon (EVEREADY 110d, GRAUWEIL 90d).

**NEXT**
1. Watch 2026-09-01 open: 4 free Swing + 4 free LT slots. Does anything actually promote? Only
   **3 candidates** exist on the feed, so this tests whether slots were ever the constraint.
2. `python -m scripts.exit_rule_health` after any risk/exit change — exits non-zero on an
   unexplained silence. Currently 0.
3. Admission-gate shadow review 2026-09-18 — **export Redis first, 30-day TTL**.

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

**NOW** — nothing in flight. The commercial launch — the gap between "site is public" and "site
is a business" — remains the largest unstarted body of work.

**STOPPED AT** — two separate threads:
- *Continuity system:* fully verified. PR #183 merged 2026-08-26; hooks, `/checkpoint` and
  `/where-are-we` all confirmed working in live sessions. The Sunday curator **ran for the first
  time 2026-08-30 03:31 UTC and correctly did nothing** (no drift → no PR), which is the intended
  silent week. Known blind spot: it is a *cloud* agent, so it cannot read Railway env and will
  always reason from code defaults — it must never be trusted on flag state.
- *Experiment tracking (new 2026-08-31):* an audit found **four** shadow/alert experiments
  running, **three for 86–96 days with no review date**. Six calendar reminders now exist
  (`collab.shreesingh@gmail.com`), each naming the flag, the file, and forcing a
  promote/retire/extend decision — plus a **monthly recurring audit** with the rediscovery
  commands so this cannot silently recur.
- *Stagnation shadow log:* shipped (`19285e7`) — a GitHub Action at 11:15 UTC Mon–Fri appending
  to `docs/validation/stagnation_shadow_log.csv`. **Has never run**; first fire is a weekday.
  Shipped but unproven.
- *Commercial launch:* [`../LAUNCH_CHECKLIST.md`](../LAUNCH_CHECKLIST.md) is the master tracker
  and is still **live**; its `🔒` gates are unmet.

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
| direct-to-main 2026-08-30/31 | `portfolio` | stale-exit outage fixed + enabled, per-book patience, `source_door`, exit-rule health check |
| #181–#182 | `seo` | SSR stock pages, sitemap, JSON-LD, ISR fix |
| #179–#180 | `selection` | SMC as a ranking factor (flag OFF) |
| #174–#178 | `selection` `ui-ux` | Stock Universe page + tab + affordance pass |
| #167–#171 | `selection` | Phase 0/1 — honest, measurable selection funnel |
| #160–#161 | `portfolio` | the two admission gates |

## Housekeeping

- Continuity system merged (PR #183, 6 commits, docs + `.claude/` only). Refresh this file with
  `/checkpoint`; reconcile it against reality with `/where-are-we`. The Sunday curator proposes
  fixes by PR and never edits state directly — disable at claude.ai/code/routines.
- Watch out when branching: cut new branches from `main`, not from a merged feature branch. A
  stale base silently proposes reverting the signal bot's `signal_history/` commits — PR #183
  nearly deleted 30 rows of live BANKNIFTY signals this way.
- Three stale PRs closed 2026-08-27 (#1 obsolete Copilot draft, #2 Speed Insights → carried to
  `seo` NEXT, #4 FVG-Tap research → REJECTED verdict recorded). All branches retained on origin.
