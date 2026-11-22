# Exceptionalism calibration — first run on real outcomes

> **STATUS: LIVE** · workstream: `selection` · 2026-10-13 · measurement only
> No threshold, weight, flag or promotion decision changed by this document.

## Why this could not run before

`scripts/exceptionalism_calibration.py` priced outcomes through
`_kite_or_yf_fetch`, one call per `(symbol, date)`. Its query has **no `LIMIT`
and no `final_selected` filter**, so a normal window is ~4,400 rows/day — tens
of thousands of provider calls. That is the pattern that earned the Yahoo
throttling documented in `fundamentals-coverage-fix`, so the script was never
run at scale and the calibration the validation phase depends on never existed.

`--prices snapshot` (now the default) reads the `ohlc:universe` snapshot once
through `services/forward_eval.PriceBook`, the same evaluator the ceiling study,
LT shadow, Phase-1 counterfactual and stagnation use.

**237,458 rows priced in 104 seconds, zero provider calls.**

## Coverage

Window 2026-08-15 → 2026-10-09, SWING, against a restored production snapshot.

```
total 208,386 | priced 188,721 (90.6%) | unpriceable 19,665
  no_series            9,418   (symbols no longer in the 2,200-symbol universe)
  no_bar_on_date      10,245   (of which 10,090 market closed, 155 symbol gaps)
  immature by horizon  1d:2  3d:4,035  5d:20,032  10d:49,894  20d:97,747
```

Matured 20-day outcomes: **90,976**.

## The result — exceptionalism does NOT beat legacy at 20d

| | n | hit rate | avg return | median | avg MaxDD | worst DD |
|---|---|---|---|---|---|---|
| `legacy_final_selected` | 769 | **38.9%** | **−1.31%** | **−2.02%** | −11.55% | −57.1% |
| `exceptionalism_qualified` | 1,500 | 37.9% | −1.85% | −2.48% | −11.47% | **−81.5%** |

`exceptionalism_better: false`. It admits roughly **twice as many** rows for a
slightly worse hit rate, worse average and median return, and a materially worse
worst-case drawdown.

Under the validation-phase rule — *no flag is enabled without measurable
improvement over legacy* — this is a **NO-GO for exceptionalism-based
admission**. The honest reading is not "exceptionalism is broken" but "on this
window it does not improve on what selection already does".

## What must NOT be concluded

* **`optimal_threshold: 86` is not a recommendation.** It is the in-sample
  optimum of a sweep over one 56-day window (326 selected, hit 37.1%, avg
  −0.80%). Picking it would be curve-fitting of exactly the kind
  `phase2-smc-as-score` warns about, where variants were statistically tied over
  43 days. Do not set it.
* **Every cohort is negative.** Avg return is below zero for legacy *and*
  exceptionalism. That is the Aug–Oct tape plus the fact that these are *scanned
  candidates*, not executed trades — no entry trigger, stop, or sizing is
  applied. These numbers do not describe book performance.
* **No significance test was run.** This script reports means and hit rates, not
  bootstrap CIs, and the observations overlap heavily by date. The −0.54pp
  hit-rate gap is well inside the noise this sample can support.

## Reproduce

```bash
python -m scripts.exceptionalism_calibration \
  --from 2026-08-15 --to 2026-10-09 --horizon SWING \
  --json-out exc_cal.json --out exc_cal.md
```

Needs `REDIS_URL` for the snapshot. `--prices fetch` restores the old
per-symbol path; it is kept only for parity checking and will throttle.

> On Windows, pipe through `PYTHONIOENCODING=utf-8` or write with `--out` — the
> markdown contains emoji and a cp1252 console cannot encode them.
