# Phase 4A — Long-Term differentiation (SHADOW ONLY)

> **STATUS: LIVE** · workstream: `selection` · created 2026-09-27
> Describes the Long-Term shadow selector and the debt/equity unit fix. Neither changes a live
> decision: the shadow writes only to its own tables and the D/E fix reaches live scoring only
> behind `FUND_DE_UNIT_FIX` (OFF). Current project state lives in [`PROJECT_STATE.md`](PROJECT_STATE.md).

## 1. Where Swing and Long-Term share selection logic (audit, 2026-09-27)

There are two Long-Term paths, and both are mostly Swing-shaped.

| Stage | Swing | Long-Term | Shared? |
|---|---|---|---|
| Universe (`load_nse_universe`, 2,200) | ✓ | ✓ | **shared** |
| Technicals / fundamentals / sentiment providers | ✓ | ✓ | **shared** |
| Validation L1 discovery — 5/20/50-day momentum, volume spike, breakout, turnover | ✓ | ✓ | **shared** (short-horizon by design) |
| Validation L2 quality gate (`evaluate_symbol_quality`) | ✓ | ✓ | **shared** |
| Validation L3 SMC band | daily | weekly (`build_longterm_trade_levels`) | LT-specific |
| Composite score (`score_from_discovery`) | ✓ | ✓ | **shared** |
| Phase-2 `select_top` (momentum20/50, SMC, quality) | ✓ | ✓ | **shared** (flag is per-horizon) |
| Exceptionalism, governor, sector cap | ✓ | ✓ | **shared** |
| `ranking_engine._score_candidates` weights | tech-led | fund 0.30 / growth 0.20 / quality 0.18 / tech 0.14 / sent 0.10 / liq 0.08 | LT-specific weights |

- **Path A — `signals_log.final_selected`** (validation funnel). This is what the site serves as
  Long-Term Final Trade Ideas and what the portfolio promotes from. Fundamentals reach it only
  through the L2 quality score. On 2026-09-25: 2,200 scanned → 17 selected, and **only 7 of those
  17 had any real fundamentals**.
- **Path B — `stock_recommendations` LONGTERM** (`generate_rankings`). This path uses the LT
  weight vector, but OHLC materialisation leaves about **2 ideas per day** (for example 2,187 →
  1,531 quality → 2 saved).

Fundamentals coverage: 1,054 of 2,200 symbols (48%) have real (non-hash) fundamentals in
production (`PHASE0_NO_SYNTHETIC`).

## 2. Debt/equity unit fix

The provider reports `debtToEquity` as a **percent** for NSE names: INFY 9.541 means 0.095x.
`scripts/refresh_stock_universe.py` was corrected for this earlier. `fundamental_analysis.py`
kept the old "divide only when > 10" rule, so every company with a true D/E below 0.10x — the
least indebted — was scored as 0.1x–10x leverage. INFY's `debt_quality` came out as 0.0.

- `debt_equity_ratio(provider) = provider / 100`, always.
- Live scoring keeps the legacy value unless **`FUND_DE_UNIT_FIX=1`**. The default is byte-identical,
  and a test proves it against a verbatim copy of the old formula.
- The corrected values (`debt_equity_ratio`, `debt_quality_de_fixed`,
  `management_quality_de_fixed`, `fundamental_score_de_fixed`) are always computed next to the
  legacy ones and cached with them.
- Flipping the flag would change **both** books, because Swing also weights `fundamental_score`
  (0.12). Cached snapshots keep the old score for up to `FUNDAMENTALS_CACHE_TTL_H` (24h) after a
  flip.

Impact numbers are in [§5](#5-debtequity-impact-2026-09-25-universe).

## 3. Shadow selector design (`lt-shadow-v1`)

`services/lt_shadow_selector.py` runs on every **live, logged LONGTERM validation scan**. It does
not run for Swing, for scans with `log_scan=False`, or for backtest scans that pass `as_of`.

- **Pool:** the scan's own records. No extra network calls.
- **Eligibility:**
  - OHLC present and avg turnover ≥ the scan's L1 floor (₹1 Cr);
  - L2 quality pass;
  - **real** fundamentals;
  - a tradable Long-Term plan (same level builder as the control).
- **Score:** within-scan percentiles combined with the **existing** Long-Term weight vector from
  `ranking_engine` — fund 0.30, growth 0.20, quality 0.18, tech 0.14.
  - Sentiment (no provider in production) and liquidity (already a floor) are dropped, and the
    remaining weights are renormalised.
  - Fundamentals and quality use the corrected D/E.
  - No new signal and no fitted weight is introduced.
- **Count:** `budget` = the control's `final_selected` count on the same scan. Shadow and control
  therefore differ in *which* stocks, never *how many*. The top `LT_SHADOW_LOG_TOP_N` (30) are
  logged, so other cut-offs can be studied later.
- **Deterministic:** the result doesn't depend on input order. Ties are averaged, and ties in the
  final order break by symbol.
- **Isolation:**
  - it only reads `records`;
  - writes go only to `lt_shadow_runs` / `lt_shadow_picks`, created lazily and never at startup;
  - nothing reads those tables except `scripts/lt_shadow_report.py`;
  - any exception is logged and dropped;
  - there is **no enforcement flag**.

**Flag:** `LT_SHADOW_SELECTOR_ENABLED` on `web`. Rollback = unset it.

### Provenance persisted per run

`scan_id`, which is the `signals_log` scan_id of the control, plus:

- the selector version;
- the frozen config (weights, eligibility, budget rule);
- the deploy SHA;
- universe, eligible, budget and control counts, the overlap, and exclusion counts by reason.

Per pick: rank, score, component percentiles, all raw inputs (including legacy *and* corrected
fundamental score and D/E), cmp, entry, stop and target.

### Evaluation

`python -m scripts.lt_shadow_report [--since YYYY-MM-DD] [--json]` reports:

- selection counts;
- shadow∩control overlap and Jaccard per run;
- Swing/Long-Term overlap (vs Swing `final_selected` on the same date);
- 20d/60d forward and excess returns for control, shadow, shadow-only, control-only and both,
  joined from `forward_returns`;
- approximate trigger/expiry;
- the latest run's shadow-only and control-only symbols.

Both groups are labelled by the same `(symbol, date)` keys. `signals_log` logs all 2,200 Long-Term
symbols per scan, so every shadow pick is already in the backfill's key set.

## 4. Known dependency gap — forward-return labels are not scheduled

`scripts/backfill_forward_returns.py` says to schedule it, but nothing does (not in
`agents/runner.py`, the scanner or any toml). The last run was **2026-09-13**, and labels stop at
2026-09-12. Shadow **collection** is unaffected: labels are a pure function of `(symbol, date)`
plus later prices, so they can be computed at any time. **Evaluation** needs the backfill to run
after the 20- and 60-trading-day windows elapse. Scheduling it is a separate, deliberate
data-volume change (it writes to `dashboard.db`, which has about 1.8 GB free). Until it's
scheduled, run it out of band before each calibration read.

## 5. Debt/equity impact (2026-09-25 universe)

_Filled from `scripts/measure_de_fix_impact.py`; see below._

## 6. Next calibration step (pre-registered — decide nothing before it)

1. Keep collecting. Make **no** changes to weights or eligibility while collecting; a change means
   a new `selector_version`.
2. Before reading results, run the forward-return backfill out of band.
3. Read `lt_shadow_report` only when there are **≥ 40 runs with 20d labels**. Treat 60d as
   decisive only at **≥ 40 runs with 60d labels** (roughly 3 months of scans).
4. Compare shadow-only vs control-only on 20d/60d excess return. Report the bootstrap CI of the
   difference, not a point estimate. The Phase 2 weight variants tied under bootstrap, and a
   tie is a valid answer here too.
5. Any live change (shadow → selection, or `FUND_DE_UNIT_FIX=1`) is a separate decision with its
   own control run. Neither is authorised by this phase.
