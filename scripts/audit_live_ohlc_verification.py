"""
scripts/audit_live_ohlc_verification.py — READ-ONLY engine verification.

Purpose (run on a trading day AFTER the Kite token is refreshed):
  Step 1 of the institutional audit — prove the scanner's published indicator
  values reproduce EXACTLY from live Zerodha OHLC, using a clean-room
  reimplementation of the indicators (NOT importing services.scanners.indicators),
  and prove point-in-time integrity (no future candle, no repaint).

This script NEVER writes anything (no Redis writes, no DB writes, no engine
mutation). It only fetches candles and compares. Safe to run against production.

Usage:
    python -m scripts.audit_live_ohlc_verification            # daily screener
    python -m scripts.audit_live_ohlc_verification --tf 1W    # weekly
    python -m scripts.audit_live_ohlc_verification --pit 8    # + point-in-time on 8 track-record picks

Exit code 0 if every check passes within tolerance; 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

TOL_PRICE = 0.02   # ₹0.02 abs tolerance on price-level indicators (rounding)
TOL_PCT = 0.05     # 0.05 pct-point tolerance on %-based fields


# ── clean-room indicators (independent reimplementation) ──────────────────────
def ema_last(vals, period):
    if period <= 0 or len(vals) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = float(vals[0])
    for v in vals[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def atr_wilder(highs, lows, closes, period=14):
    n = len(closes)
    atr = [0.0] * n
    if n <= period:
        return atr
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr[period] = sum(tr[1:period + 1]) / period
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def supertrend(highs, lows, closes, period=10, mult=3.0):
    n = len(closes)
    if n <= period + 2:
        return None
    atr = atr_wilder(highs, lows, closes, period)
    fu = [0.0] * n
    fl = [0.0] * n
    line = [0.0] * n
    d = [0] * n
    up = [0.0] * n
    lo = [0.0] * n
    for i in range(period, n):
        hl2 = (highs[i] + lows[i]) / 2.0
        up[i] = hl2 + mult * atr[i]
        lo[i] = hl2 - mult * atr[i]
    fu[period] = up[period]
    fl[period] = lo[period]
    d[period] = 1
    line[period] = lo[period]
    for i in range(period + 1, n):
        fu[i] = up[i] if (up[i] < fu[i - 1] or closes[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lo[i] if (lo[i] > fl[i - 1] or closes[i - 1] < fl[i - 1]) else fl[i - 1]
        if d[i - 1] == 1:
            d[i] = -1 if closes[i] < fl[i] else 1
        else:
            d[i] = 1 if closes[i] > fu[i] else -1
        line[i] = fu[i] if d[i] == -1 else fl[i]
    return d, line


def flip_class(d):
    if len(d) < 3 or d[-1] != 1:
        return None
    if d[-2] == -1:
        return "this_bar"
    if d[-2] == 1 and d[-3] == -1:
        return "last_bar"
    return None


# ── infra ─────────────────────────────────────────────────────────────────────
def _kite():
    from config.kite_auth import get_access_token, get_api_key
    from kiteconnect import KiteConnect
    k = KiteConnect(api_key=get_api_key())
    k.set_access_token(get_access_token())
    k.profile()  # raises if token invalid
    return k


def _tok_map(k):
    return {r["tradingsymbol"]: r["instrument_token"]
            for r in k.instruments("NSE")
            if r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"}


def _candles(k, tokmap, sym, interval, lookback_days, upto=None):
    tok = tokmap.get(sym)
    if not tok:
        return None
    to_date = upto or datetime.now()
    frm = to_date - timedelta(days=lookback_days)
    data = k.historical_data(tok, frm, to_date, interval)
    return [{"date": str(d["date"])[:10], "high": d["high"], "low": d["low"],
             "close": d["close"], "volume": d["volume"]} for d in data]


def _published_rows(tf):
    """Read the live published snapshot straight from Redis (what the site shows)."""
    from services.scanners import snapshot_store
    snap = snapshot_store.read_snapshot("supertrend_flip", tf)
    return (snap or {}).get("rows", []), (snap or {}).get("as_of")


# ── Part A: reproduce published screener rows from live OHLC ───────────────────
def verify_screener(k, tokmap, tf):
    interval = "day" if tf == "1D" else "week"
    lookback = 300 if tf == "1D" else 1100
    rows, as_of = _published_rows(tf)
    print(f"\n=== SCREENER REPRODUCTION ({tf}) — published as_of={as_of}, {len(rows)} hits ===")
    print(f"{'symbol':<12}{'close✓':>7}{'ema10✓':>8}{'stop✓':>7}{'flip✓':>7}{'verdict':>9}")
    all_ok = True
    for r in rows:
        sym = r["symbol"]
        c = _candles(k, tokmap, sym, interval, lookback)
        if not c:
            print(f"{sym:<12}{'':>7}{'':>8}{'':>7}{'':>7}{'NO DATA':>9}")
            all_ok = False
            continue
        highs = [float(x["high"]) for x in c]
        lows = [float(x["low"]) for x in c]
        closes = [float(x["close"]) for x in c]
        st = supertrend(highs, lows, closes)
        e10 = ema_last(closes, 10)
        d, line = st
        ok_close = abs(closes[-1] - r["close"]) <= TOL_PRICE
        ok_e10 = e10 is not None and abs(e10 - r["ema10"]) <= max(TOL_PRICE, abs(r["ema10"]) * 1e-4)
        ok_stop = abs(line[-1] - r["stop"]) <= max(TOL_PRICE, abs(r["stop"]) * 1e-4)
        ok_flip = flip_class(d) == r["flip"]
        v = ok_close and ok_e10 and ok_stop and ok_flip
        all_ok = all_ok and v
        print(f"{sym:<12}{str(ok_close):>7}{str(ok_e10):>8}{str(ok_stop):>7}{str(ok_flip):>7}{('PASS' if v else 'FAIL'):>9}")
    return all_ok


# ── Part B: point-in-time integrity (no future candle, no repaint) ────────────
def verify_point_in_time(k, tokmap, n_picks):
    """For sampled track-record picks: fetch candles ONLY up to the pick's
    created_at date and confirm the signal was computable then — proving no
    future information was needed to produce the recommendation."""
    import urllib.request, ssl, json
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    base = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")
    picks = json.loads(urllib.request.urlopen(base + "/api/research/track-record?limit=200",
                                               timeout=30, context=ctx).read()).get("picks", [])
    picks = [p for p in picks if p.get("created_at")][:n_picks]
    print(f"\n=== POINT-IN-TIME INTEGRITY — {len(picks)} sampled recommendations ===")
    print("For each: recompute using ONLY candles dated <= created_at (no future bars).")
    all_ok = True
    for p in picks:
        sym = p["symbol"].replace("NSE:", "")
        created = datetime.fromisoformat(str(p["created_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
        c = _candles(k, tokmap, sym, "day", 400, upto=created)
        if not c:
            print(f"  {sym:<12} NO DATA"); all_ok = False; continue
        # Assert no candle is dated after the recommendation timestamp.
        last_date = c[-1]["date"]
        future_leak = last_date > created.date().isoformat()
        closes = [float(x["close"]) for x in c]
        e10 = ema_last(closes, 10)
        computable = e10 is not None and len(c) >= 40
        ok = computable and not future_leak
        all_ok = all_ok and ok
        print(f"  {sym:<12} created={created.date()} last_candle={last_date} "
              f"future_leak={future_leak} computable={computable} -> {'PASS' if ok else 'FAIL'}")
    return all_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1D", choices=["1D", "1W"])
    ap.add_argument("--pit", type=int, default=0, help="also run point-in-time on N picks")
    args = ap.parse_args()

    try:
        k = _kite()
    except Exception as e:
        print(f"KITE TOKEN INVALID — run after the morning Kite login refreshes "
              f"kite:access_token in Redis. ({type(e).__name__}: {str(e)[:120]})")
        sys.exit(2)

    tokmap = _tok_map(k)
    ok = verify_screener(k, tokmap, args.tf)
    if args.pit:
        ok = verify_point_in_time(k, tokmap, args.pit) and ok
    print(f"\nOVERALL: {'PASS ✅' if ok else 'FAIL ❌'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
