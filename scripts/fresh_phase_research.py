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

# Fixed, transparent STRUCTURAL definitions (NOT fitted to outcomes, per
# directive). Chosen once on structural reasoning; never swept for profit.
GRAB_RECLAIM_BARS = 4        # sweep may reclaim over up to 4 bars (multi-bar
                             # grab — funnel proved 1-bar was too strict)
MAX_BARS_GRAB_TO_ENTRY = 30  # CHoCH + first retest must be timely
# "Continuation has clearly begun" = price pushed > CONT_ATR ATR beyond the
# broken swing high. Pre-continuation = the pullback into the OB happened
# BEFORE that. This REPLACES the logically self-contradictory
# "pre-ANY-expansion" gate (the CHoCH itself is the first expansion).
CONT_ATR = 1.0
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


_TF_MAP = {"5m": ("5m", "60d"), "15m": ("15m", "60d"), "1h": ("60m", "730d")}


def _load_yf_multi(symbols_csv: str, tf: str) -> dict:
    """Configurable REAL data via yfinance for the timeframe/instrument
    rethink. HONEST LIMITS: Yahoo intraday caps ~60d for 5m/15m, ~730d for
    1h(=60m). 1h therefore gives BOTH a slower timeframe AND ~12x more
    history than the 5m run — the right test for 'is 5m too fast for an
    early entry to exist'. No tuning; same fixed detector definition."""
    import yfinance as yf
    interval, period = _TF_MAP[tf]

    def _norm(df):
        if df is None or len(df) == 0:
            return []
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df = df.droplevel(1, axis=1)
        try:
            idx = df.index.tz_convert("Asia/Kolkata")
        except (TypeError, AttributeError):
            idx = df.index
        out = []
        for ts, row in zip(idx, df.itertuples(index=False)):
            o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
            if not (o > 0 and h > 0 and l > 0 and c > 0):
                continue
            out.append({"date": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                        "open": o, "high": h, "low": l, "close": c,
                        "volume": int(getattr(row, "Volume", 0) or 0)})
        return out

    out = {}
    for sym in [s.strip() for s in symbols_csv.split(",") if s.strip()]:
        cs = _norm(yf.download(sym, interval=interval, period=period,
                               progress=False, auto_adjust=False))
        if len(cs) >= 200:
            out[sym] = cs
        else:
            print(f"  (skip {sym}: only {len(cs)} {tf} candles)")
    if not out:
        raise RuntimeError("yfinance returned no usable data for given symbols/tf")
    return out


def _multi_grab(c, i, swing_low):
    """Multi-bar sweep+reclaim: bar i breaks below swing_low; a bar within
    GRAB_RECLAIM_BARS closes back above it. Returns (grab_low, reclaim_idx)
    or None. (The funnel proved the 1-bar rule was structurally too strict
    — grab_multi 501 vs grab_single 239 on real data.)"""
    if c[i]["low"] >= swing_low:
        return None
    lo = c[i]["low"]
    for k in range(i, min(i + GRAB_RECLAIM_BARS + 1, len(c))):
        lo = min(lo, c[k]["low"])
        if c[k]["close"] > swing_low:
            return lo, k
    return None


def detect_fresh_longs(c: list) -> list:
    """Post-CHoCH / pre-continuation fresh-reclaim longs (the structurally
    coherent definition chosen from funnel evidence).

      1. GRAB  — prior swing low swept then reclaimed within a few bars
                 (multi-bar; sell-side liquidity grab).
      2. CHoCH — first close back ABOVE the prior swing high (this IS the
                 first expansion — accepted, not gated against).
      3. ENTRY — FIRST retest into the origin OB AFTER the CHoCH, taken
                 BEFORE the continuation leg (price has NOT yet pushed
                 > CONT_ATR ATR beyond the broken swing high). If price ran
                 the continuation FIRST and only deep-retraced later, that
                 is the LATE case (what Setup-A does) — excluded.
      SL below the grab low, target 2R, forward-simulated (first touch).
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
        g = _multi_grab(c, i, swing_low)
        if g is None:
            continue
        grab_low, reclaim_idx = g
        grab_idx = i
        prior_high = next(((hx, hp) for hx, hp in reversed(sh)
                           if hx < grab_idx), None)
        if not prior_high:
            continue
        ph_px = prior_high[1]
        choch = None
        for j in range(reclaim_idx, min(grab_idx + MAX_BARS_GRAB_TO_ENTRY, n)):
            if c[j]["close"] > ph_px:
                choch = j
                break
        if choch is None:
            continue
        ob_idx = next((k for k in range(choch, grab_idx - 1, -1)
                       if c[k]["close"] < c[k]["open"]), grab_idx)
        ob_lo = min(c[ob_idx]["low"], grab_low)
        ob_hi = max(c[ob_idx]["open"], c[ob_idx]["close"])
        if ob_hi <= ob_lo:
            continue

        # Pre-continuation gate: the FIRST OB retest must occur BEFORE the
        # continuation leg (price pushing > CONT_ATR ATR beyond ph_px).
        cont_level = ph_px + CONT_ATR * atr
        entry_k = None
        for k in range(choch + 1, min(choch + MAX_BARS_GRAB_TO_ENTRY, n)):
            if max(x["high"] for x in c[choch:k + 1]) >= cont_level:
                break  # continuation already ran -> LATE, not fresh
            if c[k]["low"] <= ob_hi:
                entry_k = k
                break  # FIRST retest only
        if entry_k is None:
            continue

        entry = ob_hi
        sl = grab_low - 0.10 * atr
        risk = entry - sl
        if risk <= 0:
            continue
        target = entry + 2.0 * risk
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
            "bars_choch_to_entry": entry_k - choch,
            "ext_at_entry_atr": round(ext_at_entry, 2),
        })
        used_until = entry_k
    return out


def funnel_diag(c: list) -> dict:
    """DIAGNOSIS ONLY — no outcome logic, no thresholds changed. Counts how
    many candidates survive each detection stage so we can see exactly
    WHERE the funnel collapses to zero. Also counts a MULTI-BAR grab
    (break then close-back within 4 bars) alongside the strict SINGLE-bar
    grab, to test the prime suspect for the 0 result."""
    f = {"bars": 0, "swinglow_ok": 0, "grab_single": 0, "grab_multi": 0,
         "prior_high_ok": 0, "choch_ok": 0, "ob_ok": 0,
         "first_retest_ok": 0, "pre_cont_ok": 0, "resolved_ok": 0}
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
        if c[i]["low"] < swing_low and c[i]["close"] > swing_low:
            f["grab_single"] += 1
        g = _multi_grab(c, i, swing_low)
        if g is None:
            continue
        f["grab_multi"] += 1
        grab_low, reclaim_idx = g
        grab_idx = i
        prior_high = next(((hx, hp) for hx, hp in reversed(sh)
                           if hx < grab_idx), None)
        if not prior_high:
            continue
        f["prior_high_ok"] += 1
        ph_px = prior_high[1]
        choch = None
        for j in range(reclaim_idx, min(grab_idx + MAX_BARS_GRAB_TO_ENTRY, n)):
            if c[j]["close"] > ph_px:
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
        cont_level = ph_px + CONT_ATR * atr
        for k in range(choch + 1, min(choch + MAX_BARS_GRAB_TO_ENTRY, n)):
            if max(x["high"] for x in c[choch:k + 1]) >= cont_level:
                break  # continuation ran first -> LATE
            if c[k]["low"] <= ob_hi:
                f["first_retest_ok"] += 1
                f["pre_cont_ok"] += 1
                sl_ = grab_low - 0.10 * atr
                if ob_hi - sl_ > 0:
                    tgt = ob_hi + 2.0 * (ob_hi - sl_)
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


# Leading liquid NSE names — slower-moving than the index intraday.
STOCK_BASKET = ("RELIANCE.NS,HDFCBANK.NS,ICICIBANK.NS,INFY.NS,TCS.NS,"
                "SBIN.NS,LT.NS,ITC.NS")


def _run_fresh(data: dict) -> tuple[list, dict]:
    """data = {symbol: candles_list}. Returns (all_fresh_rows, summary)."""
    rows = []
    for sym, c in data.items():
        for x in detect_fresh_longs(c):
            x["symbol"] = sym
            rows.append(x)
    s = _agg(rows)
    if rows:
        s["avg_ext_atr"] = round(sum(x["ext_at_entry_atr"] for x in rows) / len(rows), 2)
        s["avg_bars"] = round(sum(x["bars_grab_to_entry"] for x in rows) / len(rows), 1)
    else:
        s["avg_ext_atr"] = 0.0
        s["avg_bars"] = 0.0
    return rows, s


def main() -> None:
    ap = argparse.ArgumentParser(description="Fresh-phase (one phase earlier) research")
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--symbols", type=str, default=None)
    ap.add_argument("--tf", choices=["5m", "15m", "1h"], default="1h",
                    help="timeframe for --yf (1h=~730d, 5m/15m=~60d)")
    ap.add_argument("--yf", action="store_true")
    ap.add_argument("--matrix", action="store_true",
                    help="comparative run: NIFTY 5m / NIFTY 15m / stock 15m / stock 1h")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--funnel", action="store_true",
                    help="diagnosis: print where detection collapses (no tuning)")
    ap.add_argument("--synthetic-days", type=int, default=90)
    ap.add_argument("--output", type=str,
                    default="backtest_results/fresh_phase.json")
    args = ap.parse_args()

    # ---- MATRIX: the timeframe/instrument comparative study -----------
    if args.matrix:
        print("=" * 78)
        print("  FRESH-PHASE COMPARATIVE MATRIX — pure observation, no tuning")
        print("  Q: does the early-reclaim edge exist more clearly in slower")
        print("     equities / higher timeframes, or is the thesis invalid?")
        print("=" * 78)
        configs = [
            ("NIFTY 5m", "5m", "^NSEI"),
            ("NIFTY 15m", "15m", "^NSEI"),
            ("STOCK 15m", "15m", STOCK_BASKET),
            ("STOCK 1h", "1h", STOCK_BASKET),
        ]
        results = {}
        print(f"\n  {'config':<11}{'n':>5}{'win%':>7}{'expR':>9}{'PF':>7}"
              f"{'avgExtATR':>11}{'avgBars':>9}")
        print("  " + "-" * 58)
        for label, tf, syms in configs:
            try:
                d = _load_yf_multi(syms, tf)
            except Exception as exc:
                print(f"  {label:<11}  data unavailable: {exc}")
                continue
            rows, s = _run_fresh(d)
            results[label] = s
            thin = " (thin)" if s["n"] < 25 else ""
            print(f"  {label:<11}{s['n']:>5}{s['win']:>7.1f}{s['expR']:>+9.3f}"
                  f"{s['PF']:>7.2f}{s['avg_ext_atr']:>11.2f}{s['avg_bars']:>9.1f}"
                  f"{thin}")
        print("\n  READ (the structural tell is avgExtATR):")
        print("  - If avgExtATR stays ~3+ across ALL configs, a truly-early")
        print("    entry does not structurally exist regardless of TF/instrument")
        print("    => the thesis itself is likely invalid, not just on NIFTY 5m.")
        print("  - If slower TF / stocks show LOWER avgExtATR *and* clearly")
        print("    better expR/PF on a non-thin n, the early edge is real and")
        print("    NIFTY-5m simply moves too fast for it.")
        print("  Honest limits: 5m/15m ~60d (small n), 1h ~730d (better);")
        print("  yfinance stock intraday can be patchy; DIRECTIONAL only;")
        print("  no Setup-A comparison here (cross-TF/instrument is not")
        print("  apples-to-apples); nothing tuned, nothing wired.")
        op = Path(args.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps({"generated_at": datetime.now().isoformat(),
                                  "matrix": results}, indent=2),
                       encoding="utf-8")
        print(f"\nWrote {op}")
        return

    # ---- single-config data load -------------------------------------
    setup_a_ok = False
    if args.synthetic:
        print("  SYNTHETIC — WIRING TEST ONLY. NOT decision-grade.")
        from backtest.runner import generate_synthetic_candles
        data = {"SYNTHETIC:INDEX": generate_synthetic_candles(days=args.synthetic_days)}
        setup_a_ok = True
    elif args.yf:
        syms = args.symbols or "^NSEI"
        print(f"  REAL yfinance {syms} @ {args.tf}. DIRECTIONAL, "
              f"{'~730d' if args.tf == '1h' else '~60d'}.")
        data = _load_yf_multi(syms, args.tf)
        setup_a_ok = args.tf == "5m"  # Setup-A is a 5m engine only
    else:
        from backtest.data_store import DataStore
        from backtest.runner import load_data_from_store
        store = DataStore(args.db) if args.db else DataStore()
        raw = load_data_from_store(store, args.symbols.split(",") if args.symbols else None)
        store.close()
        if not raw:
            print("ERROR: no market data. Use --db / --yf / --synthetic / --matrix.")
            sys.exit(1)
        data = {s: v.get("5m") or [] for s, v in raw.items()}
        setup_a_ok = True

    if args.funnel:
        print("\nDETECTION FUNNEL  (diagnosis only — counts, no tuning)\n")
        agg = {}
        for _sym, c in data.items():
            for k, v in funnel_diag(c).items():
                agg[k] = agg.get(k, 0) + v
        order = ["bars", "swinglow_ok", "grab_single", "grab_multi",
                 "prior_high_ok", "choch_ok", "ob_ok", "first_retest_ok",
                 "pre_cont_ok", "resolved_ok"]
        base = max(agg.get("swinglow_ok", 0), 1)
        for k in order:
            v = agg.get(k, 0)
            pct = "" if k == "bars" else f"  ({100*v/base:.1f}% of valid scans)"
            print(f"  {k:<16} {v:>7}{pct}")
        return

    all_fresh, fa = _run_fresh(data)
    print(f"\nFRESH-PHASE long entries: {fa['n']}  win={fa['win']}%  "
          f"expR={fa['expR']:+.3f}  PF={fa['PF']}  totR={fa['totR']:+.1f}")
    print(f"  avg bars grab->entry={fa['avg_bars']}  "
          f"avg extension at entry={fa['avg_ext_atr']} ATR")
    if len(data) > 1:
        print("\n  Per instrument:")
        for sym in data:
            sub = [x for x in all_fresh if x["symbol"] == sym]
            ss = _agg(sub)
            if ss["n"]:
                ee = sum(x["ext_at_entry_atr"] for x in sub) / len(sub)
                print(f"    {sym:<14} n={ss['n']:>3} win={ss['win']:>5.1f}% "
                      f"expR={ss['expR']:+.3f} PF={ss['PF']:>5.2f} "
                      f"avgExt={ee:.2f}ATR")

    paired = []
    if setup_a_ok:
        sa_data = {s: {"5m": c} for s, c in data.items()}
        sa = [t for t in BacktestEngine(BacktestConfig()).run_multi(sa_data)
              if probe._is_setup_a(t.setup) and t.direction == "LONG"]
        saa = _agg([{"r": t.r_multiple} for t in sa])
        print(f"\nSetup-A LONG (same 5m data): {saa['n']}  win={saa['win']}%  "
              f"expR={saa['expR']:+.3f}  PF={saa['PF']}")
        sa_by = {}
        for t in sa:
            sa_by.setdefault(t.symbol, []).append(t)
        for x in all_fresh:
            fdt = probe._parse_dt(x["entry_time"])
            cand = []
            for t in sa_by.get(x["symbol"], []):
                tdt = probe._parse_dt(t.entry_time)
                if tdt and fdt and 0 < (tdt - fdt).total_seconds() <= 6 * 3600:
                    cand.append((t, (tdt - fdt).total_seconds() / 300.0))
            if cand:
                t, lag = min(cand, key=lambda z: z[1])
                paired.append({"fresh_r": x["r"], "late_r": t.r_multiple,
                               "lag_bars": round(lag, 1)})
        if paired:
            print(f"  Lateness: {len(paired)} shared moves, Setup-A ~"
                  f"{sum(p['lag_bars'] for p in paired)/len(paired):.1f} bars later"
                  f"  | fresh expR="
                  f"{sum(p['fresh_r'] for p in paired)/len(paired):+.3f}"
                  f" vs late "
                  f"{sum(p['late_r'] for p in paired)/len(paired):+.3f}")
    else:
        print(f"\n  (Setup-A comparison skipped: Setup-A is a 5m-index engine;"
              f" comparing it at {args.tf}/equities is not apples-to-apples.)")

    print("\n  DIRECTIONAL only — no tuning, no wiring, scorer frozen.")
    op = Path(args.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "tf": args.tf, "synthetic": args.synthetic,
        "fresh_phase": fa, "paired_shared_moves": paired,
        "fresh_rows": all_fresh,
    }, indent=2), encoding="utf-8")
    print(f"Wrote {op}")


if __name__ == "__main__":
    main()
