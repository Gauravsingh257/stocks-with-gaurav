# PROJECT_STATE.md

> **STATUS: LIVE** · the authoritative answer to *"where are we?"* · last checkpoint: **2026-09-15**
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

## Aug-end → Sep-1st-week change audit — 2026-09-13 (read-only)

**Scope: 2026-08-23 → 09-07 only** (July changes deliberately excluded).

**Entry: NOTHING CHANGED.** No entry-model, gap, confirmation, entry-type or RR/target change
lands in this window — Anchor10 is 09-13, outside it. So *no* entry-quality improvement can be
attributed to this change set. Any entry improvement seen later belongs to Anchor10.

**Selection** — 08-23/24: Phase 0 real inputs (`ec31896`), honest L1∧L2∧L3 funnel (`c6e455f`),
Phase 2 SMC as a ranking factor scoped to SWING (`7d01e2b`/`94d4e8a`), plus two *loosening*
fixes — quality gate no longer requires every provider (`8b25ff0`) and unclassified sector no
longer skips a stock (`44aa119`). 08-29: `source_door` (measurement only).

**The one that actually changed what reaches the portfolio — `4fe80ae`.** The exceptionalism
final gate ran LAST and rewrote `final_selected` on every record without consulting layer1/2/3,
so it silently discarded the funnel decision: **17.6% of SWING and 22.2% of LONGTERM selections
had FAILED Layer 1** and survived by that path. Measured effect in `signals_log`, selections that
failed Layer 1:

| month | selected rows | L1-failed | share |
|---|---|---|---|
| 2026-07 | 47,788* | 37,539 | **78.6%** |
| 2026-08 | 1,481 | 210 | 14.2% |
| 2026-09 | 731 | 0 | **0.0%** |

<sub>*July truncated by the 50k export cap; the trend is unambiguous.</sub>

**Risk** — 08-31 only: stale cull made reachable (`74e2082`, dead ~7 weeks), per-book patience
Swing 20d / LT 45d (`7100b37`), `exit_rule_health` diagnostic (`814f595`), stagnation shadow
(`19285e7`, observes only).

### ⚠️ The open concern — the tightening may have cut the right tail

Matching 69 closed positions to their nearest prior scan row (approximate — 57 could not be
matched, the pre-`position_provenance` gap):

| | n | win | mean | median | med days |
|---|---|---|---|---|---|
| scan row **passed** L1 | 47 | 40.4% | **+0.86%** | −5.01% | 11 |
| scan row **failed** L1 (readmitted, now excluded) | 22 | 45.5% | **+5.44%** | −2.25% | 11 |

Identical median holding period, comparable creation dates — so **no maturity confound**. But the
+4.59pp gap is **p=0.109, not significant**, and **fragile**: removing SCANSTL (+51.18%) and BI
(+42.10%) collapses it to +1.32%. Two trades of 22 carry the effect.

**Hypothesis worth monitoring, NOT acting on:** Layer 1 is a conventional quality screen, and
outlier winners often fail conventional screens — both top performers were L1 failures. Removing
readmission may systematically trim the fat right tail while correctly removing junk. This is the
single thing most worth watching as `NEW_SELECTION` matures. **Do not loosen the funnel on this
evidence** — it is directional only, and `4fe80ae` fixed a real integrity defect.

**Now an explicit research metric (PR #185).** `python -m scripts.phase1_calibration` →
`l1_counterfactual` compares forward returns for stocks Layer 1 **now excludes** despite qualifying
as exceptional against the stocks it selects. It works at scan level, so it does not need the
excluded stocks to ever become trades. Rules: one observation per stock-day; withheld below 30
labelled 20-day outcomes; never pools across the 08-23 fix date. **Data status 2026-09-13 (after the backfill):** post-fix label rows 39,743 — 24,024
with 5d and 11,966 with 10d, but **0 with 20d**, which the metric gates on. The first post-fix
20-day window only closes around 09-21. The buckets are already large: SWING 334 selected vs 149
excluded-but-exceptional stock-days; LONGTERM 305 vs 150 — about 9 excluded stock-days per scan day
per horizon. So ≥30 labelled per bucket needs roughly four labelled post-fix days. **Earliest
honest read: ~09-25**, after re-running the backfill. Run it server-side: it uses the `REPLACE`
join, which is too heavy for a request path. Do not lower the 20d gate to read the 10d labels
early.

**Data incident (2026-09-07) — root-caused and monitored; see the `selection` section.** The
"median 75" first reported here was a measurement artefact: `ranking_runs.quality_passed` holds two
different quantities (ranking engine ~1,500 vs validation Layer-1 ~400), and pooling them hid what
actually happened — the ranking engine passed 0 all day and validation's first scan degraded.

## Provenance audit — 2026-09-13 (read-only)

**Provenance cannot be traced through the recommendation link, and the lifecycle ledger's
provenance fields are unusable.** Established, not assumed:

- `trade_lifecycle.algorithm_hash` is a **single value (`88bb0bee5688`) across all 143** SWING/LT
  rows, and `engine_version` likewise — both are stamped at *backfill* from **today's** env, not
  captured at trade time. `setup`, `recommendation_json` and `strategy_version` are **0/143**.
  `is_legacy=1` on every row: every SWING/LT record is a retroactive reconstruction.
- The `position.recommendation_id → stock_recommendations` link is broken by row recycling —
  position ids run to **1,045,380** while the immutable `research_track_record` ledger covers
  **206–519**; only **26/155** positions resolve.

**What does survive: capability markers** — which code path wrote the row.
`arm_ref_price` (arm-on-tap, from 07-07), `position_size`/`atr_pct` (risk engine, from 07-09),
`source_door` (from 08-29). Cohorts below are built from those, not from dates alone:

| cohort | n | resolved | stop-width med / max | RR@entry | conf med | outcome |
|---|---|---|---|---|---|---|
| `OLD` (no arm/sizing, ≤07-06) | 68 | **97%** | 7.40% / **40.2%** | 3.00 | 73.8 | win 55%, mean **+4.69%**, PF 3.14, 33d |
| `NEW_MECHANICS` (07-07→08-20) | 36 | **94%** | 5.23% / 14.3% | 3.00 | 80.0 | win 47%, mean **+2.33%**, PF 1.66, 14d |
| `NEW_SELECTION` (≥08-23) | 41 | **39%** | 5.00% / 8.0% | 2.33 | 76.6 | withheld — 25 still open |
| `AMBIGUOUS` | 10 | 100% | — | — | — | excluded from every comparison |

**The one mature comparison — OLD vs NEW_MECHANICS, both ~95% resolved, so censoring does NOT
apply.** Point estimates favour OLD (win 55→47%, mean +4.69→+2.33%, PF 3.14→1.66) but the gap is
**not significant** (permutation p=0.206, n=66 vs 34). The mechanism is visible in the exit mix:
STOP_HIT **20% → 47%** while STALE_EXIT fell **39% → 24%**, and average loss *worsened*
**−4.82% → −6.70%**. Tighter stops converted soft near-flat exits into full stop losses. Tail risk
did improve at the extreme (worst −15.44% → −10.41%) but losses beyond −8% became *more* frequent
(5% → 21%). This is a **shape change in the loss distribution, not a clean improvement**, and it is
the single most important thing to re-test in October.

**Correction to the 2026-09-13 framework run:** it reported RR-at-entry improving 1.65 → 3.00
across the risk boundary. At **position** level OLD is already median **3.00**; the 1.65 came from
ledger rows that include research ideas with a different target field. **The RR improvement is not
confirmed** — treat RR as UNCHANGED (and *lower*, 2.33, in `NEW_SELECTION`).

**Provenance capture FIXED 2026-09-13 (PR pending → `position_provenance`).** New append-only
table written at *both* creation doors, after the position commit so it can never block a write:
engine_version + algorithm_hash + all 22 selection/risk flags **as they were at capture**;
`scan_id` + `signals_log_id`; setup, entry_type and SMC evidence; confidence; entry geometry
(stop width, RR, entry gap); sector; regime; source_door; and an immutable **snapshot** of the
recommendation rather than a reference to it. Unresolvable fields are **named** in `missing`, so a
later audit can tell "we looked and it wasn't there" from "nobody looked".

> **Design rule — never store a pointer to something mutable.** A foreign key into recycled
> `stock_recommendations` rows decays into a *false* link, which is worse than no link because it
> still resolves.

**`scan_id` is the unlock.** `signals_log` was *always* durable and already holds the entire
candidate universe per scan — every symbol's confidence, layer1/2/3 passes, `final_selected`,
`rejection_reason` and `layer_details` (the SMC evidence). Positions simply never recorded which
scan produced them. From now on "**why this stock over the alternatives available at that time?**"
is answerable, with `selected_count` / `universe_scanned` as the denominator.

**RISK-LOSS FINDING RESOLVED — expected consequence plus an already-fixed bug, NOT a risk-model
problem.** Decomposing the −4.82% → −6.70% average-loss move by exit reason:

| cohort | STOP_HIT losses | soft exits (STALE/STRUCTURE/TREND) |
|---|---|---|
| `OLD` | n=13, avg **−6.48%** | n=16, avg −1.60% to −3.10% |
| `NEW_MECHANICS` | n=16, avg −7.16% | **n=2** |
| `NEW_SELECTION` | n=14, avg **−5.36%** (planned stop 5.00%) | n=0 |

The stop-loss itself did **not** deteriorate — in the newest cohort it is the *best* of the three
(−5.36%). The headline move is a **mix effect**: OLD's average loss was softened by 16 soft exits,
`NEW_MECHANICS` had only 2. And the `NEW_MECHANICS` window (07-07 → 08-20) is **exactly** the
period the stale cull was unreachable (died 2026-07-09 when the risk engine shipped trend-break
default-ON, fixed 2026-08-31 — `[[flag-on-but-unreachable]]`). So the one mature OLD-vs-NEW
comparison is **contaminated by a known bug that is already fixed**, and therefore *understates*
the current engine. No risk-logic change is warranted.

**Remaining hard gaps (historical only — capture is fixed going forward):** `chart_entry_json` is populated **6/143** and
`chart_exit_json` **3/143**, so chart-level review is not possible; per-trade `smc_evidence`
(BOS/CHoCH, OB, FVG, liquidity, MTF) is unrecoverable because the recommendation rows were
recycled; no market-cap field, so the small-cap concentration in `NEW_SELECTION` cannot be
quantified. Fixing capture is a prerequisite for ever answering "are we picking better stocks?".

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
| Railway | **4** services in `accomplished-passion`/production: `web`, `engine`, `scanner` (Scanner Suite cron **and** the universe OHLC snapshot both research engines read — its deploys republish it), `Redis`. Selection flags live on `web` only |
| Data health | `GET /api/research/data-health` → `usable: true`, snapshot `2026-09-13`, 2,143 symbols, 9/9 shards, **96h** shard TTL (expires Thu 09-17 14:32 IST); `scan_health` exit 0 |

---

## Workstreams at a glance

| Workstream | State | One line |
|---|---|---|
| `seo` | 🔴 **ACTIVE** | Phase 1 shipped + live; Phase 2 (linking, CWV, GSC) not started |
| `selection` | 🔴 **ACTIVE** | Anchor10 live, validating 09-14/15 · 09-07 data blackout root-caused, monitoring live (PR #185) · snapshot TTL raised to 96h, verified |
| `portfolio` | 🟡 in validation | Admission-gate metrics now complete on both doors; no threshold set yet |
| `engine` | 🟢 steady | No open work. FVG-Tap in alert-mode soak |
| `ui-ux` | 🟡 **ACTIVE** | Search Phase 1 live (PRs #186/#187): validated stock, company and sector search; 404 dead-ends fixed · a11y + responsive matrix still open |
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

**Data-integrity incident, 2026-09-07 — root-caused; monitoring LIVE (PR #185, `8822452`).**
Every ranking-engine run that Monday (both books) passed **0 of ~2,190** stocks, and the first
validation scan collapsed (Layer 1: 150 vs ~410). The cause is **deterministic**. Both engines read
the scanner's universe OHLC snapshot in Redis. Its price shards live **50h** while the manifest
lives 7 days, and the scanner republishes only on deploy and at weekday post-close. So Friday's
shards expire Sunday ~17:45 IST, before Monday's 08:30 scans, and the orphaned manifest makes the
loader return `{}` silently. The ranking engine has no fallback; validation limps on per-symbol
fetches. Earlier Phase-0 Mondays survived **only because weekend pushes redeployed the scanner** —
09-05/06 was the first quiet weekend. Impact: EDELWEISS and GLAND (SWING) were promoted at 09:22 IST
from the degraded scan, but the full-data scan an hour later picked both too. **CHENNPETRO** (LT)
came from a degraded scan, was *not* re-selected on 09-08, and expired without entering.
→ `[[universe-ohlc-snapshot-weekend-expiry]]`

Live now: `GET /api/research/data-health` (`usable`, `shards_alive`, `expires_at`);
`scripts/scan_health.py` (predictive snapshot check, zero-pass, Layer-1 collapse; `--replay` fires on
08-14/08-17/08-18/09-07 only); and `.github/workflows/scan-health.yml` (weekday 17:00 IST and Sunday
10:30 IST — a failed run is the alert). Verified in prod on 2026-09-13: endpoint `usable: true`,
check exit 0, dispatched Action green. **Shard TTL raised to 96h on 2026-09-13**
(`UNIVERSE_OHLC_TTL_SEC=345600`, `scanner` only). The variable is read solely at
`services/universe_ohlc.py:59`, for shard retention and a diagnostic, so no selection logic is
affected. Verified live: the post-change boot run published `2026-09-13` (2,143/2,173 symbols,
98.6%) with **95.99h** shards, and the Scanner Suite results were identical before and after
(286.7s vs 286.6s, same hit counts). With the real writer TTL, the Friday → Monday case, the
failed-Friday-refresh case and the holiday-Monday case all pass; the old 50h TTL fails the same
Friday. A Friday or Sunday `scan-health` failure now means a real problem — for example
post-close refreshes failing on consecutive days. Rollback: remove the variable (the scanner
redeploys). Known cosmetic: `web` still computes `stale` against 50h, so after 50h it can show
`stale: true` alongside `usable: true`. `usable` and `expires_at` are authoritative, and
`scan_health` ignores `stale`.

**Decisions:**
1. ~~Raise the snapshot TTL~~ — **DONE 2026-09-13**, 96h on `scanner`, verified (see above).
2. **Still open:** whether the ranking engine should fall back to per-symbol data when the snapshot
   is missing. That changes selection behaviour, so it was deliberately not done.
3. ~~Re-run the forward-returns backfill~~ — **DONE 2026-09-13**, server-side on `web`. 212,625 of
   220,833 stock-day pairs labelled in 78.7s (1% had no price series). Labels now run 04-25 → 09-12:
   5d through 09-05, 10d through 08-28, 20d through 08-14. **The backfill is still manual** — it
   must be re-run for new windows to fill in (`[[universe-ohlc-snapshot-weekend-expiry]]` for how
   it was run).

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

1. **Anchor10 — VALIDATED 2026-09-20 (read-only). Verdict: KEEP `ENTRY_ANCHOR_MAX_GAP_PCT=10`.**
   *Data gate first:* `/api/research/data-health` → `usable: true`, snapshot day `2026-09-18`,
   2,133 symbols, 9/9 shards, ~46h TTL left. Not a blind-data read.
   *Source:* `/api/research/anchor-shadow-status` (64 sessions logged). From **2026-09-14** the A/B
   rows are identical by design — `cfg_current` reads live scan entries, so Config A *is* the live
   Anchor10 config. The post-09-14 rows are therefore direct measurements of production:

   | Session | Ideas | Actionable % | Avg dist from entry | Median remaining RR | Extended >10% |
   |---|---|---|---|---|---|
   | Baseline 08-31 → 09-11 (at 30%) | 15–20 | 25–40% | 12.0–16.0% | 0.22–1.36 | 45–60% |
   | 2026-09-14 | 20 | 100% | 2.3% | 2.63 | 0 |
   | 2026-09-15 | 20 | 95% | 2.5% | 2.63 | 0 |
   | 2026-09-17 | 13 | 92.3% | 3.0% | 2.62 | 0 |
   | 2026-09-18 | 13 | 84.6% | 3.3% | 2.62 | 0 |

   All five shadow criteria (C1 count stability, C2 actionable ≥60%, C3 avg distance ≤6%,
   C4 median RR ≥2.0, C5 ≥3 stable sessions) **PASS**; `overall: READY`. Entry distance is the
   mechanism: extended ideas (>10% from CMP) went 45–60% → **0**.
   **Two caveats, neither changing the verdict:**
   - Idea count fell 20 → 13 on 09-17/18. Session-over-session drop is 0% and 13 sits just below the
     15–20 baseline band, but the count comes from *selection* upstream (the 09-18 ranking run
     reports `selected_count: 14` for SWING) — the anchor re-prices entries, it does not filter
     ideas. Watch it; do not attribute it to Anchor10.
   - Sessions **09-16 and 09-19 are missing** from the shadow log (`evaluated_to: 2026-09-18`); the
     daily shadow job did not record them. Worth a look, separate from this verdict.
   Rolling back to `30` would restore 12–16% average entry distance and sub-1.4 median RR.
   **No change made.**
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
4. **Swing vs Long-Term convergence — traced 2026-09-20 (read-only). Not a serving bug; a
   book-differentiation gap.** `PHASE1_UNIFIED_FEED=1` on web, so both endpoints serve
   `_authoritative_rows(horizon)` → `get_latest_signals_scan_report(horizon)`, which **does** filter
   by horizon and picks distinct scans (`VAL-SWING-2026-09-18-e0a21219` vs
   `VAL-LONGTERM-2026-09-18-c9b70325`). The lists are not literally the same.
   **The exact convergence point is layers 1–2.** For 2026-09-18 both horizons report an identical
   funnel — `total 2200 → layer1_pass 246 → layer2_pass 1340` — and diverge only at layer 3
   (413 vs 501) and final selection (**14** swing vs **17** LT). **12 of the 14 swing names are also
   LT names (86% overlap).** Horizon first enters in `services/validation_engine.py`, and only as
   *re-pricing*: `_smc_score(..., horizon)`, `base_risk = 2.0×ATR` (LT) vs `1.3×ATR` (swing),
   `target_mult` 3.5 vs 3.0, and the holding-period label. One candidate pool is priced two ways;
   nothing selects for a 6–24 month thesis.
   **The "only 3 candidates" part is not a bug at all:** `FREE_TIER_LIMIT = 3`
   ([research.py:204](../dashboard/backend/routes/research.py#L204)) gates anonymous/free callers,
   and since both books sort by confidence, a logged-out visitor sees the same top 3 in both —
   which is what "identical candidates" looked like.
   **Decision needed:** whether LONGTERM should get horizon-specific selection criteria (quality,
   fundamentals, trend persistence) rather than swing's pool at a wider stop.
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

**2026-09-23 — SRB and options trading PERMANENTLY RETIRED** (merged to `main` 2026-09-23 ~23:55 IST,
market closed; branch `chore/retire-srb-and-options`, commit `a05721b`).
**The SRB live path is retired permanently** — no scan, no auto-execute, no GTT trail, no Telegram
signal. **Options signal generation and execution are retired permanently** — no `OPTIONS-*` emitter,
no OI short-covering alert, no option execution buttons, and no code path that can resolve an NFO
contract or place an option order. **All historical SRB/options records are preserved** untouched in
`signal_history/signals_2026.csv` and `trade_ledger_2026.csv`. **`/oi-intelligence` remains live and
read-only** (snapshot job kept; it reads the option chain for analytics and can place nothing).
Evidence: SRB 19% win rate, PF 0.57, −14.25R over 58 genuine trades, negative in all six months;
option-contract signals 31% win rate, PF 0.82, −28.04R over 155 trades. Nothing ever executed —
Kite rejects every order for want of a static IP — so the record is theoretical and that block was
protective, not costly.
- **Removed (live paths only):** the SRB scan / auto-execute / GTT-trail blocks in
  `smc_mtf_engine_v4.py`; `strategies/second_red_break/live_{scanner,executor}.py`;
  `option_monitor_module.py`; `trade_executor_bot.py` — the only NFO order path — and its launcher
  thread in `run_engine_railway.py`; the OI short-covering scan and its Telegram alerts; the option
  execution buttons on zone-tap alerts; and the option-signal emitters in `engine/options.py`
  (`evaluate_signals`, `_send_trade_signal`, `_check_standalone_oi_signals`,
  `_register_option_trade`, `_check_option_exits`, `_build_signal_reasoning`).
- **Kept deliberately:** `engine/options.py` tick store + directional-bias path (index context),
  `engine/oi_short_covering.py` (dashboard history only), the read-only `/oi-intelligence` page and
  its snapshot job, SRB `strategy/utils/backtest` for offline research, and **every historical
  record** in `signal_history/` and `trade_ledger_2026.csv`.
- **Guard:** `tests/test_no_option_execution_paths.py` (11 cases) fails if a retired module is
  imported again, an `OPTIONS-*` / `SECOND-RED-BREAK` emitter returns, or
  `find_option_tradingsymbol` / `place_gtt(` reappears. There was never an env flag to switch any of
  this off, so the protection has to be structural.
- **Found, NOT fixed (pre-existing):** `BankNiftySignalEngine._collect_morning_signals` calls
  `self.detect_session_low`, which does not exist (the method is `detect_session_low_break`), so the
  OI directional bias has been raising silently and `bias_locked` never becomes True — the OI bias
  filter on index setups is dormant. Left untouched: repairing it would *enable* a dormant filter and
  change index signal behaviour.
- **Validation:** full suite 1005 passed with the same 11 failures / 9 errors as `main` (all
  pre-existing, unrelated modules); engine boots in `BACKTEST_MODE`; ruff unchanged or better
  (engine 416 → 410).

**2026-09-20 — read-only operations audit. Three live faults confirmed, nothing changed.**

- **A. SRB replays the day's signal after a mid-session restart.**
  `strategies/second_red_break/live_scanner.py` holds `_signal_emitted`, `DayState` and
  `_last_candle_time` **in memory only** (`state_db` is used by `smc_mtf_engine_v4.py` alone). On
  boot `_ensure_daily_reset()` compares the date only, then `scan()` walks every 5-minute candle
  since 09:15, and `max_entry_hour=10` is tested against the **candle's** time, not the clock.
  **Scope is narrower than feared:** the main loop is gated by `is_market_open()` (Mon–Fri
  09:00–16:30 IST), so the nightly bot-commit restarts (20:12–21:04) never scan. Only a restart
  **inside the session** replays — exactly one to date, 2026-09-15 12:56.
  **Harm is proven, not theoretical:** `signal_history/signals_2026.csv` on `main` carries both
  pairs for 09-15 — `srb_NIFTY_20260915093003` + `exit_tgt_…104647` (WIN, `pnl_r 3.0`) **and**
  `srb_NIFTY_20260915125806` + `exit_tgt_…125808` (WIN, `pnl_r 3.0`). One setup is recorded as
  **two wins, +3.0R double-counted**, alongside a second live BUY attempt.
  **Options (all trading-path — owner decision):**
  1. *Persist day state* (emitted / trade_done / last candle) keyed by date+instrument. Exact, and
     the only option that also restores in-trade management; costs a new persistence path in the
     trading loop plus a rollover/corruption story.
  2. *Reject stale trigger candles* — emit ENTRY only when the breakdown candle is within ~2 bars
     of the clock. One condition, no storage, fails safe; does not restore in-trade state, and a
     genuine signal arriving during a restart is missed (already true today).
  3. *Deterministic `signal_id`* — today the id embeds the emit time (`…125806`), so the existing
     delivery dedup cannot recognise a duplicate. Keying it to the breakdown candle would let dedup
     suppress the replay on its own.
  **Recommended: 2 + 3 as the minimal reversible guard; 1 later, for in-trade continuity.**

- **B. Every push to `main` restarts all three services.** No `watchPatterns` in `railway.toml`,
  `railway-web.toml`, `railway-engine.toml` or `railway-scanner.toml`. Engine/web/scanner deployment
  timestamps coincide exactly; the engine restarted 09-15 21:04, 09-16 20:43 + 20:55, 09-17 20:52 +
  21:03, 09-18 20:12 + 20:30 — all triggered by the nightly bot commits
  (`chore(signals): archive`, `chore(shadow): stagnation observation`), which touch only
  `signal_history/**` and `docs/validation/**`. The live engine restarts about twice an evening for
  data it never reads.
  **Remedy (NOT implemented):** Railway honours `[build] watchPatterns` per service, and each
  service already points at its own config file. Each needs a generous set — engine:
  `smc_mtf_engine_v4.py`, `engine/**`, `engine_runtime.py`, `strategies/**`, `services/**`,
  `agents/**`, `config/**`, `utils/**`, `models/**`, `run_engine_railway.py`, `Dockerfile.engine`,
  `requirements*.txt`, `railway-engine.toml`; web: `dashboard/backend/**`, `services/**`,
  `scripts/start_web.py`, `Dockerfile`, `requirements*.txt`, `railway.toml`; scanner:
  `scripts/scanner_cron.py`, `services/**`, `Dockerfile.scanner`, `railway-scanner.toml`. Docs,
  `dashboard/frontend/**` and the bot's data commits would then restart nothing. **Trap:** shared
  code (`services/**`, `config/**`) must appear in *every* service's list — a missing pattern means
  a service silently runs stale code, which is worse than a restart. The dashboard's "Watch Paths"
  field is the same control and must not disagree with the file. This does **not** remove the need
  for the SRB guard: a crash-restart can still land mid-session.

- **C. Kite cannot place orders, and the engine's journal sync is rejected.**
  - *Kite static IP:* reconfirmed 2026-09-17 09:45 IST (deployment `794e7656`) —
    `SRB BUY order FAILED: NIFTY2692223250PE — No IPs configured for this app`. Every SRB order
    since at least 09-15 fails; the Telegram alert honestly reads `❌ FAILED`. Signals fire, nothing
    executes. The fix is on the Kite developer console (static IP allowlist), not in this repo.
  - *X-Sync-Key 401 — root cause found:* `TRADES_SYNC_KEY` is set on **web** (the validator,
    `dashboard/backend/routes/agents.py:27`) but is **absent from the engine service's 29
    variables**. `services/dashboard_sync.py:35` attaches the header only when the variable is set
    locally, so the POST arrives without it and web answers 401. The engine does have
    `DASHBOARD_URL`. Fix = copy `TRADES_SYNC_KEY` to the engine service (env change + restart —
    deliberately not done mid-session).

**NEXT** — ordered:
1. **`TRADES_SYNC_KEY` on the engine service** — closed trades still never reach the dashboard journal (401).
2. **Railway `watchPatterns`** — stop docs/frontend/bot commits restarting the live engine.
3. **`detect_session_low` — OPEN, deliberately NOT fixed.** `BankNiftySignalEngine._collect_morning_signals`
   calls `self.detect_session_low(...)`, which does not exist (the method is `detect_session_low_break`),
   so the OI directional bias raises silently, `bias_locked` never becomes True, and the OI bias filter on
   index setups is dormant. This is **index behaviour, not options**, and repairing it would *enable* a
   filter that has never run — it needs an explicit decision plus its own validation, so it was left
   untouched during the 2026-09-23 retirement.
4. **Kite static IP** — owner has deliberately left it unconfigured; no API order can fill until it is set.
5. Decide FVG-Tap's fate on soak evidence.

*(The former item "SRB duplicate guard" is obsolete: the SRB live path no longer exists, so a restart
cannot replay it.)* No new engine features during the validation phase.

**BLOCKED** — all four operational fixes need Gaurav: three touch production config, one touches
the trading path.

**Why** → `[[fvg-tap-live-alert-mode]]`, `[[risk-engine]]`, `[[regime-governor-phase1]]`,
`[[component-failure-not-system-failure]]`.

## `ui-ux` — ACTIVE (stock page Phase 1 + minimal header + search Phase 1 shipped)

**NOW** — nothing in flight. Stock page Phase 1A/1B, the minimal header and search Phase 1 are all live and verified in production.

**STOPPED AT** — **2026-09-15: stock page single source of truth, PR #190 (merge ffb4908), verified live, then an engine incident caused by the merge time.**
- **Verified in production** on 9 stocks: FCL, HDFCBANK, ITC, M&M, ABB, TCS, RELIANCE, SBIN, 360ONE.
  - Company, sector, P/E and market cap are identical in key metrics and the analysis card.
  - The card CMP equals the chart caption price, labelled "Live · 12:57 IST".
  - The snapshot close is shown only as "Price used for ratios".
  - Details are in the PR #190 comments.
- **⚠ INCIDENT (engine):** PR #190 merged at **12:56 IST, during market hours**. Git Bash `TZ=Asia/Kolkata date` printed UTC, which read as 07:26. Every main push redeploys the engine (no `watchPatterns`).
  - **Cause of the duplicates:** the SRB live scanner keeps its day state in memory, so the restarted engine replayed today's candles and re-fired the 09:30 SRB NIFTY entry at 12:58.
  - **What went out:** a second live BUY attempt (failed: Kite "No IPs configured"), a duplicate Telegram entry (`srb_NIFTY_20260915125806`) and a duplicate exit (`exit_tgt_NSE_NIFTY 50_20260915125808`).
  - **Engine afterwards:** healthy, 0 active trades, Daily PnL 3.0R.
  - **Pre-existing, seen in the same logs:**
    - **No static IP is set on the Kite app,** so all API orders fail. The 09:30 SRB order failed too.
    - **Engine→dashboard sync gets HTTP 401** "Invalid or missing X-Sync-Key".
  - **Owner decisions needed:**
    - correct the duplicate Telegram alert or not
    - SRB fix: persist day state, or reject stale breakdown candles (trading logic)
    - Kite static IP
    - X-Sync-Key
    - per-service `watchPatterns`
  - **Rule:** push to main only before 09:15 IST, and get IST from PowerShell or Railway.
  - This doc was committed on a branch and deliberately **not merged the same day**, because a same-day restart would re-fire SRB again. **Merged to main 2026-09-23 ~22:40 IST** (market closed, engine scan loop idle — a restart in that window cannot replay SRB).
- **Root cause:** `/stock/<symbol>` composes two independent backend products, and each re-fetched the same facts.
  - **Key metrics** read the weekly `stock_universe` snapshot.
  - **The analysis card** read `/api/search-stock` → `analyze_stock`:
    - `name` was just the ticker
    - sector, P/E and market cap came from `services.fundamental_analysis`, a separate provider fetch with a 24h disk cache, the provider's coarse sector ("Basic Materials"), P/E rounded to 1 dp, and D/E not divided by 100 for values ≤ 10
    - CMP came from `price_resolver` (live)
  - **A third price:** key metrics labelled the snapshot close (up to a week old) "Last price". On 09-15 FCL showed ₹57.72 there against ₹54.32 in the card and chart.
- **Fix (display data only):** `analyze_stock` now attaches the canonical universe row as `reference` and uses its company name. `fundamentals` is kept untouched as the confidence input, and the reference is read *after* scoring. The frontend contract is written in the header comment of `app/stock/[symbol]/page.tsx`:
  - company, sector and all ratios → snapshot (dated)
  - current price → price resolver (source + time), shown in the card and chart caption
  - the snapshot close is labelled "Price used for ratios"
  - shared formatting lives in `lib/stockFormat.ts`
- **Tests:**
  - `tests/test_stock_search_reference.py`: identity from the universe; confidence, recommendation, levels and fundamentals identical with or without a reference; failure-safe.
  - Node tests: 44 pass.
- **Found, NOT changed (it is ranking input):** `fundamental_analysis` still divides D/E by 100 only when > 10, so FCL scores 0.87 and ITC 3.29 instead of 0.01 and 0.03. That is the bug already fixed in the universe refresh. It feeds `fundamental_score`, and `ranking_engine` reads `raw_debt_equity`, so fixing it is a ranking change that needs a decision and a calibration check.
- **Phase 2 — designed, NOT started (needs a go):**
  1. **Price and verdict hierarchy above the fold.**
     - **Header:** company, `NSE:ticker`, sector chip, and the current price large, with day change, source and time.
     - **Freshness row:** "Price: Live 12:57 IST · Fundamentals: 12 Sept snapshot".
     - **"At a glance" strip:** four tiles built only from the existing readers — Valuation (P/E vs sector), Profitability (ROE and net margin), Balance sheet (D/E), Price trend (52-week and 1-year). Each shows a tone, a label and one deterministic summary sentence. Descriptive only.
     - **Mobile:** price first, tiles 2×2, chart below.
     - **Decisions needed:**
       - **Fresh price:** today the price comes from a server render cached up to 1h. Option: a cached `/api/research/quote/{s}` using the live cache or delayed yfinance, never Kite REST from public traffic, because the engine shares the token.
       - **52-week high:** the snapshot stores only `pct_from_52w_high`, and the chart covers 6 months. Option: store it in the refresh, or give the chart a year.
       - **The card's Watchlist/Strong Buy badge** reads like advice under the Option A positioning.
  2. **Peer comparison.**
     - **Data:** the `fetchSectorRows` rows the page already loads, so no backend change.
     - **Peers:** 5–8 in the same curated sector, nearest by log market cap, turnover as tie-break. Fewer than 3 falls back to the sector median only.
     - **Layout:** one row for the stock, one for the sector median, one per peer. Columns: market cap, P/E, P/B, ROE, net margin, D/E, revenue growth, 1-year return. Every cell uses the same `readX()` tone and `lib/stockFormat`; the stock's row is highlighted.
     - **Mobile:** a scrollable table with a sticky first column. Link to `/universe?sector=`.
  3. **Missing ROE and fundamentals.** ROE is present for 649 of 2,348 stocks (28%); P/E 2,090; D/E 2,082.
     - **Three kinds of gap:** "Not reported" (null), "Not meaningful" (loss-making P/E, lender D/E) and "Stale" (snapshot older than 8 days).
     - **Unavailable cards:** de-emphasised, with a coverage hint.
     - **Verdict tiles:** a tile whose inputs are all missing says "Not enough data" and never takes a tone. Missing is never read as good (NULL, never 0).
     - **Data lift:** re-run the throttled `scripts/backfill_fundamentals_quarterly.py` (ROE from filings) outside market hours.
     - **Separately:** the `fundamental_analysis` D/E ≤ 10 bug (a ranking decision).

**Earlier — 2026-09-15: stock page Phase 1A (chart accuracy) + 1B (readable metrics), PR #189 (merge a3463e6), verified live.**
- **1A — root cause:** `/stock/FCL` showed ASX:FCL (FINEOS) under Fineotex's metrics.
  - The page embedded TradingView's `tv.js` widget with a **bare ticker**, and its `exchange` option is ignored.
  - **TradingView does not license NSE data to embedded widgets at all.** Every `NSE:` symbol tested returns "only available on TradingView", even from our own domain.
  - So ambiguous tickers charted another exchange's company, and others (M&M, BAJAJ-AUTO) showed an empty chart. An `NSE:` prefix only turns wrong into empty.
- **1A — fix:**
  - `components/NseStockChart` (lightweight-charts) draws candles from `/api/research/chart-data`, the same `<SYMBOL>.NS` series as the metric price. The TradingView widget is deleted.
  - `lib/tradingview` builds every tradingview.com link as `NSE:` + (`-`→`_`, `&` kept), checked against TradingView symbol search. All 7 link sites use it.
- **1B — readable metrics:** `lib/metricInterpretation` gives each metric a tone, label and one-line note.
  - P/E, P/B and net margin compare with the **sector median**, falling back to the NSE median when fewer than 8 stocks report.
  - D/E is "Not comparable" for **Finance**, with wider ranges for Power, Utilities, Infra, Realty and Telecom.
  - ROE, growth, promoter holding and price metrics use fixed ranges. A promoter stake under 20% reads as neutral "Widely held". Revenue growth over 100% reads as caution "Unusually high".
  - Medians reuse the sector list the page already fetched for peer links (`fetchSectorRows`), so there is **no backend change**. No metric calculation changed.
  - UI: grouped cards, larger values, a legend, icon plus text pills, tone tokens ≥ 4.5:1 in both themes, and a one-column phone layout. The missing `.stock-analysis-grid` mobile rule is added.
- **Verified in production (desktop 1440 + mobile 390):** FCL, M&M, BAJAJ-AUTO, J&KBANK, 360ONE, ABB, ITC and HDFCBANK. For each:
  - the company is correct
  - the chart is captioned `NSE: <symbol>`
  - the chart's last close equals the metric price exactly
  - no TradingView iframe loads and the link is correct
  - there is no horizontal scroll

  Unit tests: 40 `node:test` cases pass (21 new). Rollback: revert PR #189 (frontend only).
- **Seen but not changed (next phase):** the SMC analysis card beside the chart (`StockCard`, Tier-2 live analysis) shows its own sector and P/E ("Basic Materials", P/E 213.9 for FCL). The key metrics show "Chemicals" and 222x from the weekly snapshot. Two sources on one page is worth reconciling.
- **Local-testing gotcha:** `dashboard/frontend/.env.local` blanks `NEXT_PUBLIC_BACKEND_URL`, overriding `.env.production`. Build and start with the `.env.production` backend variables exported. Even then, a browser on `localhost` is CORS-blocked from the backend, so client-side fetches such as chart-data need a relay or a production check.

**Earlier — 2026-09-15: minimal global header, PR #188 (merge 624fe81), verified live.**
The header had shown operator telemetry to every visitor. It is now only: hamburger (mobile), a wide
Search pill (md+) or 44px search icon (mobile), theme toggle, account.
- **Removed from the header:**
  - `WS LIVE · v734` — outages are covered by the MarketCommandBar status dot and BackendStatusNotice
  - `Bear · 43` Market Health chip and its 5-min `marketState` poll — duplicated the Command Center
    and the tape strip
  - Daily PnL / Signals — the engine's own numbers; the Command Center shows Signals and "Your day"
  - Terminal Layout, together with `LayoutClient` state and the deleted `MultiPanelLayout`
    (two link cards)
- **Moved to an admin-only "Operator" section of the account menu** (`components/OperatorStatus.tsx`):
  - engine mode and circuit breaker
  - Kite status/TTL and Refresh/Connect Kite (`/api/kite/login`)
  - DB/WS, backend version, snapshot time, and a Product Health link
  - An **amber dot on the avatar** flags Kite needing a login, so moving the button can't hide an
    outage.
  - The section mounts only while an admin has the menu open. The header itself no longer opens an
    engine WebSocket, so visitors save one socket and one poll per tab.
- **Also fixed:** the account menu now closes on an outside press or Escape. Its old full-screen
  backdrop only covered the 56px bar, because `backdrop-filter` makes the header the containing
  block for `position: fixed`.
- **Verified:**
  - locally with a mocked admin and a regular user: dot, Operator section, Kite button target, menu
    closing, no operator UI for non-admins
  - on production at 1440 and 390: header shows only the search pill (or icon), theme and Sign In;
    no horizontal scroll; the pill, mobile icon and Ctrl+K open search; `reliance` → RELIANCE
  - rollback = revert PR #188 (frontend only)

**Earlier — 2026-09-14/15: global search Phase 1, PRs #186 + #187, verified live.**
The audit came first. Since 07-12 only **6 of 153** visitors had used search, and **11 of 24 stock
searches (46%) opened a 404**: the palette offered any ticker-shaped query as a stock, and a page
name like "watchlist" put a fake WATCHLIST stock above the real page. Now:
- `lib/searchIndex.ts` is one pure, unit-tested search core (19 `node:test` cases, run with
  `node --test dashboard/frontend/lib/searchIndex.test.mjs`). Stocks come only from the published
  universe: `/api/research/universe/sitemap`, ~37 KB gzipped, cached 12h via
  `lib/stockIndexLoader.ts`. It matches symbol, company name and sector (with everyday aliases,
  e.g. "banking" → Finance). Typo tolerance is a **fallback only**, and liquidity breaks ties.
  **No backend change.**
- `CommandPalette` groups results as Stocks / Sectors / Pages and covers all 12 nav pages; a no-match
  falls back to Stock Universe, and Ctrl/⌘+K is kept. `SearchPill` replaces the bare icon: "Search
  stocks, sectors, pages… ⌘K" on md+, a 44px icon on phones.
- `/universe` honours `?sector=` / `?q=` via `useSearchParams` under Suspense, keyed on the link.
- **Verified in a real browser on production:**
  - `RELIANCCE` → RELIANCE (HTTP 200)
  - `watchlist` + Enter → /watchlist
  - `sbin` → SBIN only
  - the Pharma and IT sector links apply their filter, including from /universe itself
  - 390px mobile works
- **Analytics:** `search_opened {trigger}`; `global_search` (the event Product Health already counts)
  with `result_type`, `position`, `action`, `input`, `fuzzy`, `trigger`, `index`; and
  `search_no_results`. The verification runs sent ~20 test search events on 2026-09-14; discount them.
- **Only production verification caught two bugs, not the tests.** Client navigation mounts the new
  page before `window.location` updates (fixed with `useSearchParams`), and typo matches cluttered
  exact ones (now a fallback).

**Search, next:**
1. **Measure ~2026-09-28:** search users (baseline 6/153), the 404 dead-end rate (baseline 46%),
   top `search_no_results` queries and result positions. Product Health counts `global_search`; the
   new props need a first-party events query.
2. **Phase 2 (not started, needs a go):** SWG status badges on stock results (in today's ideas, on the
   watchlist, held, screener hit, sector leadership), quick actions (chart, add to watchlist, analyze),
   and recents + trending on the empty state.
3. **Phase 3:** move the Research, Universe and Terminal search boxes onto the shared core.
4. **Before surfacing analysis more widely:** the on-demand analysis labels stocks "Strong Buy /
   Watchlist / Avoid" (`services/stock_search_analysis._recommendation`). Relabel within the
   analytics-not-advice positioning.

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
| **#186** + **#187** 2026-09-14 | `ui-ux` | global search Phase 1: validated stock / company / sector autocomplete, typo fallback, all pages, visible ⌘K pill, search analytics, sector deep links |
| **#185** 2026-09-13 | `selection` | 09-07 data-blackout monitoring: `data-health` endpoint, `scan_health.py` + workflow, `l1_counterfactual` metric |
| direct-to-main 2026-09-13 | `portfolio` `selection` | write-time `position_provenance` (`87a10f6`); OLD-vs-NEW audit framework (`b3d6617`) |
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
