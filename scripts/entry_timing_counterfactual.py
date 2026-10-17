"""scripts/entry_timing_counterfactual.py — would later or re-entry have done better?

READ-ONLY. Changes no position, threshold, flag or trading rule.

THE QUESTION
The stop-width counterfactual showed a wider stop does not help: of 97 trades the
5% stop closed, a 6% stop also stopped 89. But 27.8% of those later reached +8%
inside the horizon. So the upside is real and the stop is not the way to reach
it. This asks whether the ENTRY was early.

RULES ARE FIXED HERE, BEFORE ANY OUTCOME IS READ
Each is executable from information available at the time. No rule may consult a
low, high or trigger that had not yet printed.

  BASELINE    enter at the recorded entry_price on the signal day (what we did).
  DELAY_n     ignore the signal for n sessions, then enter at the OPEN of the
              next session, unconditionally. Tests "we are simply early".
  PULLBACK_x  rest a limit at entry_price*(1-x/100). Fill if any session inside
              --wait trades at/below it; gap-through fills at that session's
              open. Otherwise NO TRADE, counted as a miss. Tests "enter lower".
  RECLAIM     the re-entry rule. After the 5% stop fires, wait for the first
              session that CLOSES above the high of the stop session, then enter
              at the next session's open. Causal: uses only bars already printed.

All variants are judged over the SAME CALENDAR WINDOW measured from the signal
day, so a delayed entry gets fewer remaining sessions rather than extra future
bars. Without that, lateness buys lookahead.

THE SELECTION TRAP, HANDLED
Evaluating entry rules only on the 97 trades that stopped conditions on the
outcome: that set is pre-selected for having gone down, so any "buy lower" rule
profits from mean reversion by construction. The headline population is
therefore ALL closed executed trades. The stopped subset is reported separately
and labelled as diagnostic, not evidence.

Exploratory / holdout split is by signal date at the median, fixed before
measurement.

    python -m scripts.entry_timing_counterfactual [--ledger cached.json]

Needs REDIS_URL (universe OHLC) and the production lifecycle ledger.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import statistics as st
import sys

STOP_PCT = 5.0
TARGET_PCT = 15.0
HORIZON = 30


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _entry_for(variant: str, bars: list[dict], signal_entry: float, wait: int):
    """Return (entry_price, offset_sessions, missed_reason) using only past bars.

    `bars` starts at the signal day; bars[0] is the signal session itself.
    """
    if variant == "BASELINE":
        return signal_entry, 0, None
    if variant.startswith("DELAY_"):
        n = int(variant.split("_")[1])
        if len(bars) <= n:
            return None, None, "no_bar_after_delay"
        o = _f(bars[n].get("open"))
        return (o, n, None) if o else (None, None, "no_open")
    if variant.startswith("PULLBACK_"):
        x = float(variant.split("_")[1])
        limit = signal_entry * (1 - x / 100.0)
        for i in range(1, min(wait, len(bars) - 1) + 1):
            o, lo = _f(bars[i].get("open")), _f(bars[i].get("low"))
            if lo is None:
                continue
            if lo <= limit:
                fill = o if (o is not None and o < limit) else limit
                return fill, i, None
        return None, None, "limit_never_filled"
    raise ValueError(variant)


def _reclaim_entry(bars: list[dict], stop_session: int, wait: int):
    """First session closing above the stop session's high -> enter next open."""
    if stop_session >= len(bars):
        return None, None, "no_stop_bar"
    trigger_high = _f(bars[stop_session].get("high"))
    if trigger_high is None:
        return None, None, "no_high"
    for i in range(stop_session + 1, min(stop_session + 1 + wait, len(bars) - 1)):
        c = _f(bars[i].get("close"))
        if c is not None and c > trigger_high:
            o = _f(bars[i + 1].get("open"))
            return (o, i + 1, None) if o else (None, None, "no_open")
    return None, None, "never_reclaimed"


def _simulate(bars: list[dict], entry: float, stop_pct: float, target_pct: float,
              budget: int, cost_bps: float, slip_bps: float) -> dict:
    stop = entry * (1 - stop_pct / 100.0)
    target = entry * (1 + target_pct / 100.0)
    drag = (cost_bps + 2 * slip_bps) / 100.0
    window = bars[:budget]
    for i, b in enumerate(window, start=1):
        o, hi, lo = _f(b.get("open")), _f(b.get("high")), _f(b.get("low"))
        if hi is None or lo is None:
            continue
        s, t = lo <= stop, hi >= target
        if s and t:
            return {"outcome": "AMBIGUOUS", "days": i, "ret": None}
        if s:
            fill = o if (o is not None and o < stop) else stop
            return {"outcome": "STOP", "days": i, "ret": (fill - entry) / entry * 100 - drag}
        if t:
            fill = o if (o is not None and o > target) else target
            return {"outcome": "TARGET", "days": i, "ret": (fill - entry) / entry * 100 - drag}
    if not window:
        return {"outcome": "NO_BARS", "days": 0, "ret": None}
    last = _f(window[-1].get("close"))
    if last is None:
        return {"outcome": "NO_BARS", "days": len(window), "ret": None}
    return {"outcome": "OPEN_AT_HORIZON", "days": len(window),
            "ret": (last - entry) / entry * 100 - drag}


def _bench_ret(bench: dict, dates: list[str], a_idx: int, b_idx: int):
    try:
        a, b = bench[dates[a_idx]], bench[dates[b_idx]]
        return (b / a - 1) * 100
    except (KeyError, IndexError, ZeroDivisionError):
        return None


def report(name: str, recs: list[dict], n_pop: int) -> dict:
    traded = [r for r in recs if r.get("ret") is not None]
    missed = [r for r in recs if r.get("missed")]
    amb = sum(1 for r in recs if r.get("outcome") == "AMBIGUOUS")
    if not traded:
        print(f"  {name:12s} no priced trades (missed {len(missed)}/{n_pop})")
        return {}
    rets = [r["ret"] for r in traded]
    rs = [r["ret"] / STOP_PCT for r in traded]
    exc = [r["excess"] for r in traded if r.get("excess") is not None]
    out = collections.Counter(r["outcome"] for r in recs)
    d = {
        "n_traded": len(traded), "n_missed": len(missed), "ambiguous": amb,
        "expectancy_R": st.mean(rs), "median_R": st.median(rs),
        "win_rate": 100 * sum(1 for x in rets if x > 0) / len(rets),
        "mean_ret": st.mean(rets), "worst": min(rets),
        "sessions": st.mean([r["days"] for r in traded]),
        "excess": st.mean(exc) if exc else None,
        "stop_pct": 100 * out["STOP"] / max(1, sum(out.values())),
        "target_pct": 100 * out["TARGET"] / max(1, sum(out.values())),
    }
    print(f"  {name:12s} n={d['n_traded']:3d} miss={d['n_missed']:3d} amb={amb:2d} "
          f"E={d['expectancy_R']:+.3f}R med={d['median_R']:+.3f}R "
          f"win={d['win_rate']:4.1f}% stop={d['stop_pct']:4.1f}% tgt={d['target_pct']:4.1f}% "
          f"days={d['sessions']:4.1f} worst={d['worst']:+6.1f}% "
          f"excess={format(d['excess'], '+.2f') if d['excess'] is not None else '  n/a'}")
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="Entry-timing counterfactual (read-only)")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--wait", type=int, default=5, help="sessions a limit/reclaim may wait")
    ap.add_argument("--cost-bps", type=float, default=30.0)
    ap.add_argument("--slip-bps", type=float, default=10.0, help="per fill")
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from services.forward_eval import PriceBook, benchmark_series

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
    bench = {}
    try:
        for b in benchmark_series() or []:
            c = _f(b.get("close"))
            if c:
                bench[str(b.get("date"))[:10]] = c
    except Exception:
        pass

    CLOSED = {"STOP_HIT", "TARGET_HIT", "TIME_EXIT", "FORCED_EXIT", "MANUAL_CLOSED"}
    cand = [r for r in rows
            if str(r.get("executed")) == "1" and r.get("engine") == "SMC"
            and str(r.get("status")).upper() in CLOSED
            and _f(r.get("entry_price")) and r.get("entry_fill_at")]

    VARIANTS = ["BASELINE", "DELAY_1", "DELAY_2", "DELAY_3", "PULLBACK_2", "PULLBACK_3"]
    per: dict[str, list] = {v: [] for v in VARIANTS}
    reclaim: list[dict] = []
    stopped_ids: set[str] = set()
    no_series = 0
    meta: dict[str, dict] = {}

    for r in cand:
        sym, day = str(r.get("symbol") or ""), str(r.get("entry_fill_at"))[:10]
        bars = book._bars_from(sym, day)
        if not bars or len(bars) < 3:
            no_series += 1
            continue
        entry0 = _f(r.get("entry_price"))
        uid = str(r.get("uuid"))
        bdates = [str(b.get("date"))[:10] for b in bars]
        meta[uid] = {"date": day, "sym": sym}
        for v in VARIANTS:
            e, off, miss = _entry_for(v, bars, entry0, args.wait)
            if e is None:
                per[v].append({"uid": uid, "missed": miss, "outcome": "MISSED"})
                continue
            budget = HORIZON - off
            sim = _simulate(bars[off + 1:], e, STOP_PCT, TARGET_PCT, budget,
                            args.cost_bps, args.slip_bps)
            if sim["ret"] is not None and bench:
                bx = _bench_ret(bench, bdates, min(off, len(bdates) - 1),
                                min(off + sim["days"], len(bdates) - 1))
                sim["excess"] = (sim["ret"] - bx) if bx is not None else None
            sim["uid"] = uid
            per[v].append(sim)
            if v == "BASELINE" and sim["outcome"] == "STOP":
                stopped_ids.add(uid)
                e2, off2, miss2 = _reclaim_entry(bars, sim["days"], args.wait)
                if e2 is None:
                    reclaim.append({"uid": uid, "missed": miss2, "outcome": "MISSED"})
                else:
                    s2 = _simulate(bars[off2 + 1:], e2, STOP_PCT, TARGET_PCT,
                                   HORIZON - off2, args.cost_bps, args.slip_bps)
                    s2["uid"] = uid
                    reclaim.append(s2)

    n = len(cand) - no_series
    print(f"population: {n} closed executed SMC trades "
          f"({no_series} unpriceable, survivorship caveat)")
    print(f"costs {args.cost_bps}bps round-trip + {args.slip_bps}bps slippage per fill; "
          f"stop {STOP_PCT}% target {TARGET_PCT}%; horizon {HORIZON} sessions FROM SIGNAL "
          f"(delayed entries get fewer remaining bars, never extra)")
    print()
    print("=== ALL CLOSED TRADES — the valid population ===")
    for v in VARIANTS:
        report(v, per[v], n)
    print()
    print(f"=== RECLAIM re-entry, on the {len(stopped_ids)} baseline-stopped trades ===")
    report("RECLAIM", reclaim, len(stopped_ids))
    print()
    print("=== DIAGNOSTIC ONLY — stopped subset (conditions on outcome, biased UP) ===")
    for v in VARIANTS:
        report(v, [x for x in per[v] if x.get("uid") in stopped_ids], len(stopped_ids))
    print()
    dates = sorted({meta[u]["date"] for u in meta})
    if dates:
        split = dates[len(dates) // 2]
        print(f"=== EXPLORATORY (signal < {split}) vs HOLDOUT (>= {split}) ===")
        for label, pred in (("explore", lambda d: d < split), ("holdout", lambda d: d >= split)):
            print(f"  -- {label} --")
            for v in VARIANTS:
                sub = [x for x in per[v]
                       if x.get("uid") in meta and pred(meta[x["uid"]]["date"])]
                report(v, sub, len(sub))
    print()
    if bench:
        print("=== BY REGIME (NIFTY vs its trailing 50-bar mean on the signal day) ===")
        bd = sorted(bench)
        closes = [bench[d] for d in bd]
        reg = {d: ("RISK_ON" if closes[i] >= sum(closes[max(0, i - 49):i + 1]) /
                   len(closes[max(0, i - 49):i + 1]) else "RISK_OFF")
               for i, d in enumerate(bd) if i >= 49}
        for want in ("RISK_ON", "RISK_OFF"):
            sub_ids = {u for u, m in meta.items() if reg.get(m["date"]) == want}
            print(f"  -- {want} (n_signals={len(sub_ids)}) --")
            for v in VARIANTS:
                report(v, [x for x in per[v] if x.get("uid") in sub_ids], len(sub_ids))
    print()
    print("Setup split unavailable: `setup` is NULL on every SMC row (it is populated")
    print("only for MOMENTUM), so no setup-level breakdown can be honestly produced.")
    print("No threshold, flag, stop, target or trading rule is changed by this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
