"""
engine/market_state_engine.py — Real-Time Market State Transition Engine
=========================================================================
Detects structural shifts on NIFTY/BANKNIFTY using SMC price action +
OI intelligence to classify the current market state.

States:
  BULLISH_REVERSAL   — CHoCH/BOS bullish + confirming OI/displacement
  BEARISH_REVERSAL   — CHoCH/BOS bearish + confirming OI/displacement
  TREND_CONTINUATION — Existing trend intact with aligned structure
  RANGE              — No clear directional bias

Events tracked:
  - Liquidity sweep (sell-side/buy-side)
  - Displacement candle (large body impulse move)
  - CHoCH (Change of Character) 
  - BOS (Break of Structure)
  - Short covering (OI dropping + price rising)
  - PE/CE support/resistance walls from strike heatmap

Called from detect_market_regime() in smc_mtf_engine_v4.py.
"""

import logging
from datetime import datetime, time
from typing import Optional, Dict, List, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger("MarketState")

# Import SMC detectors
try:
    from smc_detectors import (
        detect_swing_points, classify_swings, determine_trend,
        detect_htf_bias, detect_choch, liquidity_sweep_detected,
        calculate_atr, detect_fvg,
    )
except ImportError:
    logger.error("Could not import smc_detectors — market state engine disabled")

try:
    from engine.oi_sentiment import get_oi_sentiment
except ImportError:
    get_oi_sentiment = lambda: {}

try:
    from engine.displacement_detector import (
        detect_displacement as _detect_displacement_module,
        record_displacement_event as _record_displacement,
    )
    _DISP_MODULE_OK = True
except ImportError:
    _DISP_MODULE_OK = False
    _detect_displacement_module = None
    _record_displacement = None

# =====================================================
# MODULE STATE
# =====================================================
_market_state = {
    "state": "RANGE",               # BULLISH_REVERSAL / BEARISH_REVERSAL / TREND_CONTINUATION / RANGE
    "prev_state": "RANGE",
    "confidence": 0,                # 0-10 confidence score
    "events": [],                   # List of active trigger events
    "score_breakdown": {},          # Detailed scoring
    "last_update": None,
    "transition_time": None,        # When state last changed
    "underlying_data": {},          # Per-index state
}

# Scoring weights for market state determination
STATE_WEIGHTS = {
    "CHOCH": 3,
    "BOS": 2,
    "LIQUIDITY_SWEEP": 2,
    "DISPLACEMENT": 2,
    "SHORT_COVERING": 1,   # Reduced 3→1: OI-only signal, no direct price confirmation
    "PE_SUPPORT_WALL": 2,
    "CE_RESISTANCE_WALL": 2,
    "FVG_ALIGNMENT": 1,
    "TREND_STRUCTURE": 2,
    "PRICE_ABOVE_VWAP": 1,
    "PRICE_BELOW_VWAP": 1,
    "HIGHER_LOWS": 2,
    "LOWER_HIGHS": 2,
}


def get_market_state() -> dict:
    """Return current market state (thread-safe copy)."""
    return dict(_market_state)


def get_market_state_label() -> str:
    """Return simple state string."""
    return _market_state["state"]


def get_state_events() -> list:
    """Return list of active trigger events."""
    return list(_market_state.get("events", []))


# =====================================================
# CORE: Compute Market State
# =====================================================

def update_market_state(
    fetch_ohlc_fn,
    kite_obj=None,
    oi_state: dict = None,
) -> dict:
    """
    Main entry point. Analyzes price structure + OI to determine market state.
    
    Args:
        fetch_ohlc_fn: Function to fetch OHLC data (symbol, interval, lookback)
        kite_obj: Kite API instance (optional, for spot price)
        oi_state: Current OI sentiment state dict (from get_oi_sentiment())
    
    Returns:
        dict: Current market state
    """
    now = datetime.now(_IST).replace(tzinfo=None)
    
    if not (time(9, 16) <= now.time() <= time(15, 15)):
        return _market_state
    
    bull_score = 0
    bear_score = 0
    events = []
    per_index = {}
    
    if oi_state is None:
        try:
            oi_state = get_oi_sentiment()
        except Exception:
            oi_state = {}
    
    for index_sym, index_name in [("NSE:NIFTY 50", "NIFTY"), ("NSE:NIFTY BANK", "BANKNIFTY")]:
        try:
            idx_result = _analyze_index(index_sym, index_name, fetch_ohlc_fn, now)
            if idx_result is None:
                continue
            
            per_index[index_name] = idx_result
            
            # Aggregate scores
            for event in idx_result["events"]:
                events.append(event)
                if event["direction"] == "BULL":
                    bull_score += event["weight"]
                elif event["direction"] == "BEAR":
                    bear_score += event["weight"]
                    
        except Exception as e:
            logger.warning(f"Market state error for {index_name}: {e}")
            continue
    
    # ---------------------------------------------------------
    # Add OI sentiment contribution
    # ---------------------------------------------------------
    oi_events = _score_oi_signals(oi_state)
    for event in oi_events:
        events.append(event)
        if event["direction"] == "BULL":
            bull_score += event["weight"]
        elif event["direction"] == "BEAR":
            bear_score += event["weight"]
    
    # ---------------------------------------------------------
    # Determine state
    # ---------------------------------------------------------
    prev_state = _market_state["state"]
    net = bull_score - bear_score
    total = bull_score + bear_score
    
    if total == 0:
        new_state = "RANGE"
        confidence = 0
    elif net >= 5:
        # Check if this is a reversal (from bearish/range) or continuation
        if prev_state in ("BEARISH_REVERSAL", "RANGE"):
            new_state = "BULLISH_REVERSAL"
        else:
            new_state = "TREND_CONTINUATION"
        confidence = min(10, net)
    elif net <= -5:
        if prev_state in ("BULLISH_REVERSAL", "RANGE"):
            new_state = "BEARISH_REVERSAL"
        else:
            new_state = "TREND_CONTINUATION"
        confidence = min(10, abs(net))
    elif net >= 3:
        new_state = "BULLISH_REVERSAL" if prev_state != "BULLISH_REVERSAL" else "TREND_CONTINUATION"
        confidence = min(10, net)
    elif net <= -3:
        new_state = "BEARISH_REVERSAL" if prev_state != "BEARISH_REVERSAL" else "TREND_CONTINUATION"
        confidence = min(10, abs(net))
    else:
        new_state = "RANGE"
        confidence = 0
    
    transition_time = _market_state.get("transition_time")
    if new_state != prev_state:
        transition_time = now
        logger.info(
            f"⚡ MARKET STATE TRANSITION: {prev_state} → {new_state} "
            f"(Bull={bull_score} Bear={bear_score} Net={net})"
        )
    
    _market_state.update({
        "state": new_state,
        "prev_state": prev_state,
        "confidence": confidence,
        "events": events,
        "score_breakdown": {
            "bull_score": bull_score,
            "bear_score": bear_score,
            "net": net,
        },
        "last_update": now,
        "transition_time": transition_time,
        "underlying_data": per_index,
    })
    
    return _market_state


# =====================================================
# INTERNAL: Analyze a single index
# =====================================================

def _analyze_index(
    index_sym: str,
    index_name: str,
    fetch_ohlc_fn,
    now: datetime,
) -> Optional[dict]:
    """
    Analyze price structure for one index.
    Returns dict with events list or None on failure.
    """
    try:
        data_5m = fetch_ohlc_fn(index_sym, "5minute", lookback=80)
        data_15m = fetch_ohlc_fn(index_sym, "15minute", lookback=50)
    except Exception as e:
        logger.warning(f"Failed to fetch OHLC for {index_name}: {e}")
        return None
    
    if not data_5m or len(data_5m) < 20:
        return None
    if not data_15m or len(data_15m) < 10:
        return None
    
    events = []
    current_price = data_5m[-1]["close"]
    atr = calculate_atr(data_5m, period=14)
    if atr <= 0:
        atr = 1  # safety
    
    # Today's candles only
    today_candles_5m = [c for c in data_5m if c["date"].date() == now.date()]
    today_candles_15m = [c for c in data_15m if c["date"].date() == now.date()]
    
    if len(today_candles_5m) < 5:
        return None
    
    # ---------------------------------------------------------
    # 1. CHoCH Detection (5m timeframe) — today's candles only
    # ---------------------------------------------------------
    try:
        choch_candles = today_candles_5m if len(today_candles_5m) >= 20 else data_5m
        bull_choch = detect_choch(choch_candles, direction="LONG", lookback=30, swing_left=3, swing_right=3)
        bear_choch = detect_choch(choch_candles, direction="SHORT", lookback=30, swing_left=3, swing_right=3)
        
        if bull_choch:
            events.append({
                "type": "CHOCH", "direction": "BULL",
                "weight": STATE_WEIGHTS["CHOCH"],
                "detail": f"{index_name} bullish CHoCH on 5m",
            })
        if bear_choch:
            events.append({
                "type": "CHOCH", "direction": "BEAR",
                "weight": STATE_WEIGHTS["CHOCH"],
                "detail": f"{index_name} bearish CHoCH on 5m",
            })
    except Exception as e:
        logger.debug(f"CHoCH detection error {index_name}: {e}")
    
    # ---------------------------------------------------------
    # 2. BOS / HTF Bias (15m timeframe) — today's candles only
    # ---------------------------------------------------------
    try:
        # Use today's 15m candles to avoid multi-day bias contamination
        bias_15m = today_candles_15m if len(today_candles_15m) >= 10 else data_15m
        htf_bias = detect_htf_bias(bias_15m)
        if htf_bias == "LONG":
            events.append({
                "type": "BOS", "direction": "BULL",
                "weight": STATE_WEIGHTS["BOS"],
                "detail": f"{index_name} bullish BOS on 15m",
            })
        elif htf_bias == "SHORT":
            events.append({
                "type": "BOS", "direction": "BEAR",
                "weight": STATE_WEIGHTS["BOS"],
                "detail": f"{index_name} bearish BOS on 15m",
            })
    except Exception as e:
        logger.debug(f"HTF bias error {index_name}: {e}")
    
    # ---------------------------------------------------------
    # 3. Liquidity Sweep Detection
    # ---------------------------------------------------------
    try:
        sweep = liquidity_sweep_detected(data_5m, lookback=50)
        if sweep:
            # Determine direction: buy-side sweep (wick above highs then reject) = bearish
            # sell-side sweep (wick below lows then recover) = bullish
            last_candle = data_5m[-1]
            wick_up = last_candle["high"] - max(last_candle["open"], last_candle["close"])
            wick_down = min(last_candle["open"], last_candle["close"]) - last_candle["low"]
            
            if wick_down > wick_up:
                # Sell-side sweep + recovery = bullish
                events.append({
                    "type": "LIQUIDITY_SWEEP", "direction": "BULL",
                    "weight": STATE_WEIGHTS["LIQUIDITY_SWEEP"],
                    "detail": f"{index_name} sell-side liquidity sweep (bullish)",
                })
            else:
                events.append({
                    "type": "LIQUIDITY_SWEEP", "direction": "BEAR",
                    "weight": STATE_WEIGHTS["LIQUIDITY_SWEEP"],
                    "detail": f"{index_name} buy-side liquidity sweep (bearish)",
                })
    except Exception as e:
        logger.debug(f"Liquidity sweep error {index_name}: {e}")
    
    # ---------------------------------------------------------
    # 4. Displacement Detection (strong impulse candle)
    #    Phase 2: use unified displacement_detector module when available
    # ---------------------------------------------------------
    try:
        disp_found = False
        if _DISP_MODULE_OK and _detect_displacement_module is not None:
            # Use the full Phase 2 module (ATR ratio + body dominance + FVG check)
            near_sweep = any(e["type"] == "LIQUIDITY_SWEEP" for e in events)
            disp_event = _detect_displacement_module(
                today_candles_5m,
                near_sweep=near_sweep,
                lookback=6,
            )
            if disp_event is not None:
                direction = "BULL" if disp_event["direction"] == "bullish" else "BEAR"
                events.append({
                    "type": "DISPLACEMENT", "direction": direction,
                    "weight": STATE_WEIGHTS["DISPLACEMENT"],
                    "detail": (
                        f"{index_name} displacement ({disp_event['strength']}, "
                        f"{direction.lower()}, "
                        f"atr={disp_event['atr_ratio']}x, "
                        f"body={int(disp_event['body_ratio']*100)}%, "
                        f"fvg={'Y' if disp_event['created_fvg'] else 'N'}, "
                        f"conf={disp_event['confidence']})"
                    ),
                    "displacement_detail": disp_event,
                })
                if _record_displacement is not None:
                    liq_ctx = "sweep_present" if near_sweep else "no_sweep"
                    _record_displacement(index_sym, disp_event, liquidity_context=liq_ctx)
                disp_found = True
        if not disp_found:
            # Fallback: simple body-based check (legacy)
            for candle in today_candles_5m[-5:]:
                body = abs(candle["close"] - candle["open"])
                if body > atr * 1.5:
                    direction = "BULL" if candle["close"] > candle["open"] else "BEAR"
                    events.append({
                        "type": "DISPLACEMENT", "direction": direction,
                        "weight": STATE_WEIGHTS["DISPLACEMENT"],
                        "detail": f"{index_name} displacement candle ({direction.lower()}, body={body:.0f} vs ATR={atr:.0f})",
                    })
                    break
    except Exception as e:
        logger.debug(f"Displacement error {index_name}: {e}")
    
    # ---------------------------------------------------------
    # 5. Higher Lows / Lower Highs detection (intraday structure)
    # ---------------------------------------------------------
    try:
        if len(today_candles_5m) >= 10:
            swing_highs, swing_lows = detect_swing_points(today_candles_5m, left=2, right=2)
            
            if len(swing_lows) >= 2:
                # Check last 2 swing lows — higher lows?
                if swing_lows[-1][1] > swing_lows[-2][1]:
                    events.append({
                        "type": "HIGHER_LOWS", "direction": "BULL",
                        "weight": STATE_WEIGHTS["HIGHER_LOWS"],
                        "detail": f"{index_name} higher lows forming ({swing_lows[-2][1]:.0f} → {swing_lows[-1][1]:.0f})",
                    })
            
            if len(swing_highs) >= 2:
                if swing_highs[-1][1] < swing_highs[-2][1]:
                    events.append({
                        "type": "LOWER_HIGHS", "direction": "BEAR",
                        "weight": STATE_WEIGHTS["LOWER_HIGHS"],
                        "detail": f"{index_name} lower highs forming ({swing_highs[-2][1]:.0f} → {swing_highs[-1][1]:.0f})",
                    })
    except Exception as e:
        logger.debug(f"Swing structure error {index_name}: {e}")
    
    # ---------------------------------------------------------
    # 6. VWAP Position
    # ---------------------------------------------------------
    try:
        if today_candles_5m:
            total_vwap_num = sum(c["close"] * c["volume"] for c in today_candles_5m)
            total_volume = sum(c["volume"] for c in today_candles_5m)
            if total_volume > 0:
                vwap = total_vwap_num / total_volume
                if current_price > vwap * 1.002:
                    events.append({
                        "type": "PRICE_ABOVE_VWAP", "direction": "BULL",
                        "weight": STATE_WEIGHTS["PRICE_ABOVE_VWAP"],
                        "detail": f"{index_name} price {current_price:.0f} above VWAP {vwap:.0f}",
                    })
                elif current_price < vwap * 0.998:
                    events.append({
                        "type": "PRICE_BELOW_VWAP", "direction": "BEAR",
                        "weight": STATE_WEIGHTS["PRICE_BELOW_VWAP"],
                        "detail": f"{index_name} price {current_price:.0f} below VWAP {vwap:.0f}",
                    })
    except Exception as e:
        logger.debug(f"VWAP error {index_name}: {e}")
    
    # ---------------------------------------------------------
    # 7. FVG alignment (unfilled FVG in direction of move)
    # ---------------------------------------------------------
    try:
        bull_fvg = detect_fvg(data_5m, direction="LONG", lookback=20)
        bear_fvg = detect_fvg(data_5m, direction="SHORT", lookback=20)
        
        if bull_fvg:
            fvg_low, fvg_high = bull_fvg
            if current_price >= fvg_low:  # Price has respected or is above bullish FVG
                events.append({
                    "type": "FVG_ALIGNMENT", "direction": "BULL",
                    "weight": STATE_WEIGHTS["FVG_ALIGNMENT"],
                    "detail": f"{index_name} bullish FVG {fvg_low:.0f}-{fvg_high:.0f}",
                })
        if bear_fvg:
            fvg_low, fvg_high = bear_fvg
            if current_price <= fvg_high:
                events.append({
                    "type": "FVG_ALIGNMENT", "direction": "BEAR",
                    "weight": STATE_WEIGHTS["FVG_ALIGNMENT"],
                    "detail": f"{index_name} bearish FVG {fvg_low:.0f}-{fvg_high:.0f}",
                })
    except Exception as e:
        logger.debug(f"FVG error {index_name}: {e}")
    
    return {
        "index": index_name,
        "current_price": current_price,
        "atr": atr,
        "events": events,
    }


# =====================================================
# INTERNAL: Score OI signals for market state
# =====================================================

def _score_oi_signals(oi_state: dict) -> list:
    """Convert OI sentiment state into market state events."""
    events = []
    
    if not oi_state or not oi_state.get("last_update"):
        return events
    
    pattern = oi_state.get("price_oi_pattern", "NONE")
    sentiment = oi_state.get("sentiment", "NEUTRAL")
    pcr_bias = oi_state.get("pcr_bias", "NEUTRAL")
    
    # Short covering is a KEY reversal signal
    if pattern == "SHORT_COVERING":
        events.append({
            "type": "SHORT_COVERING", "direction": "BULL",
            "weight": STATE_WEIGHTS["SHORT_COVERING"],
            "detail": f"OI short covering detected (price up + OI down)",
        })
    elif pattern == "SHORT_BUILDUP":
        events.append({
            "type": "SHORT_COVERING", "direction": "BEAR",
            "weight": STATE_WEIGHTS["SHORT_COVERING"],
            "detail": f"OI short buildup detected (price down + OI up)",
        })
    
    # PCR-derived support/resistance from heatmap (already computed in oi_sentiment)
    # The score_breakdown in oi_state tells us if PE/CE walls were detected
    breakdown = oi_state.get("score_breakdown", [])
    if isinstance(breakdown, list):
        for item in breakdown:
            if "PE wall" in str(item):
                events.append({
                    "type": "PE_SUPPORT_WALL", "direction": "BULL",
                    "weight": STATE_WEIGHTS["PE_SUPPORT_WALL"],
                    "detail": str(item),
                })
            elif "CE wall" in str(item):
                events.append({
                    "type": "CE_RESISTANCE_WALL", "direction": "BEAR",
                    "weight": STATE_WEIGHTS["CE_RESISTANCE_WALL"],
                    "detail": str(item),
                })
    
    return events


# =====================================================
# UTILITY: Reset for new day
# =====================================================

def reset_market_state():
    """Reset market state at start of new trading day."""
    _market_state.update({
        "state": "RANGE",
        "prev_state": "RANGE",
        "confidence": 0,
        "events": [],
        "score_breakdown": {},
        "last_update": None,
        "transition_time": None,
        "underlying_data": {},
    })
    logger.info("Market state reset for new day")
