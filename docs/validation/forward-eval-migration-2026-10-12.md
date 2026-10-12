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

> **Parity for these two is NOT verified end-to-end from here.** Their tables
> (`signals_log`, `lt_shadow_runs`, `forward_returns`) live on the production
> volume, which this environment cannot reach. That is exactly why the
> `--returns-source` switch exists: run each script twice server-side and diff.
> The arithmetic itself is pinned by unit tests.

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

**No evidence that culling would have helped**, on a small sample. The 27
`no_bar_on_date` rows are worth a look — likely logged dates that are not
trading days for those symbols.

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
