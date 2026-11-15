"""
scripts/feature_excess_return_study.py — can any feature we already have pick
stocks that END UP higher than the market?

READ-ONLY. No thresholds, weights, flags or live settings are touched, and
nothing imports this module.

## Why

`docs/validation/mfe-target-reach-2026-10-18.md` established that the engine has
a real excursion edge (selected names travel 2.6-5.1pp further than
below-budget ones) and **no demonstrable return edge** (every close-to-close
comparison against rank-21+ straddles zero; selected names beat the benchmark
48.1% of the time while rejected ones beat it 55-59%). Excursion is not realised
return, and that study did not say WHICH features cause the gap.

This one asks the narrower question: among the features already computed at scan
time, does any of them rank stocks by **realised benchmark-relative return**?

## Pre-registered before looking at any test-set result

FEATURES (all knowable at scan time, so no look-ahead):

    confidence       the ranker's own score
    exc_score        exceptionalism score
    exc_threshold    the bar it was measured against
    required_move    (target - entry) / entry
    stop_distance    (entry - stop_loss) / entry
    planned_rr       required_move / stop_distance
    entry_gap        (entry - cmp) / cmp, how far the plan sits from spot

TARGET: realised return over N trading days from the scan-date close, measured
        benchmark-relative. See "why raw return is already benchmark-neutral".

DECISION RULE, fixed in advance. A feature is reported as a CANDIDATE only if
all four hold:
    1. the per-day rank correlation has the SAME SIGN in train and in test;
    2. the test-set top-vs-bottom tercile difference has a 95% CI excluding 0;
    3. both halves clear the minimum day count;
    4. the effect is economically meaningful (>= MIN_EFFECT_PP).
Anything else is reported as NOT ESTABLISHED. Seven features are tested, so by
chance alone one or two will look good in a single split — the train/test sign
agreement is the guard, and the count of features tested is printed so the
reader can apply their own correction.

## Method

- Base = close of the SCAN DATE (scans publish pre-open, so that close is
  tradable after publication). No look-ahead.
- MATURED observations only. No bar at D+N -> dropped and counted, never
  zero-filled.
- One observation per (scan_date, symbol).
- Population = every candidate the ranker scored (selected plus both rejected
  cohorts), because restricting to the selected top-20 throws away the range the
  feature is supposed to order.
- **Time-separated split with an embargo.** Train is the earliest days, test the
  latest, and between them sits a gap of at least N trading days — the horizon
  length — so no train observation's outcome window overlaps the test period.
  Without the embargo the split leaks.
- Terciles, not deciles, and computed WITHIN each scan day: the ranker is
  cross-sectional, so a feature must order stocks against that day's peers, not
  against other days. Terciles keep ~20 names per bucket where deciles would
  leave ~6.
- Statistical claims use `forward_eval.block_bootstrap`, which resamples scan
  DAYS, because overlapping forward windows make observations dependent.

## Why raw return is already benchmark-neutral here

The benchmark's forward return is a CONSTANT within one scan day. The day-block
bootstrap takes per-day means for each bucket and then differences them inside
that same day, so the benchmark term cancels exactly. A top-vs-bottom difference
on raw return therefore *is* the benchmark-relative comparison, and needs no
excess-return column. The absolute excess level is still reported descriptively,
because "the top bucket beats its peers" and "the top bucket beats the market"
are different claims.

    python -m scripts.feature_excess_return_study [--days 180] [--horizon SWING]

Needs REDIS_URL (universe OHLC snapshot) and reads the public signals-log export.
"""

from __future__ import annotations

import argparse
import collections
import os
import statistics as st
import sys

HORIZON_DAYS = 20          # primary outcome horizon
SECONDARY = 10
BOOTSTRAP = 5000
MIN_DAYS_PER_HALF = 8
MIN_EFFECT_PP = 1.0        # a tercile spread below this is noise, not an edge
MIN_PER_DAY = 30           # a scan day with fewer candidates cannot form terciles

FEATURES = ("confidence", "exc_score", "exc_threshold",
            "required_move", "stop_distance", "planned_rr", "entry_gap")


def _load_redis_url() -> bool:
    if os.getenv("REDIS_URL"):
        return True
    try:
        with open(".env", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("REDIS_URL="):
                    os.environ["REDIS_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return True
    except OSError:
        pass
    return False


def _f(v):
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _features(row: dict) -> dict[str, float]:
    """Everything derivable from one scan row. Absent values are omitted, never
    defaulted to zero — a zero would be a value the ranker never saw."""
    out: dict[str, float] = {}
    for k in ("confidence", "exc_score", "exc_threshold"):
        v = _f(row.get(k))
        if v is not None:
            out[k] = v
    entry, stop, target, cmp_ = (_f(row.get("entry")), _f(row.get("stop_loss")),
                                 _f(row.get("target")), _f(row.get("cmp")))
    if entry and entry > 0:
        if target:
            out["required_move"] = (target - entry) / entry * 100.0
        if stop and stop < entry:
            out["stop_distance"] = (entry - stop) / entry * 100.0
        if target and stop and stop < entry:
            sd = (entry - stop) / entry
            if sd > 0:
                out["planned_rr"] = ((target - entry) / entry) / sd
        if cmp_ and cmp_ > 0:
            out["entry_gap"] = (entry - cmp_) / cmp_ * 100.0
    return out


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, implemented here to avoid a scipy dependency.
    Average ranks for ties, then Pearson on the ranks."""
    n = len(xs)
    if n < 5:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return (num / (dx * dy)) if dx and dy else None


def _bench_forward(bench: list[dict], horizons) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {h: {} for h in horizons}
    closes: list[tuple[str, float]] = []
    for b in bench:
        c = _f(b.get("close"))
        if c is not None:
            closes.append((str(b.get("date"))[:10], c))
    for i, (d, c0) in enumerate(closes):
        for h in horizons:
            if i + h < len(closes) and c0:
                out[h][d] = (closes[i + h][1] - c0) / c0 * 100.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Feature vs realised excess return (read-only)")
    ap.add_argument("--api", default=os.getenv("DASHBOARD_URL",
                                               "https://web-production-2781a.up.railway.app"))
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--horizon", default="SWING", choices=["SWING", "LONGTERM"])
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    if not _load_redis_url():
        print("::error::REDIS_URL not set and not found in .env")
        return 2

    from scripts.ceiling_cohort_study import _cohort, _export
    from services.forward_eval import (
        Observation,
        PriceBook,
        benchmark_series,
        block_bootstrap,
        dedupe,
        evaluate,
    )

    book = PriceBook.from_universe_snapshot()
    if not book._series:
        print("::error::no universe OHLC snapshot available")
        return 2
    print(f"price book : {len(book._series)} symbols")
    bench_fwd = _bench_forward(benchmark_series("NIFTY 50"), (HORIZON_DAYS, SECONDARY))

    # ── gather ───────────────────────────────────────────────────────────────
    raw: list[Observation] = []
    feats: dict[tuple[str, str], dict[str, float]] = {}
    for kind in ("selected", "discovery_rejected"):
        for row in _export(args.api, kind, args.days, args.horizon):
            if not _cohort(row):
                continue
            key = (str(row["date"])[:10], row["symbol"])
            feats.setdefault(key, _features(row))
            raw.append(Observation(symbol=row["symbol"], date=row["date"], cohort="ALL"))
    obs = dedupe(raw, precedence=("ALL",))
    priced, cov = evaluate(obs, book, horizons=(SECONDARY, HORIZON_DAYS))
    c = cov.to_dict()
    print(f"observations: {len(obs)} | priced {c['priced']} ({c['priced_pct']}%) | "
          f"no_series {c['no_series']} (survivorship) | immature {c['immature_by_horizon']}")

    # ── per-day panel: date -> [(features, ret)] for the primary horizon ─────
    panel: dict[str, list[tuple[dict[str, float], float, float | None]]] =         collections.defaultdict(list)
    for o, res in priced:
        oc = res.get(HORIZON_DAYS)
        if oc is None or oc.ret is None:
            continue
        f = feats.get((str(o.date)[:10], o.symbol))
        if f:
            d = str(o.date)[:10]
            b = bench_fwd[HORIZON_DAYS].get(d)
            panel[d].append((f, oc.ret, (oc.ret - b) if b is not None else None))
    usable = sorted(d for d, rows in panel.items() if len(rows) >= MIN_PER_DAY)
    print(f"matured scan days with >={MIN_PER_DAY} candidates: {len(usable)} "
          f"({usable[0]} .. {usable[-1]})" if usable else "no usable days")
    if len(usable) < 2 * MIN_DAYS_PER_HALF + 1:
        print(f"::warning::only {len(usable)} usable days — cannot form two halves plus an "
              f"embargo of {HORIZON_DAYS} days. INSUFFICIENT EVIDENCE; stopping.")
        return 0

    # ── time-separated split with an embargo ─────────────────────────────────
    # The embargo must be at least the horizon, or a train observation's outcome
    # window reaches into the test period and the split leaks.
    half = (len(usable) - HORIZON_DAYS) // 2
    if half < MIN_DAYS_PER_HALF:
        print(f"::warning::embargo of {HORIZON_DAYS} days leaves only {half} days per half "
              f"(<{MIN_DAYS_PER_HALF}). INSUFFICIENT EVIDENCE; stopping.")
        return 0
    train_days = set(usable[:half])
    test_days = set(usable[half + HORIZON_DAYS:])
    print(f"train: {len(train_days)} days ({usable[0]} .. {usable[half - 1]})")
    print(f"embargo: {HORIZON_DAYS} days (dropped, so no train outcome reaches the test window)")
    print(f"test : {len(test_days)} days ({usable[half + HORIZON_DAYS]} .. {usable[-1]})")

    def day_rho(days: set[str], feat: str) -> tuple[float | None, int]:
        """Mean per-day rank correlation between the feature and the outcome."""
        rhos = []
        for d in days:
            rows = [(f[feat], r) for f, r, _ in panel[d] if feat in f]
            if len(rows) >= MIN_PER_DAY:
                rho = _spearman([a for a, _ in rows], [b for _, b in rows])
                if rho is not None:
                    rhos.append(rho)
        return (st.mean(rhos) if rhos else None), len(rhos)

    def tercile_bootstrap(days: set[str], feat: str) -> dict:
        """Top vs bottom tercile WITHIN each day, bootstrapped across days.

        The benchmark's forward return is constant inside a day, so it cancels
        from this within-day difference: the result is already
        benchmark-relative.
        """
        synth: list[tuple[Observation, dict]] = []
        from services.forward_eval import Outcome
        for d in days:
            rows = [(f[feat], r) for f, r, _ in panel[d] if feat in f]
            if len(rows) < MIN_PER_DAY:
                continue
            rows.sort(key=lambda t: t[0])
            k = len(rows) // 3
            for i, (_, ret) in enumerate(rows):
                bucket = "LO" if i < k else ("HI" if i >= len(rows) - k else None)
                if bucket:
                    synth.append((Observation(symbol=f"{d}:{i}", date=d, cohort=bucket),
                                  {HORIZON_DAYS: Outcome(ret=ret)}))
        return block_bootstrap(synth, "LO", "HI", HORIZON_DAYS, metric="ret",
                               iters=BOOTSTRAP, seed=args.seed, min_days=MIN_DAYS_PER_HALF)

    def bucket_excess(days: set[str], feat: str) -> dict[str, tuple[float, float, int]]:
        """Median excess vs the benchmark, and beat-rate, per tercile bucket.

        'The top bucket beats its same-day peers' and 'the top bucket beats the
        market' are different claims, so the absolute level is reported too.
        """
        acc: dict[str, list[float]] = {"HI": [], "LO": []}
        for d in days:
            rows = [(f[feat], e) for f, _, e in panel[d] if feat in f and e is not None]
            if len(rows) < MIN_PER_DAY:
                continue
            rows.sort(key=lambda t: t[0])
            k = len(rows) // 3
            acc["LO"].extend(e for _, e in rows[:k])
            acc["HI"].extend(e for _, e in rows[-k:])
        return {b: (st.median(v), sum(1 for x in v if x > 0) * 100.0 / len(v), len(v))
                for b, v in acc.items() if v}

    # ── absolute level, descriptively: does the top bucket beat the market? ──
    print(f"\n{'=' * 104}")
    print(f"FEATURE vs REALISED {HORIZON_DAYS}d RETURN   ({len(FEATURES)} features tested — "
          f"apply your own multiple-comparison discount)")
    print(f"{'=' * 104}")
    print(f"{'feature':15s} {'rho_train':>10s} {'rho_test':>9s} {'sign':>5s} "
          f"{'HI-LO_test':>11s} {'95% CI':>18s} {'days':>5s}  verdict")

    candidates = []
    for feat in FEATURES:
        rt, nt = day_rho(train_days, feat)
        rs, ns = day_rho(test_days, feat)
        if rt is None or rs is None or nt < MIN_DAYS_PER_HALF or ns < MIN_DAYS_PER_HALF:
            print(f"{feat:15s} {'n/a':>10s} {'n/a':>9s} {'-':>5s} {'-':>11s} "
                  f"{'-':>18s} {nt:2d}/{ns:2d}  NOT ESTABLISHED (too few days)")
            continue
        same = (rt > 0) == (rs > 0)
        b = tercile_bootstrap(test_days, feat)
        if b.get("withheld"):
            print(f"{feat:15s} {rt:+10.3f} {rs:+9.3f} {'yes' if same else 'NO':>5s} "
                  f"{'-':>11s} {'withheld':>18s} {b['days']:5d}  NOT ESTABLISHED")
            continue
        lo, hi = b["ci95"]
        diff, sig = b["diff"], b["significant"]
        ok = same and sig and abs(diff) >= MIN_EFFECT_PP
        verdict = "CANDIDATE" if ok else "NOT ESTABLISHED"
        if not same:
            verdict += " (sign flips train->test)"
        elif not sig:
            verdict += " (CI includes 0)"
        elif abs(diff) < MIN_EFFECT_PP:
            verdict += f" (|{diff:.2f}pp| < {MIN_EFFECT_PP}pp)"
        print(f"{feat:15s} {rt:+10.3f} {rs:+9.3f} {'yes' if same else 'NO':>5s} "
              f"{diff:+10.2f}pp [{lo:+7.2f},{hi:+7.2f}] {b['days']:5d}  {verdict}")
        if ok:
            candidates.append((feat, rt, rs, diff, (lo, hi)))

    print(f"\n{'=' * 104}")
    if candidates:
        print("CANDIDATE features — survived train/test sign agreement, a test-set CI excluding "
              "zero, and the minimum effect size:")
        for feat, rt, rs, diff, (lo, hi) in candidates:
            print(f"  {feat}: rho {rt:+.3f} -> {rs:+.3f}, HI-LO {diff:+.2f}pp "
                  f"95%CI [{lo:+.2f},{hi:+.2f}]")
            for b, (med, beat, n) in sorted(bucket_excess(test_days, feat).items()):
                print(f"      {b} bucket vs NIFTY: median {med:+.2f}%  beat {beat:.1f}%  n={n}")
        print("\nThis is ONE time-split on ONE window. It is a reason to pre-register a proper "
              "test, not a reason to change a weight.")
    else:
        print("NO feature met the pre-registered bar. On this window and this split, none of the "
              f"{len(FEATURES)} features we already compute orders stocks by realised "
              "benchmark-relative return.\nThat is the finding. Nothing is tuned to manufacture "
              "one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
