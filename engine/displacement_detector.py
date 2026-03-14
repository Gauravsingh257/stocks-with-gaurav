"""
engine/displacement_detector.py
=====================================
Early Displacement Detection Module
Phase 1 of the Institutional SMC Pipeline Upgrade

Purpose:
    Detect strong institutional momentum shifts BEFORE CHoCH occurs.
    Identifies displacement candles (large body impulse moves with FVG creation)
    that signal smart money activity 30–40 minutes earlier than classic CHOCH.

Pipeline position:
    Liquidity Sweep → [DISPLACEMENT DETECTION] → CHoCH → BOS → OB+FVG → Entry

Returns:
    {
        direction    : "bullish" | "bearish"
        strength     : "weak" | "medium" | "strong"
        created_fvg  : bool
        timestamp    : datetime
        atr_ratio    : float  (candle_range / ATR — e.g. 2.3 means 2.3× ATR)
        body_ratio   : float  (body / full_range — 0.0–1.0)
        confidence   : "low" | "medium" | "high"
        candle_idx   : int    (index in input list)
        price        : float  (close of the displacement candle)
    }

Exposes:
    detect_displacement(candles, atr_mult=1.8, body_ratio_min=0.7) -> dict | None
    detect_displacement_sequence(candles) -> list[dict]
    DISPLACEMENT_EVENTS  — deque(maxlen=200) storing recent events from live engine
"""

import logging
from collections import deque
from datetime import datetime
from typing import Optional

logger = logging.getLogger("DisplacementDetector")

# ---------------------------------------------------------------------------
# Module-level event buffer (live engine writes here)
# ---------------------------------------------------------------------------
DISPLACEMENT_EVENTS: deque = deque(maxlen=200)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_ATR_PERIOD        = 14
_ATR_MULT_WEAK     = 1.8      # minimum: candle_range > 1.8 × ATR
_ATR_MULT_MEDIUM   = 2.5      # medium: candle_range > 2.5 × ATR
_ATR_MULT_STRONG   = 3.5      # strong: candle_range > 3.5 × ATR
_BODY_RATIO_MIN    = 0.70     # body must be at least 70% of full range
_LOOKBACK_BARS     = 5        # scan last N candles for displacement


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _calculate_atr(candles: list, period: int = _ATR_PERIOD) -> float:
    """Simple ATR (Average True Range) — average of last `period` true ranges."""
    if len(candles) < period + 1:
        if len(candles) < 2:
            return 0.0
        period = len(candles) - 1

    true_ranges = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


def _classify_strength(range_val: float, atr: float) -> str:
    """Classify displacement strength based on candle range vs ATR."""
    if atr <= 0:
        return "weak"
    ratio = range_val / atr
    if ratio >= _ATR_MULT_STRONG:
        return "strong"
    elif ratio >= _ATR_MULT_MEDIUM:
        return "medium"
    return "weak"


def _classify_confidence(strength: str, created_fvg: bool, near_sweep: bool) -> str:
    """
    Confidence level based on:
      - displacement strength
      - whether an FVG imbalance was created
      - whether a liquidity sweep was recently detected
    """
    points = 0
    if strength == "strong":
        points += 3
    elif strength == "medium":
        points += 2
    else:
        points += 1

    if created_fvg:
        points += 1

    if near_sweep:
        points += 2   # major boost — sweep + displacement = institutional footprint

    if points >= 5:
        return "high"
    elif points >= 3:
        return "medium"
    return "low"


def _fvg_created(candles: list, idx: int) -> bool:
    """
    Check if the candle at `idx` created a Fair Value Gap (3-candle imbalance).
    
    Bullish FVG: candle[idx-1].high < candle[idx+1].low  — gap above previous
    Bearish FVG: candle[idx-1].low  > candle[idx+1].high — gap below previous

    Requires both idx-1 and idx+1 to exist.
    """
    if idx < 1 or idx + 1 >= len(candles):
        return False

    prev  = candles[idx - 1]
    curr  = candles[idx]
    nxt   = candles[idx + 1]

    # Bullish FVG (displacement candle moves up strongly)
    if curr["close"] > curr["open"]:        # bullish candle
        return prev["high"] < nxt["low"]    # true gap above prev high

    # Bearish FVG (displacement candle moves down strongly)
    if curr["close"] < curr["open"]:        # bearish candle
        return prev["low"] > nxt["high"]    # true gap below prev low

    return False


def _analyse_candle(candle: dict, atr: float, idx: int, candles: list) -> Optional[dict]:
    """
    Analyse one candle for displacement characteristics.
    Returns a displacement dict or None.
    """
    if atr <= 0:
        return None

    high  = candle["high"]
    low   = candle["low"]
    open_ = candle["open"]
    close = candle["close"]

    candle_range = high - low
    body = abs(close - open_)

    # Guard: skip flat/doji candles
    if candle_range < 0.001:
        return None

    body_ratio = body / candle_range

    # Gate 1: rangefilter — must be wider than ATR * minimum multiplier
    if candle_range < atr * _ATR_MULT_WEAK:
        return None

    # Gate 2: body dominance — must have strong directional body (not just wicks)
    if body_ratio < _BODY_RATIO_MIN:
        return None

    # All gates passed → displacement confirmed
    direction  = "bullish" if close > open_ else "bearish"
    strength   = _classify_strength(candle_range, atr)
    created    = _fvg_created(candles, idx)

    ts = candle.get("date", datetime.now())
    if not isinstance(ts, datetime):
        try:
            ts = datetime.fromisoformat(str(ts))
        except Exception:
            ts = datetime.now()

    return {
        "direction"  : direction,
        "strength"   : strength,
        "created_fvg": created,
        "timestamp"  : ts,
        "atr_ratio"  : round(candle_range / atr, 2),
        "body_ratio" : round(body_ratio, 2),
        "confidence" : None,   # filled after sweep context added
        "candle_idx" : idx,
        "price"      : round(close, 2),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_displacement(
    candles: list,
    atr_mult: float = _ATR_MULT_WEAK,
    body_ratio_min: float = _BODY_RATIO_MIN,
    near_sweep: bool = False,
    lookback: int = _LOOKBACK_BARS,
) -> Optional[dict]:
    """
    Detect the most recent displacement candle in the last `lookback` candles.

    Args:
        candles        : list of OHLCV candle dicts (date/open/high/low/close/volume)
        atr_mult       : override ATR multiplier threshold (default 1.8)
        body_ratio_min : override body dominance threshold (default 0.70)
        near_sweep     : whether a liquidity sweep was recently detected
                         (boosts confidence tier)
        lookback       : how many recent candles to scan (default 5)

    Returns:
        dict with displacement details, or None if no displacement found.
        Confidence is filled here using sweep context.
    """
    if len(candles) < _ATR_PERIOD + 2:
        return None

    atr = _calculate_atr(candles)
    if atr <= 0:
        return None

    # Scan the last `lookback` candles, skip the very last (incomplete)
    start_idx = max(_ATR_PERIOD, len(candles) - lookback - 1)
    end_idx   = len(candles) - 1  # exclude live/forming candle

    best: Optional[dict] = None

    for i in range(start_idx, end_idx):
        result = _analyse_candle(candles[i], atr, i, candles)
        if result is None:
            continue
        # Keep the strongest (prefer strong > medium > weak)
        if best is None:
            best = result
        else:
            order = {"weak": 0, "medium": 1, "strong": 2}
            if order.get(result["strength"], 0) > order.get(best["strength"], 0):
                best = result

    if best is None:
        return None

    # Add confidence using sweep context
    best["confidence"] = _classify_confidence(
        best["strength"], best["created_fvg"], near_sweep
    )
    return best


def detect_displacement_sequence(
    candles: list,
    lookback: int = 30,
    near_sweep: bool = False,
) -> list:
    """
    Scan `lookback` candles for all displacement events (not just the most recent one).
    Useful for building a timeline of recent institutional activity.

    Returns:
        List of displacement dicts sorted by candle_idx ascending.
    """
    if len(candles) < _ATR_PERIOD + 2:
        return []

    atr = _calculate_atr(candles)
    if atr <= 0:
        return []

    start_idx = max(_ATR_PERIOD, len(candles) - lookback - 1)
    end_idx   = len(candles) - 1

    results = []
    for i in range(start_idx, end_idx):
        r = _analyse_candle(candles[i], atr, i, candles)
        if r is not None:
            r["confidence"] = _classify_confidence(
                r["strength"], r["created_fvg"], near_sweep
            )
            results.append(r)

    return results


def record_displacement_event(symbol: str, event: dict, liquidity_context: str = "") -> None:
    """
    Write a displacement event to the module-level DISPLACEMENT_EVENTS deque.
    Called by the live engine after detecting displacement.

    Args:
        symbol            : e.g. "NSE:NIFTY 50"
        event             : dict from detect_displacement()
        liquidity_context : "sweep_present" | "no_sweep" | ""
    """
    if event is None:
        return

    entry = {
        "symbol"           : symbol,
        "timestamp"        : event.get("timestamp", datetime.now()),
        "direction"        : event["direction"],
        "strength"         : event["strength"],
        "created_fvg"      : event["created_fvg"],
        "atr_ratio"        : event["atr_ratio"],
        "body_ratio"       : event["body_ratio"],
        "confidence"       : event["confidence"],
        "price"            : event["price"],
        "liquidity_context": liquidity_context,
        "recorded_at"      : datetime.now(),
    }

    DISPLACEMENT_EVENTS.appendleft(entry)   # newest first
    logger.info(
        f"[DISPLACEMENT] {symbol} | {event['direction'].upper()} | "
        f"strength={event['strength']} | atr={event['atr_ratio']}x | "
        f"fvg={'Y' if event['created_fvg'] else 'N'} | "
        f"confidence={event['confidence']} | liq={liquidity_context or 'none'}"
    )


def get_recent_displacement_events(symbol: str = None, limit: int = 50) -> list:
    """
    Return recent displacement events, optionally filtered by symbol.
    Events are returned newest-first.
    """
    events = list(DISPLACEMENT_EVENTS)

    if symbol:
        events = [e for e in events if symbol.upper() in e.get("symbol", "").upper()]

    return events[:limit]
