"""PHASE F-FORENSICS Phase 2/11 — replay the EXACT SMC detector output the
engine saw at scan time for selected real winners/losers. No synthetic data:
fetches real daily OHLC, slices to the real scan date (no look-ahead), runs
the same engine.swing detectors the live scanner uses."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import pandas as pd
from data.ingestion import DataIngestion
from engine.swing import (
    detect_weekly_trend, detect_daily_structure, detect_daily_ob,
    detect_daily_fvg, calculate_relative_strength, swing_volume_signal,
)
from services.research_levels import df_to_candles, daily_candles_to_weekly

# (symbol, scan_date, label, outcome) — pulled from trade_database.csv
CASES = [
    ("NSE:ADANIENT",   "2024-06-03", "BIGGEST LOSER  bear_down",    "STOP -26.8%"),
    ("NSE:AFFLE",      "2025-02-03", "LOSER choppy",                "STOP -16.0%"),
    ("NSE:ADANIPOWER", "2026-01-15", "WINNER bull (target hit)",    "TARGET +19.5%"),
    ("NSE:63MOONS",    "2024-10-01", "BIGGEST WINNER sideways",     "TIME +42.0%"),
]

ing = DataIngestion(source="yfinance")

def slice_to(df, cutoff):
    d = df.copy(); d.columns = [c.lower() for c in d.columns]
    cut = pd.Timestamp(cutoff)
    if "date" not in d.columns and isinstance(d.index, pd.DatetimeIndex):
        d = d.reset_index().rename(columns={d.reset_index().columns[0]: "date"})
        d.columns = [c.lower() for c in d.columns]
    if "date" in d.columns:
        ds = pd.to_datetime(d["date"], errors="coerce", utc=True).dt.tz_convert(None)
        d = d.assign(date=ds)
        return d.loc[ds <= cut].reset_index(drop=True)
    return d


def _ts(v):
    t = pd.Timestamp(v)
    return t.tz_localize(None) if t.tz is not None else t

try:
    nifty = df_to_candles(slice_to(ing.fetch_historical("NSE:NIFTY 50", interval="day", days=900), "2026-05-01"))
    for _c in nifty: _c["date"] = _ts(_c["date"])
except Exception:
    nifty = []

for sym, sd, label, outcome in CASES:
    print("=" * 78)
    print(f"{label}  |  {sym}  scan={sd}  outcome={outcome}")
    print("-" * 78)
    try:
        raw = ing.fetch_historical(sym, interval="day", days=900)
        df = slice_to(raw, sd)
        candles = df_to_candles(df)
        if len(candles) < 60:
            print(f"  insufficient candles ({len(candles)})"); continue
        weekly = daily_candles_to_weekly(candles)
        nf = [c for c in nifty if _ts(c["date"]) <= _ts(sd)] if nifty else []
        wt = detect_weekly_trend(weekly)
        ds, ds_info = detect_daily_structure(candles)
        ob = detect_daily_ob(candles, "LONG")
        fvg = detect_daily_fvg(candles, "LONG")
        rs = calculate_relative_strength(candles, nf) if nf else None
        vs = swing_volume_signal(candles)
        px = candles[-1]["close"]
        print(f"  WHAT THE ENGINE SAW @ scan:")
        print(f"    price            : {px:.1f}")
        print(f"    weekly_trend     : {wt}")
        print(f"    daily_structure  : {ds}  info={ds_info}")
        print(f"    daily_OB (LONG)  : {ob}")
        print(f"    daily_FVG (LONG) : {fvg}")
        print(f"    RS vs NIFTY(10d) : {rs}")
        print(f"    volume_signal    : {vs}")
        # forward path (what actually happened next 20 bars)
        fwd = df_to_candles(slice_to(raw, "2026-05-01"))
        post = [c for c in fwd if _ts(c["date"]) > _ts(sd)][:20]
        if post:
            lo = min(c["low"] for c in post); hi = max(c["high"] for c in post)
            print(f"  NEXT 20 BARS     : low={lo:.1f} ({(lo-px)/px*100:+.1f}%)  high={hi:.1f} ({(hi-px)/px*100:+.1f}%)")
    except Exception as e:
        print(f"  replay failed: {e}")
