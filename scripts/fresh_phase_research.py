"""
scripts/fresh_phase_research.py  —  Phase B, item 5 (THE research target)
=========================================================================
The structural discovery: Setup-A is a LATE continuation re-entry engine
(0 fresh trades, ~all 3rd+ tap, ~all LATE lifecycle). So the real question
is no longer "fix Setup-A". It is:

  Does a real edge exist ONE STRUCTURAL PHASE EARLIER — at the fresh
  liquidity-grab -> CHoCH -> FIRST reclaim — i.e. in the trades Setup-A
  structurally never takes?

This is an INDEPENDENT structural scanner on RAW price data. It does NOT
use Setup-A, the scorer, or any optimization. It detects the early phase
with fixed, transparent, measurable rules, forward-simulates the outcome,
and then quantifies HOW MUCH LATER Setup-A enters on the same moves.

PURE OBSERVATIONAL RESEARCH. No scoring, no filters tuned, no production
wiring. Small samples are DIRECTIONAL only — confirm on Phase C frozen
windows before believing magnitudes.

Run locally:
    python scripts/fresh_phase_research.py --yf
    python scripts/fresh_phase_research.py --db path\\to\\store.db
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import smc_detectors as smc
from backtest.engine import BacktestEngine, BacktestConfig
import setup_a_frequency_probe as probe

# Fixed, transparent thresholds (NOT fitted — definitional, per directive).
MAX_EXT_AT_ENTRY_ATR = 1.5   # fresh = entry before price extends > this
MAX_BARS_GRAB_TO_ENTRY = 30  # the reclaim must come reasonably soon
FORWARD_BARS = 80            # outcome resolution horizon
SWING_L = 3
SWING_R = 3


def _atr_at(candles, i, period=14):
    seg = candles[max(0, i - period - 1):i + 1]
    if len(seg) < period + 1:
        return 0.0
    trs = []
    for k in range(1, len(seg)):
        h, l, pc = seg[k]["high"], seg[k]["low"], seg[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def detect_fresh_longs(c: list) -> list:
    """First-touch fresh-reclaim longs on raw 5m data.

    Phase definition (measurable, no scoring):
      1. GRAB   — a prior swing low is swept (bar low < swing low) AND that
                  bar closes back ABOVE it (sweep + reclaim of sell-side
                  liquidity).
      2. CHoCH  — first close back ABOVE the most recent swing high after
                  the grab (the early structure shift — NOT a full
                  displacement BOS).
      3. ENTRY  — FIRST return into the fresh demand zone (last down candle
                  before the CHoCH leg = origin OB), taken BEFORE the move
                  extends ( <= MAX_EXT_AT_ENTRY_ATR ) and before a
                  displacement candle prints. SL below the grab low,
                  target = 2R. Forward-simulated outcome (first touch).
    """
    out = []
    n = len(c)
    if n < 120:
        return out
    used_until = -1
    for i in range(40, n - FORWARD_BARS - 1):
        if i <= used_until:
            continue
        sh, sl = smc.detect_swing_points(c[:i + 1], SWING_L, SWING_R)
        if len(sl) < 2 or len(sh) < 1:
            continue
        atr = _atr_at(c, i)
        if atr <= 0:
            continue
        swing_low_idx, swing_low = sl[-1]
        if swing_low_idx >= i:
            continue
        # 1. GRAB: this bar sweeps + reclaims the last swing low.
        if not (c[i]["low"] < swing_low and c[i]["close"] > swing_low):
            continue
        grab_idx, grab_low = i, c[i]["low"]
        # most recent swing high before the grab (the level CHoCH breaks)
        prior_high = None
        for hi_idx, hi_px in reversed(sh):
            if hi_idx < grab_idx:
                prior_high = (hi_idx, hi_px)
                break
        if not prior_high:
            continue
        # 2. CHoCH: first close above prior_high within a sane window.
        choch = None
        for j in range(grab_idx + 1, min(grab_idx + MAX_BARS_GRAB_TO_ENTRY, n)):
            if c[j]["close"] > prior_high[1]:
                choch = j
                break
        if choch is None:
            continue
        # origin demand OB = last down candle in [grab, choch]
        ob_idx = None
        for k in range(choch, grab_idx - 1, -1):
            if c[k]["close"] < c[k]["open"]:
                ob_idx = k
                break
        if ob_idx is None:
            ob_idx = grab_idx
        ob_lo = min(c[ob_idx]["low"], grab_low)
        ob_hi = max(c[ob_idx]["open"], c[ob_idx]["close"])
        if ob_hi <= ob_lo:
            continue
        # 3. FIRST retest into the fresh zone, before extension/displacement
        entry_k = None
        for k in range(choch + 1, min(choch + MAX_BARS_GRAB_TO_ENTRY, n)):
            run_high = max(x["high"] for x in c[choch:k + 1])
            ext = (run_high - grab_low) / atr
            max_body = max(abs(x["close"] - x["open"]) for x in c[choch:k + 1])
            displaced = max_body >= 1.8 * atr
            if c[k]["low"] <= ob_hi:               # tagged the zone
                if ext <= MAX_EXT_AT_ENTRY_ATR and not displaced:
                    entry_k = k
                break  # only the FIRST retest counts (fresh, not re-entry)
        if entry_k is None:
            continue

        entry = ob_hi
        sl = grab_low - 0.10 * atr
        risk = entry - sl
        if risk <= 0:
            continue
        target = entry + 2.0 * risk
        # forward outcome (first touch of SL or target)
        r = None
        for f in range(entry_k + 1, min(entry_k + 1 + FORWARD_BARS, n)):
            if c[f]["low"] <= sl:
                r = -1.0
                break
            if c[f]["high"] >= target:
                r = 2.0
                break
        if r is None:
            continue  # unresolved within horizon — excluded (no fabrication)
        ext_at_entry = (max(x["high"] for x in c[choch:entry_k + 1]) - grab_low) / atr
        out.append({
            "grab_idx": grab_idx, "choch_idx": choch, "entry_idx": entry_k,
            "entry_time": c[entry_k]["date"], "entry": round(entry, 2),
            "sl": round(sl, 2), "target": round(target, 2), "r": r,
            "win": 1 if r > 0 else 0,
            "bars_grab_to_entry": entry_k - grab_idx,
            "ext_at_entry_atr": round(ext_at_entry, 2),
        })
        used_until = entry_k  # don't double-count the same structural move
    return out


def funnel_diag(c: list) -> dict:
    """DIAGNOSIS ONLY — no outcome logic, no thresholds changed. Counts how
    many candidates survive each detection stage so we can see exactly
    WHERE the funnel collapses to zero. Also counts a MULTI-BAR grab
    (break then close-back within 4 bars) alongside the strict SINGLE-bar
    grab, to test the prime suspect for the 0 result."""
    f = {"bars": 0, "swinglow_ok": 0, "grab_single": 0, "grab_multi": 0,
         "prior_high_ok": 0, "choch_ok": 0, "ob_ok": 0,
         "first_retest_ok": 0, "fresh_gate_ok": 0, "resolved_ok": 0}
    n = len(c)
    if n < 120:
        return f
    for i in range(40, n - FORWARD_BARS - 1):
        f["bars"] += 1
        sh, sl = smc.detect_swing_points(c[:i + 1], SWING_L, SWING_R)
        if len(sl) < 2 or len(sh) < 1:
            continue
        atr = _atr_at(c, i)
        if atr <= 0:
            continue
        swing_low_idx, swing_low = sl[-1]
        if swing_low_idx >= i:
            continue
        f["swinglow_ok"] += 1
        single = c[i]["low"] < swing_low and c[i]["close"] > swing_low
        # multi-bar sweep: this bar breaks below, a later bar (<=4) closes back
        multi = False
        if c[i]["low"] < swing_low:
            for k in range(i, min(i + 5, n)):
                if c[k]["close"] > swing_low:
                    multi = True
                    break
        if single:
            f["grab_single"] += 1
        if not multi:
            continue
        f["grab_multi"] += 1
        grab_idx, grab_low = i, c[i]["low"]
        prior_high = next(((hi, hp) for hi, hp in reversed(sh)
                           if hi < grab_idx), None)
        if not prior_high:
            continue
        f["prior_high_ok"] += 1
        choch = None
        for j in range(grab_idx + 1, min(grab_idx + MAX_BARS_GRAB_TO_ENTRY, n)):
            if c[j]["close"] > prior_high[1]:
                choch = j
                break
        if choch is None:
            continue
        f["choch_ok"] += 1
        ob_idx = next((k for k in range(choch, grab_idx - 1, -1)
                       if c[k]["close"] < c[k]["open"]), grab_idx)
        ob_lo = min(c[ob_idx]["low"], grab_low)
        ob_hi = max(c[ob_idx]["open"], c[ob_idx]["close"])
        if ob_hi <= ob_lo:
            continue
        f["ob_ok"] += 1
        entry_k = None
        for k in range(choch + 1, min(choch + MAX_BARS_GRAB_TO_ENTRY, n)):
            run_high = max(x["high"] for x in c[choch:k + 1])
            ext = (run_high - grab_low) / atr
            mb = max(abs(x["close"] - x["open"]) for x in c[choch:k + 1])
            if c[k]["low"] <= ob_hi:
                f["first_retest_ok"] += 1
                if ext <= MAX_EXT_AT_ENTRY_ATR and mb < 1.8 * atr:
                    f["fresh_gate_ok"] += 1
                    entry = ob_hi
                    sl_ = grab_low - 0.10 * atr
                    if entry - sl_ > 0:
                        tgt = entry + 2.0 * (entry - sl_)
                        for ff in range(k + 1, min(k + 1 + FORWARD_BARS, n)):
                            if c[ff]["low"] <= sl_ or c[ff]["high"] >= tgt:
                                f["resolved_ok"] += 1
                                break
                break
    return f


def _agg(rows):
    if not rows:
        return {"n": 0, "win": 0.0, "expR": 0.0, "PF": 0.0, "totR": 0.0}
    rs = [x["r"] for x in rows]
    w = [x for x in rs if x > 0]
    l = [x for x in rs if x <= 0]
    pf = 999.0 if not l else round(sum(w) / abs(sum(l)), 2)
    return {"n": len(rs), "win": round(100 * len(w) / len(rs), 1),
            "expR": round(sum(rs) / len(rs), 3), "PF": pf,
            "totR": round(sum(rs), 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Fresh-phase (one phase earlier) research")
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--symbols", type=str, default=None)
    ap.add_argument("--yf", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--funnel", action="store_true",
                    help="diagnosis: print where detection collapses (no tuning)")
    ap.add_argument("--synthetic-days", type=int, default=90)
    ap.add_argument("--output", type=str,
                    default="backtest_results/fresh_phase.json")
    args = ap.parse_args()

    if args.synthetic:
        print("=" * 72)
        print("  SYNTHETIC — WIRING TEST ONLY. NOT decision-grade.")
        print("=" * 72)
        from backtest.runner import generate_synthetic_candles
        data = {"SYNTHETIC:INDEX": {"5m": generate_synthetic_candles(days=args.synthetic_days)}}
    elif args.yf:
        print("=" * 72)
        print("  REAL yfinance ^NSEI (~60d 5m). DIRECTIONAL, small sample.")
        print("=" * 72)
        data = probe._load_yfinance()
    else:
        from backtest.data_store import DataStore
        from backtest.runner import load_data_from_store
        store = DataStore(args.db) if args.db else DataStore()
        data = load_data_from_store(store, args.symbols.split(",") if args.symbols else None)
        store.close()
        if not data:
            print("ERROR: no market data. Use --db / --yf / --synthetic.")
            sys.exit(1)

    if args.funnel:
        print("\nDETECTION FUNNEL  (diagnosis only — counts, no tuning)")
        print("Shows exactly where candidates die. grab_single vs grab_multi")
        print("tests whether the 1-bar grab rule is the killer.\n")
        agg = {}
        for sym, tf in data.items():
            fn = funnel_diag(tf.get("5m") or [])
            for k, v in fn.items():
                agg[k] = agg.get(k, 0) + v
        order = ["bars", "swinglow_ok", "grab_single", "grab_multi",
                 "prior_high_ok", "choch_ok", "ob_ok", "first_retest_ok",
                 "fresh_gate_ok", "resolved_ok"]
        base = max(agg.get("swinglow_ok", 0), 1)
        for k in order:
            v = agg.get(k, 0)
            pct = "" if k == "bars" else f"  ({100*v/base:.1f}% of valid scans)"
            print(f"  {k:<16} {v:>7}{pct}")
        print("\n  READ: the first stage that crashes to ~0 is the bug to")
        print("  fix (detection-correctness only — NOT outcome tuning). If")
        print("  grab_single<<grab_multi, the 1-bar grab rule is confirmed")
        print("  as the cause and the fix is a multi-bar sweep window.")
        return

    # 1. Independent fresh-phase entries on raw data
    all_fresh = []
    for sym, tf in data.items():
        c = tf.get("5m") or []
        fr = detect_fresh_longs(c)
        for x in fr:
            x["symbol"] = sym
        all_fresh.extend(fr)

    # 2. Setup-A entries on the SAME data (for the lateness comparison)
    sa = [t for t in BacktestEngine(BacktestConfig()).run_multi(data)
          if probe._is_setup_a(t.setup) and t.direction == "LONG"]

    fa = _agg(all_fresh)
    saa = _agg([{"r": t.r_multiple} for t in sa])

    print(f"\nFRESH-PHASE long entries (raw data, first-touch reclaim): {fa['n']}")
    print(f"  win={fa['win']}%  expR={fa['expR']:+.3f}  PF={fa['PF']}  totR={fa['totR']:+.1f}")
    if all_fresh:
        be = sum(x["bars_grab_to_entry"] for x in all_fresh) / len(all_fresh)
        ee = sum(x["ext_at_entry_atr"] for x in all_fresh) / len(all_fresh)
        print(f"  avg bars grab->entry={be:.1f}  avg extension at entry={ee:.2f} ATR")
    print(f"\nSetup-A LONG entries (same data, the LATE engine):     {saa['n']}")
    print(f"  win={saa['win']}%  expR={saa['expR']:+.3f}  PF={saa['PF']}  totR={saa['totR']:+.1f}")

    # 3. Lateness: for each fresh entry, did Setup-A fire LATER on the same
    #    move? Quantify the structural delay.
    print("\n" + "=" * 72)
    print("  LATENESS — does Setup-A enter the SAME moves, but later?")
    print("=" * 72)
    sa_by_sym = {}
    for t in sa:
        sa_by_sym.setdefault(t.symbol, []).append(t)
    paired = []
    for x in all_fresh:
        sym = x["symbol"]
        c = data[sym]["5m"]
        fresh_dt = probe._parse_dt(x["entry_time"])
        cand = []
        for t in sa_by_sym.get(sym, []):
            tdt = probe._parse_dt(t.entry_time)
            if tdt and fresh_dt and 0 < (tdt - fresh_dt).total_seconds() <= 6 * 3600:
                cand.append((t, (tdt - fresh_dt).total_seconds() / 300.0))
        if cand:
            t, lag_bars = min(cand, key=lambda z: z[1])
            paired.append({"fresh_r": x["r"], "late_r": t.r_multiple,
                           "lag_bars": round(lag_bars, 1),
                           "fresh_ext": x["ext_at_entry_atr"]})
    if paired:
        avg_lag = sum(p["lag_bars"] for p in paired) / len(paired)
        fr_w = 100 * sum(1 for p in paired if p["fresh_r"] > 0) / len(paired)
        lt_w = 100 * sum(1 for p in paired if p["late_r"] > 0) / len(paired)
        print(f"  Shared moves (fresh entry THEN a later Setup-A entry): {len(paired)}")
        print(f"  Avg structural delay: Setup-A enters ~{avg_lag:.1f} 5m bars later")
        print(f"  On those SAME moves:  fresh win={fr_w:.0f}%  vs  "
              f"late Setup-A win={lt_w:.0f}%")
        print(f"  fresh expR={sum(p['fresh_r'] for p in paired)/len(paired):+.3f}  "
              f"vs late expR={sum(p['late_r'] for p in paired)/len(paired):+.3f}")
    else:
        print("  No directly pairable shared moves in this window (small "
              "sample / index-only). Phase C multi-symbol will populate this.")

    print("\n" + "=" * 72)
    print("  READ: if FRESH expR/PF clearly beats Setup-A AND Setup-A is")
    print("  shown entering the same moves N bars later with worse stats,")
    print("  that is direct evidence the edge lives one phase EARLIER and")
    print("  the current engine is structurally too late. Small sample =>")
    print("  DIRECTIONAL; confirm on Phase C frozen-window multi-symbol.")
    print("  Nothing here is wired to production. Pure research.")

    op = Path(args.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "synthetic": args.synthetic,
        "fresh_phase": fa, "setup_a_long": saa,
        "paired_shared_moves": paired,
        "fresh_rows": all_fresh,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {op}")


if __name__ == "__main__":
    main()
