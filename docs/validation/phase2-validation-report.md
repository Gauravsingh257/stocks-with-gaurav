# Phase 2 (SMC-as-score) — Validation Report

> **STATUS: LIVE** · workstream: `selection` · measurement date: **2026-08-29**
> Read-only analysis. No strategy, selection, entry, exit, threshold or portfolio behaviour was
> changed in producing this report.
>
> **Verdict: INCONCLUSIVE.** Unchanged, but for a stronger reason than first recorded.
>
> **CORRECTION (2026-08-30):** the effective Phase-2 sample is **2 positions, not 7.** A
> door-attribution audit established that 5 of the 7 post-cutover swing positions entered through
> `seed_from_recommendations`, a path that never applies Phase 2. Only **KRISHANA** and **ANTHEM**
> are confirmed Phase-2-influenced. Section 4's original framing treated all 7 as a post-Phase-2
> cohort; that was wrong. See §3a.
>
> With n=2 outcomes, no performance conclusion of any kind is available. Re-run at n≥30
> *Phase-2-attributed* closes — not merely 30 post-cutover closes.

## 1. What Phase 2 actually does

`PHASE2_SMC_AS_SCORE` converts SMC from a hard pass/fail gate into one factor in a
cross-sectional ranking. The substitution is deliberately count-neutral
([validation_engine.py:1024-1070](../../services/validation_engine.py#L1024)): the budget handed
to `select_top` is the number L1+L2+L3 *would* have selected on that same scan, so the flag
changes **which** stocks are chosen, never **how many**. Dropped candidates are stamped
`below_rank_budget`; kept ones receive `smc.phase2_score` and `smc.phase2_components`.

Scoring is z-scored **within a single scan**, never against a fixed threshold — an absolute cut
would drift with the market and quietly become a gate again.

## 2. Is Phase 2 actually live and contributing? — **YES, confirmed**

Verified in the Railway environment, not from code defaults:

| Flag | Value |
|---|---|
| `PHASE2_SMC_AS_SCORE` | **1** |
| `PHASE2_HORIZONS` | unset → defaults to `SWING` only (Long-Term untouched, per PR #180) |
| `PHASE0_KITE_OHLC`, `PHASE0_NO_SYNTHETIC`, `PHASE0_REAL_SECTORS` | 1 |
| `PHASE1_STRICT_FUNNEL`, `PHASE1_UNIFIED_FEED`, `PHASE1_SECTOR_UNKNOWN_STRICT` | 1 |
| `EXCEPTIONALISM_ENABLED`, `REGIME_GOVERNOR_ENABLED` | 1 |
| `SECTOR_LEADERSHIP_SCORING_ENABLED`, `SECTOR_DIVERSIFICATION_ENABLED` | 1 |

**Output-layer proof:** every live swing idea sampled (3/3) carries a populated `phase2_score`
and `phase2_components` inside `smc_evidence` — e.g. `ENTERO` 1.42637, `RAMRAT` 0.80403,
`OAL` 0.34670. Phase 2 is scoring and ordering real candidates, not merely configured.

## 3. Attribution — what the trace can and cannot prove

Target trace: `candidate → ranking → SMC score → selection → arm → entry`.

**Provable:** the pipeline currently applies Phase 2 to every SWING scan, and the ideas it emits
carry Phase 2 scores.

**Not provable, and not claimed:** that any specific one of the 7 open post-cutover positions was
*selected because of* Phase 2. The trace breaks at persistence — `research_recommendations` /
track-record ledger rows store `confidence_score`, `rr_planned`, `stop_loss` and outcomes, but
**not** `smc_evidence` and **not** `created_at`. Once an idea is promoted, its Phase 2 score is
not retained anywhere queryable.

Two further reasons attribution is weak:

- **Arm-on-tap decouples dates.** A position's `entered_at` can trail its idea's `created_at`
  (e.g. `KRISHANA` created 08-26, entered 08-27), so entry date alone does not establish which
  ranking produced it.
- **Phase 2 did not change alone.** Phase 0, Phase 1, the sector flags and the regime governor
  were all enabled in the same window. Any cohort difference is attributable to *the combined
  change*, not to Phase 2 specifically.

> **This is the single biggest gap.** Without persisting `phase2_score` on the recommendation
> row, no future re-run of this report can attribute outcomes to Phase 2 either. See §7.

## 3a. Door attribution — the correction (added 2026-08-30)

A follow-up audit answered what §3 said could not be answered, using a source this report had not
consulted: the admission gate's own `source_door` record.

**Two doors write into the swing book, and only one applies Phase 2.**

| Door | Path | Phase 2? |
|---|---|---|
| 1 — `promote_to_portfolio` | `validation_engine` (Phase 2 rewrites `final_selected`) → `signals_log` → `select_from_final_ideas` | **YES** |
| 2 — `seed_from_recommendations` | `ranking_engine` (**no Phase 2 anywhere in the module**) → agent → `stock_recommendations` → `trade_tracker` → `running_trades` → seed | **NO** |

Per-position, from `admission_gate:decisions:*` in Redis and corroborated by the id namespace
(`recommendation_id` is polysemous — door 1 stores a `signals_log` id, door 2 a
`stock_recommendations` id, confirmed against the track-record ledger):

| Symbol | recommendation_id | Door | Phase 2 applied? |
|---|---:|---|---|
| **KRISHANA** | 919968 | `promote_to_portfolio` | **YES** |
| **ANTHEM** | 938783 | `promote_to_portfolio` | **YES** |
| EBGNG | 455 | `seed_from_recommendations` | NO |
| BEPL | 456 | `seed_from_recommendations` | NO |
| AWL | 473 | `seed_from_recommendations` | NO |
| POLICYBZR | 477 | `seed_from_recommendations` | NO |
| DCBBANK | 478 | `seed_from_recommendations` | NO |

**Consequences for this report:**

- The **effective Phase-2 outcome sample is 2**, not 7. Section 4's post-cutover tables mix both
  doors and therefore do **not** measure Phase 2; read them as "post-cutover book activity".
- The three realized post-cutover closes (`KRONOX`, `SWIGGY`, `SHANTIGEAR`) were also created via
  the `stock_recommendations` store, so the realized post-cohort is **not** Phase 2 either.
- The clean stop-distance finding in §4 stands, and is now better explained: it is a property of
  which door admitted the position, not of Phase 2.

`source_door` is now persisted on every new position (2026-08-30), so future re-runs can split the
cohorts directly instead of reconstructing this from a 30-day Redis log. The five existing door-2
rows predate the column and remain NULL; their attribution survives only in Redis, which expires
around **2026-09-23**.

## 4. Measured facts

> ⚠️ **Read these tables as post-cutover *book activity*, not as a Phase 2 cohort.** Per §3a, 5 of
> the 7 open positions and all 3 closed ones entered through the non-Phase-2 door. The split below
> is by date, which is what was available before door attribution existed. It is retained as the
> measured record; it does not measure Phase 2.

Cutover taken as **2026-08-24**. Split by `created_at` for closed rows, `entered_at` for open.

### Open positions

| | Post-cutover | Pre-cutover |
|---|---|---|
| n | **7** | 11 |
| mean P/L % | **+0.12** | **+2.56** |
| median P/L % | −0.36 | +2.03 |
| positive | 3/7 (43%) | 10/11 (91%) |
| mean drawdown % | −0.85 (worst −2.79) | −0.14 (worst −1.55) |
| mean confidence | 76.5 | 74.8 |
| **mean days held** | **2.0** | **47.6** |

### Closed positions (realized)

| | Post-cutover | Pre-cutover |
|---|---|---|
| n | **3** | 68 |
| win rate | **0/3 (0%)** | 32/68 (47%) |
| mean P/L % | −5.63 | +2.56 |
| avg win / avg loss | — / −5.63 | +11.51 / −5.40 |
| profit factor | n/a | **1.90** |
| mean days held | 1.7 | 18.7 |
| exits | 3× `STOP_HIT` | 28 `STOP_HIT`, 20 `TARGET_HIT`, 12 `STALE_EXIT`, 4 `TREND_BREAK`, 3 `STRUCTURE_BREAK` |

Post-cutover closes: `KRONOX` −8.50 (1d), `SWIGGY` −3.29 (1d), `SHANTIGEAR` −5.11 (3d).

**Combined post-cutover: 10 entries — 7 open, 3 stopped out within 1–3 days (30%).**

### Risk characteristics — the one clean, unconfounded difference

Stop distance as % of entry:

- **Post-cutover: 3.85 – 5.00%**, tightly clustered (6 of 7 at exactly 5.00%)
- **Pre-cutover: 5.00 – 14.31%**, wide and variable (`RAYMOND` 14.31, `GRAUWEIL` 12.05)

This is a **structural** difference, visible without waiting for outcomes. It is almost certainly
the risk engine's stop-cap rather than Phase 2, but it is the clearest evidence that the new
cohort is being constructed differently.

## 5. Conclusions — separated from the facts above

**Verdict: INCONCLUSIVE.** Specifically *not* a FAIL, despite post-cutover P/L looking worse.

**The decisive reason (added 2026-08-30): the Phase-2 sample is 2 positions.** Even if every
confound below were controlled, n=2 supports no conclusion. Everything that follows was the
original reasoning and still holds — it is simply no longer the binding constraint.

The headline comparison (+0.12% vs +2.56%) is **not evidence of anything**, because:

1. **Holding-period confound (fatal).** Post-cutover positions have held 2 days; pre-cutover
   47.6. Unrealized P/L grows with exposure time. Comparing them measures elapsed time, not
   selection quality.
2. **Survivorship bias in the closed post-cohort.** In a 5-day window, only *fast losers* reach
   an exit — winners are still open by construction. "0/3 wins" is an artefact of the window, not
   a hit rate.
3. **Underpowered.** n=10 post vs n=79 pre. Nothing at this size is distinguishable from noise,
   and the prior bootstrap already found the weight variants statistically **tied**.
4. **Not isolated.** Phase 0/1, sector flags and the regime governor changed in the same window.
5. **Regime uncontrolled.** No matched market-condition comparison was performed.
6. **Cohort contamination (found 2026-08-30).** 5 of the 7 never passed through Phase 2 at all —
   the "post-cutover cohort" was a date range, not a treatment group. This is the error that
   matters most, because it made the sample look ~3.5× larger than it is.

The 30% stop-out rate within 1–3 days is the one number worth watching. It is **not** currently
alarming — tighter stops (§4) mechanically produce more, earlier stop-outs, which is the intended
trade-off of the risk engine — but if it persists past n≥30 it would warrant investigation.

## 6. Funnel discrepancy — **misleading reporting, not a pipeline bug**

Observed: `quality_passed: 404` alongside `ranked_candidates: 1582` — arithmetically impossible
for a nested funnel.

**Root cause: two different writers populate the same three columns with different semantics.**

- [`ranking_engine.py:1080`](../../services/ranking_engine.py#L1080) writes a genuine nested
  funnel. `ranked_candidates` is derived from `candidate_rows`, which only grows *after*
  `quality_passed += 1`, so there `ranked ≤ quality_passed` always holds. Observed run 613
  (1569 / 1569) is this writer, and is self-consistent.
- [`swing_alpha_agent.py:271-278`](../../agents/swing_alpha_agent.py#L271) — the
  `research_feed_tick` path — maps **validation-engine layer counts** into those columns:
  `quality_passed ← funnel.layer1_pass`, `ranked_candidates ← funnel.layer2_pass`.

Those layers are **independent parallel gates**, not stages:
`final_selected = L1 AND L2 AND L3` ([validation_engine.py:49](../../services/validation_engine.py#L49)).
L2 exceeding L1 is therefore entirely expected. Worse, the mapping is semantically crossed —
L1 is *discovery* but lands in a column called `quality_passed`, while L2 *is* quality and lands
in `ranked_candidates`.

**Impact:** cosmetic/observability only. Selection behaviour is unaffected; both writers are
internally correct. But the public Research funnel and the coverage card display an impossible
funnel, which is exactly the class of dishonesty PR #169 set out to remove. **No change made** —
awaiting approval.

## 7. Recommended next step

**Persist `phase2_score` and `phase2_components` on the recommendation row at selection time.**

This is the blocking dependency for every future measurement. Right now the score exists in
memory during a scan, is exposed on the live feed, and is then discarded — so outcomes can never
be joined back to the score that caused them. Until it is stored, re-running this report at n=30
or n=100 will hit the same wall in §3 and remain inconclusive forever.

It is additive, write-only, and changes no selection behaviour.

Secondary, lower priority: rename or split the two `ranking_runs` writers so the funnel columns
mean one thing (§6).

## 8. Reproducing this report

```bash
railway variables --service web | grep -iE "PHASE|EXCEPTION|REGIME|SECTOR"
curl -s ".../api/portfolio/swing?limit=40"              # open cohort
curl -s ".../api/portfolio/journal/all?horizon=SWING&limit=100"   # realized cohort
curl -s ".../api/research/swing?limit=8"                # phase2_score presence
curl -s ".../api/research/ranking-runs?limit=6"         # funnel counts
```

Cohorts split on `created_at` (closed) / `entered_at` (open) against 2026-08-24. Duplicate
journal rows excluded via `is_duplicate`.
