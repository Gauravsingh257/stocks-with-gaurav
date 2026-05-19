"""
scripts/fvg_tap_backtest.py
===========================
Pre-committed, no-tuning backtest of the EXACT model the user specified:

  5m-style CHoCH gate  ->  bullish/bearish FVG from the displacement  ->
  FIRST tap of that FVG  ->  ENGULFING confirmation candle  ->
  MARKET entry at the NEXT bar's open  ->  SL just beyond the FVG  ->
  fixed R target.

This is the legitimate test of "can the gap be fixed": fix it in a
backtest on BLIND data first, judged against the frozen gate, before any
production decision. NOT Setup-A, NOT the scorer (frozen), NOT wired to
anything. No parameter is tuned to improve results.

FIXED, PRE-DECLARED PARAMETERS (chosen before seeing any result; never
swept — sweeping them for profit would be the curve-fitting trap):
  swing fractal      = 3/3
  R multiple         = 2.0   (codebase + frozen-gate standard)
  SL buffer          = 0.10 ATR beyond the FVG edge
  first tap only     (a 2nd/3rd tap is NOT this setup)
  confirm window     = 6 bars after the tap
  outcome horizon    = 80 bars (unresolved => excluded, never fabricated)
  entry              = next bar OPEN after the confirmation close
                       (realistic live fill; slippage/cost NOT modelled)

PRE-COMMITTED GATE (stated before numbers, judged on the best-powered,
non-thin config — STOCK 1h):
  profit factor >= 1.30  AND  expectancy_R clearly > 0  AND
  win-rate >= 40% (>=33% is mere 2R breakeven)  AND
  positive on a non-thin n (>= ~60 trades).
Anything weaker = NOT supported; it joins the honest negatives. One
clean chart (18 May) is n=1 and decides nothing.

Run locally:
    python scripts/fvg_tap_backtest.py --matrix
    python scripts/fvg_tap_backtest.py --yf --tf 1h --symbols RELIANCE.NS --diag
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
import setup_a_frequency_probe as probe
import fresh_phase_research as fp  # reuse loaders/_agg (DRY, already proven)

R_MULT = 2.0
SWING = 3
SL_BUF_ATR = 0.10
CONFIRM_WIN = 6
HORIZON = 80


def _bull_engulf(prev, cur):
    return (cur["close"] > cur["open"] and prev["close"] < prev["open"]
            and cur["close"] >= prev["open"] and cur["open"] <= prev["close"])


def _bear_engulf(prev, cur):
    return (cur["close"] < cur["open"] and prev["close"] > prev["open"]
            and cur["close"] <= prev["open"] and cur["open"] >= prev["close"])


def detect_fvg_tap(c: list, direction: str) -> tuple[list, dict]:
    """Exact spec, point-in-time, both directions symmetric. Returns
    (trades, funnel_counts)."""
    out: list = []
    f = {"scans": 0, "choch": 0, "fvg": 0, "tap": 0, "confirm": 0,
         "entry": 0, "resolved": 0}
    n = len(c)
    if n < 120:
        return out, f
    long = direction == "LONG"
    used_until = -1
    for i in range(40, n - HORIZON - 2):
        if i <= used_until:
            continue
        f["scans"] += 1
        sh, sl = smc.detect_swing_points(c[:i + 1], SWING, SWING)
        if len(sh) < 2 or len(sl) < 2:
            continue
        atr = fp._atr_at(c, i)
        if atr <= 0:
            continue

        # --- CHoCH gate (5m structure) --------------------------------
        if long:
            piv_idx, piv = sl[-1]                       # last swing low
            lh = next(((hx, hp) for hx, hp in reversed(sh)
                       if hx < piv_idx), None)          # lower-high before it
            if not lh:
                continue
            choch = next((j for j in range(piv_idx + 1, i + 1)
                          if c[j]["close"] > lh[1]), None)
        else:
            piv_idx, piv = sh[-1]
            hl = next(((lx, lp) for lx, lp in reversed(sl)
                       if lx < piv_idx), None)
            if not hl:
                continue
            choch = next((j for j in range(piv_idx + 1, i + 1)
                          if c[j]["close"] < hl[1]), None)
        if choch is None or choch < i - CONFIRM_WIN - 12:
            continue
        f["choch"] += 1

        # --- FVG from the displacement that made the CHoCH ------------
        fvg = None
        for a in range(max(piv_idx, choch - 8), choch + 1):
            if a + 2 > i:
                break
            if long and c[a]["high"] < c[a + 2]["low"]:
                fvg = (c[a]["high"], c[a + 2]["low"], a + 2)
            elif not long and c[a]["low"] > c[a + 2]["high"]:
                fvg = (c[a + 2]["high"], c[a]["low"], a + 2)
        if fvg is None:
            continue
        fvg_lo, fvg_hi, fvg_done = fvg
        if fvg_hi <= fvg_lo:
            continue
        f["fvg"] += 1

        # --- FIRST tap into the FVG ----------------------------------
        tap = None
        for k in range(fvg_done + 1, min(fvg_done + 25, i + 1)):
            if long and c[k]["low"] <= fvg_hi:
                tap = k
                break
            if not long and c[k]["high"] >= fvg_lo:
                tap = k
                break
        if tap is None:
            continue
        f["tap"] += 1

        # --- ENGULFING confirmation at/after the tap -----------------
        conf = None
        for k in range(tap, min(tap + CONFIRM_WIN, i + 1)):
            if k < 1:
                continue
            if long and _bull_engulf(c[k - 1], c[k]):
                conf = k
                break
            if not long and _bear_engulf(c[k - 1], c[k]):
                conf = k
                break
        if conf is None or conf + 1 > i:
            continue
        f["confirm"] += 1

        # --- entry = NEXT bar open after confirmation ----------------
        e_idx = conf + 1
        entry = c[e_idx]["open"]
        if long:
            sl_px = fvg_lo - SL_BUF_ATR * atr
            risk = entry - sl_px
            if risk <= 0:
                continue
            tgt = entry + R_MULT * risk
        else:
            sl_px = fvg_hi + SL_BUF_ATR * atr
            risk = sl_px - entry
            if risk <= 0:
                continue
            tgt = entry - R_MULT * risk
        f["entry"] += 1

        # --- forward outcome, first touch ----------------------------
        r = None
        for ff in range(e_idx + 1, min(e_idx + 1 + HORIZON, n)):
            hi, lo = c[ff]["high"], c[ff]["low"]
            if long:
                if lo <= sl_px:
                    r = -1.0
                    break
                if hi >= tgt:
                    r = R_MULT
                    break
            else:
                if hi >= sl_px:
                    r = -1.0
                    break
                if lo <= tgt:
                    r = R_MULT
                    break
        if r is None:
            continue
        f["resolved"] += 1
        out.append({"dir": direction, "entry_idx": e_idx,
                    "entry_time": c[e_idx]["date"], "entry": round(entry, 2),
                    "sl": round(sl_px, 2), "target": round(tgt, 2),
                    "r": r, "win": 1 if r > 0 else 0})
        used_until = e_idx
    return out, f


def _both(c: list):
    rows, f1 = detect_fvg_tap(c, "LONG")
    s, f2 = detect_fvg_tap(c, "SHORT")
    rows = rows + s
    fun = {k: f1.get(k, 0) + f2.get(k, 0) for k in f1}
    return rows, fun


def _gate(s: dict) -> bool:
    return (s["n"] >= 60 and s["PF"] >= 1.30 and s["expR"] > 0.0
            and s["win"] >= 40.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="FVG-Tap model backtest (pre-committed, no tuning)")
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--symbols", type=str, default=None)
    ap.add_argument("--tf", choices=["5m", "15m", "1h"], default="1h")
    ap.add_argument("--yf", action="store_true")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--synthetic-days", type=int, default=120)
    ap.add_argument("--diag", action="store_true")
    ap.add_argument("--output", type=str,
                    default="backtest_results/fvg_tap.json")
    args = ap.parse_args()

    print("=" * 76)
    print("  FVG-TAP MODEL — pre-committed backtest, NO tuning, NO wiring")
    print("  GATE (declared before results, judged on STOCK 1h, n>=60):")
    print("    PF>=1.30 AND expR>0 AND win>=40% AND non-thin n")
    print("  R fixed=2.0 (never swept). One clean chart = n=1 = decides nothing.")
    print("=" * 76)

    def _load(tf, syms):
        if args.synthetic:
            from backtest.runner import generate_synthetic_candles
            return {"SYNTH": generate_synthetic_candles(days=args.synthetic_days)}
        if args.yf or args.matrix:
            return fp._load_yf_multi(syms, tf)
        from backtest.data_store import DataStore
        from backtest.runner import load_data_from_store
        store = DataStore(args.db) if args.db else DataStore()
        raw = load_data_from_store(store, syms.split(",") if syms else None)
        store.close()
        return {s: v.get("5m") or [] for s, v in raw.items()}

    if args.matrix:
        configs = [("NIFTY 5m", "5m", "^NSEI"),
                   ("NIFTY 15m", "15m", "^NSEI"),
                   ("STOCK 15m", "15m", fp.STOCK_BASKET),
                   ("STOCK 1h", "1h", fp.STOCK_BASKET)]
        print(f"\n  {'config':<11}{'n':>5}{'win%':>7}{'expR':>9}{'PF':>7}"
              f"{'totR':>8}  gate")
        print("  " + "-" * 50)
        res = {}
        for label, tf, syms in configs:
            try:
                data = fp._load_yf_multi(syms, tf)
            except Exception as exc:
                print(f"  {label:<11}  unavailable: {exc}")
                continue
            allr = []
            for _sym, c in data.items():
                rr, _ = _both(c)
                allr += rr
            s = fp._agg(allr)
            res[label] = s
            g = "Y" if _gate(s) else "N"
            thin = " thin" if s["n"] < 60 else ""
            print(f"  {label:<11}{s['n']:>5}{s['win']:>7.1f}{s['expR']:>+9.3f}"
                  f"{s['PF']:>7.2f}{s['totR']:>+8.1f}   {g}{thin}")
        print("\n  VERDICT: only a 'Y' on the non-thin STOCK 1h row (or "
              "broadly Y across configs) supports building this. A lone "
              "thin Y, or N on STOCK 1h, = NOT supported (curve-fit risk).")
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(
            {"generated_at": datetime.now().isoformat(), "matrix": res},
            indent=2), encoding="utf-8")
        print(f"\nWrote {args.output}")
        return

    data = _load(args.tf, args.symbols or "^NSEI")
    allr, fun = [], {}
    for _sym, c in data.items():
        rr, fc = _both(c)
        allr += rr
        for k, v in fc.items():
            fun[k] = fun.get(k, 0) + v
    s = fp._agg(allr)
    if args.diag:
        print("\n  DETECTION FUNNEL (LONG+SHORT, counts only):")
        for k in ["scans", "choch", "fvg", "tap", "confirm", "entry", "resolved"]:
            print(f"    {k:<10} {fun.get(k, 0):>7}")
    print(f"\n  FVG-TAP trades: n={s['n']}  win={s['win']}%  "
          f"expR={s['expR']:+.3f}  PF={s['PF']}  totR={s['totR']:+.1f}")
    print(f"  Gate ({args.tf}): {'PASS' if _gate(s) else 'FAIL'} "
          f"(needs PF>=1.30, expR>0, win>=40%, n>=60)")
    print("  DIRECTIONAL unless n large; STOCK 1h ~730d is the real test.")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(
        {"generated_at": datetime.now().isoformat(), "tf": args.tf,
         "summary": s, "funnel": fun, "rows": allr}, indent=2),
        encoding="utf-8")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
