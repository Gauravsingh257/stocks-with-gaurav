"""
scripts/ranking_inputs_study.py — which RANKING INPUTS, if any, pick stocks that
beat the benchmark?

READ-ONLY. No thresholds, weights, flags or targets are touched. Nothing imports
this module.

## Why this exists separately from feature_excess_return_study.py

That study could only reach what `/api/research/signals-log-export` returns: the
ranker's final `confidence` score plus geometry derived from the stop. It found
nothing, but the finding was weak because three of its seven "features" were the
same number (swing's planned RR is a hard constant 3.000) and the ranker's real
inputs were never in the sample.

They were in the database all along. `signals_log.layer_details` carries them at
**100% coverage on all 1,270,705 production rows, 2026-04-25 to 2026-10-16**
across 129 scan dates. An earlier conclusion that we would have to wait ~2 months
for history was wrong — it was drawn from a stale partial local copy of the table.

This script reads `signals_log` directly, so it needs no new endpoint. It runs
either locally against a DB copy or on the service itself, where the database,
the Redis universe snapshot and `services/forward_eval` already live.

## Pre-registered before any test-set result was seen

Candidate features are every numeric leaf of `layer_details`. The ones that
matter most, and the reason this is the right question to ask, are
`score_breakdown.{trend, volume, momentum, smc}` — the ranker's own weighted
components — plus `score_breakdown.composite`, the three `quality.*` sub-scores
and `smc.confirmation_score`. Those ten are >=90% populated in every month from
2026-05 onward. Everything else is reported with its usable-day count and
skipped when too sparse; nothing is silently dropped.

TARGET: realised return over 20 trading days from the scan-date close,
benchmark-relative.

DECISION RULE, fixed in advance. A feature is a CANDIDATE only if ALL hold:
  1. same per-day rank-correlation sign in train and test;
  2. test-set top-vs-bottom tercile 95% CI excluding zero;
  3. >= MIN_DAYS_PER_HALF scan days in each half;
  4. |effect| >= MIN_EFFECT_PP, so a statistically real triviality cannot pass.
Many features are tested, so some will look good by chance. The train/test sign
agreement is the guard, the number tested is printed, and a feature that only
survives one of the two windows below is reported as such, not promoted.

## Windows

- `full`     2026-05-01 onward. Maximum power and a genuine forward-in-time
             split, but it straddles the 2026-08-23 Phase 0/1/2 selection
             change, so a sign flip could be a pipeline change rather than
             noise. Reported as the primary with that confound named.
- `pre_era`  2026-05-01 .. 2026-08-23. Era-consistent and still well powered
             (79 scan dates), but it describes the OLD pipeline.
A feature surviving BOTH is materially stronger than one surviving either.

2026-04 is excluded throughout: `quality.data_authenticity` is 'synthetic' for
~78% of that month's rows, against ~95% 'real' from May.

## Method

Base = close of the scan date (scans publish pre-open, so no look-ahead).
Matured observations only; a missing bar at D+N is dropped and counted, never
zero-filled. One observation per (scan_date, symbol). Terciles computed WITHIN
each scan day, because the ranker is cross-sectional. Statistical claims use
`forward_eval.block_bootstrap`, which resamples scan DAYS since overlapping
forward windows make observations dependent.

Raw return is already benchmark-relative for the tercile test: the benchmark's
forward move is constant within a scan day and the bootstrap differences two
buckets inside that same day, so the benchmark term cancels exactly. The
absolute excess level is reported separately, because "beats its peers" and
"beats the market" are different claims.

    python -m scripts.ranking_inputs_study [--db /data/dashboard.db] [--from 2026-05-01]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import statistics as st
import sys

HORIZON = 20
BOOTSTRAP = 5000
MIN_DAYS_PER_HALF = 8
MIN_EFFECT_PP = 1.0
MIN_PER_DAY = 30
ERA_SPLIT = "2026-08-23"       # Phase 0/1/2 selection change
SYNTHETIC_CUTOFF = "2026-05-01"

PRIMARY = ("score_breakdown.trend", "score_breakdown.volume",
           "score_breakdown.momentum", "score_breakdown.smc",
           "score_breakdown.composite", "quality.score",
           "quality.technical_score", "quality.fundamental_score",
           "smc.confirmation_score", "smc.partial_hits")


def _leaves(d, p="", out=None):
    """Numeric leaves of the JSON, flattened to dotted paths. bool is excluded:
    True/False ranks as 1/0 and would masquerade as a continuous feature."""
    out = {} if out is None else out
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                _leaves(v, p + k + ".", out)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                f = float(v)
                if f == f:                      # NaN-safe
                    out[p + k] = f
    return out


def _spearman(xs, ys):
    """Rank correlation without a scipy dependency; average ranks for ties."""
    n = len(xs)
    if n < 5:
        return None

    def ranks(v):
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


def _bench_forward(bench, h):
    closes = []
    for b in bench:
        try:
            closes.append((str(b.get("date"))[:10], float(b["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return {d: (closes[i + h][1] - c0) / c0 * 100.0
            for i, (d, c0) in enumerate(closes) if i + h < len(closes) and c0}


def main() -> int:
    ap = argparse.ArgumentParser(description="Ranking inputs vs excess return (read-only)")
    ap.add_argument("--db", default=os.path.join(os.getenv("DATA_DIR", "."), "dashboard.db"))
    ap.add_argument("--from", dest="date_from", default=SYNTHETIC_CUTOFF)
    ap.add_argument("--horizon-label", default="SWING")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    sys.path.insert(0, "/app")
    sys.path.insert(0, os.getcwd())
    from services.forward_eval import (
        Observation,
        Outcome,
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
    last_bar = max(str(b[-1].get("date"))[:10] for b in book._series.values() if b)
    print(f"price book : {len(book._series)} symbols | last bar {last_bar}")
    bench = _bench_forward(benchmark_series("NIFTY 50"), HORIZON)
    print(f"benchmark  : {len(bench)} dated {HORIZON}d forward returns")

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT date, symbol, layer_details FROM signals_log "
        "WHERE date >= ? AND horizon = ? AND layer_details IS NOT NULL "
        "AND length(layer_details) > 40",
        (args.date_from, args.horizon_label)).fetchall()
    print(f"signals_log: {len(rows)} rows from {args.date_from} ({args.horizon_label})")

    obs, feats = [], {}
    for date, symbol, ld in rows:
        try:
            lv = _leaves(json.loads(ld))
        except Exception:
            continue
        if not lv:
            continue
        key = (str(date)[:10], symbol)
        feats.setdefault(key, lv)
        obs.append(Observation(symbol=symbol, date=str(date)[:10], cohort="ALL"))
    obs = dedupe(obs, precedence=("ALL",))
    priced, cov = evaluate(obs, book, horizons=(HORIZON,))
    c = cov.to_dict()
    print(f"observations: {len(obs)} | priced {c['priced']} ({c['priced_pct']}%) | "
          f"no_series {c['no_series']} (survivorship) | immature {c['immature_by_horizon']}")

    panel: dict[str, list[tuple[dict, float, float | None]]] = collections.defaultdict(list)
    for o, res in priced:
        oc = res.get(HORIZON)
        if oc is None or oc.ret is None:
            continue
        f = feats.get((o.date, o.symbol))
        if f:
            b = bench.get(o.date)
            panel[o.date].append((f, oc.ret, (oc.ret - b) if b is not None else None))

    all_days = sorted(d for d, r in panel.items() if len(r) >= MIN_PER_DAY)
    print(f"matured scan days with >={MIN_PER_DAY} candidates: {len(all_days)}"
          + (f" ({all_days[0]} .. {all_days[-1]})" if all_days else ""))
    if not all_days:
        print("::warning::no usable days. INSUFFICIENT EVIDENCE; stopping.")
        return 0

    candidate_keys = sorted({k for r in panel.values() for f, _, _ in r for k in f})
    print(f"numeric layer_details features found: {len(candidate_keys)}")

    def rho(days, feat):
        out = []
        for d in days:
            rr = [(f[feat], r) for f, r, _ in panel[d] if feat in f]
            if len(rr) >= MIN_PER_DAY:
                v = _spearman([a for a, _ in rr], [b for _, b in rr])
                if v is not None:
                    out.append(v)
        return (st.mean(out) if out else None), len(out)

    def terciles(days, feat):
        synth = []
        for d in days:
            rr = [(f[feat], r) for f, r, _ in panel[d] if feat in f]
            if len(rr) < MIN_PER_DAY:
                continue
            rr.sort(key=lambda t: t[0])
            k = len(rr) // 3
            for i, (_, ret) in enumerate(rr):
                b = "LO" if i < k else ("HI" if i >= len(rr) - k else None)
                if b:
                    synth.append((Observation(symbol=f"{d}:{i}", date=d, cohort=b),
                                  {HORIZON: Outcome(ret=ret)}))
        return block_bootstrap(synth, "LO", "HI", HORIZON, metric="ret",
                               iters=BOOTSTRAP, seed=args.seed, min_days=MIN_DAYS_PER_HALF)

    def excess(days, feat):
        acc = {"HI": [], "LO": []}
        for d in days:
            rr = [(f[feat], e) for f, _, e in panel[d] if feat in f and e is not None]
            if len(rr) < MIN_PER_DAY:
                continue
            rr.sort(key=lambda t: t[0])
            k = len(rr) // 3
            acc["LO"].extend(e for _, e in rr[:k])
            acc["HI"].extend(e for _, e in rr[-k:])
        return {b: (st.median(v), sum(1 for x in v if x > 0) * 100.0 / len(v), len(v))
                for b, v in acc.items() if v}

    survivors: dict[str, set[str]] = {}
    for label, days in (("full", all_days),
                        ("pre_era", [d for d in all_days if d < ERA_SPLIT])):
        half = (len(days) - HORIZON) // 2
        print(f"\n{'=' * 108}\nWINDOW {label}: {len(days)} usable days", end="")
        if half < MIN_DAYS_PER_HALF:
            print(f" -> embargo of {HORIZON} leaves {half} per half "
                  f"(<{MIN_DAYS_PER_HALF}). INSUFFICIENT; skipped.")
            survivors[label] = set()
            continue
        train, test = set(days[:half]), set(days[half + HORIZON:])
        print(f" | train {len(train)} ({days[0]} .. {days[half - 1]}) | "
              f"embargo {HORIZON} | test {len(test)} ({days[half + HORIZON]} .. {days[-1]})")
        print(f"{'=' * 108}")
        print(f"{'feature':34s} {'rho_tr':>8s} {'rho_te':>8s} {'sign':>5s} "
              f"{'HI-LO_te':>9s} {'95% CI':>18s} {'days':>5s}  verdict")
        keep = set()
        for feat in candidate_keys:
            rt, nt = rho(train, feat)
            rs, ns = rho(test, feat)
            star = "*" if feat in PRIMARY else " "
            if rt is None or rs is None or nt < MIN_DAYS_PER_HALF or ns < MIN_DAYS_PER_HALF:
                print(f"{star}{feat:33s} {'n/a':>8s} {'n/a':>8s} {'-':>5s} {'-':>9s} "
                      f"{'-':>18s} {nt:2d}/{ns:2d}  skipped (too few days)")
                continue
            b = terciles(test, feat)
            if b.get("withheld"):
                print(f"{star}{feat:33s} {rt:+8.3f} {rs:+8.3f} {'-':>5s} {'-':>9s} "
                      f"{'withheld':>18s} {b['days']:5d}  NOT ESTABLISHED")
                continue
            same = (rt > 0) == (rs > 0)
            lo, hi = b["ci95"]
            diff, sig = b["diff"], b["significant"]
            ok = same and sig and abs(diff) >= MIN_EFFECT_PP
            why = ("CANDIDATE" if ok else
                   "NOT ESTABLISHED" + (" (sign flips)" if not same else
                                        " (CI includes 0)" if not sig else
                                        f" (|{diff:.2f}pp| < {MIN_EFFECT_PP}pp)"))
            print(f"{star}{feat:33s} {rt:+8.3f} {rs:+8.3f} {'yes' if same else 'NO':>5s} "
                  f"{diff:+8.2f}pp [{lo:+7.2f},{hi:+7.2f}] {b['days']:5d}  {why}")
            if ok:
                keep.add(feat)
        survivors[label] = keep

    print(f"\n{'=' * 108}")
    both = survivors.get("full", set()) & survivors.get("pre_era", set())
    either = (survivors.get("full", set()) | survivors.get("pre_era", set())) - both
    print(f"features tested: {len(candidate_keys)} "
          f"(* = one of the {len(PRIMARY)} pre-registered primaries) — apply your own "
          f"multiple-comparison discount")
    if both:
        print("\nSURVIVED BOTH WINDOWS — the only results worth acting on:")
        for f in sorted(both):
            for b, (med, beat, n) in sorted(excess(all_days, f).items()):
                print(f"  {f:34s} {b} bucket vs NIFTY: median {med:+.2f}%  beat {beat:.1f}%  n={n}")
    if either:
        print("\nSurvived only ONE window — reported, NOT promoted:")
        for f in sorted(either):
            w = "full" if f in survivors.get("full", set()) else "pre_era"
            print(f"  {f}  (only in {w})")
    if not both and not either:
        print("\nNO feature met the pre-registered bar in either window. On this data, none of "
              f"the {len(candidate_keys)} numeric ranking inputs we log orders stocks by realised "
              "benchmark-relative return.\nThat is the finding. Nothing was tuned to produce one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
