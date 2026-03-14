"""
engine/liquidity_engine.py
Phase 5: Standalone Liquidity Sweep Detection Module

Detects:
  - Equal Highs / Equal Lows (within tolerance) → hunt zones
  - Session sweep (previous session high/low taken)
  - Previous-close sweep

Returns a standard LiquiditySweep dict:
  {
    "type":       str  — "EQUAL_HIGHS" | "EQUAL_LOWS" | "SESSION_SWEEP_HIGH" |
                         "SESSION_SWEEP_LOW" | "PREV_CLOSE_SWEEP",
    "level":      float,   # the swept price level
    "strength":   float,   # 0.0–1.0 normalised
    "bar_index":  int,     # index of the sweep candle in the supplied list
  }

Returns None if no sweep found.
"""

from __future__ import annotations
from typing import Optional


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(
    candles: list,
    lookback: int = 50,
    eq_tolerance_pct: float = 0.2,
) -> Optional[dict]:
    """
    Main entry-point.  Checks for the three sweep variants in priority order:
      1. Equal highs / equal lows             (highest priority — engineered sweep)
      2. Session high / low sweep             (macro level taken)
      3. Previous-close sweep                 (common intraday magnet)

    Parameters
    ----------
    candles         : list of OHLCV dicts with keys date/open/high/low/close/volume
    lookback        : how many candles to inspect
    eq_tolerance_pct: how close two highs/lows must be (% of price) to be "equal"

    Returns
    -------
    dict or None
    """
    if not candles or len(candles) < 10:
        return None

    window = candles[-lookback:] if len(candles) > lookback else candles

    # Try in priority order
    result = _detect_equal_highs(window, eq_tolerance_pct)
    if result:
        return result

    result = _detect_equal_lows(window, eq_tolerance_pct)
    if result:
        return result

    result = _detect_session_sweep(window)
    if result:
        return result

    result = _detect_prev_close_sweep(window)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_equal_highs(candles: list, tol_pct: float = 0.2) -> Optional[dict]:
    """
    Finds a liquidity sweep of equal (clustered) highs.

    Algorithm:
      - Walk backward from the last bar.
      - Collect all candle highs that are within `tol_pct`% of each other.
      - If >= 2 such highs exist (a pool), check whether a later candle swept
        (traded above) that pool and then CLOSED BACK BELOW it (rejection wick).

    Returns a sweep dict if found, else None.
    """
    if len(candles) < 6:
        return None

    # Build candidate pool from [0 .. -3] (leave last 3 candles as sweepers)
    pool_range = candles[:-3]
    sweep_range = candles[-3:]

    for pivot_idx in range(len(pool_range) - 1, max(len(pool_range) - 20, -1), -1):
        pivot_high = pool_range[pivot_idx]["high"]
        tol = pivot_high * tol_pct / 100

        # Find another candle within tolerance
        matches = [
            i for i in range(len(pool_range))
            if i != pivot_idx and abs(pool_range[i]["high"] - pivot_high) <= tol
        ]
        if not matches:
            continue

        # Pool level = average of matched highs
        pool_level = (pivot_high + sum(pool_range[m]["high"] for m in matches)) / (len(matches) + 1)

        # Check if any sweep-range candle swept above and closed below (rejection)
        for s_idx, s_candle in enumerate(sweep_range):
            if s_candle["high"] > pool_level and s_candle["close"] < pool_level:
                abs_bar_idx = len(candles) - 3 + s_idx
                # Strength: how far above the pool the wick reached
                wick_ext = s_candle["high"] - pool_level
                body_range = abs(s_candle["high"] - s_candle["low"]) or 1
                strength = min(1.0, wick_ext / body_range)
                return {
                    "type": "EQUAL_HIGHS",
                    "level": round(pool_level, 2),
                    "strength": round(strength, 3),
                    "bar_index": abs_bar_idx,
                }

    return None


def _detect_equal_lows(candles: list, tol_pct: float = 0.2) -> Optional[dict]:
    """
    Symmetric counterpart of _detect_equal_highs — sweeps of clustered lows.
    """
    if len(candles) < 6:
        return None

    pool_range = candles[:-3]
    sweep_range = candles[-3:]

    for pivot_idx in range(len(pool_range) - 1, max(len(pool_range) - 20, -1), -1):
        pivot_low = pool_range[pivot_idx]["low"]
        tol = pivot_low * tol_pct / 100

        matches = [
            i for i in range(len(pool_range))
            if i != pivot_idx and abs(pool_range[i]["low"] - pivot_low) <= tol
        ]
        if not matches:
            continue

        pool_level = (pivot_low + sum(pool_range[m]["low"] for m in matches)) / (len(matches) + 1)

        for s_idx, s_candle in enumerate(sweep_range):
            if s_candle["low"] < pool_level and s_candle["close"] > pool_level:
                abs_bar_idx = len(candles) - 3 + s_idx
                wick_ext = pool_level - s_candle["low"]
                body_range = abs(s_candle["high"] - s_candle["low"]) or 1
                strength = min(1.0, wick_ext / body_range)
                return {
                    "type": "EQUAL_LOWS",
                    "level": round(pool_level, 2),
                    "strength": round(strength, 3),
                    "bar_index": abs_bar_idx,
                }

    return None


def _detect_session_sweep(candles: list) -> Optional[dict]:
    """
    Detects a sweep of the previous day's high or low.

    Uses the first candle of today's session versus yesterday's extremes:
      - If today's bar wicks below yesterday's low and closes above it → SWEEP_LOW
      - If today's bar wicks above yesterday's high and closes below it → SWEEP_HIGH
    """
    if len(candles) < 3:
        return None

    try:
        from datetime import datetime as _dt

        today = candles[-1]["date"].date()
        # Collect yesterday's candles
        yesterday = [c for c in candles if c["date"].date() < today]
        today_candles = [c for c in candles if c["date"].date() == today]

        if not yesterday or not today_candles:
            return None

        prev_high = max(c["high"] for c in yesterday)
        prev_low = min(c["low"] for c in yesterday)

        for idx, tc in enumerate(today_candles):
            # Sweep of previous session LOW
            if tc["low"] < prev_low and tc["close"] > prev_low:
                abs_idx = len(candles) - len(today_candles) + idx
                wick = prev_low - tc["low"]
                body = abs(tc["high"] - tc["low"]) or 1
                return {
                    "type": "SESSION_SWEEP_LOW",
                    "level": round(prev_low, 2),
                    "strength": round(min(1.0, wick / body), 3),
                    "bar_index": abs_idx,
                }
            # Sweep of previous session HIGH
            if tc["high"] > prev_high and tc["close"] < prev_high:
                abs_idx = len(candles) - len(today_candles) + idx
                wick = tc["high"] - prev_high
                body = abs(tc["high"] - tc["low"]) or 1
                return {
                    "type": "SESSION_SWEEP_HIGH",
                    "level": round(prev_high, 2),
                    "strength": round(min(1.0, wick / body), 3),
                    "bar_index": abs_idx,
                }
    except Exception:
        pass

    return None


def _detect_prev_close_sweep(candles: list) -> Optional[dict]:
    """
    Detects a sweep of the previous candle's close (common intraday trap).

    Looks at the last 5 candles:
      - Short wick below prev close + close above = bull sweep
      - Short wick above prev close + close below = bear sweep
    """
    if len(candles) < 5:
        return None

    for i in range(-1, -5, -1):
        c = candles[i]
        prev_close = candles[i - 1]["close"]

        # Bullish sweep of prev close
        if c["low"] < prev_close and c["close"] > prev_close:
            wick = prev_close - c["low"]
            body = abs(c["high"] - c["low"]) or 1
            strength = min(1.0, wick / body)
            if strength > 0.1:
                return {
                    "type": "PREV_CLOSE_SWEEP",
                    "level": round(prev_close, 2),
                    "strength": round(strength, 3),
                    "bar_index": len(candles) + i,
                }
        # Bearish sweep of prev close
        if c["high"] > prev_close and c["close"] < prev_close:
            wick = c["high"] - prev_close
            body = abs(c["high"] - c["low"]) or 1
            strength = min(1.0, wick / body)
            if strength > 0.1:
                return {
                    "type": "PREV_CLOSE_SWEEP",
                    "level": round(prev_close, 2),
                    "strength": round(strength, 3),
                    "bar_index": len(candles) + i,
                }

    return None
