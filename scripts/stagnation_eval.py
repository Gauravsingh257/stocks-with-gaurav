"""
scripts/stagnation_eval.py — did culling the stagnant position actually help?

READ-ONLY. `scripts/stagnation_shadow.py` only *collects*: each row is a position
that met the stagnation band on a date, plus the best replacement candidate that
was available that day. Nothing ever evaluated whether firing would have paid —
the question the shadow exists to answer.

This closes it using `services/forward_eval.py`, the same evaluator the ceiling
study, exceptionalism calibration, LT shadow and the Phase-1 counterfactual use,
so the maturity, look-ahead and survivorship rules are identical everywhere. It
does NOT read the stale `forward_returns` table.

Two cohorts, measured from the SAME observation date so market conditions match:

    HELD        the stagnant position, if it had been kept
    REPLACEMENT the best candidate that was available instead

A positive REPLACEMENT − HELD difference is evidence the cull would have helped.
Significance comes from a date-block bootstrap, because the daily observations
overlap and are not independent.

    python -m scripts.stagnation_eval [--csv PATH] [--book SWING] [--horizon 20]

Needs REDIS_URL for the universe OHLC snapshot. Changes no strategy logic and
promotes nothing — the give-back/stale-exit rules are untouched.
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import sys

DEFAULT_CSV = os.path.join("docs", "validation", "stagnation_shadow_log.csv")
HORIZONS = (5, 10, 20)


def load_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("date") and r.get("symbol")]


def build_observations(rows: list[dict], book_filter: str | None):
    """One HELD and (where known) one REPLACEMENT observation per logged row."""
    from services.forward_eval import Observation

    obs: list[Observation] = []
    paired_dates: set[str] = set()
    skipped_no_candidate = 0
    for r in rows:
        if r.get("would_fire") != "1":
            continue
        if book_filter and (r.get("book") or "").upper() != book_filter.upper():
            continue
        date = str(r["date"])[:10]
        obs.append(Observation(symbol=r["symbol"], date=date, cohort="HELD",
                               meta={"book": r.get("book"), "days_held": r.get("days_held")}))
        cand = (r.get("best_candidate") or "").strip()
        if cand:
            obs.append(Observation(symbol=cand, date=date, cohort="REPLACEMENT",
                                   meta={"book": r.get("book"), "for": r["symbol"]}))
            paired_dates.add(date)
        else:
            skipped_no_candidate += 1
    return obs, paired_dates, skipped_no_candidate


def main() -> int:
    ap = argparse.ArgumentParser(description="Stagnation shadow evaluation (read-only)")
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--book", default=None, help="SWING | LONGTERM | MOMENTUM (default: all)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from services.forward_eval import PriceBook, block_bootstrap, cohort_stats, evaluate

    rows = load_rows(args.csv)
    obs, paired_dates, no_cand = build_observations(rows, args.book)
    if not obs:
        print("::warning::no would_fire observations matched")
        return 0
    counts = collections.Counter(o.cohort for o in obs)
    print(f"stagnation observations: {len(rows)} logged | would_fire used: {dict(counts)} "
          f"| rows without a replacement candidate: {no_cand} | paired dates: {len(paired_dates)}")

    book = PriceBook.from_universe_snapshot()
    if not book._series:
        print("::error::no universe OHLC snapshot available (REDIS_URL?)")
        return 2

    priced, cov = evaluate(obs, book, horizons=HORIZONS)
    c = cov.to_dict()
    print(f"priced: {c['priced']}/{c['total']} ({c['priced_pct']}%) | unpriceable {c['unpriceable']} "
          f"(no_series {c['no_series']}, no_bar_on_date {c['no_bar_on_date']} "
          f"= market_closed {c['no_bar_market_closed']} + symbol_gap {c['no_bar_symbol_gap']}) — {c['note']}")
    if c["no_bar_market_closed"]:
        print(f"  note: the collector logs on calendar days, so {c['no_bar_market_closed']} rows fall on "
              f"a weekend/holiday. Not a data defect — nothing to price.")

    for h in HORIZONS:
        stats = cohort_stats(priced, h)
        if not stats:
            continue
        print(f"\n--- forward {h}d (matured only) ---")
        for cohort in ("HELD", "REPLACEMENT"):
            s = stats.get(cohort)
            if s:
                print(f"  {cohort:12s} n={s['n']:4d} win={s['win_rate_pct']:5.1f}% "
                      f"mean={s['mean']:+6.2f}% med={s['median']:+6.2f}% "
                      f"p10={s['p10']:+6.1f} p90={s['p90']:+6.1f}")

    print("\n=== DATE-BLOCK analysis (REPLACEMENT - HELD, bootstrapped across dates) ===")
    for h in HORIZONS:
        r = block_bootstrap(priced, "HELD", "REPLACEMENT", h, iters=5000, seed=args.seed, min_days=5)
        if r.get("withheld"):
            print(f"  {h:2d}d: {r['reason']} — withheld")
            continue
        lo, hi = r["ci95"]
        verdict = ("replacement BETTER" if r["significant"] and r["diff"] > 0 else
                   "replacement WORSE" if r["significant"] else "NO measurable difference")
        print(f"  {h:2d}d: dates={r['days']} HELD {r['HELD_day_mean']:+.2f}% "
              f"REPLACEMENT {r['REPLACEMENT_day_mean']:+.2f}% diff {r['diff']:+.2f}pp "
              f"95%CI [{lo:+.2f},{hi:+.2f}] -> {verdict}")
    print("\nEvidence only — no exit rule, threshold or flag is changed by this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
