# PROJECT_STATE.md

> **STATUS: LIVE** · the authoritative answer to *"where are we?"* · last checkpoint: **2026-09-13**
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
launched** (no payments, no legal pages). August was spent making stock *selection* honest and
measurable (Phase 0/1/2 all live, now in validation) and putting ~2,300 stock pages into Google via
a new **SEO** workstream. Early September closed a long-running integrity bug: the phantom "Entry
Triggered" Telegram alerts were never a gate defect — a **duplicate Railway deployment** was running
the whole app against its own throwaway database. Mid-September turned to the *instruments*: two
shadow programmes had accumulated evidence they structurally could not act on, and both were
measurement defects rather than failing subsystems. Fixing them put the first selection-quality
change of the validation phase into production (`ENTRY_ANCHOR_MAX_GAP_PCT=10`). The immediate next
move is validating that over two live sessions, then finishing SEO Phase 2. The largest *unstarted*
body of work is the commercial launch: payments, legal pages, monitoring.

## North-star objective — portfolio quality, OLD vs NEW

> Set 2026-09-13. **MEASURE FIRST → COMPARE FAIRLY → IDENTIFY CAUSE → THEN CHANGE.**

The question that now governs this project: **did the late-Aug / early-Sep 2026 engine changes
actually improve (1) stock selection, (2) entry quality, (3) risk/SL quality, (4) portfolio
outcomes?** Not whether any individual feature can be tuned — features are not optimised in
isolation until the portfolio-level answer is in.

**Target date: ~2026-10-15**, when the NEW cohort should be substantially resolved. Earlier runs
will reproduce the 2026-09-13 artefact and must not be used to justify a change.

**Framework:** `python -m scripts.portfolio_quality_audit [--book SWING] [--json out.json]` —
read-only, over the canonical `trade_lifecycle` ledger (reconciles 1:1 with the journals;
`/api/lifecycle/validate` currently all-green). It enforces three rules:

1. Cohorts split on **position creation**, never close date — a change is judged on the trades
   it *selected*.
2. **Outcome metrics are withheld** below 80% resolved / n≥30, and the report says why. Closed
   trades are a censored sample — winners stay open, stop-outs close fast.
3. **Entry-time metrics always reported** (stop width, RR-at-entry, entry gap, sector, regime,
   confidence) — fixed at open, so outcome cannot bias them. The honest early signal.

**Cohorts** (creation date, IST): `pre_risk` <2026-07-09 · `risk` 07-09→08-23 (PR #90) ·
`selection` 08-23→09-13 (PRs #170-180 + stale-exit) · `anchor10` ≥09-13. Deliberately *not*
boundaries: the 09-02 phantom-alert fix and the 09-13 measurement fixes — neither changed
trading behaviour.

**Already established (entry-time, censoring-immune — these hold):**

| Metric | pre_risk | risk | selection |
|---|---|---|---|
| stop width median / max | 5.0% / **39.2%** | 5.0% / 9.4% | 5.0% / 9.7% |
| stops >10% | **14** | **0** | **0** |
| RR at entry (median) | **1.65** | **3.00** | **3.00** |
| confidence (median) | 72.1 | 77.2 | 76.7 |

Attributable to the **risk engine** (`[[risk-engine]]`, PR #90): the stop cap and the RR floor.
Both improvements land exactly on the 07-09 boundary and neither can be produced by censoring.

**Not yet answerable** — win rate, expectancy, profit factor, winners vs losers, regime- and
sector-adjusted outcomes. `risk` is 62% resolved, `selection` 42%, `anchor10` has no positions.
**Do not read the outcome columns until the audit says `mature=True`.**

**Open measurement gaps:** (a) `context_json` sector/regime is not yet populated in production —
sector lands on the next `web` restart via the backfill, regime needs
`python -m scripts.lifecycle_enrich_context` run against the prod DB; (b) `entry_gap_pct` is not
captured at creation, so entry-gap attribution still relies on `arm_ref_price`, which exists only
on arm-on-tap rows (43/93).

## Live system check — 2026-09-13

Verified against the running system, not from docs:

| | |
|---|---|
| Engine | **live**, `v4.2.1`, scheduler running, Kite connected |
| Books | Swing 14 active · Momentum live. Journal holds **127** real closed trades (+26 re-seed dupes excluded) |
| Alerts | Telegram ↔ website **in sync**. The duplicate Railway project is silenced and has stayed silenced |
| SEO | sitemap **2,316 URLs**; `/stock/*` returns `X-Nextjs-Prerender: 1` → ISR confirmed working |
| Backend | Railway `web-production-2781a` healthy, deploy `afd78c8a`. **`api.stockswithgaurav.com` does not resolve (NXDOMAIN)** — frontend talks to the Railway URL directly, so nothing is broken |
| Railway | **4** services in `accomplished-passion`/production: `web`, `engine`, `scanner` (Scanner Suite cron), `Redis`. Selection flags live on `web` only |

---

## Workstreams at a glance

| Workstream | State | One line |
|---|---|---|
| `seo` | 🔴 **ACTIVE** | Phase 1 shipped + live; Phase 2 (linking, CWV, GSC) not started |
| `selection` | 🔴 **ACTIVE** | Anchor10 **live** since 2026-09-13 — first quality change of the validation phase; needs 2 live sessions |
| `portfolio` | 🟡 in validation | Admission-gate metrics now complete on both doors; no threshold set yet |
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

## `selection` — ACTIVE

**NOW** — **Anchor10 is LIVE.** `ENTRY_ANCHOR_MAX_GAP_PCT=10` set on `web` 2026-09-13 (PR #184),
replacing the historical 30%. Planned entries now re-anchor to `CMP − 0.5·ATR` when more than 10%
from price instead of 30%, so a LIMIT order no longer sits a third of the way below a running
stock. **Unexercised until Monday 2026-09-14** — it was enabled on a Sunday, so no scan has run
under it yet. Phase 0/1/2 remain live and in validation.

Two things the next session must not re-derive:
- **This value is a module constant read at import** ([validation_engine.py:44](../services/validation_engine.py#L44)).
  A Railway variable change alone does **not** restart the service — it needs an explicit
  `railway redeploy --service web`. Rollback to `30` needs the same. This is not the usual
  "remove the env var, no redeploy" flag.
- The A/B shadow **goes flat by design** from 2026-09-14: `cfg_current` reads live scan entries,
  so Config A now *is* Config B. The rollback signal must come from live idea quality, not from
  `anchor-shadow-status`.

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

**NEXT** — ordered:

1. **Validate Anchor10 on 2026-09-14 and 09-15.** Checklist: idea count not down >20% vs the
   ~15–20/session baseline; actionable% (within 5% of entry) up from the 25–40% Config-A norm;
   median remaining-RR ≥2.0 (Config A was running 0.22–1.36); no stop-inversion drops. Two clean
   sessions → keep; a count collapse → roll back to `30` **and redeploy**.
2. **Confidence-inversion — CLOSED 2026-09-13. The stated hypothesis is REFUTED; do not act.**
   The decisive scan-gap join was run (read-only, via `portfolio_journal → /api/portfolio/{id}`,
   using `arm_ref_price` as the scan-time price; n=43 of 93 carry it — it exists only for
   arm-on-tap rows). **The `swing_alpha_agent` penalty does not explain the inversion and is
   barely present in traded positions:** mean confidence is flat across the penalty bands
   (≤3% gap → 76.7, 3–5% → 76.1, >5% → 76.0) and `r(gap, confidence) = **+0.139**` — the wrong
   sign for the hypothesis. Only 9 of 43 positions fall in any penalty band at all, because the
   reachability filter already removes extended ideas before they can become positions.
   **A stronger candidate signal emerged instead:** `r(entry_gap, outcome) = **−0.266**`
   (p=0.083) — bigger arm-to-entry gap, worse outcome — with the 3–5% band at **−6.18% mean and
   0/8 wins**. That is independent support for the Anchor10 change already shipped, which
   tightens exactly this gap. Neither result is significant at n=43; **no ranking or confidence
   change is justified**. Full prior diagnosis retained in `[[confidence-inversion-is-confounded]]`.
   The pattern reproduces (sample identified: SWING, duplicates excluded, n=93, r=−0.138 vs the
   −0.142 reported; q4 −0.95%/21.7% win vs q1 +2.69%/60.9%) but it is **not significant**
   (permutation p=0.131 on the quartile spread, p=0.188 on the correlation) and is **heavily
   confounded**: q4 median hold **5 days** vs q1 **23 days**, exit mix 74% STOP_HIT vs 35%.
   The dominant relationship is `days_held → outcome` (**r=+0.246**) — which is an *outcome*, not
   a predictor. Removing 2 of 93 trades halves the effect. Residual worth watching: within the
   short (r=−0.332, n=32) and medium (r=−0.279, n=22) holding strata the sign persists, which the
   days_held confound does not explain. Superseded by the scan-gap join in item 2 above.
3. **OLD vs NEW engine audit — run 2026-09-13. Verdict: no detectable deterioration, one proven
   improvement, and the headline comparison is invalid as stated.** Splitting SWING closed trades
   on position creation at 2026-08-23 looks catastrophic (OLD n=78: 53.8% win, +3.03% mean,
   PF 2.23 → NEW n=15: 13.3% win, −2.63% mean, PF 0.41) — but that is **censoring, not decay**.
   The NEW cohort is only **48% resolved** (15 closed vs 16 still open at +0.68% mean, 8/16
   positive), and median hold falls monotonically by creation month — 40 → 23 → 11 → 7 → 2 days —
   which is what a shrinking exposure window mechanically produces. Matched on holding period
   (≤7 days) the gap is **not significant** (OLD −1.27% n=20 vs NEW −3.64% n=12, permutation
   p=0.215). The exit mix confirms it: NEW is 87% STOP_HIT because only fast resolutions have
   closed. **The one genuine, censoring-immune improvement is stop-width discipline** (set at
   entry, so outcome cannot bias it): p90 fell 12.05% (July) → 5.00% (Aug/Sep) and candidates
   above a 10% cap went 6 → 4 → **0 → 0**, attributable to the risk engine's stop cap
   (`[[risk-engine]]`, PR #90). Everything else is **not yet measurable** — re-run once the NEW
   cohort matures. Measurement gap: `sector` is not stored on position rows, so sector/regime
   attribution was impossible.
4. **`/api/research/swing` and `/api/research/longterm` return identical candidates** — same three
   symbols, same confidence scores, differing only in the `setup` label. Odd for 1–8 week vs
   6–24 month horizons, and it makes the books less independent than assumed. Also only **3**
   candidates are served where ranking runs report `selected_count: 20`.
5. Measure Phase 2's effect — but see `docs/validation/phase2-validation-report.md`: the effective
   sample is **2 positions, not 7** (only KRISHANA and ANTHEM came through the Phase-2 door).
6. Bootstrap said the weight variants are statistically **tied** — do not re-optimise on 43 days.

**BLOCKED** — nothing. Gated on *time and data*, not on a decision.

**Why** → `[[phase2-smc-as-score]]`, `[[selection-engine-teardown-phase0]]`,
`[[calibration-validation-phase]]`, `[[explainability-principles]]`.

## `portfolio` — in validation (exit discipline restored)

**NOW** — nothing in flight. The admission gate now records **complete metrics on both doors**
(PR #184, 2026-09-13). Enforcement stays OFF and every `PROMOTE_*` threshold stays a no-op.

**STOPPED AT** — three arcs, all closed:

- **2026-09-13 — the seed door was measuring nothing.** `seed_from_recommendations` passed
  `row_d.get("turnover_cr")` / `atr_pct`, but `row_d` comes from
  `running_trades LEFT JOIN stock_recommendations` and **neither table has either column** — so
  both were `None` 100% of the time (0/14, against 42/42 on the promotion door). Because the gate
  **fails closed on a null metric**, setting either liquidity threshold would have rejected that
  entire door — a quarter of all admissions — for *missing data*, not for quality. Now sourced from
  `risk_engine.liquidity_metrics()`, the same helper the promotion door already feeds the gate
  through, so the two doors are finally comparable. Also fixed: `admission_shadow_report.py --json`
  wrote only the summary, so the documented "export before the 30-day TTL" preserved counts and
  lost every per-candidate metric Step 4 calibrates on.

- **2026-09-02 — the phantom "Entry Triggered" alerts, solved.** Telegram kept announcing entries
  (SBIFUNDS, OLAELEC, NIACL, and many more daily) that never appeared on the site. The cause was
  **not** a gate bypass: a *second Railway project* (`affectionate-luck`, service misleadingly named
  `engine`) was running `scripts/start_web.py` — the entire dashboard app, portfolio tracker
  included — against its own **volume-less, ephemeral** `/app/dashboard.db`, holding the **same** bot
  token and channel. Its book was real to itself and invisible to the site. Fixed read-only-safely by
  **deleting `TELEGRAM_BOT_TOKEN` from that service** and restarting it — no code, no canonical
  change. Verified silent, and still silent 2026-09-10. → `[[duplicate-railway-project-phantom-alerts]]`
- **2026-08-31 — stale-exit outage fixed and live.** The cull sat unreachable inside the trend-break
  `else` for ~7 weeks while its flag read `"1"`. `PORTFOLIO_STALE_EXIT_INDEPENDENT=1` on `web` fixed
  it and fired immediately (8 positions closed 00:31, combined −2.30%). Per-book patience live:
  Swing **20d**, LT **45d**. → `[[flag-on-but-unreachable]]`
- **`source_door` now proven.** Positions created 2026-09-02 carry `promote_to_portfolio` /
  `seed_from_recommendations` — the attribution column works. This also answers the open question
  from the last checkpoint: promotion happens normally when slots free up, so **slots were never the
  binding constraint**.

**NEXT**
1. **Decide the duplicate project's fate.** It is silenced but still deployed and still auto-builds
   from `main`. Delete `affectionate-luck` once the channel has been clean for a few sessions — but
   establish *why* it exists first; nothing in the repo records it.
2. `python -m scripts.exit_rule_health` after any risk/exit change — exits non-zero on an
   unexplained silence. Currently 0.
3. **Admission-gate shadow review 2026-09-18 — calibration done 2026-09-13, value NOT set.**
   On the 56-row dataset `PROMOTE_MAX_STOP_WIDTH_PCT` splits cleanly by book and a single global
   cap is misleading: **SWING** n=40, median 5.00%, **max 9.73%** — a 10% cap is a *no-op* there;
   **LONGTERM** n=16, median 8.00%, max 14.66% — a 10% cap flags **31%** of the book. All five
   candidates above 10% are LT, all via `promote_to_portfolio`. LT uses 2.0×ATR base risk against
   swing's 1.3× *by design* ([validation_engine.py](../services/validation_engine.py)), so a
   global 10 would read as a guardrail while actually being a 31% cut to one book — the same
   one-threshold-two-books mistake the per-book stale-exit patience fixed.
   **Recommended: `PROMOTE_MAX_STOP_WIDTH_PCT=12`** — flags 2/56 (AVALON 14.66%, LTFOODS 12.68%),
   zero swing impact, and it is *safe if enforcement is ever flipped by accident*, which a 10 is
   not. Longer term, add per-book support mirroring `_STALE_EXIT_MIN_DAYS_BY_BOOK`, then
   SWING=10 / LONGTERM=12. Raw rows already exported to
   `docs/validation/admission_shadow_raw_2026-09-12.json` (the 30-day TTL would have eaten the
   08-19 rows on the review date itself). On the 56-row dataset, calibrate
   **`PROMOTE_MAX_STOP_WIDTH_PCT` first** — it is the only threshold that is populated 56/56 on
   *both* doors, needs no new data, and targets a known real failure (the 2026-08 audit's GARUDA
   entered through the no-policy door; one live row still shows a 39% stop width against a 10%
   cap). Distribution: median 5.0%, p90 9.9%, max 14.7% → a cap of 10 flags ~10%.
   `PROMOTE_MIN_PRICE` is the safe second. The two **liquidity** thresholds
   (`MIN_TURNOVER_CR`, `MAX_ATR_PCT`) must wait for a few sessions of the newly-plumbed seed-door
   metrics — they had zero seed-door coverage before 2026-09-13.

**BLOCKED** — nothing.

**Why** → `[[duplicate-railway-project-phantom-alerts]]` (why `entry_gate` was never the culprit),
`[[two-portfolio-gates]]` (the two gates mean *opposite* things by "admission" — the single
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
  commands so this cannot silently recur. **The same blind spot recurred at the deployment layer:**
  a duplicate Railway project ran the full app unnoticed for ≥2 weeks (see `portfolio`). The calendar
  now audits flags; nothing audits *deployments*.
- *Stagnation shadow log:* **running and proven.** The 11:15 UTC Mon–Fri Action has appended to
  `docs/validation/stagnation_shadow_log.csv` on every weekday since 2026-09-01. Review 2026-09-28.
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
| **#184** + env 2026-09-13 | `selection` `portfolio` | Anchor10 evaluation-window fix → `ENTRY_ANCHOR_MAX_GAP_PCT=10` **live on `web`**; admission-gate seed-door metrics; raw shadow export |
| **no-PR 2026-09-02** | `portfolio` | duplicate Railway project silenced (Telegram token deleted) — config-only, so it leaves **no git trace**; this row is the only record |
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
