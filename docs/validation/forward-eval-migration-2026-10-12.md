# Shared forward-return evaluator — migration report

> **STATUS: LIVE** · workstream: `selection` · 2026-10-12 · measurement only
> No strategy logic, weight, threshold, flag or promotion decision changed.

## Why

Four evaluations were stalled or duplicated:

| Consumer | Outcome source before | Problem |
|---|---|---|
| `ceiling_cohort_study` | OHLC-direct (inline) | the good method, but trapped in one script |
| `exceptionalism_calibration` | **its own** `attach_forward_returns` | duplicate arithmetic that could drift |
| `lt_shadow_report` | `forward_returns` table | **stale since 2026-09-13** |
| `phase1_calibration` | `forward_returns` table | **stale since 2026-09-13** |
| stagnation | *none — collector only* | the shadow's own question was never answered |

`forward_returns` is written **only** by the manual `scripts/backfill_forward_returns.py`.
Confirmed 2026-10-12: **no GitHub workflow and no `agents/runner.py` job** invokes it.

Per the brief, the fix removes the dependency rather than adding a scheduler to hide it.

## What was built

`services/forward_eval.py` — one evaluator, bias controls preserved from the
ceiling study:

* **No look-ahead** — base is the close of the observation date; scans publish pre-open.
* **Maturity gating** — a horizon reports only when bar `n` genuinely exists; otherwise `None`.
* **Survivorship reported** — `Coverage` counts `no_series` / `no_bar_on_date` / `immature`.
* **Overlap handling** — `block_bootstrap` resamples **dates**, never observations.
* **Unrounded internals** — rounding is presentation only, so means/bootstraps don't drift.

Sources: `from_universe_snapshot()` (production OHLC), `from_fetcher()` (Kite/yfinance,
used by exceptionalism), `from_mapping()` (tests).

## Old vs new, per consumer

### 1. `ceiling_cohort_study` — refactored, differences fully attributed

Run on identical data (SWING, 60 days), before vs after:

| | before | after | cause |
|---|---|---|---|
| price series | 2165 | **2168** | the old code dropped symbols with ≤20 bars outright; now maturity gating handles them. **Exactly 3 such symbols** |
| PHASE2_CUT | 4436 | **4407** | ↓29 |
| CEILING_CUT | 8646 | **8675** | ↑29 |
| 20d CEILING_CUT mean | −0.70% | −0.70% | unchanged |
| 20d block CI | [−1.35,+2.98] | [−1.41,+3.02] | follows the cohort reassignment |

**The ±29 is a fixed correctness bug.** 67 `(date,symbol)` keys carry both
`CEILING_CUT` and `PHASE2_CUT`. The old dedup was **order-dependent** (`SELECTED`
won, otherwise *last row seen* won), so 29 landed on `PHASE2_CUT` by accident of
iteration order. The shared `dedupe()` applies fixed precedence
`SELECTED > CEILING_CUT > PHASE2_CUT`, so the result is now deterministic.

**Every verdict is unchanged** — all CIs still straddle zero at 5/10/20d, and
`PHASE2_CUT` still underperforms both cohorts at every horizon.

### 2. `exceptionalism_calibration` — duplicate arithmetic deleted

`attach_forward_returns` kept its public shape (`row.forward[h] = {ret, mae, mfe}`,
2dp) and now delegates to the shared evaluator. ~25 lines of duplicated
ret/MAE/MFE/maturity logic removed. Parity is pinned by
`test_parity_with_exceptionalism_attach_forward_returns`, which reproduces the
original formula exactly (base = `row.cmp`, MAE/MFE over bars `1..h`, `len(bars) <= h` ⇒ pending).

### 3–4. `lt_shadow_report` + `phase1_calibration` — off the stale table

Both default to OHLC-direct and expose `--returns-source {ohlc,table}`.
`enrich_rows()` populates the same `fwd_{w}d_pct` / `excess_{w}d_pct` keys the SQL
join produced, so all downstream stats code is untouched. Excess return is
benchmark-relative against the NIFTY 50 index snapshot. Both now emit
`outcome_coverage` so unpriceable rows are visible.

#### Parity was run — and it found a real defect

A genuine production snapshot was restored read-only
(`scripts/restore_test_dashboard_db.py`, `dashboard-20261012-023000.db.gz`,
`signals_log` 1,233,305 rows, `forward_returns` 212,709) and both scripts were
run in both modes against it.

**`phase1_calibration --returns-source ohlc` crashed**: `KeyError: 'mae_10d_pct'`.
`enrich_rows` only wrote `mae_`/`mfe_` on horizons that had matured, but a
`LEFT JOIN` yields its columns on **every** row, and `outcomes()` indexes
`r["mae_10d_pct"]` directly. No fixture caught it because in a fixture
everything matures.

The same root cause was **silently** degrading `lt_shadow_report`:
`_entry_trigger_rate()` reads `base_close` and `mae_5d_pct`, which the OHLC path
never populated, so it reported zero triggers instead of failing loudly.

Fixed: `enrich_rows` now seeds every join column to `None` before any branch,
fills `mae_`/`mfe_` alongside each return, sets `base_close` from the observation
date, and takes `mae_windows` for horizons where only the excursions are wanted
(`lt_shadow_report` passes `(5, 20)`). Two regression tests pin the contract.

Two further honesty defects surfaced from the same run and were fixed:

* **`merge_coverage()`** — both consumers summed only total/priced/unpriceable
  across their queries, discarding the `no_series` / `no_bar` / `immature`
  breakdown, which is the entire point of reporting coverage.
* **`_trigger()` 20d denominator** — `triggered_20d_pct` divided 20d hits by the
  **5d** count, so a pick whose 20th bar has not happened yet was reported as
  `0.0%` triggered. It now carries `n_labelled_20d` and withholds the rate when
  that is zero.

**This is why the parity run mattered.** The OHLC default had never been executed
end-to-end against real data before — only against fixtures.

#### Results, both modes, same restored snapshot

`phase1_calibration --days 14 --horizon SWING` — **85 shared keys, 10 differ**:

| | table | ohlc |
|---|---|---|
| funnel counts (16,343 / 4,266 / 12,077, dropped 73.9%) | identical | identical |
| `entry_gap`, `sector_bypass`, `l1_counterfactual` n | identical | identical |
| 5d outcomes, `legacy_final` | `labelled: 0` | **10,002** labelled, median −1.38%, win 37.5% |
| 5d outcomes, `strict_final_kept` | `labelled: 0` | **2,547**, median −1.08%, win 42.0% |
| 5d outcomes, `dropped_by_strict` | `labelled: 0` | **7,455**, median −1.46%, win 36.0% |
| 10d / 20d | null | null — genuinely immature in a 14-day window |
| `outcome_coverage` | absent | priced 34,385/83,600 (41.1%) |

`lt_shadow_report` — **81 shared keys, 7 differ**: `returns_source` plus the six
`trigger_expiry_approx.*.n_labelled`, which go from **0** to **88–93**. The 20d
and 60d forward blocks are withheld in *both* modes, for different reasons:
the table's labels stop at **2026-08-14** while the picks are 09-28 → 10-11, and
OHLC has only ~10 bars after the earliest pick. Coverage:
`no_series 6, no_bar_on_date 60 (all market_closed), immature 304`.

**The difference is the whole point of the PR**: where the stale table answered
nothing, OHLC answers 10,002 five-day outcomes and 91 entry-trigger rates.

> **Caveat on reproducibility.** The `dashboard.db` snapshot is frozen, so both
> modes saw identical DB input. The `ohlc:universe` snapshot is **live** — it
> gained today's bar mid-session between two runs, moving matured 5d counts from
> 8,758 to 10,002. OHLC-mode figures are as-of-run-time by construction.

> **Not a tuning signal.** All three 5d medians are negative; that is the
> late-September/October tape in a 14-day window, not evidence about the strict
> funnel. Per the validation-phase rule, nothing is tuned on it.

### 5. stagnation — new evaluation, first answer

`scripts/stagnation_eval.py` compares, from the same observation date:
**HELD** (the stagnant position) vs **REPLACEMENT** (the best candidate available).

First run over 69 logged rows:

```
would_fire: HELD 69, REPLACEMENT 55 | 14 rows had no candidate | 21 paired dates
priced 67/124 (54.0%) | unpriceable 57 (no_series 3, no_bar_on_date 27)
 5d: HELD +2.06% (n=40, win 57.5%)  REPLACEMENT +3.08% (n=27, win 48.1%)
10d: HELD +1.73% (n=28)             REPLACEMENT −2.29% (n=17)
20d: HELD +0.96% (n=8)              REPLACEMENT −10.75% (n=1)
block:  5d diff −1.18pp CI [−7.36,+5.91] -> NO measurable difference
       10d diff −4.73pp CI [−11.22,+1.76] -> NO measurable difference
       20d only 1 comparable date -> withheld
```

**No evidence that culling would have helped**, on a small sample.

#### The 27 `no_bar_on_date` rows — resolved, zero data defects

All 57 unpriceable observations were classified against the snapshot:

| class | n | evidence |
|---|---|---|
| market closed (weekend/holiday) | **27** | **2026-08-30** (a Sunday, 19 obs) and **2026-10-02** (Gandhi Jayanti, 8 obs). *Zero* bars exist on either date across all 2,168 symbols |
| symbol absent from the universe | 3 | `BODALCHEM` on 08-31 and 09-01 — not in the snapshot nor in `data/nse_universe_full.json`. Reported as `no_series`, not dropped |
| missing OHLC for a trading date | **0** | — |
| symbol-mapping problem | **0** | every other logged symbol resolved |
| genuine data defect | **0** | — |

The cause is the **collector, not the data**: `stagnation-shadow.yml` runs on
calendar days, so it logs on weekends and holidays, where by definition nothing
can be priced. 22 of the 24 logged dates are real trading days.

`Coverage` now proves this instead of leaving it open —
`PriceBook.market_closed_on()` splits `no_bar_on_date` into
`no_bar_market_closed` + `no_bar_symbol_gap`, so a future run distinguishes a
holiday from an actual gap without a manual investigation. A fetcher-backed book
has no calendar and never claims a closure. Reporting only: the stagnation
numbers above are **byte-identical** before and after, and no exit rule changed.

## Tests

`tests/test_forward_eval.py` — 23 tests: close-to-close arithmetic, MAE/MFE
excursions (entry bar excluded), explicit base override, `NSE:` tolerance,
maturity gating, **no-look-ahead** (a rally *before* the date must not leak),
missing symbol vs non-trading date, zero/negative base, NaN, dedupe precedence,
cohort stats, bootstrap determinism/withholding/null-result, **parity with both
replaced implementations**, fetcher-vs-mapping equivalence, and `enrich_rows`
key/coverage/excess behaviour.

Two existing suites were pinned to `source="table"` because they seed
`forward_returns`; a new test asserts the OHLC default **ignores** those seeded
labels and withholds honestly.

Full suite: **1177 passed**, same 28 known failures. The single extra
(`test_expiry_manager::test_preload_when_near_expiry`) is the known UTC-vs-IST
date-boundary flake and fails identically on `main`.

## Remaining blockers

1. **End-to-end parity for LT shadow and Phase-1 needs a server-side run** of each
   script under both `--returns-source` values.
2. **`no_bar_on_date` is high** in stagnation (27 of 124) — investigate before
   treating that evaluation as settled.
3. **The label backfill is still unscheduled.** This PR removes four consumers'
   dependency on it; `services/outcome_labeling.py` and the `forward_returns`
   table remain for anything else that reads them.
