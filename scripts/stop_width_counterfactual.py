"""scripts/stop_width_counterfactual.py — would a 6% stop have kept the winners?

READ-ONLY. Changes no position, threshold, flag or trading rule.

THE QUESTION
165 closed SMC trades show median MAE -5.01% against a 5% stop, and the breach
rate falls from 52.7% to 25.5% between a 5% and a 6% stop. That says the stop
sits on the median adverse excursion. It does NOT say a wider stop would have
earned more, because MAE/MFE carry no ORDERING: a trade whose MAE is -5% and
MFE +9% might have run first and faded, or faded first and recovered. Those
have opposite implications and MAE/MFE cannot tell them apart.

So this replays each trade against its actual daily bars and applies both rules
forward from entry, which does encode order.

WHAT DAILY BARS CANNOT SETTLE
When a single session's low breaches the stop AND its high reaches the target,
the sequence inside that session is unknowable from OHLC. Those trades are
counted as AMBIGUOUS and reported separately — never silently resolved in the
favourable direction, which is the standard way this analysis flatters itself.

EXECUTION ASSUMPTIONS (stated, not hidden)
  * Entry at the recorded entry_price on entry_fill_at.
  * A stop fills at the stop price, EXCEPT on a gap-through (open below the
    stop), where it fills at the open — the realistic, worse outcome.
  * A target fills at the target price; a gap-through above fills at the open.
  * Costs: `--cost-bps` round-trip (default 30bps = brokerage + impact + STT).
  * Horizon cap: `--max-days` trading sessions, after which the trade is marked
    OPEN_AT_HORIZON and marked to the last close. Unbounded holds would let a
    wider stop borrow return from the future.

    python -m scripts.stop_width_counterfactual [--wide 6.0] [--max-days 30]

Needs REDIS_URL (universe OHLC snapshot) and the production lifecycle ledger.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import statistics as st
import sys


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def simulate(bars: list[dict], entry: float, stop_pct: float, target_pct: float,
             max_days: int, cost_bps: float) -> dict:
    """Walk bars forward applying one stop/target rule. Returns the outcome."""
    stop = entry * (1 - stop_pct / 100.0)
    target = entry * (1 + target_pct / 100.0)
    for i, b in enumerate(bars[:max_days], start=1):
        o, hi, lo = _f(b.get("open")), _f(b.get("high")), _f(b.get("low"))
        if hi is None or lo is None:
            continue
        hit_stop, hit_target = lo <= stop, hi >= target
        if hit_stop and hit_target:
            # Both inside one session. OHLC cannot order them.
            return {"outcome": "AMBIGUOUS", "days": i, "exit": None, "ret_pct": None}
        if hit_stop:
            fill = o if (o is not None and o < stop) else stop     # gap-through
            return {"outcome": "STOP", "days": i, "exit": fill,
                    "ret_pct": (fill - entry) / entry * 100 - cost_bps / 100.0}
        if hit_target:
            fill = o if (o is not None and o > target) else target
            return {"outcome": "TARGET", "days": i, "exit": fill,
                    "ret_pct": (fill - entry) / entry * 100 - cost_bps / 100.0}
    horizon = bars[:max_days]
    if not horizon:
        return {"outcome": "NO_BARS", "days": 0, "exit": None, "ret_pct": None}
    last = _f(horizon[-1].get("close"))
    if last is None:
        return {"outcome": "NO_BARS", "days": len(horizon), "exit": None, "ret_pct": None}
    return {"outcome": "OPEN_AT_HORIZON", "days": len(horizon), "exit": last,
            "ret_pct": (last - entry) / entry * 100 - cost_bps / 100.0}


def main() -> int:
    ap = argparse.ArgumentParser(description="5% vs 6% stop counterfactual (read-only)")
    ap.add_argument("--narrow", type=float, default=5.0)
    ap.add_argument("--wide", type=float, default=6.0)
    ap.add_argument("--target", type=float, default=15.0)
    ap.add_argument("--max-days", type=int, default=30)
    ap.add_argument("--cost-bps", type=float, default=30.0)
    ap.add_argument("--reached", type=float, default=8.0,
                    help="secondary question: how many later reached this gain")
    ap.add_argument("--ledger", default=None, help="cached ledger JSON (else fetch)")
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from services.forward_eval import PriceBook

    if args.ledger:
        with open(args.ledger, encoding="utf-8") as fh:
            rows = json.load(fh)
    else:
        from scripts.portfolio_quality_audit import DEFAULT_API, load_ledger
        rows = load_ledger(DEFAULT_API, limit=5000)

    book = PriceBook.from_universe_snapshot()
    if not book._series:
        print("::error::no universe OHLC snapshot (REDIS_URL?)")
        return 2

    CLOSED = {"STOP_HIT", "TARGET_HIT", "TIME_EXIT", "FORCED_EXIT", "MANUAL_CLOSED"}
    cand = [r for r in rows
            if str(r.get("executed")) == "1" and r.get("engine") == "SMC"
            and str(r.get("status")).upper() in CLOSED
            and _f(r.get("entry_price")) and r.get("entry_fill_at")]

    res = {"narrow": [], "wide": []}
    counts = {"narrow": collections.Counter(), "wide": collections.Counter()}
    no_series = reached_after_stop = narrow_stopped = 0
    retained = collections.Counter()
    for r in cand:
        sym = str(r.get("symbol") or "")
        day = str(r.get("entry_fill_at"))[:10]
        bars = book._bars_from(sym, day)
        if not bars:
            no_series += 1
            continue
        entry = _f(r.get("entry_price"))
        fwd = bars[1:]                       # entry day is the fill, not a test bar
        n = simulate(fwd, entry, args.narrow, args.target, args.max_days, args.cost_bps)
        w = simulate(fwd, entry, args.wide, args.target, args.max_days, args.cost_bps)
        counts["narrow"][n["outcome"]] += 1
        counts["wide"][w["outcome"]] += 1
        if n["ret_pct"] is not None:
            res["narrow"].append(n["ret_pct"])
        if w["ret_pct"] is not None:
            res["wide"].append(w["ret_pct"])
        # The headline question: of trades the NARROW stop closed, how many later
        # ran to +X, and what did the WIDE stop actually do with them?
        if n["outcome"] == "STOP":
            narrow_stopped += 1
            after = fwd[n["days"]:args.max_days]
            if any((_f(b.get("high")) or -1e9) >= entry * (1 + args.reached / 100.0)
                   for b in after):
                reached_after_stop += 1
            retained[w["outcome"]] += 1

    print(f"candidates: {len(cand)} closed executed SMC trades with entry data")
    print(f"unpriceable (symbol absent from snapshot): {no_series}")
    print(f"simulated: {len(cand) - no_series}   horizon {args.max_days} sessions   "
          f"target {args.target}%   costs {args.cost_bps}bps round-trip")
    print()
    for k, pct in (("narrow", args.narrow), ("wide", args.wide)):
        c = counts[k]
        tot = sum(c.values())
        if not tot:
            continue
        v = res[k]
        print(f"--- stop {pct}% ---")
        for o in ("STOP", "TARGET", "OPEN_AT_HORIZON", "AMBIGUOUS", "NO_BARS"):
            if c[o]:
                print(f"    {o:16s} {c[o]:4d}  ({100*c[o]/tot:5.1f}%)")
        if v:
            print(f"    mean ret {st.mean(v):+6.2f}%   median {st.median(v):+6.2f}%   "
                  f"n_priced {len(v)}")
    print()
    print("=== the ordering question (what MAE/MFE could not answer) ===")
    print(f"  trades the {args.narrow}% stop closed: {narrow_stopped}")
    print(f"  ...of which later reached +{args.reached}% within the horizon: "
          f"{reached_after_stop}"
          f"  ({100*reached_after_stop/narrow_stopped if narrow_stopped else 0:.1f}%)")
    print(f"  what the {args.wide}% stop did with those same trades:")
    for o, k in retained.most_common():
        print(f"    {o:16s} {k:4d}  ({100*k/narrow_stopped if narrow_stopped else 0:5.1f}%)")
    print()
    print("Costs and gap-fills are applied. AMBIGUOUS trades are excluded from the")
    print("return means rather than resolved favourably — daily OHLC cannot order a")
    print("session that touched both levels. Scanned candidates are not included;")
    print("these are executed positions only. No rule, flag or threshold is changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
