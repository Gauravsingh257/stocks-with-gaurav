"""
SMC Detectors — Corrected Implementations
==========================================
Rebuilt per Phase 3 Theoretical Validation audit.
All functions use dict-based candle format: {"open", "high", "low", "close"}
to remain compatible with the live engine's data pipeline.

Changes from original:
  F2.1  detect_fvg         — proper 3-candle gap, no false tolerance, 30-bar lookback, displacement filter
  F2.2  detect_order_block — 30-bar scan, body-zone, displacement direction check, quality scoring
  F2.3  detect_htf_bias    — swing-based BOS with 3-bar fractals, displacement validation
  F2.4  detect_choch       — requires trend context, 3-bar fractals, returns break info
  F2.5  premium/discount   — swing-range based, OTE zones, no dangerous defaults
  F2.6  liquidity          — equal highs/lows detection, PDH/PDL, sweep rejection
"""

from __future__ import annotations
from typing import Optional, Tuple, List, Dict
import logging

logger = logging.getLogger(__name__)


# =====================================================
# SHARED: ATR CALCULATION
# =====================================================

def calculate_atr(candles: list, period: int = 14) -> float:
    """Average True Range — unchanged from original, kept here for import convenience."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


# =====================================================
# SHARED: SWING POINT DETECTION (3-bar fractals)
# =====================================================

def detect_swing_points(candles: list, left: int = 3, right: int = 3) -> Tuple[list, list]:
    """
    Detect swing highs and lows using N-bar fractals.
    
    A swing high requires `left` lower highs on the left AND `right` lower highs on the right.
    A swing low requires `left` higher lows on the left AND `right` higher lows on the right.
    
    Returns:
        (swing_highs, swing_lows) — each is a list of (index, price) tuples
    """
    swing_highs = []
    swing_lows = []
    
    for i in range(left, len(candles) - right):
        # --- Swing High ---
        is_sh = True
        current_high = candles[i]["high"]
        for j in range(1, left + 1):
            if candles[i - j]["high"] >= current_high:
                is_sh = False
                break
        if is_sh:
            for j in range(1, right + 1):
                if candles[i + j]["high"] >= current_high:
                    is_sh = False
                    break
        if is_sh:
            swing_highs.append((i, current_high))
        
        # --- Swing Low ---
        is_sl = True
        current_low = candles[i]["low"]
        for j in range(1, left + 1):
            if candles[i - j]["low"] <= current_low:
                is_sl = False
                break
        if is_sl:
            for j in range(1, right + 1):
                if candles[i + j]["low"] <= current_low:
                    is_sl = False
                    break
        if is_sl:
            swing_lows.append((i, current_low))
    
    return swing_highs, swing_lows


def classify_swings(swing_highs: list, swing_lows: list) -> list:
    """
    Merge and classify swing points chronologically as HH/LH/HL/LL.
    
    Returns list of dicts: {"index", "price", "type": "SH"/"SL", "label": "HH"/"LH"/"HL"/"LL"}
    """
    points = []
    for idx, price in swing_highs:
        points.append({"index": idx, "price": price, "type": "SH"})
    for idx, price in swing_lows:
        points.append({"index": idx, "price": price, "type": "SL"})
    points.sort(key=lambda p: p["index"])
    
    last_sh = None
    last_sl = None
    for p in points:
        if p["type"] == "SH":
            if last_sh is None:
                p["label"] = "SH"  # unclassified first
            elif p["price"] > last_sh:
                p["label"] = "HH"
            else:
                p["label"] = "LH"
            last_sh = p["price"]
        else:
            if last_sl is None:
                p["label"] = "SL"
            elif p["price"] > last_sl:
                p["label"] = "HL"
            else:
                p["label"] = "LL"
            last_sl = p["price"]
    return points


def determine_trend(classified_points: list, min_points: int = 4) -> str:
    """
    Determine trend from classified swing points.
    
    Returns: "BULLISH", "BEARISH", "RANGING", or "UNKNOWN"
    """
    if len(classified_points) < min_points:
        return "UNKNOWN"
    
    recent = classified_points[-min_points:]
    labels = [p.get("label", "") for p in recent]
    
    bullish = sum(1 for l in labels if l in ("HH", "HL"))
    bearish = sum(1 for l in labels if l in ("LH", "LL"))
    
    if bullish >= min_points * 0.6:
        return "BULLISH"
    elif bearish >= min_points * 0.6:
        return "BEARISH"
    return "RANGING"


# =====================================================
# F2.1: FAIR VALUE GAP (FVG) — REWRITTEN
# =====================================================

def detect_fvg(candles: list, direction: str, lookback: int = 30,
               min_gap_atr_ratio: float = 0.1) -> Optional[Tuple[float, float]]:
    """
    Detect the nearest active Fair Value Gap.
    
    Bullish FVG: candle3.low > candle1.high  (gap above C1, below C3)
    Bearish FVG: candle1.low > candle3.high  (gap below C1, above C3)
    
    Fixes from original:
    - Removed false 0.2% tolerance that accepted overlapping wicks
    - Scans full lookback window (30 bars), not just last 3 candles
    - Requires displacement candle (C2 body > 0.5 * ATR)
    - Returns the NEAREST unfilled FVG to current price
    
    Args:
        candles: list of OHLC dicts
        direction: "LONG" or "SHORT"
        lookback: how many bars back to scan (default 30)
        min_gap_atr_ratio: minimum gap size as fraction of ATR (default 0.1)
    
    Returns:
        (low, high) of the best FVG zone, or None
    """
    if len(candles) < 5:
        return None
    
    atr = calculate_atr(candles)
    if atr <= 0:
        return None
    
    min_gap = atr * min_gap_atr_ratio
    min_c2_body = atr * 0.5  # displacement filter
    
    current_price = candles[-1]["close"]
    scan_start = max(0, len(candles) - lookback)
    
    best_fvg = None
    best_distance = float("inf")
    
    for i in range(scan_start, len(candles) - 2):
        c1 = candles[i]
        c2 = candles[i + 1]
        c3 = candles[i + 2]
        
        # Displacement filter: C2 must be impulsive
        c2_body = abs(c2["close"] - c2["open"])
        if c2_body < min_c2_body:
            continue
        
        if direction == "LONG":
            # Bullish FVG: C3 low is STRICTLY above C1 high (genuine gap)
            gap = c3["low"] - c1["high"]
            if gap <= 0:
                continue  # No actual gap — wicks overlap
            if gap < min_gap:
                continue  # Gap too small to be meaningful
            # C2 should be bullish
            if c2["close"] <= c2["open"]:
                continue
            
            fvg_low = c1["high"]
            fvg_high = c3["low"]
            
            # Check if FVG is still active (not yet filled by subsequent price action)
            filled = False
            for j in range(i + 3, len(candles)):
                if candles[j]["low"] <= fvg_low:
                    filled = True
                    break
            if filled:
                continue
            
            # Nearest to current price (below price for long entries)
            dist = abs(current_price - (fvg_low + fvg_high) / 2)
            if dist < best_distance:
                best_distance = dist
                best_fvg = (fvg_low, fvg_high)
        
        elif direction == "SHORT":
            # Bearish FVG: C1 low is STRICTLY above C3 high
            gap = c1["low"] - c3["high"]
            if gap <= 0:
                continue
            if gap < min_gap:
                continue
            # C2 should be bearish
            if c2["close"] >= c2["open"]:
                continue
            
            fvg_low = c3["high"]
            fvg_high = c1["low"]
            
            # Check if filled
            filled = False
            for j in range(i + 3, len(candles)):
                if candles[j]["high"] >= fvg_high:
                    filled = True
                    break
            if filled:
                continue
            
            dist = abs(current_price - (fvg_low + fvg_high) / 2)
            if dist < best_distance:
                best_distance = dist
                best_fvg = (fvg_low, fvg_high)
    
    return best_fvg


def detect_all_fvgs(candles: list, direction: str, lookback: int = 30,
                    min_gap_atr_ratio: float = 0.1) -> list:
    """
    Return ALL active (unfilled) FVGs in the lookback window.
    Each FVG is a dict: {"low", "high", "index", "gap_size", "quality"}
    """
    if len(candles) < 5:
        return []
    
    atr = calculate_atr(candles)
    if atr <= 0:
        return []
    
    min_gap = atr * min_gap_atr_ratio
    min_c2_body = atr * 0.5
    scan_start = max(0, len(candles) - lookback)
    fvgs = []
    
    for i in range(scan_start, len(candles) - 2):
        c1 = candles[i]
        c2 = candles[i + 1]
        c3 = candles[i + 2]
        
        c2_body = abs(c2["close"] - c2["open"])
        if c2_body < min_c2_body:
            continue
        
        if direction == "LONG":
            gap = c3["low"] - c1["high"]
            if gap <= 0 or gap < min_gap:
                continue
            if c2["close"] <= c2["open"]:
                continue
            fvg_low, fvg_high = c1["high"], c3["low"]
            filled = any(candles[j]["low"] <= fvg_low for j in range(i + 3, len(candles)))
            if not filled:
                quality = min(10.0, (gap / atr) * 4.0 + (c2_body / atr) * 2.0)
                fvgs.append({"low": fvg_low, "high": fvg_high, "index": i + 1,
                             "gap_size": gap, "quality": round(quality, 1)})
        
        elif direction == "SHORT":
            gap = c1["low"] - c3["high"]
            if gap <= 0 or gap < min_gap:
                continue
            if c2["close"] >= c2["open"]:
                continue
            fvg_low, fvg_high = c3["high"], c1["low"]
            filled = any(candles[j]["high"] >= fvg_high for j in range(i + 3, len(candles)))
            if not filled:
                quality = min(10.0, (gap / atr) * 4.0 + (c2_body / atr) * 2.0)
                fvgs.append({"low": fvg_low, "high": fvg_high, "index": i + 1,
                             "gap_size": gap, "quality": round(quality, 1)})
    
    return fvgs


# =====================================================
# F2.2: ORDER BLOCK — REWRITTEN
# =====================================================

def detect_order_block(candles: list, direction: str, lookback: int = 30,
                       min_displacement_mult: float = 2.0,
                       min_body_atr_ratio: float = 0.3) -> Optional[Tuple[float, float]]:
    """
    Detect the nearest valid Order Block.
    
    Bullish OB: last bearish candle before a strong bullish displacement
    Bearish OB: last bullish candle before a strong bearish displacement
    
    Fixes from original:
    - Scans 30 bars (was 5 fixed positions)
    - Returns OB BODY (open-to-close), not full candle range
    - Requires displacement candles to move in the correct direction
    - OB candle must have meaningful body (> 0.3 ATR)
    - Returns nearest untested OB to current price
    
    Returns:
        (low, high) of the OB body zone, or None
    """
    if len(candles) < 10:
        return None
    
    atr = calculate_atr(candles)
    if atr <= 0:
        return None
    
    current_price = candles[-1]["close"]
    scan_start = max(0, len(candles) - lookback)
    
    best_ob = None
    best_distance = float("inf")
    
    for i in range(scan_start, len(candles) - 3):
        ob = candles[i]
        ob_body = abs(ob["close"] - ob["open"])
        ob_range = ob["high"] - ob["low"]
        
        if ob_range <= 0:
            continue
        
        # OB candle must have meaningful body
        if ob_body < atr * min_body_atr_ratio:
            continue
        
        # Look at next 3 candles for impulse
        end_idx = min(i + 4, len(candles))
        impulse = candles[i + 1:end_idx]
        if not impulse:
            continue
        
        impulse_high = max(c["high"] for c in impulse)
        impulse_low = min(c["low"] for c in impulse)
        impulse_range = impulse_high - impulse_low
        
        # Displacement filter
        if impulse_range < ob_range * min_displacement_mult:
            continue
        
        if direction == "LONG":
            # OB candle must be bearish (red)
            if ob["close"] >= ob["open"]:
                continue
            
            # Impulse must be bullish (moving up from the OB)
            displacement = impulse_high - ob["high"]
            if displacement <= 0:
                continue
            
            # At least first impulse candle should be bullish
            if impulse[0]["close"] <= impulse[0]["open"]:
                continue
            
            # OB zone = body (open to close for bearish candle: close < open)
            ob_low = ob["close"]   # body low (close of bearish candle)
            ob_high = ob["open"]   # body high (open of bearish candle)
            
            # Check if OB already mitigated (price closed below ob_low after formation)
            mitigated = False
            for j in range(i + len(impulse), len(candles)):
                if candles[j]["close"] < ob_low:
                    mitigated = True
                    break
            if mitigated:
                continue
            
            # Nearest OB below current price
            dist = abs(current_price - (ob_low + ob_high) / 2)
            if dist < best_distance and current_price >= ob_low:
                best_distance = dist
                best_ob = (ob_low, ob_high)
        
        elif direction == "SHORT":
            # OB candle must be bullish (green)
            if ob["close"] <= ob["open"]:
                continue
            
            # Impulse must be bearish
            displacement = ob["low"] - impulse_low
            if displacement <= 0:
                continue
            
            if impulse[0]["close"] >= impulse[0]["open"]:
                continue
            
            # OB zone = body (open to close for bullish candle: close > open)
            ob_low = ob["open"]    # body low (open of bullish candle)
            ob_high = ob["close"]  # body high (close of bullish candle)
            
            # Check if mitigated
            mitigated = False
            for j in range(i + len(impulse), len(candles)):
                if candles[j]["close"] > ob_high:
                    mitigated = True
                    break
            if mitigated:
                continue
            
            dist = abs(current_price - (ob_low + ob_high) / 2)
            if dist < best_distance and current_price <= ob_high:
                best_distance = dist
                best_ob = (ob_low, ob_high)
    
    return best_ob


# =====================================================
# F2.3: HTF BIAS / BOS — REWRITTEN
# =====================================================

def detect_htf_bias(candles: list, swing_left: int = 3, swing_right: int = 3,
                    min_displacement_atr: float = 0.3) -> Optional[str]:
    """
    Determine HTF directional bias based on swing-structure BOS.
    
    Fixes from original:
    - Uses proper swing highs/lows (3-bar fractals) instead of rolling max/min
    - Finds the LAST valid BOS (close beyond a swing point)
    - Requires meaningful displacement (0.3 ATR default)
    - Classifies swing sequence (HH/HL/LH/LL) for additional context
    
    Returns:
        "LONG" if latest BOS is bullish, "SHORT" if bearish, None if unclear
    """
    if len(candles) < 25:
        return None
    
    atr = calculate_atr(candles)
    if atr <= 0:
        return None
    
    swing_highs, swing_lows = detect_swing_points(candles, left=swing_left, right=swing_right)
    
    if not swing_highs and not swing_lows:
        return None
    
    min_disp = atr * min_displacement_atr
    
    # Find the most recent BOS event by scanning swing breaks in reverse chronological order
    latest_bullish_bos = None  # index of the most recent bullish BOS
    latest_bearish_bos = None
    
    # Bullish BOS: close above a swing high with displacement
    for sh_idx, sh_price in reversed(swing_highs):
        # Check candles after the swing for a break
        search_start = sh_idx + swing_right  # must wait for swing confirmation
        for j in range(search_start, len(candles)):
            close = candles[j]["close"]
            if close > sh_price:
                displacement = close - sh_price
                if displacement >= min_disp:
                    if latest_bullish_bos is None or j > latest_bullish_bos:
                        latest_bullish_bos = j
                break  # only check first break attempt per swing
    
    # Bearish BOS: close below a swing low with displacement
    for sl_idx, sl_price in reversed(swing_lows):
        search_start = sl_idx + swing_right
        for j in range(search_start, len(candles)):
            close = candles[j]["close"]
            if close < sl_price:
                displacement = sl_price - close
                if displacement >= min_disp:
                    if latest_bearish_bos is None or j > latest_bearish_bos:
                        latest_bearish_bos = j
                break
    
    # Return the bias of the MOST RECENT BOS
    if latest_bullish_bos is not None and latest_bearish_bos is not None:
        return "LONG" if latest_bullish_bos > latest_bearish_bos else "SHORT"
    elif latest_bullish_bos is not None:
        return "LONG"
    elif latest_bearish_bos is not None:
        return "SHORT"
    
    # ---------------------------------------------------------------
    # FALLBACK: Price-action momentum when swing fractals can't confirm
    # In persistent drops, 3-bar swing lows never confirm (each new candle
    # makes a lower low). Use EMA slope + consecutive-close direction.
    # ---------------------------------------------------------------
    if len(candles) >= 20:
        # Check recent price momentum (last 10 candles)
        recent = candles[-10:]
        closes = [c["close"] for c in recent]
        
        # Count consecutive lower closes
        lower_closes = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i-1])
        higher_closes = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        
        # Check if price is well below its 20-period mean (trending)
        mean_20 = sum(c["close"] for c in candles[-20:]) / 20
        price_vs_mean = (closes[-1] - mean_20) / atr if atr > 0 else 0
        
        # Strong bearish momentum: 7+ lower closes out of 9, or price > 1.5 ATR below mean
        if lower_closes >= 7 or (lower_closes >= 5 and price_vs_mean < -1.5):
            return "SHORT"
        
        # Strong bullish momentum: 7+ higher closes out of 9, or price > 1.5 ATR above mean
        if higher_closes >= 7 or (higher_closes >= 5 and price_vs_mean > 1.5):
            return "LONG"
    
    return None


# =====================================================
# F2.4: CHOCH — REWRITTEN
# =====================================================

def detect_choch(candles: list, direction: str, lookback: int = 30,
                 swing_left: int = 3, swing_right: int = 3) -> bool:
    """
    Detect Change of Character — a break AGAINST the prevailing trend.
    
    Fixes from original:
    - Uses 3-bar fractals (was 1-bar)
    - Requires TREND CONTEXT: determines current trend from swing sequence,
      then checks if the break is actually counter-trend
    - Only returns True for genuine reversals, not continuations
    
    Args:
        candles: OHLC dict list
        direction: "LONG" (checking for bullish CHoCH in a bearish trend)
                   or "SHORT" (checking for bearish CHoCH in a bullish trend)
        lookback: max candles to scan
    
    Returns:
        True if a valid CHoCH was detected
    """
    if len(candles) < lookback:
        return False
    
    recent = candles[-lookback:]
    swing_highs, swing_lows = detect_swing_points(recent, left=swing_left, right=swing_right)
    
    if not swing_highs or not swing_lows:
        return False
    
    # Classify swings and determine trend
    classified = classify_swings(swing_highs, swing_lows)
    trend = determine_trend(classified)
    
    if direction == "LONG":
        # Bullish CHoCH: current trend should be BEARISH, and price breaks above last swing high
        # (If trend is already bullish, breaking a swing high is BOS, not CHoCH)
        if trend not in ("BEARISH", "RANGING"):
            return False
        
        if not swing_highs:
            return False
        last_sh_idx, last_sh_price = swing_highs[-1]
        # Check if any candle after the swing high closed above it
        for j in range(last_sh_idx + swing_right, len(recent)):
            if recent[j]["close"] > last_sh_price:
                return True
    
    elif direction == "SHORT":
        # Bearish CHoCH: current trend should be BULLISH, and price breaks below last swing low
        if trend not in ("BULLISH", "RANGING"):
            return False
        
        if not swing_lows:
            return False
        last_sl_idx, last_sl_price = swing_lows[-1]
        for j in range(last_sl_idx + swing_right, len(recent)):
            if recent[j]["close"] < last_sl_price:
                return True
    
    return False


def detect_choch_setup_d(candles: list, lookback: int = 30,
                         swing_left: int = 3, swing_right: int = 3) -> Optional[Tuple[str, int]]:
    """
    CHoCH detection for Setup-D — returns (direction, break_index) or None.
    Uses proper swing detection and trend context.
    """
    if len(candles) < lookback:
        return None
    
    recent = candles[-lookback:]
    swing_highs, swing_lows = detect_swing_points(recent, left=swing_left, right=swing_right)
    
    if not swing_highs or not swing_lows:
        return None
    
    classified = classify_swings(swing_highs, swing_lows)
    trend = determine_trend(classified)
    
    last_idx = len(recent) - 1
    # Recency scales with lookback: within the latter half of the window (was hard-coded 15)
    recency_limit = lookback // 2

    # Bullish CHoCH (in bearish trend)
    if trend in ("BEARISH", "RANGING") and swing_highs:
        sh_idx, sh_price = swing_highs[-1]
        if last_idx - sh_idx <= recency_limit:
            for j in range(sh_idx + swing_right, len(recent)):
                if recent[j]["close"] > sh_price:
                    # Map back to original candle index
                    original_idx = len(candles) - lookback + j
                    return "LONG", original_idx
    
    # Bearish CHoCH (in bullish trend)
    if trend in ("BULLISH", "RANGING") and swing_lows:
        sl_idx, sl_price = swing_lows[-1]
        if last_idx - sl_idx <= recency_limit:
            for j in range(sl_idx + swing_right, len(recent)):
                if recent[j]["close"] < sl_price:
                    original_idx = len(candles) - lookback + j
                    return "SHORT", original_idx
    
    return None


def detect_choch_opening_gap(candles_today: list,
                              open_window: int = 5,
                              swing_right: int = 3) -> Optional[Tuple[str, int]]:
    """
    Dedicated CHoCH detector for opening-gap setups.

    Gap days open far from yesterday's close. The conventional multi-day
    lookback window mixes yesterday's 25,300 highs with today's 24,320 open,
    making the standard CHoCH unreachable. This function works entirely within
    today's bars.

    Logic:
        Bullish:  Liq sweep = today's day-low is in bars 0-14 (gap-down opening)
                  CHoCH     = first bar where close > max(high of bars 1..open_window)
                              (bar 0 skipped — opening spike wick is noise)
        Bearish:  Liq sweep = today's day-high is in bars 0-14
                  CHoCH     = first bar where close < min(low  of bars 1..open_window)

    Returns (direction, original_idx) relative to candles_today, or None.
    """
    if len(candles_today) < open_window + swing_right + 2:
        return None

    # Skip bar 0 (opening spike/gap candle whose wicks create false ceilings/floors)
    open_high = max(c["high"] for c in candles_today[1:open_window])
    open_low  = min(c["low"]  for c in candles_today[1:open_window])

    day_low_idx  = min(range(len(candles_today)), key=lambda i: candles_today[i]["low"])
    day_high_idx = max(range(len(candles_today)), key=lambda i: candles_today[i]["high"])

    # Bullish: sweep happened in opening bars (gap-down + intraday low formed early)
    if day_low_idx < 15:
        for j in range(open_window, len(candles_today)):
            if candles_today[j]["close"] > open_high:
                return "LONG", j

    # Bearish: sweep happened in opening bars (gap-up + intraday high formed early)
    if day_high_idx < 15:
        for j in range(open_window, len(candles_today)):
            if candles_today[j]["close"] < open_low:
                return "SHORT", j

    return None


# =====================================================
# F2.5: PREMIUM / DISCOUNT / OTE — REWRITTEN
# =====================================================

def get_swing_range(candles: list, swing_left: int = 3, swing_right: int = 3) -> Tuple[float, float]:
    """
    Get the current structural swing range (last swing low to last swing high).
    Falls back to lookback max/min if insufficient swings.
    """
    swing_highs, swing_lows = detect_swing_points(candles, left=swing_left, right=swing_right)
    
    if swing_highs and swing_lows:
        # Use the most recent swing high and swing low
        sh_price = swing_highs[-1][1]
        sl_price = swing_lows[-1][1]
        if sh_price > sl_price:
            return sl_price, sh_price
    
    # Fallback: use last 50 candles range
    if len(candles) >= 50:
        recent = candles[-50:]
        return min(c["low"] for c in recent), max(c["high"] for c in recent)
    elif candles:
        return min(c["low"] for c in candles), max(c["high"] for c in candles)
    
    return 0.0, 0.0


def is_discount_zone(candles: list, price: float) -> bool:
    """
    Price is in the discount zone (lower 50%) of the structural swing range.
    
    Fixes from original:
    - Uses swing-based range, not arbitrary 100-candle lookback
    - Returns False (not True!) when insufficient data
    """
    if len(candles) < 15:
        return False  # F2.5: was dangerously returning True
    
    low, high = get_swing_range(candles)
    if high <= low:
        return False
    
    eq = (high + low) / 2
    return price < eq


def is_premium_zone(candles: list, price: float) -> bool:
    """Price is in the premium zone (upper 50%) of the structural swing range."""
    if len(candles) < 15:
        return False  # F2.5: was dangerously returning True
    
    low, high = get_swing_range(candles)
    if high <= low:
        return False
    
    eq = (high + low) / 2
    return price > eq


def get_zone_detail(candles: list, price: float) -> Dict[str, any]:
    """
    Detailed zone classification with OTE levels.
    
    Returns:
        {
            "zone": "DEEP_DISCOUNT" | "DISCOUNT" | "EQUILIBRIUM" | "PREMIUM" | "DEEP_PREMIUM",
            "pct": float (0-100, position within range),
            "ote_long": (low_price, high_price) — 62-79% retracement from high (buy zone),
            "ote_short": (low_price, high_price) — 62-79% retracement from low (sell zone),
            "in_ote_long": bool,
            "in_ote_short": bool,
            "swing_low": float,
            "swing_high": float,
        }
    """
    if len(candles) < 15:
        return {"zone": "UNKNOWN", "pct": 50.0, "ote_long": None, "ote_short": None,
                "in_ote_long": False, "in_ote_short": False, "swing_low": 0, "swing_high": 0}
    
    low, high = get_swing_range(candles)
    rng = high - low
    if rng <= 0:
        return {"zone": "UNKNOWN", "pct": 50.0, "ote_long": None, "ote_short": None,
                "in_ote_long": False, "in_ote_short": False, "swing_low": low, "swing_high": high}
    
    pct = ((price - low) / rng) * 100
    
    if pct <= 20:
        zone = "DEEP_DISCOUNT"
    elif pct <= 50:
        zone = "DISCOUNT"
    elif pct <= 60:
        zone = "EQUILIBRIUM"
    elif pct <= 80:
        zone = "PREMIUM"
    else:
        zone = "DEEP_PREMIUM"
    
    # OTE for LONG: 62-79% retracement from high → price zone between 21-38% of range
    ote_long_low = low + rng * 0.21   # 79% retracement from high
    ote_long_high = low + rng * 0.38  # 62% retracement from high
    
    # OTE for SHORT: 62-79% retracement from low → price zone between 62-79% of range  
    ote_short_low = low + rng * 0.62
    ote_short_high = low + rng * 0.79
    
    return {
        "zone": zone,
        "pct": round(pct, 1),
        "ote_long": (round(ote_long_low, 2), round(ote_long_high, 2)),
        "ote_short": (round(ote_short_low, 2), round(ote_short_high, 2)),
        "in_ote_long": ote_long_low <= price <= ote_long_high,
        "in_ote_short": ote_short_low <= price <= ote_short_high,
        "swing_low": round(low, 2),
        "swing_high": round(high, 2),
    }


def near_equilibrium(candles: list, price: float, tol: float = 0.1) -> bool:
    """Price is near the 50% equilibrium level of the swing range."""
    if len(candles) < 15:
        return False
    low, high = get_swing_range(candles)
    rng = high - low
    if rng <= 0:
        return False
    eq = (high + low) / 2
    return abs(price - eq) < (rng * tol)


# =====================================================
# F2.6: LIQUIDITY DETECTION — REWRITTEN
# =====================================================

def detect_equal_highs(candles: list, lookback: int = 50,
                       tolerance_pct: float = 0.001, min_touches: int = 2) -> list:
    """
    Detect equal highs (buy-side liquidity resting above).
    
    Equal highs = 2+ candles touching the same high level within tolerance.
    These clusters attract stop-loss orders from short sellers.
    
    Returns:
        List of {"price": float, "touches": int, "index": int}
    """
    if len(candles) < lookback:
        return []
    
    recent = candles[-lookback:]
    levels = []
    used = set()
    
    for i in range(len(recent)):
        if i in used:
            continue
        h1 = recent[i]["high"]
        touches = 1
        touch_indices = [i]
        
        for j in range(i + 1, len(recent)):
            if j in used:
                continue
            h2 = recent[j]["high"]
            if h1 > 0 and abs(h2 - h1) / h1 <= tolerance_pct:
                touches += 1
                touch_indices.append(j)
        
        if touches >= min_touches:
            for idx in touch_indices:
                used.add(idx)
            avg_price = sum(recent[k]["high"] for k in touch_indices) / len(touch_indices)
            levels.append({
                "price": round(avg_price, 2),
                "touches": touches,
                "index": len(candles) - lookback + touch_indices[-1]
            })
    
    return levels


def detect_equal_lows(candles: list, lookback: int = 50,
                      tolerance_pct: float = 0.001, min_touches: int = 2) -> list:
    """Detect equal lows (sell-side liquidity resting below)."""
    if len(candles) < lookback:
        return []
    
    recent = candles[-lookback:]
    levels = []
    used = set()
    
    for i in range(len(recent)):
        if i in used:
            continue
        l1 = recent[i]["low"]
        touches = 1
        touch_indices = [i]
        
        for j in range(i + 1, len(recent)):
            if j in used:
                continue
            l2 = recent[j]["low"]
            if l1 > 0 and abs(l2 - l1) / l1 <= tolerance_pct:
                touches += 1
                touch_indices.append(j)
        
        if touches >= min_touches:
            for idx in touch_indices:
                used.add(idx)
            avg_price = sum(recent[k]["low"] for k in touch_indices) / len(touch_indices)
            levels.append({
                "price": round(avg_price, 2),
                "touches": touches,
                "index": len(candles) - lookback + touch_indices[-1]
            })
    
    return levels


def liquidity_sweep_detected(candles: list, lookback: int = 50) -> bool:
    """
    Detect if the current candle swept a liquidity level and reversed (rejected).
    
    Fixes from original:
    - Uses equal highs/lows (not rolling max/min)
    - Requires actual sweep (wick beyond) + close back inside (rejection)
    - No excessive 5% buffer
    
    Returns:
        True if a liquidity sweep+rejection was detected on the latest candle
    """
    if len(candles) < lookback + 1:
        return False
    
    current = candles[-1]
    eq_highs = detect_equal_highs(candles[:-1], lookback=lookback)
    eq_lows = detect_equal_lows(candles[:-1], lookback=lookback)
    
    # Check buy-side sweep (swept highs and closed back below = bearish rejection)
    for level in eq_highs:
        price = level["price"]
        if current["high"] > price and current["close"] < price:
            return True
    
    # Check sell-side sweep (swept lows and closed back above = bullish rejection)
    for level in eq_lows:
        price = level["price"]
        if current["low"] < price and current["close"] > price:
            return True
    
    # Also check PDH/PDL-style sweep: previous range high/low
    if len(candles) >= 20:
        prev = candles[-lookback - 1:-1]
        range_high = max(c["high"] for c in prev)
        range_low = min(c["low"] for c in prev)
        
        # Sweep high + rejection
        if current["high"] > range_high and current["close"] < range_high:
            return True
        # Sweep low + rejection
        if current["low"] < range_low and current["close"] > range_low:
            return True
    
    return False


def minor_liquidity(candles: list) -> bool:
    """Short-range liquidity check (20-bar lookback)."""
    return liquidity_sweep_detected(candles, lookback=20)


# =====================================================
# STRUCTURE BIAS HELPER (used by get_ltf_structure_bias)
# =====================================================

def get_ltf_structure_bias(ltf_data: list) -> str:
    """
    Determine the most recent structural bias on LTF.
    Uses swing classification instead of simple break detection.
    """
    if not ltf_data or len(ltf_data) < 25:
        return "NEUTRAL"
    
    recent = ltf_data[-30:]
    swing_highs, swing_lows = detect_swing_points(recent, left=3, right=3)
    
    if not swing_highs and not swing_lows:
        return "NEUTRAL"
    
    classified = classify_swings(swing_highs, swing_lows)
    trend = determine_trend(classified, min_points=3)
    
    if trend == "BULLISH":
        return "BULLISH"
    elif trend == "BEARISH":
        return "BEARISH"
    return "NEUTRAL"


# =====================================================
# SETUP-E: ENHANCED ORDER BLOCK DETECTION (WICK ZONES)
# =====================================================

def detect_order_block_v2(candles: list, direction: str, lookback: int = 50,
                          min_displacement_mult: float = 1.5,
                          min_body_atr_ratio: float = 0.2,
                          wick_extension_pct: float = 0.3) -> Optional[Tuple[float, float]]:
    """
    Enhanced OB detection for Setup-E:
    - 50-bar lookback (vs 30 in v1) — covers full morning session on 5m
    - Zone includes body + partial wick (captures institutional footprint)
    - Relaxed displacement (1.5x vs 2x) — allows moderate OBs
    - Lower body threshold (0.2 ATR vs 0.3) — doesn't discard small-body OBs
    - Mitigation check allows minor wick sweeps (close must break, not wick)
    """
    if len(candles) < 10:
        return None

    atr = calculate_atr(candles)
    if atr <= 0:
        return None

    current_price = candles[-1]["close"]
    scan_start = max(0, len(candles) - lookback)

    best_ob = None
    best_distance = float("inf")

    for i in range(scan_start, len(candles) - 3):
        ob = candles[i]
        ob_body = abs(ob["close"] - ob["open"])
        ob_range = ob["high"] - ob["low"]

        if ob_range <= 0:
            continue

        if ob_body < atr * min_body_atr_ratio:
            continue

        end_idx = min(i + 4, len(candles))
        impulse = candles[i + 1:end_idx]
        if not impulse:
            continue

        impulse_high = max(c["high"] for c in impulse)
        impulse_low = min(c["low"] for c in impulse)
        impulse_range = impulse_high - impulse_low

        if impulse_range < ob_range * min_displacement_mult:
            continue

        if direction == "LONG":
            if ob["close"] >= ob["open"]:
                continue
            displacement = impulse_high - ob["high"]
            if displacement <= 0:
                continue
            if impulse[0]["close"] <= impulse[0]["open"]:
                continue

            # Body + partial wick for zone
            body_low = ob["close"]
            body_high = ob["open"]
            wick_below = body_low - ob["low"]
            ob_low = body_low - wick_below * wick_extension_pct
            ob_high = body_high

            # Minimum zone size: at least 0.3 ATR (prevents micro-zones)
            zone_size = ob_high - ob_low
            min_zone = atr * 0.3
            if zone_size < min_zone:
                # Expand zone symmetrically using the full candle range
                expansion = (min_zone - zone_size) / 2
                ob_low = max(ob["low"], ob_low - expansion)
                ob_high = min(ob["high"], ob_high + expansion)

            # Mitigation: only invalidated by a CLOSE below ob_low (not wick)
            mitigated = False
            for j in range(i + len(impulse), len(candles)):
                if candles[j]["close"] < ob_low:
                    mitigated = True
                    break
            if mitigated:
                continue

            dist = abs(current_price - (ob_low + ob_high) / 2)
            if dist < best_distance and current_price >= ob_low:
                best_distance = dist
                best_ob = (round(ob_low, 2), round(ob_high, 2))

        elif direction == "SHORT":
            if ob["close"] <= ob["open"]:
                continue
            displacement = ob["low"] - impulse_low
            if displacement <= 0:
                continue
            if impulse[0]["close"] >= impulse[0]["open"]:
                continue

            body_low = ob["open"]
            body_high = ob["close"]
            wick_above = ob["high"] - body_high
            ob_low = body_low
            ob_high = body_high + wick_above * wick_extension_pct

            # Minimum zone size: at least 0.3 ATR
            zone_size = ob_high - ob_low
            min_zone = atr * 0.3
            if zone_size < min_zone:
                expansion = (min_zone - zone_size) / 2
                ob_low = max(ob["low"], ob_low - expansion)
                ob_high = min(ob["high"], ob_high + expansion)

            mitigated = False
            for j in range(i + len(impulse), len(candles)):
                if candles[j]["close"] > ob_high:
                    mitigated = True
                    break
            if mitigated:
                continue

            dist = abs(current_price - (ob_low + ob_high) / 2)
            if dist < best_distance and current_price <= ob_high:
                best_distance = dist
                best_ob = (round(ob_low, 2), round(ob_high, 2))

    return best_ob


# =====================================================
# SETUP-E: TWO-TIER CHoCH (MACRO HTF + MICRO LTF)
# =====================================================

def detect_choch_setup_e(candles_ltf: list, candles_htf: list,
                         lookback: int = 30,
                         swing_left: int = 3, swing_right: int = 2) -> Optional[Tuple[str, int]]:
    """
    Two-tier CHoCH for Setup-E:
    1. MACRO trend from HTF (1H) — stable, doesn't flip on intraday rally
    2. MICRO structure from LTF (5m) — detects the actual break

    Key difference from Setup-D: CHoCH fires AGAINST the macro trend, not the
    micro trend. This prevents the "trend reclassification" bug where a rally
    within a downtrend causes the algo to fire SHORT instead of LONG.

    Uses swing_right=2 (vs 3 in Setup-D) for faster LTF confirmation.

    Returns:
        (direction, break_index_in_ltf) or None
    """
    if len(candles_ltf) < lookback:
        return None

    # Step 1: Determine MACRO trend from HTF
    macro_bias = detect_htf_bias(candles_htf) if candles_htf and len(candles_htf) >= 25 else None
    # Also get HTF swing trend for additional context
    if candles_htf and len(candles_htf) >= 25:
        htf_sh, htf_sl = detect_swing_points(candles_htf, left=3, right=3)
        htf_classified = classify_swings(htf_sh, htf_sl)
        htf_trend = determine_trend(htf_classified, min_points=4)
    else:
        htf_trend = "UNKNOWN"

    # Step 2: Detect LTF swing structure
    recent = candles_ltf[-lookback:]
    swing_highs, swing_lows = detect_swing_points(recent, left=swing_left, right=swing_right)

    if not swing_highs or not swing_lows:
        return None

    last_idx = len(recent) - 1
    recency_limit = lookback // 2

    # Determine effective macro direction.
    # When both macro_bias and htf_trend are undetermined, use price position
    # relative to the HTF range as a tiebreaker — prevents firing both directions.
    effective_macro = None
    if macro_bias in ("LONG", "SHORT"):
        effective_macro = macro_bias
    elif htf_trend in ("BULLISH",):
        effective_macro = "LONG"
    elif htf_trend in ("BEARISH",):
        effective_macro = "SHORT"
    else:
        # Fallback: if recent HTF price is in bottom 40% of range → SHORT bias,
        # top 40% → LONG bias, middle → truly unknown (allow both but with caution)
        if candles_htf and len(candles_htf) >= 10:
            htf_recent = candles_htf[-10:]
            range_high = max(c["high"] for c in htf_recent)
            range_low = min(c["low"] for c in htf_recent)
            range_span = range_high - range_low
            if range_span > 0:
                position = (htf_recent[-1]["close"] - range_low) / range_span
                if position < 0.4:
                    effective_macro = "SHORT"  # price in lower range → bearish context
                elif position > 0.6:
                    effective_macro = "LONG"   # price in upper range → bullish context
                # else: middle → None (both directions allowed)

    # Step 3: Bullish CHoCH — macro must NOT be clearly LONG/BULLISH
    # (CHoCH = change of character = AGAINST the trend)
    if effective_macro != "LONG":
        sh_idx, sh_price = swing_highs[-1]
        if last_idx - sh_idx <= recency_limit:
            for j in range(sh_idx + swing_right, len(recent)):
                if recent[j]["close"] > sh_price:
                    original_idx = len(candles_ltf) - lookback + j
                    return "LONG", original_idx

    # Step 4: Bearish CHoCH — macro must NOT be clearly SHORT/BEARISH
    if effective_macro != "SHORT":
        sl_idx, sl_price = swing_lows[-1]
        if last_idx - sl_idx <= recency_limit:
            for j in range(sl_idx + swing_right, len(recent)):
                if recent[j]["close"] < sl_price:
                    original_idx = len(candles_ltf) - lookback + j
                    return "SHORT", original_idx

    return None
