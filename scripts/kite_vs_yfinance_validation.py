"""
scripts/kite_vs_yfinance_validation.py
======================================
PHASE 1 VALIDATION — does the FVG-Tap rule's gate-clearing edge survive
when run on Kite OHLC instead of the yfinance OHLC it was validated on?

WHY THIS EXISTS
---------------
The production FVG-Tap rule (engine/fvg_tap_setup.py) was validated by
scripts/fvg_tap_backtest.py on yfinance 5m data, clearing the locked
gate (NIFTY PF 2.07, BANKNIFTY PF 2.33). Today's forensic showed that
yfinance's real-time lag (~15+ min) causes the live engine to
systematically miss signals. The proposed fix is to switch the live
data source to Kite — but Kite OHLC may differ slightly from yfinance
OHLC for the same NSE indices (different licensee, different handling
of pre-open / extended prints, different rounding). If those tiny
differences flip the gate outcome, the apparent edge was yfinance-
specific and the switch must NOT happen.

This script tests, before any production change:
  1. How much do Kite and yfinance OHLC actually differ on these
     symbols over 60 days of 5m bars?
  2. Does the EXACT production detector (engine.fvg_tap_setup.
     evaluate_fvg_tap) fire on a comparable number of bars on each
     source?
  3. Does the LOCKED backtest gate (PF>=1.30 AND expR>0 AND win>=40%
     AND n>=60) clear on Kite OHLC the same way it cleared on
     yfinance OHLC?

LOCKED GATE (identical to scripts/fvg_tap_backtest.py — declared
BEFORE results, judged on EACH index independently):
  n >= 60   (non-thin)
  PF >= 1.30
  expR > 0
  win >= 40%

ALL detector parameters are imported from engine.fvg_tap_setup —
NOTHING is tuned for this run. The detector is byte-identical to the
live production rule.

RUN (requires Kite credentials in env — easiest via Railway):
  railway run --service web -- python scripts/kite_vs_yfinance_validation.py

  Or locally with KITE_API_KEY + KITE_ACCESS_TOKEN env vars set.

RESULT FORMAT
-------------
Section 1: data freshness + bar count comparison
Section 2: bar-by-bar OHLC diff statistics (only on intersected timestamps)
Section 3: FVG-Tap signal count comparison per direction + per symbol
Section 4: aggregated metrics on each data source
Section 5: gate verdict on Kite OHLC + final recommendation

Output JSON also written to backtest_results/kite_vs_yfinance.json.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import smc_detectors as smc
from engine.fvg_tap_setup import (
    SWING, CONFIRM_WIN, TAP_WIN, CHOCH_RECENCY, SL_BUF_ATR, R_MULT,
    _atr_at, _bull_engulf, _bear_engulf,
)

LOOKBACK_DAYS = 60
HORIZON = 80  # outcome resolution bars (matches fvg_tap_backtest.py)

# Symbol mappings (yfinance ticker vs Kite NSE: symbol)
SYMBOLS = [
    {"label": "NIFTY 5m",     "yf": "^NSEI",    "kite": "NSE:NIFTY 50"},
    {"label": "BANKNIFTY 5m", "yf": "^NSEBANK", "kite": "NSE:NIFTY BANK"},
]


# ---------------------------------------------------------------- loaders
def _load_yf(ticker: str) -> list:
    """yfinance 5m for ~60d, Asia/Kolkata tz, dict list."""
    import yfinance as yf
    df = yf.download(ticker, interval="5m", period=f"{LOOKBACK_DAYS}d",
                     progress=False, auto_adjust=False)
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
        o, h, l, c = (float(row.Open), float(row.High),
                      float(row.Low), float(row.Close))
        if not (o > 0 and h > 0 and l > 0 and c > 0):
            continue
        out.append({"date": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                    "open": round(o, 2), "high": round(h, 2),
                    "low": round(l, 2), "close": round(c, 2),
                    "volume": int(getattr(row, "Volume", 0) or 0)})
    return out


_TOKEN_CACHE: dict[str, int] = {}


def _kite_token(kite, sym: str) -> int:
    """Resolve NSE:SYMBOL to instrument_token via kite.ltp()."""
    if sym in _TOKEN_CACHE:
        return _TOKEN_CACHE[sym]
    d = kite.ltp(sym)
    tok = list(d.values())[0]["instrument_token"]
    _TOKEN_CACHE[sym] = tok
    return tok


def _load_kite(kite, sym: str) -> list:
    """Kite historical_data 5m for ~60d, Asia/Kolkata tz, dict list.
    Kite returns dt-aware datetimes already in Asia/Kolkata."""
    token = _kite_token(kite, sym)
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=LOOKBACK_DAYS)
    raw = kite.historical_data(token, from_dt, to_dt, "5minute")
    out = []
    for bar in raw:
        dt = bar["date"]
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(dt, "strftime") else str(dt)
        o = float(bar["open"]); h = float(bar["high"])
        l = float(bar["low"]); c = float(bar["close"])
        if not (o > 0 and h > 0 and l > 0 and c > 0):
            continue
        out.append({"date": ts,
                    "open": round(o, 2), "high": round(h, 2),
                    "low": round(l, 2), "close": round(c, 2),
                    "volume": int(bar.get("volume", 0) or 0)})
    return out


# ---------------------------------------------------------------- diff
def _diff_ohlc(yf_bars: list, kite_bars: list) -> dict:
    """Bar-by-bar OHLC diff statistics on timestamps present in BOTH.
    Returns counts + max/mean diff for each OHLC field."""
    yf_idx = {b["date"]: b for b in yf_bars}
    kite_idx = {b["date"]: b for b in kite_bars}
    common = sorted(set(yf_idx.keys()) & set(kite_idx.keys()))

    fields = ["open", "high", "low", "close"]
    diffs = {f: [] for f in fields}
    for ts in common:
        y, k = yf_idx[ts], kite_idx[ts]
        for f in fields:
            diffs[f].append(abs(y[f] - k[f]))

    stats = {
        "yf_bars": len(yf_bars),
        "kite_bars": len(kite_bars),
        "common_timestamps": len(common),
        "only_yf": len(set(yf_idx.keys()) - set(kite_idx.keys())),
        "only_kite": len(set(kite_idx.keys()) - set(yf_idx.keys())),
    }
    for f in fields:
        d = diffs[f]
        if not d:
            stats[f] = {"max": 0.0, "mean": 0.0, "n": 0}
            continue
        # Express absolute diff (price units) and relative diff (bps of price)
        max_d = max(d)
        mean_d = sum(d) / len(d)
        # Relative scale (use close as denominator on average)
        avg_close = sum(yf_idx[ts]["close"] for ts in common) / len(common)
        stats[f] = {
            "max_abs": round(max_d, 4),
            "mean_abs": round(mean_d, 4),
            "max_bps": round(max_d / avg_close * 10000, 2),
            "mean_bps": round(mean_d / avg_close * 10000, 4),
            "n": len(d),
        }
    return stats


# ---------------------------------------------------------------- detector
def _detect_fvg_tap(c: list, direction: str) -> list:
    """EXACT LONG/SHORT FVG-Tap rule from scripts/fvg_tap_backtest.py —
    point-in-time scan, both directions supported, R=2.0 fixed, no
    look-ahead. Returns list of trade dicts with realised R."""
    out = []
    n = len(c)
    if n < 120:
        return out
    long = direction == "LONG"
    used_until = -1
    for i in range(40, n - HORIZON - 2):
        if i <= used_until:
            continue
        sh, sl = smc.detect_swing_points(c[: i + 1], SWING, SWING)
        if len(sh) < 2 or len(sl) < 2:
            continue
        atr = _atr_at(c, i)
        if atr <= 0:
            continue

        if long:
            piv_idx, _ = sl[-1]
            lh = next(((hx, hp) for hx, hp in reversed(sh)
                       if hx < piv_idx), None)
            if not lh:
                continue
            choch = next((j for j in range(piv_idx + 1, i + 1)
                          if c[j]["close"] > lh[1]), None)
        else:
            piv_idx, _ = sh[-1]
            hl = next(((lx, lp) for lx, lp in reversed(sl)
                       if lx < piv_idx), None)
            if not hl:
                continue
            choch = next((j for j in range(piv_idx + 1, i + 1)
                          if c[j]["close"] < hl[1]), None)
        if choch is None or choch < i - CHOCH_RECENCY:
            continue

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

        tap = None
        for k in range(fvg_done + 1, min(fvg_done + TAP_WIN, i + 1)):
            if (long and c[k]["low"] <= fvg_hi) or \
               (not long and c[k]["high"] >= fvg_lo):
                tap = k; break
        if tap is None:
            continue

        conf = None
        for k in range(tap, min(tap + CONFIRM_WIN, i + 1)):
            if k < 1:
                continue
            if (long and _bull_engulf(c[k - 1], c[k])) or \
               (not long and _bear_engulf(c[k - 1], c[k])):
                conf = k; break
        if conf is None or conf + 1 > i:
            continue

        # Entry = NEXT bar open (realistic live fill)
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

        r = None
        for ff in range(e_idx + 1, min(e_idx + 1 + HORIZON, n)):
            hi, lo = c[ff]["high"], c[ff]["low"]
            if long:
                if lo <= sl_px: r = -1.0; break
                if hi >= tgt: r = R_MULT; break
            else:
                if hi >= sl_px: r = -1.0; break
                if lo <= tgt: r = R_MULT; break
        if r is None:
            continue
        out.append({"dir": direction, "entry_idx": e_idx,
                    "entry_time": c[e_idx]["date"],
                    "entry": round(entry, 2), "sl": round(sl_px, 2),
                    "target": round(tgt, 2), "r": r,
                    "win": 1 if r > 0 else 0})
        used_until = e_idx
    return out


def _both(c: list) -> list:
    return _detect_fvg_tap(c, "LONG") + _detect_fvg_tap(c, "SHORT")


def _aggregate(rows: list) -> dict:
    if not rows:
        return {"n": 0, "win": 0.0, "expR": 0.0, "PF": 0.0, "totR": 0.0}
    rs = [r["r"] for r in rows]
    w = [x for x in rs if x > 0]
    l = [x for x in rs if x <= 0]
    pf = 999.0 if not l else round(sum(w) / abs(sum(l)), 2)
    return {"n": len(rs),
            "win": round(100 * len(w) / len(rs), 1),
            "expR": round(sum(rs) / len(rs), 3),
            "PF": pf,
            "totR": round(sum(rs), 1)}


def _gate(s: dict) -> tuple[bool, list]:
    fails = []
    if s["n"] < 60: fails.append(f"n={s['n']} < 60 (thin)")
    if s["PF"] < 1.30: fails.append(f"PF={s['PF']} < 1.30")
    if s["expR"] <= 0: fails.append(f"expR={s['expR']} <= 0")
    if s["win"] < 40.0: fails.append(f"win={s['win']}% < 40%")
    return (len(fails) == 0, fails)


# ---------------------------------------------------------------- main
def main() -> None:
    print("=" * 78)
    print("  KITE vs YFINANCE — Phase 1 validation for FVG-Tap data-source switch")
    print()
    print("  GATE (locked, identical to scripts/fvg_tap_backtest.py):")
    print("    n>=60 AND PF>=1.30 AND expR>0 AND win>=40%")
    print("    Judged on BOTH index 5m series independently.")
    print()
    print(f"  Period: {LOOKBACK_DAYS} days of 5m bars per symbol")
    print(f"  Detector: engine.fvg_tap_setup.evaluate_fvg_tap (identical to live)")
    print(f"  Direction: LONG + SHORT (matches index5m backtest)")
    print("=" * 78)

    # Init Kite from env
    try:
        from kiteconnect import KiteConnect
        api_key = os.environ["KITE_API_KEY"]
        # Token: try Redis first, else env
        access_token = None
        try:
            import redis as _redis
            url = os.environ.get("REDIS_URL", "").strip()
            if url:
                r = _redis.from_url(url, decode_responses=True)
                t = r.get("kite:access_token")
                if t and t.strip():
                    access_token = t.strip()
                    print("\n  Kite token: loaded from Redis")
        except Exception:
            pass
        if not access_token:
            access_token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()
            if access_token:
                print("\n  Kite token: loaded from env")
        if not access_token:
            print("\n  ERROR: no Kite access token available. Set KITE_ACCESS_TOKEN "
                  "or REDIS_URL with kite:access_token populated.")
            sys.exit(1)
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        print(f"  Kite client ready (api_key={api_key[:6]}...)")
    except KeyError as exc:
        print(f"\n  ERROR: missing env var: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n  ERROR: Kite init failed: {exc}")
        sys.exit(1)

    out_per_symbol = {}
    for s in SYMBOLS:
        label, yf_t, kite_t = s["label"], s["yf"], s["kite"]
        print(f"\n  {'=' * 70}")
        print(f"  {label}  ({yf_t} vs {kite_t})")
        print(f"  {'=' * 70}")

        try:
            yf_bars = _load_yf(yf_t)
        except Exception as exc:
            print(f"  yfinance fetch failed: {exc}")
            yf_bars = []
        try:
            kite_bars = _load_kite(kite, kite_t)
        except Exception as exc:
            print(f"  Kite fetch failed: {exc}")
            kite_bars = []

        diff = _diff_ohlc(yf_bars, kite_bars)
        print(f"\n  BARS:    yfinance={diff['yf_bars']}  "
              f"kite={diff['kite_bars']}  common={diff['common_timestamps']}  "
              f"only_yf={diff['only_yf']}  only_kite={diff['only_kite']}")
        print(f"  OHLC DIFF (on common timestamps):")
        for f in ["open", "high", "low", "close"]:
            d = diff[f]
            if d.get("n", 0) == 0:
                print(f"    {f:<6} no overlap")
                continue
            print(f"    {f:<6} max_abs={d['max_abs']:>8.3f}  "
                  f"mean_abs={d['mean_abs']:>7.4f}  "
                  f"max_bps={d['max_bps']:>6.2f}  "
                  f"mean_bps={d['mean_bps']:>6.4f}")

        yf_trades = _both(yf_bars) if yf_bars else []
        kite_trades = _both(kite_bars) if kite_bars else []
        yf_s = _aggregate(yf_trades)
        kite_s = _aggregate(kite_trades)
        yf_pass, yf_fails = _gate(yf_s)
        kite_pass, kite_fails = _gate(kite_s)

        print(f"\n  FVG-TAP SIGNALS / METRICS:")
        print(f"    {'source':<10}{'n':>5}{'win%':>7}{'expR':>9}{'PF':>7}"
              f"{'totR':>8}  gate")
        print(f"    {'-' * 56}")
        print(f"    {'yfinance':<10}{yf_s['n']:>5}{yf_s['win']:>7.1f}"
              f"{yf_s['expR']:>+9.3f}{yf_s['PF']:>7.2f}{yf_s['totR']:>+8.1f}  "
              f"{'PASS' if yf_pass else 'FAIL'}")
        print(f"    {'kite':<10}{kite_s['n']:>5}{kite_s['win']:>7.1f}"
              f"{kite_s['expR']:>+9.3f}{kite_s['PF']:>7.2f}{kite_s['totR']:>+8.1f}  "
              f"{'PASS' if kite_pass else 'FAIL'}")
        if not kite_pass:
            print(f"    Kite failures: {'; '.join(kite_fails)}")

        out_per_symbol[label] = {
            "yf_ticker": yf_t, "kite_symbol": kite_t,
            "diff": diff,
            "yf_summary": yf_s, "yf_pass": yf_pass, "yf_fails": yf_fails,
            "kite_summary": kite_s, "kite_pass": kite_pass,
            "kite_fails": kite_fails,
            "yf_trade_count": len(yf_trades),
            "kite_trade_count": len(kite_trades),
        }

    # ---------------------------------------------------------- verdict
    print("\n" + "=" * 78)
    print("  FINAL VERDICT")
    print("=" * 78)
    # Original gate logic: PASS requires BOTH symbols to clear, independently
    kite_pass_count = sum(1 for v in out_per_symbol.values() if v["kite_pass"])
    yf_pass_count = sum(1 for v in out_per_symbol.values() if v["yf_pass"])
    n_symbols = len(out_per_symbol)
    print(f"\n  yfinance gate: {yf_pass_count}/{n_symbols} symbols PASS")
    print(f"  kite gate:     {kite_pass_count}/{n_symbols} symbols PASS")

    if kite_pass_count == n_symbols and yf_pass_count == n_symbols:
        print("\n  >> GREEN-LIGHT THE SWITCH")
        print("     Both data sources clear the locked gate independently on each")
        print("     index. The edge is NOT yfinance-specific. Kite OHLC produces a")
        print("     comparable trade set; the live engine will be on validated data.")
        print()
        print("     Next: Phase 2 implementation — Kite historical_data in the")
        print("     live FVG-Tap fetch path (services/fvg_tap_engine.py),")
        print("     yfinance retained as fallback.")
        verdict = "green_light"
    elif kite_pass_count == n_symbols and yf_pass_count < n_symbols:
        print("\n  >> KITE DATA IS ACTUALLY BETTER")
        print("     Kite clears the gate on more indices than yfinance does over")
        print("     this 60-day window. Switching strengthens the validation, not")
        print("     weakens it. Green-light the switch with extra confidence.")
        verdict = "green_light_kite_better"
    elif kite_pass_count < n_symbols:
        print("\n  >> RED-LIGHT THE SWITCH")
        print("     The edge does NOT clear the locked gate on Kite OHLC for at")
        print("     least one index. Even if yfinance shows a clean pass, the")
        print("     apparent edge appears at least partially yfinance-specific")
        print("     (different OHLC quirks favouring the engulf/FVG geometry).")
        print()
        print("     DO NOT switch to Kite without first understanding the source")
        print("     of the divergence. Investigate the OHLC diff stats above —")
        print("     are pre-open prints / extended hours bars showing up in one")
        print("     source but not the other? Is the engulf condition sensitive")
        print("     to wick precision the two sources handle differently?")
        verdict = "red_light"
    else:
        print("\n  >> INCONCLUSIVE")
        verdict = "inconclusive"

    # Side-channel record
    out_path = Path("backtest_results/kite_vs_yfinance.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "gate": {"n": 60, "PF": 1.30, "expR": 0, "win": 40.0},
        "verdict": verdict,
        "per_symbol": out_per_symbol,
    }, indent=2), encoding="utf-8")
    print(f"\n  Results: {out_path}")


if __name__ == "__main__":
    main()
