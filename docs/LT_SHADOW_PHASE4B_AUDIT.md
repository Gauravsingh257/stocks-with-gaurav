# Phase 4B — Long-Term shadow: data-integrity audit

> **STATUS: HISTORICAL** · workstream: `selection` · measured 2026-09-27 (Sunday, before the first shadow run)
> Point-in-time audit of the forward-return labels and fundamentals coverage that the Long-Term
> shadow evaluation depends on. Design: [`LT_SHADOW_PHASE4A.md`](LT_SHADOW_PHASE4A.md). Nothing
> here changed the selector, its weights or eligibility, `FUND_DE_UNIT_FIX`, or any live selection.

## 1. Forward-return labels

### How they're made
`scripts/backfill_forward_returns.py` works in four steps:

1. `SELECT DISTINCT symbol, date FROM signals_log` (242,988 pairs; 5.6s).
2. `yf.download` of daily bars for every corpus symbol in chunks of 200.
3. `compute_label` per pair: 5/10/20/60-trading-day forward return, MFE, MAE, and excess over
   NIFTY. A window that hasn't elapsed is left NULL.
4. Chunked upsert into `forward_returns`, committing every 2,000 rows.

It is idempotent and always re-labels **every** pair, including rows whose 60-day window closed
long ago and can never change. **Nothing schedules it.** The last run was 2026-09-13.

### Storage and runtime (measured)

| Item | Value |
|---|---|
| Volume `/data` | 4.84 GB total · 2.91 GB used · **1.91 GB free** |
| `dashboard.db` | 2.90 GB, of which `signals_log` is **2,259 MB** |
| `forward_returns` + 2 indexes | 47.1 + 7.3 + 4.6 = **59 MB** (212,709 rows, about 280 B/row) |
| Growth if kept current | about 2.4k new pairs per trading day, so ~0.7 MB/day (~170 MB/yr) |
| WAL during a run | bounded by the pages rewritten, ≤ table size (~60 MB); chunked commits let it checkpoint |
| Price fetch | **232s** for 2,449 symbols (2,408 returned bars); the bulk chart endpoint isn't throttled like `.info` |
| Labelling | **40s** for 242,425 pairs · peak memory **109 MB** |
| Estimated full run | about 5–7 minutes including the upsert |

Labels are not a volume risk. `signals_log`, at 78% of the DB and with no retention, is the risk.

### Where it can run

| Option | Verdict |
|---|---|
| **A. Manual** `railway ssh` on `web` before each calibration read | Zero new risk. Sufficient scientifically, because labels are a pure function of `(symbol, date)` + later prices, so being current *at read time* is all that matters. Operationally fragile: it's how labels went stale. |
| **B. Weekly out-of-band subprocess on `web`** (the pattern `dashboard_db_backup` uses) | **Recommended when approved.** `web` is the only service that mounts the volume. Add an **incremental** mode that labels only unlabelled pairs + rows with `bars_available < 60`: ~170k rows now, about 140k in steady state instead of 243k and growing. Add a free-space precheck (abort below ~1 GB), a subprocess timeout, a `BACKFILL_ENABLED` flag (default OFF), Saturday off-hours after the 02:30 backup, and `wal_checkpoint(TRUNCATE)` at the end. |
| C. Compute on `scanner`/local/CI and POST to `web` | Rejected: needs a new authenticated bulk-write endpoint for no gain. |
| D. Daily schedule | Unnecessary churn: the windows are 20/60 trading days. |
| E. FastAPI startup hook | Never. That caused the 2026-08-02 outage. |

**Safest recommendation:** use **A now**, running the existing script by hand right before any
calibration read. Build **B** (incremental + guards, flag OFF) as a separate approved change. Not
scheduled in this phase.

### Current label coverage (as of the 2026-09-13 run)

| | Count |
|---|---|
| Distinct `(symbol, date)` in `signals_log` | 242,988 (2026-04-25 → 2026-09-25, 112 dates) |
| Labelled rows | 212,709 — complete 60d window: 72,055 · 20–59 bars: 88,817 · < 20 bars: 51,837 |
| Unlabelled pairs | **30,279**: 22,236 are Sep 2026 (never run) and 7,788 are older |
| Persistently unlabelled symbols (≤ 2026-08-14) | **139** (7,788 pairs). 56 have never priced (bad/SME tickers); 83 are gaps on some dates |
| LT control, strict-funnel era (≥ 2026-08-01) | 1,067 picks · **322 distinct symbols** · 41 dates · 0–20 per scan (mean 17.2) |
| LT control 20d labels | Aug: 193 of 565 · Sep: 0 of 502 · 60d: 0 |
| Shadow runs | 0 — the first is Mon 2026-09-28 08:40 IST |

## 2. Fundamentals coverage (Yahoo `.info`)

| Setting | Real fundamentals / 2,200 |
|---|---|
| Local, 8 workers (production's `FUND_FETCH_CONCURRENCY`) | 854 |
| Local, 2 workers + 3 retries | **1,944** (the available ceiling) |
| Production, 37 LT scans 2026-08-25 → 09-25 | **709 – 1,981**, varying day to day |

**Mechanism:**

- The 08:30 IST Swing scan fetches first. Its misses ("Invalid Crumb" 401s) are cached as hash
  snapshots for 24h (`FUNDAMENTALS_CACHE_TTL_H`), so the 08:40 Long-Term scan inherits the same
  coverage exactly. The paired Swing/LT counts are identical every day.
- Throttling sets in part-way through each run, so loss is **systematic by fetch order**, which is
  alphabetical.

  | Universe-order quintile (A→Z) | 1 | 2 | 3 | 4 | 5 |
  |---|---|---|---|---|---|
  | Mean presence (fetchable names) | 0.92 | 0.87 | 0.47 | **0.16** | 0.40 |

  Size is **not** the driver: presence is 0.64 / 0.64 / 0.63 / 0.67 / 0.67 across turnover
  quintiles (median ₹0.09 → ₹123 Cr).
- Of 1,944 fetchable names, 731 are present in ≥ 90% of scans and 224 in ≤ 10%.

**Effect on the shadow's eligible pool, replayed for 2026-09-25:**

| | Full coverage | Production coverage that day |
|---|---|---|
| Common support (liquid + L2 + plan) | 1,218 | 1,218 |
| … with real fundamentals = shadow-eligible | 1,086 (89%) | **646 (53%)** |
| Shadow's 17 picks still present | 17 | 13 (lost: MARKSANS, MCX, SHADOWFAX, SOTL — all M–S) |
| Control picks the shadow can rank | 17 of 17 | **7 of 17** (10 excluded: no fundamentals) |
| Shadow rank of control picks (median) | 362 of 1,086 | 211 of 646 |

## 3. Biases and confounders for shadow vs control

1. **Fundamentals-coverage restriction (systematic).**
   - The shadow can only pick from names the throttled fetch reached that day. That skews
     alphabetical and shifts daily (pool 53–89%).
   - Expected effect: shadow picks are "best of a partial pool", which dilutes it. The universe
     also differs from the control's, since 10 of 17 control picks had no fundamentals on 09-25.
   - *Mitigation:* the primary comparison is shadow vs **control picks the shadow could rank**
     (common support), stratified by `support_coverage_pct`. Both are now logged.
2. **Differential label missingness.**
   - For 2026-08-01…14, control picks were unlabelled 10.5% of the time vs 4.6% for the LT
     universe.
   - If missing names skew to halted or suspended stocks, the control's measured return is biased
     **up**.
   - *Mitigation:* report `n_labelled/n` per group (the report does) and run a worst-case
     sensitivity check on missing labels.
3. **Overlapping windows and repeated symbols.**
   - Daily scans with 20/60-day windows, and 1,067 control picks from only 322 symbols, mean the
     effective sample is far smaller than the row count.
   - *Mitigation:* use a date-block bootstrap (block ≥ the window) and report distinct symbols.
4. **Control regime drift.**
   - Pre-August `final_selected` was a different funnel (hundreds per scan), so the shadow must
     only be compared with the **same-scan** control.
   - A live flag flip mid-collection would also change the control. The allowlisted selection
     flags are now snapshotted per run.
5. **Label base timing.**
   - Labels base on the scan-date close, but the scan runs pre-market (cmp = prior close).
   - This is identical for both groups, so it isn't differential. It does misalign trigger/expiry
     by one session, which is why the report labels those numbers "approximate".
6. **Unequal days.** The control count varies 0–20 per scan, so report per-run means as well as
   per-pick pooling.
7. **Sector concentration.** A fundamentals ranking can cluster by sector. Join
   `stock_universe.sector` at read time; no extra logging is needed.
8. **Repeated looks.** Every peek inflates false positives, so read only on the pre-registered
   schedule.

## 4. Instrumentation added (logging only, `log_schema: 2`)

Selection is unchanged. A test proves the selected set equals the plain score order.

- `lt_shadow_runs.coverage`: universe real-fundamentals count, the common-support size, its
  with-fundamentals count, and `support_coverage_pct`.
- `lt_shadow_picks` now also holds **every control pick**, with its shadow rank/score or the
  eligibility `exclusion`.
- `config.control_env`: an allowlisted snapshot of the flags that shape the control funnel and the
  data (no secrets).
- `scripts/lt_shadow_report.py` gains `control_shadow_eligible` / `control_shadow_ineligible`,
  distinct symbols per group, and per-run coverage.

The tables did not exist in production yet (verified), so every run from the first one uses this
schema.

## 5. Data required before any calibration read

1. At least **40 shadow runs** with `log_schema: 2` — a *first look*, not a decision. Decision grade
   needs at least **6 non-overlapping 20-trading-day blocks** (~6 months of runs) and ≥ 150
   distinct symbols in each of shadow and control-common-support. 40 daily runs span only about 2
   non-overlapping 20-day windows.
2. `forward_returns` refreshed **after** the last run's 20d (and, for the 60d read, 60d) window
   has elapsed. Label coverage ≥ 95% in each compared group and within 3 pp of each other;
   otherwise report the worst-case sensitivity.
3. The same `control_env` across all runs being pooled. If it changed, split at the change.
4. Coverage strata with enough runs on each side, e.g. `support_coverage_pct` ≥ 80 vs < 80.
5. **Primary metric:** shadow vs control-common-support, 20d **excess** return, with a date-block
   bootstrap CI of the difference. 60d is decisive. A tie is a valid result.

Earliest dates, if runs start 2026-09-28: first look at 20d around late Dec 2026; decision-grade
20d around Apr 2027; 60d later still. The 1-of-17 overlap on the 2026-09-25 replay is **not**
evidence of anything.
