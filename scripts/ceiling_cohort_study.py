"""
scripts/ceiling_cohort_study.py — does the top-20 ceiling actually pick winners?

READ-ONLY. Compares forward returns of three cohorts from `signals_log`:

  SELECTED     final_selected=1 (the served top-20)
  CEILING_CUT  L1∧L2∧entry, survived the Phase-2 rank budget, lost at the
               exceptionalism gate (`EXCEPTIONALISM_SOFT_CEILING`, default 20)
  PHASE2_CUT   eligible but `below_rank_budget`

Method (2026-10-11 audit):
- Entry = close of the SCAN DATE for every cohort. Scans publish pre-open, so
  that day's close is tradable after publication — no look-ahead.
- Only matured observations count (the bar at D+N must exist).
- One observation per (scan_date, symbol); SELECTED wins a tie.
- Overlapping windows are handled by aggregating per scan day, then
  bootstrapping across days (a block bootstrap), not across raw observations.
- Symbols missing from the current universe snapshot are counted and reported
  as a survivorship caveat rather than silently dropped.
- ALL THREE cohort pairs are bootstrapped, not just SELECTED vs CEILING_CUT.
  The PHASE2_CUT gap is the large one and previously had no confidence interval.
- `--by-regime` repeats every pair inside RISK_ON / RISK_OFF, where the regime is
  classified from the NIFTY 50 benchmark on the scan date — independent of
  `signals_log`, so it cannot inherit a selection-side bias.

    python -m scripts.ceiling_cohort_study [--days 60] [--horizon SWING]

Needs REDIS_URL (universe OHLC) and reads the public signals-log export.
"""

from __future__ import annotations

import argparse
import collections
import os
import statistics as st
import sys

DEFAULT_API = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")
HORIZONS = (5, 10, 20)
BOOTSTRAP = 5000


def _export(api: str, kind: str, days: int, horizon: str, limit: int = 50000) -> list[dict]:
    import requests

    url = f"{api.rstrip('/')}/api/research/signals-log-export"
    resp = requests.get(
        url,
        params={"days": days, "kind": kind, "limit": limit, "horizon": horizon},
        timeout=300,
        headers={"User-Agent": "ceiling-study/1"},
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def _cohort(row: dict) -> str | None:
    if row.get("final_selected"):
        return "SELECTED"
    if not (row.get("layer1_pass") and row.get("layer2_pass") and row.get("entry") is not None):
        return None
    return "PHASE2_CUT" if "below_rank_budget" in (row.get("rejection_reason") or []) else "CEILING_CUT"


def _describe(v: list[float]) -> str:
    s = sorted(v)
    wins = sum(1 for x in v if x > 0)
    return (f"n={len(v):5d} win={wins * 100 / len(v):5.1f}% mean={st.mean(v):+6.2f}% "
            f"med={st.median(v):+6.2f}% p10={s[int(len(s) * .1)]:+6.1f} p90={s[int(len(s) * .9)]:+6.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Ceiling cohort forward-return study (read-only)")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--horizon", default="SWING", choices=["SWING", "LONGTERM"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--by-regime", action="store_true",
                    help="also split every pair by market regime, classified from the "
                         "NIFTY 50 benchmark on the scan date (independent of signals_log)")
    ap.add_argument("--min-days", type=int, default=5,
                    help="withhold a bootstrap below this many comparable scan days")
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
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
    last = max(str(b[-1].get("date"))[:10] for b in book._series.values() if b)
    print(f"price series: {len(book._series)} symbols | last bar {last}")

    raw: list[Observation] = []
    for kind in ("selected", "discovery_rejected"):
        for row in _export(args.api, kind, args.days, args.horizon):
            coh = _cohort(row)
            if coh:
                raw.append(Observation(symbol=row["symbol"], date=row["date"], cohort=coh))
    obs = dedupe(raw, precedence=("SELECTED", "CEILING_CUT", "PHASE2_CUT"))
    print(f"deduped observations: {len(obs)} "
          f"{dict(collections.Counter(o.cohort for o in obs))}")

    priced, cov = evaluate(obs, book, horizons=HORIZONS)
    c = cov.to_dict()
    print(f"priced observations: {c['priced']} | unpriceable: {c['unpriceable']} "
          f"({100 - int(c['priced_pct'])}% — survivorship caveat)")

    for n in HORIZONS:
        print(f"\n--- forward {n}d (matured only) ---")
        grouped: dict[str, list[float]] = collections.defaultdict(list)
        for o, res in priced:
            oc = res.get(n)
            if oc is not None and oc.ret is not None:
                grouped[o.cohort].append(oc.ret)
        for coh in ("SELECTED", "CEILING_CUT", "PHASE2_CUT"):
            if grouped[coh]:
                print(f"  {coh:12s} {_describe(grouped[coh])}")

    # Every pair, not just SELECTED vs CEILING_CUT. The original study bootstrapped
    # only the ceiling comparison, so the PHASE2_CUT gap — the one that is actually
    # large — was reported as raw means with no confidence interval.
    PAIRS = (("SELECTED", "CEILING_CUT"),
             ("SELECTED", "PHASE2_CUT"),
             ("CEILING_CUT", "PHASE2_CUT"))

    def run_pairs(rows, label: str) -> None:
        print(f"\n=== DATE-BLOCK analysis — {label} "
              f"(per scan day, bootstrapped across days) ===")
        for a, b in PAIRS:
            for n in HORIZONS:
                r = block_bootstrap(rows, a, b, n, iters=BOOTSTRAP,
                                    seed=args.seed, min_days=args.min_days)
                if r.get("withheld"):
                    print(f"  {a:11s} vs {b:11s} {n:2d}d: "
                          f"{r.get('reason') or 'too few days'} — withheld")
                    continue
                lo, hi = r["ci95"]
                verdict = "SIGNIFICANT" if r["significant"] else "no measurable difference"
                print(f"  {a:11s} vs {b:11s} {n:2d}d: days={r['days']:3d} "
                      f"{r[a + '_day_mean']:+.2f}% vs {r[b + '_day_mean']:+.2f}% "
                      f"diff {r['diff']:+.2f}pp 95%CI [{lo:+.2f},{hi:+.2f}] "
                      f"B-won {r['days_b_higher']}/{r['days']} -> {verdict}")

    run_pairs(priced, "ALL DAYS")

    if args.by_regime:
        # Regime from the NIFTY 50 benchmark, NOT from signals_log — the export
        # carries no regime field, and an independent classifier cannot inherit a
        # selection-side bias. RISK_ON = benchmark closed at or above its own
        # trailing 50-bar mean on the scan date.
        try:
            bench = benchmark_series() or []
        except Exception as e:
            print(f"\n::warning::regime split skipped — benchmark unavailable ({e})")
            return 0
        closes: list[float] = []
        regime: dict[str, str] = {}
        for bar in bench:
            c = bar.get("close")
            if c is None:
                continue
            closes.append(float(c))
            if len(closes) >= 50:
                mean50 = sum(closes[-50:]) / 50.0
                regime[str(bar.get("date"))[:10]] = (
                    "RISK_ON" if float(c) >= mean50 else "RISK_OFF")
        if not regime:
            print("\n::warning::regime split skipped — fewer than 50 benchmark bars")
            return 0
        tagged = collections.Counter(regime.get(o.date, "UNKNOWN") for o, _ in priced)
        print(f"\nregime tagging: {dict(tagged)} "
              f"(RISK_ON = NIFTY 50 >= its trailing 50-bar mean on the scan date)")
        for want in ("RISK_ON", "RISK_OFF"):
            subset = [(o, res) for o, res in priced if regime.get(o.date) == want]
            days = len({o.date for o, _ in subset})
            if days < args.min_days:
                print(f"\n=== {want}: only {days} scan days — withheld ===")
                continue
            run_pairs(subset, f"{want} ({days} scan days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
