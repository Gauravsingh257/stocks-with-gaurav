"""
OHLC-backed trade levels for AI Research Center (replaces hash-based placeholders).

Uses engine.swing.score_swing_candidate when possible; otherwise ATR-based fallback.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

from engine.indicators import calculate_atr
from engine.swing import detect_weekly_trend, score_swing_candidate

log = logging.getLogger("services.research_levels")

NIFTY_DAILY_SYMBOL = os.getenv("RESEARCH_NIFTY_SYMBOL", "NSE:NIFTY 50")
RESEARCH_MAX_ENTRY_VS_CLOSE_PCT = float(os.getenv("RESEARCH_MAX_ENTRY_VS_CLOSE_PCT", "0.30"))
RESEARCH_POOL_MULT = max(3, int(os.getenv("RESEARCH_POOL_MULT", "5")))


def df_to_candles(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Normalize DataIngestion DataFrame to list[dict] for engine.swing."""
    if df is None or df.empty:
        return []
    frame = df.copy()
    if "date" not in frame.columns:
        if frame.index.name in ("date", "Date") or isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index()
            frame.rename(columns={frame.columns[0]: "date"}, inplace=True)
    colmap = {c: c.lower() for c in frame.columns}
    frame = frame.rename(columns=colmap)
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(frame.columns)):
        return []
    out: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        out.append(
            {
                "date": row.get("date"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]) if "volume" in frame.columns and pd.notna(row.get("volume")) else 0.0,
            }
        )
    return out


def daily_candles_to_weekly(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate daily OHLCV to weekly (Friday-aligned)."""
    if len(candles) < 5:
        return []
    pdf = pd.DataFrame(candles)
    pdf["date"] = pd.to_datetime(pdf["date"], errors="coerce")
    pdf = pdf.dropna(subset=["date"])
    if pdf.empty:
        return []
    pdf = pdf.set_index("date").sort_index()
    w = pdf.resample("W-FRI").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    w = w.dropna(subset=["close"])
    w = w.reset_index()
    return df_to_candles(w)


def entry_vs_close_sane(entry: float, close: float) -> bool:
    if close <= 0:
        return False
    return abs(entry - close) / close <= RESEARCH_MAX_ENTRY_VS_CLOSE_PCT


def _split_targets(direction: str, entry: float, target: float) -> tuple[float, float]:
    """Derive T1 (partial) and T2 (full SMC target) from single swing target."""
    if direction == "LONG":
        span = target - entry
        t1 = round(entry + span * 0.55, 2)
        t2 = round(target, 2)
    else:
        span = entry - target
        t1 = round(entry - span * 0.55, 2)
        t2 = round(target, 2)
    return t1, t2


def atr_fallback_levels(
    symbol: str,
    candles: list[dict[str, Any]],
) -> tuple[float, float, list[float], str] | None:
    """When score_swing_candidate fails quality gates, use ATR / structure-neutral plan at CMP."""
    if len(candles) < 30:
        return None
    close = candles[-1]["close"]
    atr = calculate_atr(candles, 14)
    if atr <= 0 or close <= 0:
        return None
    window = min(20, len(candles))
    sma = sum(c["close"] for c in candles[-window:]) / window
    direction = "LONG" if close >= sma else "SHORT"
    min_risk = max(atr * 1.5, close * 0.03)
    if direction == "LONG":
        entry = round(close, 2)
        sl = round(entry - min_risk, 2)
        if sl >= entry:
            return None
        r = entry - sl
        t1 = round(entry + r * 1.5, 2)
        t2 = round(entry + r * 3.0, 2)
        setup = "ATR_FALLBACK_LONG"
    else:
        entry = round(close, 2)
        sl = round(entry + min_risk, 2)
        if sl <= entry:
            return None
        r = sl - entry
        t1 = round(entry - r * 1.5, 2)
        t2 = round(entry - r * 3.0, 2)
        setup = "ATR_FALLBACK_SHORT"
    if not entry_vs_close_sane(entry, close):
        return None
    return entry, sl, [t1, t2], setup


def build_swing_trade_levels(
    symbol: str,
    daily_df: pd.DataFrame,
    nifty_daily: list[dict[str, Any]],
) -> tuple[float, float, list[float], str, dict[str, Any] | None] | None:
    """
    Returns (entry, stop_loss, [t1, t2], setup_label, smc_meta) or None.
    smc_meta is the raw score_swing_candidate result dict (has real reasons, rs, research etc.)
    when the SMC path was used; None for ATR fallback.
    """
    candles = df_to_candles(daily_df)
    if len(candles) < 30:
        return None
    close = candles[-1]["close"]
    weekly = daily_candles_to_weekly(candles)
    sw = score_swing_candidate(symbol, candles, weekly, nifty_daily)
    if sw:
        entry = float(sw["entry"])
        sl = float(sw["sl"])
        target = float(sw["target"])
        direction = str(sw["direction"])
        if not entry_vs_close_sane(entry, close):
            log.debug("Swing levels failed CMP sanity for %s", symbol)
            return None
        t1, t2 = _split_targets(direction, entry, target)
        wt = sw.get("weekly_trend", "?")
        ds = sw.get("daily_structure", "?")
        setup = f"SMC_SWING_{wt}_{ds}"
        return entry, sl, [t1, t2], setup, sw

    fb = atr_fallback_levels(symbol, candles)
    if fb:
        entry, sl, targets, setup = fb
        return entry, sl, targets, setup, None
    return None


def _find_weekly_demand_zone(weekly: list[dict[str, Any]], atr: float) -> tuple[float, float] | None:
    """Find the most recent weekly demand zone (swing low cluster) within 20% of CMP."""
    if len(weekly) < 8:
        return None
    close = weekly[-1]["close"]
    # Find recent weekly swing lows (local lows surrounded by higher lows)
    lows = []
    for i in range(2, len(weekly) - 1):
        if weekly[i]["low"] < weekly[i - 1]["low"] and weekly[i]["low"] < weekly[i + 1]["low"]:
            lows.append(weekly[i]["low"])
    if not lows:
        return None
    # Pick the nearest swing low that is below CMP and within 20%
    candidates = sorted([l for l in lows if l < close and (close - l) / close < 0.20], reverse=True)
    if not candidates:
        return None
    zone_low = candidates[0]
    zone_high = round(zone_low + atr, 2)
    return round(zone_low, 2), min(zone_high, round(close * 0.985, 2))


def _find_key_resistance(weekly: list[dict[str, Any]]) -> float | None:
    """Find resistance level (recent swing high above CMP for target projection)."""
    if len(weekly) < 8:
        return None
    close = weekly[-1]["close"]
    highs = []
    for i in range(2, len(weekly) - 1):
        if weekly[i]["high"] > weekly[i - 1]["high"] and weekly[i]["high"] > weekly[i + 1]["high"]:
            highs.append(weekly[i]["high"])
    # Nearest swing high above CMP up to 40% away
    candidates = sorted([h for h in highs if h > close and (h - close) / close < 0.40])
    return round(candidates[0], 2) if candidates else None


def build_longterm_trade_levels(
    daily_df: pd.DataFrame,
) -> tuple[float, float, list[float], float, list[float], str] | None:
    """
    Long-horizon levels from weekly structure:
    - Entry zone: nearest weekly demand zone (swing low cluster) or pullback level
    - Stop: below zone with ATR buffer (respects weekly structure)
    - Target: nearest weekly resistance or 15% min appreciation
    """
    candles = df_to_candles(daily_df)
    if len(candles) < 60:
        return None
    close = candles[-1]["close"]
    atr = calculate_atr(candles, 14)
    if close <= 0 or atr <= 0:
        return None
    weekly = daily_candles_to_weekly(candles)
    wt = detect_weekly_trend(weekly) if len(weekly) >= 12 else "NEUTRAL"

    # 52W stats for context
    lookback = min(252, len(candles))
    hi52 = max(c["high"] for c in candles[-lookback:])
    lo52 = min(c["low"] for c in candles[-lookback:])

    # Entry zone: demand zone from weekly swing lows, or ATR-based pullback from CMP
    demand_zone = _find_weekly_demand_zone(weekly, atr)
    if demand_zone:
        zone_low, zone_high = demand_zone
        entry = round((zone_low + zone_high) / 2, 2)
        entry_zone = [zone_low, zone_high]
        stop = round(zone_low - 1.5 * atr, 2)
    else:
        # No weekly demand zone: use 2-ATR pullback entry below CMP
        entry = round(close - 2 * atr, 2)
        entry_zone = [round(entry - atr, 2), round(entry + atr * 0.5, 2)]
        stop = round(entry - 2.5 * atr, 2)

    # Target: nearest key resistance above CMP, or 15% gain minimum
    resistance = _find_key_resistance(weekly)
    min_target = round(close * 1.15, 2)
    long_target = max(resistance or 0, min_target)
    long_target = round(long_target, 2)

    # Ensure positive R/R ≥ 2
    risk = abs(entry - stop)
    reward = abs(long_target - entry)
    if risk <= 0 or reward / risk < 1.5:
        # Recalculate with 15% target and ATR stop
        entry = round(close, 2)
        stop = round(close - 3 * atr, 2)
        long_target = round(close * 1.20, 2)
        entry_zone = [round(close - atr, 2), round(close, 2)]

    # Sanity: entry must relate to close (long-term entry can be up to 25% below CMP)
    if close > 0 and abs(entry - close) / close > 0.30:
        entry = round(close - 2 * atr, 2)

    setup_note = f"LONGTERM_{wt}_52W({round((close-lo52)/lo52*100,0):.0f}%aboveLow)"
    return entry, stop, [long_target], long_target, entry_zone, setup_note
