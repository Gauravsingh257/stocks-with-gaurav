"""
scripts/measure_de_fix_impact.py — before/after impact of the debt/equity unit fix.

    python -m scripts.measure_de_fix_impact --symbols symbols.json [--control control.json] [--workers 8]

`symbols.json` is a JSON list of NSE symbols (e.g. one LONGTERM scan's universe);
`control.json` optionally lists that scan's final_selected symbols. Snapshots come
through services.fundamental_analysis exactly as the live scan builds them (honours
FUNDAMENTALS_CACHE_DIR), so both scores are the real ones — the legacy value that
live ranking uses today and the corrected value behind FUND_DE_UNIT_FIX.

Reports: how many names the fix touches, the change in debt_quality and
fundamental_score, and what moves in the Long-Term ranking inputs — the
cross-sectional percentile of `fundamental_score` (weight 0.30) and of the
`quality` factor (0.18), plus top-N membership of the fundamental block
(fund 0.30 + growth 0.20 + quality 0.18). Read-only; changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import fundamental_analysis as fa  # noqa: E402


def _pct(values: dict[str, float]) -> dict[str, float]:
    order = sorted(values, key=lambda s: (values[s], s))
    n = max(len(order) - 1, 1)
    return {s: i / n for i, s in enumerate(order)}


def _top(values: dict[str, float], k: int) -> set[str]:
    return set(sorted(values, key=lambda s: (-values[s], s))[:k])


def measure(snaps: dict, control: set[str] | None = None) -> dict:
    real = {s: v for s, v in snaps.items() if v is not None and v.data_source != "hash"
            and v.fundamental_score_de_fixed is not None}
    if not real:
        return {"symbols_requested": len(snaps), "real_fundamentals": 0,
                "error": "no real fundamentals — provider blocked or cache holds only hash fallbacks"}
    with_de = {s: v for s, v in real.items() if v.raw_debt_equity_provider is not None}
    # Compare unrounded values: `debt_equity_ratio` is stored to 4 dp, so comparing
    # it with the legacy value would count every name above 10% as "moved".
    touched = {s: v for s, v in with_de.items()
               if fa.legacy_debt_equity(v.raw_debt_equity_provider) != fa.debt_equity_ratio(v.raw_debt_equity_provider)}

    def legacy_quality(v):
        return (v.roce + v.roe + v.management_quality) / 3

    def fixed_quality(v):
        return (v.roce + v.roe + v.management_quality_de_fixed) / 3

    f_old = {s: v.fundamental_score for s, v in real.items()}
    f_new = {s: v.fundamental_score_de_fixed for s, v in real.items()}
    q_old = {s: legacy_quality(v) for s, v in real.items()}
    q_new = {s: fixed_quality(v) for s, v in real.items()}
    growth = {s: (v.revenue_growth + v.earnings_growth) / 2 for s, v in real.items()}
    pf_old, pf_new, pq_old, pq_new, pg = _pct(f_old), _pct(f_new), _pct(q_old), _pct(q_new), _pct(growth)
    block_old = {s: (0.30 * pf_old[s] + 0.20 * pg[s] + 0.18 * pq_old[s]) / 0.68 for s in real}
    block_new = {s: (0.30 * pf_new[s] + 0.20 * pg[s] + 0.18 * pq_new[s]) / 0.68 for s in real}

    d_score = [f_new[s] - f_old[s] for s in touched]
    d_pct = sorted(((pf_new[s] - pf_old[s]) * 100, s) for s in real)
    movers = sorted(touched, key=lambda s: -(pf_new[s] - pf_old[s]))[:15]
    out = {
        "symbols_requested": len(snaps),
        "real_fundamentals": len(real),
        "with_debt_equity": len(with_de),
        "touched_by_fix": len(touched),
        "touched_pct_of_real": round(len(touched) / len(real) * 100, 1) if real else None,
        "provider_range_of_touched": [min(v.raw_debt_equity_provider for v in touched.values()),
                                      max(v.raw_debt_equity_provider for v in touched.values())] if touched else None,
        "delta_fundamental_score_touched": {
            "mean": round(statistics.fmean(d_score), 4), "median": round(statistics.median(d_score), 4),
            "min": round(min(d_score), 4), "max": round(max(d_score), 4)} if d_score else None,
        "delta_fundamental_pctile_pts": {
            "mean_abs": round(statistics.fmean(abs(x) for x, _ in d_pct), 2),
            "max_up": [round(d_pct[-1][0], 1), d_pct[-1][1]], "max_down": [round(d_pct[0][0], 1), d_pct[0][1]]},
        "top_n_overlap_fundamental_score": {k: len(_top(f_old, k) & _top(f_new, k)) for k in (25, 50, 100)},
        "top_n_overlap_lt_fundamental_block": {k: len(_top(block_old, k) & _top(block_new, k)) for k in (25, 50, 100)},
        "largest_movers": [{"symbol": s, "provider_de": touched[s].raw_debt_equity_provider,
                            "legacy_de": fa.legacy_debt_equity(touched[s].raw_debt_equity_provider),
                            "fixed_de": touched[s].debt_equity_ratio,
                            "fund_old": round(f_old[s], 4), "fund_new": round(f_new[s], 4),
                            "pctile_old": round(pf_old[s] * 100, 1), "pctile_new": round(pf_new[s] * 100, 1)}
                           for s in movers],
    }
    if control:
        c = [s for s in control if s in real]
        out["control_final_selected"] = {
            "n": len(control), "with_real_fundamentals": len(c), "touched": sum(1 for s in c if s in touched),
            "mean_delta_fund_pctile_pts": round(statistics.fmean((pf_new[s] - pf_old[s]) * 100 for s in c), 2)
            if c else None}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--control")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--retries", type=int, default=2, help="refetch provider misses (rate-limit / crumb errors)")
    args = ap.parse_args()
    with open(args.symbols, encoding="utf-8") as f:
        symbols = json.load(f)
    control = None
    if args.control:
        with open(args.control, encoding="utf-8") as f:
            control = set(json.load(f))
    with ThreadPoolExecutor(args.workers) as ex:
        snaps = dict(zip(symbols, ex.map(fa._fetch_snapshot, symbols), strict=True))
    for _ in range(args.retries):
        missed = [s for s, v in snaps.items() if v.data_source == "hash"]
        if not missed:
            break
        time.sleep(30)
        for s in missed:
            fa._cache_path(s).unlink(missing_ok=True)  # a miss is cached too; drop it before refetching
        with ThreadPoolExecutor(max(1, args.workers // 2)) as ex:
            snaps.update(zip(missed, ex.map(fa._fetch_snapshot, missed), strict=True))
    print(json.dumps(measure(snaps, control), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
