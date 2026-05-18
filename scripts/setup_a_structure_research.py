"""
scripts/setup_a_structure_research.py
=====================================
PHASE A of the structure-first edge investigation.

PURE RESEARCH. No scoring, no weights, no curve-fitting, no production
wiring. engine/setup_a_quality.py is FROZEN and is NOT imported here.

Question being investigated (NOT "does SMC have edge?" — that is not what
the scorer probe tested):
  Does planned execution on FRESH structure have real edge BEFORE
  continuation becomes obvious — i.e. are the winners the quiet/early
  ones and the losers the late continuation chases?

It reuses the proven data + backtest path from the frequency probe, then
for every Setup-A trade computes a STRUCTURE-FIRST feature set (only the
user's list), and reports:
  1. Winners vs losers: per-feature distribution + separation.
  2. A composite "setup maturity" index → EARLY vs LATE outcome split
     (the core hypothesis test).

EXCLUDED on purpose (per direction): liquidity-sweep bonus,
displacement-excitement bias, late-continuation bias, and constant /
non-discriminating features.

HONEST SCOPE: point-in-time reconstruction with the project's own
detectors; small samples are DIRECTIONAL, not conclusive. Decide nothing
from a 40-trade run — that is what the frozen-window study (Phase C) is
for.

Run locally (reliable path):
    python scripts/setup_a_structure_research.py --yf
    python scripts/setup_a_structure_research.py --db path/to/store.db
    python scripts/setup_a_structure_research.py --synthetic   # wiring only
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import smc_detectors as smc
from backtest.engine import BacktestEngine, BacktestConfig
import setup_a_frequency_probe as probe  # reuse proven loaders (no scorer)


# --------------------------------------------------------------------------
# STRUCTURE-FIRST FEATURES  (point-in-time; no look-ahead; no sweep/displace)
# --------------------------------------------------------------------------
def _structural_features(trade, data: dict) -> dict | None:
    tf = data.get(trade.symbol)
    if not tf:
        return None
    ltf = probe._point_in_time(tf.get("5m") or [], trade.entry_time)
    htf = probe._point_in_time(tf.get("1h") or [], trade.entry_time)
    if len(ltf) < 40:
        return None

    direction = trade.direction
    atr = smc.calculate_atr(ltf) or 0.0
    if atr <= 0:
        return None
    entry = float(trade.entry_price)
    closes = [c["close"] for c in ltf]
    highs = [c["high"] for c in ltf]
    lows = [c["low"] for c in ltf]

    sh, sl = smc.detect_swing_points(ltf, left=3, right=3)

    # --- expansion origin: last swing in trade direction's favour before
    # entry (LONG -> last swing low; SHORT -> last swing high) ----------
    if direction == "LONG":
        origin_idx, origin_px = (sl[-1] if sl else (max(0, len(ltf) - 20), min(lows[-20:])))
    else:
        origin_idx, origin_px = (sh[-1] if sh else (max(0, len(ltf) - 20), max(highs[-20:])))
    bars_from_origin = len(ltf) - 1 - origin_idx
    dist_from_origin_atr = abs(entry - origin_px) / atr

    # --- bars since BOS/CHoCH: first close beyond the prior opposite
    # swing after the origin (structure break that started this leg) ----
    bos_idx = None
    if direction == "LONG":
        prior_high = max((p for i, p in sh if i < origin_idx), default=None)
        if prior_high is not None:
            for j in range(origin_idx + 1, len(ltf)):
                if closes[j] > prior_high:
                    bos_idx = j
                    break
    else:
        prior_low = min((p for i, p in sl if i < origin_idx), default=None)
        if prior_low is not None:
            for j in range(origin_idx + 1, len(ltf)):
                if closes[j] < prior_low:
                    bos_idx = j
                    break
    bars_since_bos = (len(ltf) - 1 - bos_idx) if bos_idx is not None else -1

    # --- imbalance freshness: bars since the (dir) FVG, + first-tap ----
    fvg = smc.detect_fvg(ltf, direction)
    imbalance_fresh_bars = -1
    fvg_first_tap = 0
    if fvg:
        f_lo, f_hi = fvg
        formed = None
        for j in range(len(ltf) - 3, 2, -1):
            a, c = ltf[j - 1], ltf[j + 1]
            gap = (a["high"] < c["low"]) if direction == "LONG" else (a["low"] > c["high"])
            if gap:
                formed = j
                break
        if formed is not None:
            imbalance_fresh_bars = len(ltf) - 1 - formed
            taps = sum(1 for k in range(formed + 1, len(ltf))
                       if ltf[k]["low"] <= f_hi and ltf[k]["high"] >= f_lo)
            fvg_first_tap = 1 if taps <= 1 else 0

    # --- fresh origin-OB reclaim: OB exists & mitigated <=1x ----------
    ob = smc.detect_order_block(ltf, direction)
    ob_fresh = 0
    if ob:
        o_lo, o_hi = ob
        mit = sum(1 for c in ltf[-15:] if c["low"] <= o_hi and c["high"] >= o_hi)
        ob_fresh = 1 if mit <= 2 else 0

    # --- compression before expansion: range of the 10 bars before the
    # leg vs ATR (tight coil -> small ratio) ---------------------------
    pre = ltf[max(0, origin_idx - 10):origin_idx + 1]
    compression = ((max(c["high"] for c in pre) - min(c["low"] for c in pre)) / atr
                   if pre else 99.0)

    # --- HTF alignment QUALITY (not boolean): aligned * trend strength
    htf_bias = smc.detect_htf_bias(htf) if htf else None
    htf_quality = 0.0
    if htf_bias == direction and len(htf) >= 20:
        hatr = smc.calculate_atr(htf) or atr
        hmean = sum(x["close"] for x in htf[-20:]) / 20
        htf_quality = max(0.0, min(1.0, abs(htf[-1]["close"] - hmean) / (hatr * 2 or 1)))

    # --- entry location efficiency: 0 = deep discount(L)/premium(S)
    # (good), 1 = chasing. Range = recent dealing range. -------------
    rng_hi = max(highs[-30:])
    rng_lo = min(lows[-30:])
    span = max(rng_hi - rng_lo, 1e-9)
    pos = (entry - rng_lo) / span
    entry_eff = pos if direction == "LONG" else (1 - pos)  # lower = better

    # --- move maturity / exhaustion: legs since BOS + extension ------
    legs = 0
    if direction == "LONG":
        legs = sum(1 for i in range(max(1, len(sh) - 6), len(sh))
                   if sh[i][1] > sh[i - 1][1]) if len(sh) > 1 else 0
    else:
        legs = sum(1 for i in range(max(1, len(sl) - 6), len(sl))
                   if sl[i][1] < sl[i - 1][1]) if len(sl) > 1 else 0
    extension_atr = abs(closes[-1] - origin_px) / atr
    maturity_raw = (dist_from_origin_atr * 0.4 + extension_atr * 0.3
                    + max(0, bars_since_bos) * 0.02 + legs * 0.5
                    + entry_eff * 2.0)

    # --- reaction quality after tap: confirmation candle body/wick ---
    last = ltf[-1]
    body = abs(last["close"] - last["open"])
    rng = max(last["high"] - last["low"], 1e-9)
    if direction == "LONG":
        rej_wick = (min(last["open"], last["close"]) - last["low"]) / rng
        directional = 1.0 if last["close"] > last["open"] else 0.0
    else:
        rej_wick = (last["high"] - max(last["open"], last["close"])) / rng
        directional = 1.0 if last["close"] < last["open"] else 0.0
    reaction_quality = round(directional * (0.5 + 0.5 * rej_wick), 3)

    # --- session bucket (IST) ----------------------------------------
    dt = probe._parse_dt(trade.entry_time)
    hm = (dt.hour * 60 + dt.minute) if dt else 0
    if hm < 600:
        sess = "OPEN(9:15-10)"
    elif hm < 720:
        sess = "MORN(10-12)"
    elif hm < 810:
        sess = "MID(12-13:30)"
    elif hm < 900:
        sess = "NOON(13:30-15)"
    else:
        sess = "CLOSE(15+)"

    return {
        "r": float(trade.r_multiple),
        "win": 1 if trade.r_multiple > 0 else 0,
        "dist_from_origin_atr": round(dist_from_origin_atr, 2),
        "bars_from_origin": bars_from_origin,
        "bars_since_bos": bars_since_bos,
        "imbalance_fresh_bars": imbalance_fresh_bars,
        "fvg_first_tap": fvg_first_tap,
        "ob_fresh": ob_fresh,
        "compression": round(compression, 2),
        "htf_quality": round(htf_quality, 2),
        "entry_eff": round(entry_eff, 2),
        "legs_since_bos": legs,
        "extension_atr": round(extension_atr, 2),
        "maturity_raw": round(maturity_raw, 2),
        "reaction_quality": reaction_quality,
        "session": sess,
    }


NUMERIC = ["dist_from_origin_atr", "bars_from_origin", "bars_since_bos",
           "imbalance_fresh_bars", "compression", "htf_quality",
           "entry_eff", "legs_since_bos", "extension_atr",
           "maturity_raw", "reaction_quality"]
BINARY = ["fvg_first_tap", "ob_fresh"]


def _wl(rows):
    return ([r for r in rows if r["win"] == 1], [r for r in rows if r["win"] == 0])


def _mean(xs):
    return round(st.mean(xs), 3) if xs else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description="Setup-A structure-first research (Phase A)")
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--symbols", type=str, default=None)
    ap.add_argument("--yf", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--synthetic-days", type=int, default=90)
    ap.add_argument("--output", type=str,
                    default="backtest_results/setup_a_structure.json")
    args = ap.parse_args()

    if args.synthetic:
        print("=" * 72)
        print("  SYNTHETIC — WIRING TEST ONLY. NOT decision-grade.")
        print("=" * 72)
        from backtest.runner import generate_synthetic_candles
        data = {"SYNTHETIC:INDEX": {"5m": generate_synthetic_candles(days=args.synthetic_days)}}
    elif args.yf:
        print("=" * 72)
        print("  REAL yfinance ^NSEI (trailing ~60d 5m). DIRECTIONAL, small sample.")
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

    trades = BacktestEngine(BacktestConfig()).run_multi(data)
    sa = [t for t in trades if probe._is_setup_a(t.setup)]
    rows = [f for f in (_structural_features(t, data) for t in sa) if f]
    print(f"\nSetup-A trades: {len(sa)}  |  with structure: {len(rows)}")
    if len(rows) < 8:
        print("Too few rows for even directional read.")
        sys.exit(0)

    win, los = _wl(rows)
    print(f"Winners: {len(win)}  Losers: {len(los)}  "
          f"(baseline win {100*len(win)/len(rows):.1f}%, "
          f"expR {_mean([r['r'] for r in rows]):+.3f})\n")

    print("WINNERS vs LOSERS — structural traits (mean):")
    print(f"  {'feature':<24} {'winners':>9} {'losers':>9} {'separation':>11}")
    print("  " + "-" * 55)
    seps = []
    for k in NUMERIC:
        wv, lv = [r[k] for r in win], [r[k] for r in los]
        mw, ml = _mean(wv), _mean(lv)
        allv = [r[k] for r in rows]
        sd = st.pstdev(allv) if len(allv) > 1 else 0.0
        sep = round((mw - ml) / sd, 2) if sd > 0 else 0.0
        seps.append((k, sep, mw, ml))
        print(f"  {k:<24} {mw:>9.3f} {ml:>9.3f} {sep:>11.2f}")
    for k in BINARY:
        pw = round(100 * _mean([r[k] for r in win]), 1)
        pl = round(100 * _mean([r[k] for r in los]), 1)
        print(f"  {k:<24} {pw:>8.1f}% {pl:>8.1f}% {'(present %)':>11}")

    seps.sort(key=lambda x: -abs(x[1]))
    print("\n  Strongest separators (|winner-loser| / stdev):")
    for k, s, mw, ml in seps[:5]:
        tilt = "winners higher" if s > 0 else "losers higher"
        print(f"    {k:<24} sep={s:+.2f}  ({tilt})")

    # ---- SETUP MATURITY: the core hypothesis test --------------------
    mats = sorted(r["maturity_raw"] for r in rows)
    med = mats[len(mats) // 2]
    early = [r for r in rows if r["maturity_raw"] <= med]
    late = [r for r in rows if r["maturity_raw"] > med]
    print("\n" + "=" * 72)
    print("  SETUP MATURITY  (EARLY = quiet/fresh/first-leg; LATE = chase)")
    print("=" * 72)
    for lab, grp in (("EARLY (<=median maturity)", early),
                     ("LATE  (> median maturity)", late)):
        if grp:
            rs = [r["r"] for r in grp]
            wr = 100 * sum(1 for r in grp if r["win"]) / len(grp)
            print(f"  {lab:<28} n={len(grp):>3}  win={wr:>5.1f}%  "
                  f"expR={_mean(rs):+.3f}  totR={sum(rs):+.1f}")
    print("\n  Hypothesis: if EARLY expR >> LATE expR, the edge is in fresh")
    print("  structure BEFORE obvious continuation — exactly the thesis.")
    print("  Small sample => DIRECTIONAL signal to confirm on frozen windows.")

    # session breakdown
    print("\n  By session (expR):")
    for s in ["OPEN(9:15-10)", "MORN(10-12)", "MID(12-13:30)",
              "NOON(13:30-15)", "CLOSE(15+)"]:
        g = [r for r in rows if r["session"] == s]
        if g:
            print(f"    {s:<16} n={len(g):>3}  win={100*sum(r['win'] for r in g)/len(g):>5.1f}%"
                  f"  expR={_mean([r['r'] for r in g]):+.3f}")

    op = Path(args.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "synthetic": args.synthetic,
        "n": len(rows), "winners": len(win), "losers": len(los),
        "separators": [{"feature": k, "sep": s, "win_mean": mw, "los_mean": ml}
                       for k, s, mw, ml in seps],
        "maturity": {
            "early": {"n": len(early),
                      "expR": _mean([r["r"] for r in early]) if early else 0},
            "late": {"n": len(late),
                     "expR": _mean([r["r"] for r in late]) if late else 0},
        },
        "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {op}  (per-trade rows included for Phase B chart replay)")


if __name__ == "__main__":
    main()
