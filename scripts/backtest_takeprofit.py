"""
scripts/backtest_takeprofit.py — research only, writes nothing.

Tests a FIXED TAKE-PROFIT (bank the move at +X%) against the real closed-trade
track record, replayed bar-by-bar over daily OHLC.

This is deliberately NOT the rule rejected on 2026-08-18. That was a RATCHETING
TRAIL, and it lost because a trail on 5-8% daily-ATR names gets whipsawed out of
exactly the +14% average winners the book depends on. A fixed target cannot
whipsaw: it either trades through the level or it does not. Whether that is
better is an open question this script answers rather than assumes.

Both traps documented in that write-up are honoured:
  * the peak/exit is replayed BAR BY BAR, never reconstructed from stored MFE
  * a CONTROL run (no take-profit) must reproduce the real outcomes, and its
    drift is printed first — if the control drifts, no other row is believable
  * fills use the daily CLOSE, because the live tracker samples CMP every ~2 min
    and never sees a one-tick intraday spike through a level

    python -m scripts.backtest_takeprofit --source db --db /data/dashboard.db
"""
from __future__ import annotations

import argparse
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_giveback_path import Bar, PathTrade, fetch_bars  # noqa: E402
from scripts.backtest_giveback import Trade, load_trades  # noqa: E402


def simulate_tp(t: Trade, bars: list[Bar], stop_loss: float, target: float | None,
                tp_pct: float | None, partial: bool = False) -> tuple[float, str]:
    """Replay bars with an optional fixed take-profit.

    tp_pct=None  -> CONTROL (original stop + target only).
    partial=True -> bank half at tp_pct, let the rest run to the original target,
                    which is the "capture some, keep optionality" compromise.
    Adverse-first within the bar: stop is always checked before any profit exit,
    so the rule is never flattered.
    """
    entry = t.entry
    tp_price = entry * (1 + tp_pct / 100.0) if tp_pct else None
    banked = 0.0
    size = 1.0

    def pct(px: float) -> float:
        return (px - entry) / entry * 100.0

    for bar in bars:
        px = bar.close                      # close-fill model (see docstring)
        if stop_loss > 0 and px <= stop_loss:
            return round(banked + size * pct(px), 2), "STOP"
        if tp_price and px >= tp_price:
            if partial and size == 1.0:
                banked += 0.5 * pct(px)     # bank half, keep half running
                size = 0.5
            else:
                return round(banked + size * pct(px), 2), "TAKE_PROFIT"
        if target and px >= target:
            return round(banked + size * pct(px), 2), "TARGET"
    return round(banked + size * t.actual_pct, 2) if banked else t.actual_pct, "ACTUAL"


def metrics(vals: list[float]) -> dict:
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "n": len(vals),
        "mean": round(st.mean(vals), 2) if vals else 0.0,
        "median": round(st.median(vals), 2) if vals else 0.0,
        "win_rate": round(len(wins) / len(vals) * 100, 1) if vals else 0.0,
        "pf": round(gp / gl, 2) if gl else float("inf"),
        "avg_win": round(st.mean(wins), 2) if wins else 0.0,
        "avg_loss": round(st.mean(losses), 2) if losses else 0.0,
        "worst": round(min(vals), 2) if vals else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["api", "db", "file"], default="db")
    ap.add_argument("--db", default="/data/dashboard.db")
    ap.add_argument("--api", default=os.getenv("DASHBOARD_URL", ""))
    ap.add_argument("--file", default="journal.json")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--horizon", default=None)
    ap.add_argument("--price-tolerance", type=float, default=25.0)
    ap.add_argument("--cache", default=None)
    args = ap.parse_args()

    trades, skips = load_trades(args)
    if not trades:
        print("no usable trades", file=sys.stderr)
        return 1
    from datetime import datetime, timedelta

    from scripts.backtest_giveback import _rows_from_api, _rows_from_db, _rows_from_file
    raw = (_rows_from_db(args.db) if args.source == "db"
           else _rows_from_api(args.api, args.limit) if args.source == "api"
           else _rows_from_file(args.file))

    # Mirror backtest_giveback_path exactly: key by symbol|created_at, and
    # reconstruct the ENTRY date as closed_at - days_held (created_at is ARM
    # time; replaying from it stops trades out before they existed).
    by_key = {}
    for r in raw:
        if r.get("is_duplicate"):
            continue
        by_key[f"{r.get('symbol')}|{r.get('created_at')}"] = r

    windows = {}
    for key, r in by_key.items():
        end = str(r.get("closed_at") or "")[:10]
        if not end:
            continue
        try:
            closed = datetime.fromisoformat(end)
            start = (closed - timedelta(days=int(r.get("days_held") or 0))).date().isoformat()
        except Exception:
            continue
        windows[key] = (start, end)

    bars_by_key = fetch_bars(trades, windows, args.cache)

    usable = []
    stops = {}
    dropped = {}
    for t in trades:
        key = next((k for k, r in by_key.items()
                    if r.get("symbol") == t.symbol
                    and abs(float(r.get("entry_price") or 0) - t.entry) < 1e-6), None)
        if key is None or key not in bars_by_key:
            dropped["no_bars"] = dropped.get("no_bars", 0) + 1
            continue
        bars = bars_by_key[key]
        lo = min(b.low for b in bars); hi = max(b.high for b in bars)
        if not (lo * (1 - args.price_tolerance / 100) <= t.entry <= hi * (1 + args.price_tolerance / 100)):
            dropped["price_unreconcilable"] = dropped.get("price_unreconcilable", 0) + 1
            continue
        r = by_key[key]
        try:
            sl = float(r.get("stop_loss") or 0)
        except (TypeError, ValueError):
            sl = 0.0
        tgt = r.get("target_1")
        try:
            tgt = float(tgt) if tgt else None
        except (TypeError, ValueError):
            tgt = None
        stops[len(usable)] = (sl, tgt)
        usable.append((len(usable), PathTrade(trade=t, bars=bars)))

    if not usable:
        print("No trades survived bar reconciliation.", file=sys.stderr)
        return 1
    print(f"trades={len(trades)} usable_with_bars={len(usable)} dropped={dropped}")

    actual = [p.trade.actual_pct for _, p in usable]
    print("REAL (what actually happened):", metrics(actual))

    control = [simulate_tp(p.trade, p.bars, *stops[i], None)[0] for i, p in usable]
    cm, am = metrics(control), metrics(actual)
    drift = round(cm["mean"] - am["mean"], 3)
    print(f"CONTROL (replay, no TP)     : {cm}")
    print(f"  >> control drift vs real  : {drift:+.3f} pp "
          f"{'PASS' if abs(drift) < 0.5 else 'FAIL - do not trust rows below'}\n")

    print(f"{'policy':<26}{'n':>5}{'mean':>8}{'median':>8}{'win%':>7}{'PF':>7}{'avgWin':>8}{'avgLoss':>9}{'worst':>8}")
    print("-" * 86)
    print(f"{'CONTROL (as-is)':<26}{cm['n']:>5}{cm['mean']:>8}{cm['median']:>8}{cm['win_rate']:>7}{cm['pf']:>7}{cm['avg_win']:>8}{cm['avg_loss']:>9}{cm['worst']:>8}")
    for tp in (8, 10, 12, 15, 20, 25):
        for partial in (False, True):
            vals = [simulate_tp(p.trade, p.bars, *stops[i], tp, partial)[0] for i, p in usable]
            m = metrics(vals)
            name = f"{'half at' if partial else 'full at'} +{tp}%"
            print(f"{name:<26}{m['n']:>5}{m['mean']:>8}{m['median']:>8}{m['win_rate']:>7}{m['pf']:>7}{m['avg_win']:>8}{m['avg_loss']:>9}{m['worst']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
