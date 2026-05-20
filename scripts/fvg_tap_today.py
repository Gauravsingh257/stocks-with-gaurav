"""
scripts/fvg_tap_today.py
========================
Did the EXACT live FVG-Tap rule trigger anywhere on TODAY's NIFTY +
BANKNIFTY 5m? Replays the production detector
(engine.fvg_tap_setup.evaluate_fvg_tap) bar-by-bar across today's full
yfinance 5m data — no look-ahead, identical decision logic to what
runs every 5 min on Railway. Reports the exact bar/time/price of every
signal that would have fired today, or confirms 'no trigger today'.

Run locally:
    python scripts/fvg_tap_today.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.fvg_tap_setup import evaluate_fvg_tap

YF = {"NIFTY 50 (^NSEI)": "^NSEI", "BANKNIFTY (^NSEBANK)": "^NSEBANK"}


def _load(ticker: str):
    import yfinance as yf
    df = yf.download(ticker, interval="5m", period="5d",
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
        o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
        if not (o > 0 and h > 0 and l > 0 and c > 0):
            continue
        out.append({"date": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                    "open": o, "high": h, "low": l, "close": c,
                    "volume": int(getattr(row, "Volume", 0) or 0)})
    return out


def main() -> None:
    today = date.today()
    print("=" * 72)
    print(f"  FVG-TAP — did it trigger today ({today})?")
    print("  Same production detector (engine.fvg_tap_setup.evaluate_fvg_tap)")
    print("  evaluated bar-by-bar against today's full 5m data.")
    print("=" * 72)

    for label, ticker in YF.items():
        try:
            bars = _load(ticker)
        except Exception as exc:
            print(f"\n  {label}: data fetch failed: {exc}")
            continue
        if not bars:
            print(f"\n  {label}: no data")
            continue

        # Index of the first bar that belongs to today
        today_idx = next((i for i, b in enumerate(bars)
                          if b["date"].startswith(str(today))), None)
        if today_idx is None:
            print(f"\n  {label}: no bars today (market closed / yfinance lag)")
            continue

        last_idx = len(bars) - 1
        todays_n = last_idx - today_idx + 1
        print(f"\n  {label}: {todays_n} 5m bars today "
              f"({bars[today_idx]['date'][11:16]} → "
              f"{bars[last_idx]['date'][11:16]})")

        fires = []
        # For each closed bar TODAY, run evaluator with history up to and
        # including that bar — exactly what the live tick does each 5 min.
        for i in range(today_idx, last_idx + 1):
            window = bars[: i + 1]
            for direction in ("LONG", "SHORT"):
                sig = evaluate_fvg_tap(window, direction)
                if sig:
                    fires.append((bars[i]["date"], direction, sig))

        if not fires:
            print(f"    → NO FVG-Tap signal triggered today on {label}.")
            print(f"      (the 4-condition sequence — 5m CHoCH → FVG → "
                  f"first tap → engulfing — was not satisfied at any bar)")
            continue

        print(f"    → {len(fires)} FVG-Tap signal(s) would have fired today:")
        for ts, direction, sig in fires:
            print(f"      [{ts[11:16]}] {direction}  entry={sig['entry']}  "
                  f"SL={sig['sl']}  T={sig['target']}  RR={sig['rr']}  "
                  f"FVG=[{sig['fvg_low']}..{sig['fvg_high']}]")

    print("\n" + "=" * 72)
    print("  Honest read: a 'no trigger' result is the rule working as")
    print("  designed — the 4-condition setup is rare; many days fire zero.")
    print("=" * 72)


if __name__ == "__main__":
    main()
