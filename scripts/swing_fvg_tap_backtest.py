"""
scripts/swing_fvg_tap_backtest.py
=================================
PRE-COMMITTED backtest of a swing variant of the validated FVG-Tap rule:

  Weekly trend = BULLISH or STRONG_BULL  (macro alignment, point-in-time)
  -> 1H CHoCH  ->  1H FVG  ->  first 1H tap  ->  1H engulfing  ->
  entry at NEXT 1H bar's open  ->  SL beyond FVG  ->  R=2.0 target

WHY THIS EXISTS
---------------
The G2-6 daily-state-machine (services/equity_state_machine.py) is a
different rule (daily Order Block + daily FVG + confirmation candle)
that has fired zero validation signals so far. The proposal: reformulate
the swing engine to reuse the EXACT proven FVG-Tap rule
(engine/fvg_tap_setup.py — backtest-cleared on NIFTY 5m PF 2.07,
BANKNIFTY 5m PF 2.33) but applied on 1H bars for NSE stocks, with the
weekly bull gate as a macro filter.

This script tests that proposal on real OHLC data. Pre-committed gate
locked BEFORE seeing any results; parameters identical to the production
detector (NO tuning, NO curve-fit). If the gate clears, the recommended
next step is SHADOW deployment behind a new flag, not direct production
switch. If it fails, this is honestly recorded as a negative result and
nothing ships.

LOCKED PARAMETERS (chosen before any run; identical to the production
detector; no parameter is swept):
  swing fractal     = 3/3
  R multiple        = 2.0
  SL buffer         = 0.10 ATR beyond the FVG edge
  CHoCH recency     = CONFIRM_WIN + 12 = 18 bars
  tap window        = 25 bars after FVG completion
  confirm window    = 6 bars after the tap
  weekly gate       = BULLISH or STRONG_BULL
                      (engine.swing.detect_weekly_trend, evaluated
                      POINT-IN-TIME from a forward-resampled weekly
                      series — no look-ahead)
  direction         = LONG only (incoherent to short under a bull gate)
  outcome horizon   = 60 bars (~10 trading days on 1H)
  entry             = next bar OPEN after the engulfing confirmation
                      (realistic live fill; slippage/cost NOT modelled)

PRE-COMMITTED GATE (declared before results were seen):
  n >= 30 trades   (non-thin sample)
  PF >= 1.30      (matches production-detector validation gate)
  expR >= 0.20    (must clearly beat zero under LONG-only weekly-bull
                   filter; floor higher than original bidirectional rule)
  win rate >= 40% (>=33% is bare 2R breakeven)

If the gate passes on this 12-stock first-pass universe, the NEXT STEP
is expansion to the full F2-Good+ universe (~150 names) + walk-forward
across multiple regime windows BEFORE any production wiring. One
clean window = not enough.

Run:
    python scripts/swing_fvg_tap_backtest.py
    python scripts/swing_fvg_tap_backtest.py --diag
    python scripts/swing_fvg_tap_backtest.py --symbols RELIANCE.NS,TCS.NS
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import smc_detectors as smc
from engine.fvg_tap_setup import (
    SWING, CONFIRM_WIN, TAP_WIN, CHOCH_RECENCY, SL_BUF_ATR, R_MULT,
    _atr_at, _bull_engulf,
)
from engine.swing import detect_weekly_trend
import fresh_phase_research as fp  # reuse _load_yf_multi loader

# 12 liquid NSE F&O names — ~3x the existing STOCK_BASKET for sample
# size, all heavily traded with reliable 1H data on yfinance. Frozen
# universe for this run (no add/drop after results).
SWING_UNIVERSE = (
    "RELIANCE.NS,HDFCBANK.NS,ICICIBANK.NS,INFY.NS,TCS.NS,"
    "SBIN.NS,LT.NS,ITC.NS,AXISBANK.NS,BAJFINANCE.NS,"
    "KOTAKBANK.NS,MARUTI.NS"
)

HORIZON = 60  # forward outcome resolution in 1H bars (~2 trading weeks)


def aggregate_to_4h(candles_1h: list) -> list:
    """Group every 4 consecutive 1H bars into one 4H OHLC bar. Drops
    the final incomplete group. Chronological grouping (no fancy
    session alignment) — NSE 6h sessions make true session-aligned 4H
    awkward, and chronological preserves OHLC integrity. Used for the
    4H variant of the same locked rule."""
    out = []
    for i in range(0, len(candles_1h) - 3, 4):
        chunk = candles_1h[i:i + 4]
        out.append({
            "date": chunk[0]["date"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b.get("volume", 0) for b in chunk),
        })
    return out


def build_weekly_trends(candles_1h: list) -> list:
    """Resample 1H candles to weekly (W-FRI close), then compute
    detect_weekly_trend AT EACH completed week using only weeks at or
    before that point — i.e. the trend that would have been KNOWN at
    the START of the following week. Returns sorted list of
    (week_end_iso_string, trend) tuples. Lookup is bisect-O(logN)."""
    import pandas as pd

    if len(candles_1h) < 200:
        return []

    df = pd.DataFrame(candles_1h)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    weekly = (df.resample("W-FRI")
                .agg({"open": "first", "high": "max",
                      "low": "min", "close": "last"})
                .dropna())
    if len(weekly) < 12:
        return []

    weekly_records = [
        {"date": idx.strftime("%Y-%m-%dT%H:%M:%S"),
         "open": float(row.open), "high": float(row.high),
         "low": float(row.low), "close": float(row.close)}
        for idx, row in weekly.iterrows()
    ]
    trends = []
    for i in range(len(weekly_records)):
        trend = detect_weekly_trend(weekly_records[: i + 1])
        trends.append((weekly_records[i]["date"], trend))
    return trends


def get_trend_at(trends: list, ts_iso: str) -> str:
    """Return the weekly trend computed from the most recent week whose
    Friday close is STRICTLY BEFORE the start of the bar's current
    calendar week. NEUTRAL if not enough history yet. RIGOROUSLY
    point-in-time — eliminates the Friday-afternoon look-ahead where
    the bar's own OHLC would otherwise contribute to its own gate.

    Trade-off vs naïve bisect: Monday bars use a trend known last
    Friday (3 days old) — slightly stale but provably no look-ahead."""
    if not trends:
        return "NEUTRAL"
    from datetime import datetime as _dt, timedelta as _td

    bar_dt = _dt.fromisoformat(ts_iso)
    # Start of bar's calendar week (Monday 00:00). Any trend whose
    # Friday week-end is < this cutoff is from a fully-completed week.
    start_of_week = (bar_dt - _td(days=bar_dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    cutoff = start_of_week.strftime("%Y-%m-%dT%H:%M:%S")
    keys = [t[0] for t in trends]
    idx = bisect_right(keys, cutoff) - 1
    if idx < 0:
        return "NEUTRAL"
    return trends[idx][1]


def scan_swing_fvg_tap(candles_1h: list,
                       trends: list | None = None) -> tuple[list, dict]:
    """Scan 1H bars for the swing FVG-Tap rule under the weekly bull
    gate. Returns (trades, funnel_counts). Trades have realised R
    (first-touch SL or 2R target within HORIZON). LONG only.

    `trends`: optional pre-computed weekly trend series (from
    build_weekly_trends on the FULL history). When the caller passes
    a small slice (walk-forward windowing, especially on 4H where
    each window has <12 weeks), pre-computing on full history avoids
    a starvation of trend signal without introducing lookahead —
    get_trend_at is still point-in-time per bar."""
    out: list = []
    f = {"scans": 0, "weekly_bull": 0, "choch": 0, "fvg": 0,
         "tap": 0, "confirm": 0, "entry": 0, "resolved": 0}

    c = candles_1h
    n = len(c)
    if n < 200:
        return out, f

    if trends is None:
        trends = build_weekly_trends(c)
    if not trends:
        return out, f

    used_until = -1
    for i in range(80, n - HORIZON - 2):
        if i <= used_until:
            continue
        f["scans"] += 1

        # --- Weekly bull macro gate (point-in-time) ------------------
        trend = get_trend_at(trends, c[i]["date"])
        if trend not in ("BULLISH", "STRONG_BULL"):
            continue
        f["weekly_bull"] += 1

        # --- Swings + ATR --------------------------------------------
        sh, sl = smc.detect_swing_points(c[: i + 1], SWING, SWING)
        if len(sh) < 2 or len(sl) < 2:
            continue
        atr = _atr_at(c, i)
        if atr <= 0:
            continue

        # --- CHoCH gate (LONG: close above prior lower-high) ---------
        piv_idx, _ = sl[-1]
        lh = next(((hx, hp) for hx, hp in reversed(sh)
                   if hx < piv_idx), None)
        if not lh:
            continue
        choch = next((j for j in range(piv_idx + 1, i + 1)
                      if c[j]["close"] > lh[1]), None)
        if choch is None or choch < i - CHOCH_RECENCY:
            continue
        f["choch"] += 1

        # --- FVG from the displacement that made the CHoCH -----------
        fvg = None
        for a in range(max(piv_idx, choch - 8), choch + 1):
            if a + 2 > i:
                break
            if c[a]["high"] < c[a + 2]["low"]:
                fvg = (c[a]["high"], c[a + 2]["low"], a + 2)
        if fvg is None:
            continue
        fvg_lo, fvg_hi, fvg_done = fvg
        if fvg_hi <= fvg_lo:
            continue
        f["fvg"] += 1

        # --- FIRST tap into the FVG ----------------------------------
        tap = None
        for k in range(fvg_done + 1, min(fvg_done + TAP_WIN, i + 1)):
            if c[k]["low"] <= fvg_hi:
                tap = k
                break
        if tap is None:
            continue
        f["tap"] += 1

        # --- ENGULFING confirmation within CONFIRM_WIN of the tap ----
        conf = None
        for k in range(tap, min(tap + CONFIRM_WIN, i + 1)):
            if k < 1:
                continue
            if _bull_engulf(c[k - 1], c[k]):
                conf = k
                break
        if conf is None or conf + 1 > i:
            continue
        f["confirm"] += 1

        # --- Entry = NEXT bar OPEN after confirmation ----------------
        e_idx = conf + 1
        entry = c[e_idx]["open"]
        sl_px = fvg_lo - SL_BUF_ATR * atr
        risk = entry - sl_px
        if risk <= 0:
            continue
        tgt = entry + R_MULT * risk
        f["entry"] += 1

        # --- Forward outcome (first touch) ---------------------------
        r = None
        for ff in range(e_idx + 1, min(e_idx + 1 + HORIZON, n)):
            hi, lo = c[ff]["high"], c[ff]["low"]
            if lo <= sl_px:
                r = -1.0
                break
            if hi >= tgt:
                r = R_MULT
                break
        if r is None:
            continue
        f["resolved"] += 1
        out.append({
            "entry_idx": e_idx,
            "entry_time": c[e_idx]["date"],
            "entry": round(entry, 2),
            "sl": round(sl_px, 2),
            "target": round(tgt, 2),
            "r": r,
            "win": 1 if r > 0 else 0,
            "weekly_trend": trend,
        })
        used_until = e_idx
    return out, f


def _aggregate(rows: list) -> dict:
    if not rows:
        return {"n": 0, "win": 0.0, "expR": 0.0, "PF": 0.0, "totR": 0.0}
    rs = [r["r"] for r in rows]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    pf = 999.0 if not losses else round(sum(wins) / abs(sum(losses)), 2)
    return {
        "n": len(rs),
        "win": round(100 * len(wins) / len(rs), 1),
        "expR": round(sum(rs) / len(rs), 3),
        "PF": pf,
        "totR": round(sum(rs), 1),
    }


def _gate(s: dict) -> tuple[bool, list]:
    """Returns (passed, list_of_failures). Gate is locked at the top
    of this file; do NOT change after seeing results."""
    fails = []
    if s["n"] < 30:
        fails.append(f"n={s['n']} < 30 (thin)")
    if s["PF"] < 1.30:
        fails.append(f"PF={s['PF']} < 1.30")
    if s["expR"] < 0.20:
        fails.append(f"expR={s['expR']} < 0.20")
    if s["win"] < 40.0:
        fails.append(f"win={s['win']}% < 40%")
    return (len(fails) == 0, fails)


def walk_forward(data: dict, n_windows: int = 3) -> list:
    """Split each symbol's candles into N non-overlapping equal
    sequential windows; run the scan independently in each. Returns a
    list of per-window results: [{window_idx, start_date, end_date,
    summary, passed, per_symbol_n}, ...]. Each window is an
    out-of-sample test for the others — passing 1/3 = window-specific
    artifact; 2/3 or 3/3 = regime-robust edge.

    Pre-computes weekly trends from each symbol's FULL series and
    passes them into scan_swing_fvg_tap so per-window slices (which
    may be shorter than the 12-week minimum for detect_weekly_trend,
    especially on 4H) still have a usable point-in-time trend lookup.
    No lookahead — get_trend_at honours the bar's date."""
    if n_windows < 2:
        raise ValueError("walk_forward needs >=2 windows")

    # Pre-compute full-series weekly trends per symbol.
    trends_by_sym = {sym: build_weekly_trends(c) for sym, c in data.items()}

    # Per-symbol slice plan (each symbol's bar count may differ).
    results = []
    for w in range(n_windows):
        window_rows: list = []
        per_sym: dict = {}
        start_dates, end_dates = [], []
        for sym, c in data.items():
            n = len(c)
            if n < 200:  # need enough for the scan to be valid
                continue
            slice_size = n // n_windows
            lo = w * slice_size
            hi = (w + 1) * slice_size if w < n_windows - 1 else n
            window_slice = c[lo:hi]
            if len(window_slice) < 200:
                continue
            start_dates.append(window_slice[0]["date"])
            end_dates.append(window_slice[-1]["date"])
            rows, _ = scan_swing_fvg_tap(
                window_slice, trends=trends_by_sym.get(sym))
            for r in rows:
                r["symbol"] = sym
                r["window"] = w + 1
            window_rows.extend(rows)
            per_sym[sym] = _aggregate(rows)
        summary = _aggregate(window_rows)
        passed, failures = _gate(summary)
        results.append({
            "window": w + 1,
            "start": min(start_dates) if start_dates else None,
            "end": max(end_dates) if end_dates else None,
            "summary": summary,
            "passed": passed,
            "failures": failures,
            "per_symbol_n": {s: v["n"] for s, v in per_sym.items()},
            "rows": window_rows,
        })
    return results


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Swing FVG-Tap (1H stocks, weekly-bull gate) — pre-committed backtest")
    ap.add_argument("--symbols", type=str, default=SWING_UNIVERSE,
                    help="comma-separated yfinance symbols (default: 12-stock universe)")
    ap.add_argument("--diag", action="store_true",
                    help="print per-symbol funnel + per-symbol breakdown")
    ap.add_argument("--tf", choices=["1h", "4h"], default="1h",
                    help="bar timeframe (1h native, 4h aggregated from 1h)")
    ap.add_argument("--walk-forward", action="store_true",
                    help="split history into N non-overlapping windows + gate each")
    ap.add_argument("--n-windows", type=int, default=3,
                    help="number of walk-forward windows (default 3)")
    ap.add_argument("--output", type=str,
                    default="backtest_results/swing_fvg_tap.json")
    args = ap.parse_args()

    print("=" * 78)
    print("  SWING FVG-TAP (1H stocks, weekly-bull macro gate)")
    print("  Pre-committed backtest — NO tuning, NO production wiring")
    print()
    print("  GATE (locked before results): n>=30 AND PF>=1.30 AND")
    print("                                expR>=0.20 AND win>=40%")
    print("  Rule = EXACT engine.fvg_tap_setup detector (R=2.0, LONG only)")
    print("  Weekly trend = engine.swing.detect_weekly_trend, point-in-time")
    print(f"  Universe ({len(args.symbols.split(','))}): {args.symbols}")
    print("=" * 78)

    print("\n  Loading 1H data via yfinance (~730d each)...")
    try:
        data = fp._load_yf_multi(args.symbols, "1h")
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return
    print(f"  Loaded {len(data)} symbols.")

    if args.tf == "4h":
        print("  Aggregating 1H -> 4H bars (4-bar chronological groups)...")
        data = {sym: aggregate_to_4h(c) for sym, c in data.items()}
        sizes = [len(c) for c in data.values()]
        if sizes:
            print(f"  Post-aggregation: {len(data)} symbols, "
                  f"bars/symbol min={min(sizes)} avg={sum(sizes)//len(sizes)} "
                  f"max={max(sizes)}")

    # ─── WALK-FORWARD MODE ────────────────────────────────────────────
    if args.walk_forward:
        print(f"\n  WALK-FORWARD: {args.n_windows} non-overlapping windows. "
              f"Each window is an out-of-sample test for the others; an")
        print(f"  edge that only passes the full single-window aggregate is "
              f"regime-specific and should NOT ship.")
        wf = walk_forward(data, n_windows=args.n_windows)
        print(f"\n  {'win':<5}{'period':<48}{'n':>5}{'win%':>7}{'expR':>9}"
              f"{'PF':>7}{'totR':>8}  gate")
        print("  " + "-" * 95)
        for w in wf:
            s = w["summary"]
            period = f"{w['start'][:10]} - {w['end'][:10]}" if w["start"] else "n/a"
            g = "PASS" if w["passed"] else "FAIL"
            print(f"  {w['window']:<5}{period:<48}{s['n']:>5}{s['win']:>7.1f}"
                  f"{s['expR']:>+9.3f}{s['PF']:>7.2f}{s['totR']:>+8.1f}  {g}")
        n_pass = sum(1 for w in wf if w["passed"])
        print(f"\n  WALK-FORWARD VERDICT: {n_pass}/{len(wf)} windows PASS")
        if n_pass == len(wf):
            print("    ROBUST PASS — edge survives in every independent window.")
            print("    JUSTIFIES building SHADOW engine. Soak + scorecard next.")
        elif n_pass >= (len(wf) + 1) // 2:
            print("    MAJORITY PASS — edge present but regime-sensitive.")
            print("    Build SHADOW for forward validation; cautiously.")
            print("    Per-window failures expose the regime risk; do NOT")
            print("    paper over by relaxing gates.")
        else:
            print("    FAIL — edge does NOT survive walk-forward.")
            print("    The single-window aggregate that 'passed' was a")
            print("    window-specific artifact. Do NOT ship. Either")
            print("    propose a different rule and re-validate from scratch,")
            print("    or accept this approach is not the answer.")
        # For walk-forward failures, list the specific failed criteria
        for w in wf:
            if not w["passed"]:
                print(f"    Window {w['window']} failures: {'; '.join(w['failures'])}")
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps({
            "generated_at": datetime.now().isoformat(),
            "rule": "swing_fvg_tap_1h_weekly_bull_gated_walkforward",
            "universe": list(data.keys()),
            "gate": {"n": 30, "PF": 1.30, "expR": 0.20, "win": 40.0},
            "n_windows": args.n_windows,
            "windows": [{k: v for k, v in w.items() if k != "rows"} for w in wf],
            "windows_passed": n_pass,
            "rows": [r for w in wf for r in w["rows"]],
        }, indent=2), encoding="utf-8")
        print(f"\n  Results written: {args.output}")
        return

    all_rows: list = []
    per_symbol: dict = {}
    combined_funnel: dict = {}

    for sym, c in data.items():
        rows, funnel = scan_swing_fvg_tap(c)
        for r in rows:
            r["symbol"] = sym
        all_rows.extend(rows)
        per_symbol[sym] = {"funnel": funnel, "summary": _aggregate(rows)}
        for k, v in funnel.items():
            combined_funnel[k] = combined_funnel.get(k, 0) + v

    summary = _aggregate(all_rows)
    passed, failures = _gate(summary)

    if args.diag:
        print("\n  PER-SYMBOL BREAKDOWN")
        print(f"  {'symbol':<18}{'n':>5}{'win%':>7}{'expR':>9}{'PF':>7}{'totR':>8}")
        print("  " + "-" * 54)
        for sym in sorted(per_symbol.keys()):
            s = per_symbol[sym]["summary"]
            print(f"  {sym:<18}{s['n']:>5}{s['win']:>7.1f}"
                  f"{s['expR']:>+9.3f}{s['PF']:>7.2f}{s['totR']:>+8.1f}")

        print("\n  COMBINED DETECTION FUNNEL")
        for k in ["scans", "weekly_bull", "choch", "fvg", "tap",
                  "confirm", "entry", "resolved"]:
            print(f"    {k:<14} {combined_funnel.get(k, 0):>7}")

    print("\n  AGGREGATE RESULT (all 12 symbols, LONG only, weekly-bull gated)")
    print(f"    n         = {summary['n']}")
    print(f"    win rate  = {summary['win']}%")
    print(f"    expR      = {summary['expR']:+.3f}")
    print(f"    PF        = {summary['PF']}")
    print(f"    totR      = {summary['totR']:+.1f}")

    print("\n  GATE VERDICT")
    if passed:
        print(f"    PASS — all 4 criteria met (n>=30, PF>=1.30, "
              f"expR>=0.20, win>=40%).")
        print(f"    NEXT STEP: build as SHADOW-mode engine (new "
              f"EQUITY_FVG_TAP_MODE flag, default-OFF), soak for ~2 "
              f"weeks, validate via parallel scorecard. DO NOT promote "
              f"to alert until forward-shadow gate also clears. Then "
              f"compare against the existing G2-6 to decide replace vs "
              f"run-alongside.")
    else:
        print(f"    FAIL — {len(failures)} criteria miss: " + "; ".join(failures))
        print(f"    HONEST NEGATIVE: the rule that works on 5m indexes "
              f"does NOT clear the same-rigor gate on 1H stocks under "
              f"a weekly-bull filter. Do NOT relax the gate to manufacture "
              f"a pass. Do NOT ship. Options: (a) accept the rule is "
              f"domain-specific to 5m indexes, (b) test a different "
              f"timeframe (4H, daily) keeping all other params locked, "
              f"(c) propose a structurally different swing rule and "
              f"backtest from scratch with a fresh pre-committed gate.")

    print(f"\n  HONEST LIMITS")
    print(f"  - 12-stock first-pass universe is suggestive, not definitive.")
    print(f"  - yfinance 1H = ~730d = ONE multi-regime window.")
    print(f"  - Walk-forward across N independent windows is the real test.")
    print(f"  - Slippage/transaction cost NOT modelled (next-bar open fill).")
    print(f"  - If PASS: this earns 'build+shadow', not 'wire live'.")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "rule": "swing_fvg_tap_1h_weekly_bull_gated",
        "universe": list(data.keys()),
        "gate": {"n": 30, "PF": 1.30, "expR": 0.20, "win": 40.0},
        "summary": summary,
        "passed": passed,
        "failures": failures,
        "per_symbol": {s: v["summary"] for s, v in per_symbol.items()},
        "combined_funnel": combined_funnel,
        "rows": all_rows,
    }, indent=2), encoding="utf-8")
    print(f"\n  Results written: {args.output}")


if __name__ == "__main__":
    main()
