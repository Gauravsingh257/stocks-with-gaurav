"""
engine/oi_sentiment.py — Centralized OI-Based Market Sentiment Tracker
=======================================================================
Tracks Open Interest, Change in OI, PCR trends, and OI buildup/unwinding
across NIFTY and BANKNIFTY to produce a unified market sentiment signal.

Feeds into detect_market_regime() as Signal 4 alongside VWAP, Opening Range,
and 15m Structure.

OI Interpretation Framework:
─────────────────────────────
  Price UP   + OI UP   → Long Buildup    (BULLISH — fresh longs entering)
  Price UP   + OI DOWN → Short Covering   (WEAK BULLISH — shorts exiting, not fresh demand)
  Price DOWN + OI UP   → Short Buildup    (BEARISH — fresh shorts entering)
  Price DOWN + OI DOWN → Long Unwinding   (WEAK BEARISH — longs exiting)

PCR (Put-Call Ratio) Interpretation:
─────────────────────────────────────
  PCR > 1.2  → Heavy put writing = floor support (BULLISH)
  PCR < 0.7  → Heavy call writing = ceiling resistance (BEARISH)
  0.7–1.2    → Balanced (NEUTRAL)

  PCR Rising  → Increasing put interest → market support building (BULLISH)
  PCR Falling → Increasing call interest → market ceiling forming (BEARISH)
"""

import logging
import os
import pickle
import time as t
from datetime import datetime, timedelta, date as dt_date
from collections import deque

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

from engine import config as cfg

logger = logging.getLogger(__name__)

# =====================================================
# CONSTANTS
# =====================================================
OI_SENTIMENT_REFRESH_SECS = cfg.OI_SENTIMENT_REFRESH_SECS    # 120 = 2 min
OI_PCR_BULLISH_THRESHOLD = cfg.OI_PCR_BULLISH_THRESHOLD      # 1.2
OI_PCR_BEARISH_THRESHOLD = cfg.OI_PCR_BEARISH_THRESHOLD      # 0.7
OI_PCR_STRONG_BULL = getattr(cfg, 'OI_PCR_STRONG_BULL', 1.3)
OI_PCR_STRONG_BEAR = getattr(cfg, 'OI_PCR_STRONG_BEAR', 0.6)
OI_PCR_HISTORY_SIZE = cfg.OI_PCR_HISTORY_SIZE                 # 30 readings (~1 hr at 2-min refresh)
OI_CHANGE_THRESHOLD_PCT = cfg.OI_CHANGE_THRESHOLD_PCT         # 2% min change to count as significant
OI_BUILDUP_MIN_SIGNALS = cfg.OI_BUILDUP_MIN_SIGNALS           # Need 2+ signals to call a bias

# Scoring weights
OI_W_PCR_LEVEL = getattr(cfg, 'OI_WEIGHT_PCR_LEVEL', 2)
OI_W_PCR_STRONG = getattr(cfg, 'OI_WEIGHT_PCR_STRONG', 3)
OI_W_PCR_TREND = getattr(cfg, 'OI_WEIGHT_PCR_TREND', 2)
OI_W_LONG_BUILDUP = getattr(cfg, 'OI_WEIGHT_LONG_BUILDUP', 3)
OI_W_SHORT_COVERING = getattr(cfg, 'OI_WEIGHT_SHORT_COVERING', 3)
OI_W_SHORT_BUILDUP = getattr(cfg, 'OI_WEIGHT_SHORT_BUILDUP', 3)
OI_W_LONG_UNWINDING = getattr(cfg, 'OI_WEIGHT_LONG_UNWINDING', 2)
OI_W_OI_CHANGE_BIAS = getattr(cfg, 'OI_WEIGHT_OI_CHANGE_BIAS', 1)
OI_W_HEATMAP_WALL = getattr(cfg, 'OI_WEIGHT_HEATMAP_WALL', 2)


# =====================================================
# STATE — Module-level (same pattern as MARKET_REGIME)
# =====================================================
_oi_state = {
    # Per-index snapshots: {"NIFTY": {...}, "BANKNIFTY": {...}}
    "snapshots": {},
    
    # PCR history: deque of (timestamp, pcr_value, total_call_oi, total_put_oi)
    "pcr_history": deque(maxlen=OI_PCR_HISTORY_SIZE),
    
    # Overall sentiment: BULLISH / BEARISH / NEUTRAL
    "sentiment": "NEUTRAL",
    
    # Sub-signals
    "pcr_bias": "NEUTRAL",         # From current PCR level
    "pcr_trend": "NEUTRAL",        # From PCR direction over time
    "oi_change_bias": "NEUTRAL",   # From OI change + price action
    "price_oi_pattern": "NONE",    # LONG_BUILDUP / SHORT_BUILDUP / SHORT_COVERING / LONG_UNWINDING
    
    # Scores (for detect_market_regime integration)
    "bull_score": 0,
    "bear_score": 0,
    
    # Metadata
    "last_update": None,
    "details": "",
}


def get_oi_sentiment():
    """
    Returns current OI sentiment state dict.
    Thread-safe read (dict copy).
    """
    return dict(_oi_state)


def get_oi_scores():
    """
    Returns (bull_score, bear_score) from OI analysis.
    Used by detect_market_regime() to add OI signal.
    """
    return _oi_state["bull_score"], _oi_state["bear_score"]


def get_oi_summary_text():
    """
    Returns a formatted string for Telegram/logging.
    """
    s = _oi_state
    if not s["last_update"]:
        return "OI Sentiment: No data yet"
    
    age_mins = (datetime.now(_IST) - s["last_update"]).seconds // 60
    
    pcr_hist = s["pcr_history"]
    pcr_val = pcr_hist[-1][1] if pcr_hist else "N/A"
    
    lines = [
        f"📊 <b>OI SENTIMENT: {s['sentiment']}</b>",
        f"PCR: {pcr_val} ({s['pcr_bias']})",
        f"PCR Trend: {s['pcr_trend']}",
        f"OI Pattern: {s['price_oi_pattern']}",
        f"OI Bias: {s['oi_change_bias']}",
        f"Scores: Bull={s['bull_score']} Bear={s['bear_score']}",
        f"Updated: {age_mins}m ago",
    ]
    return "\n".join(lines)


# =====================================================
# CORE: Compute OI Sentiment
# =====================================================

def update_oi_sentiment(kite_obj, fetch_ohlc_fn=None):
    """
    Main entry point. Called from the main engine loop.
    
    Fetches option chain OI for NIFTY + BANKNIFTY, computes:
      1. PCR and PCR trend
      2. Total OI changes (call vs put)
      3. Price + OI correlation (buildup/covering/unwinding)
      4. Combined sentiment score
    
    Args:
        kite_obj: Kite API instance (can be None in backtest mode)
        fetch_ohlc_fn: Function to fetch OHLC data (for price direction)
    
    Returns:
        dict: Current OI sentiment state
    """
    
    now = datetime.now(_IST)
    
    # Throttle: only refresh every OI_SENTIMENT_REFRESH_SECS
    if _oi_state["last_update"]:
        elapsed = (now - _oi_state["last_update"]).total_seconds()
        if elapsed < OI_SENTIMENT_REFRESH_SECS:
            return _oi_state
    
    if not kite_obj:
        return _oi_state
    
    try:
        # ---------------------------------------------------------
        # Step 1: Fetch OI data for both indices
        # ---------------------------------------------------------
        total_call_oi = 0
        total_put_oi = 0
        index_oi_data = {}
        
        for ul in cfg.OPT_UNDERLYINGS:
            sym = ul["symbol"]       # e.g. "NSE:NIFTY 50"
            name = ul["name"]        # e.g. "NIFTY"
            step = ul["step"]        # 50 for NIFTY, 100 for BANKNIFTY
            
            oi_data = _fetch_index_oi(kite_obj, sym, name, step)
            if oi_data:
                index_oi_data[name] = oi_data
                total_call_oi += oi_data["call_oi"]
                total_put_oi += oi_data["put_oi"]
        
        if total_call_oi == 0 and total_put_oi == 0:
            logger.warning("OI Sentiment: No OI data fetched")
            return _oi_state
        
        # ---------------------------------------------------------
        # Step 2: Compute PCR
        # ---------------------------------------------------------
        pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
        pcr = round(pcr, 3)
        
        # Store in history
        _oi_state["pcr_history"].append((now, pcr, total_call_oi, total_put_oi))
        
        # PCR level bias
        if pcr > OI_PCR_BULLISH_THRESHOLD:
            pcr_bias = "BULLISH"
        elif pcr < OI_PCR_BEARISH_THRESHOLD:
            pcr_bias = "BEARISH"
        else:
            pcr_bias = "NEUTRAL"
        
        # ---------------------------------------------------------
        # Step 3: PCR Trend (compare to previous readings)
        # ---------------------------------------------------------
        pcr_trend = _compute_pcr_trend()
        
        # ---------------------------------------------------------
        # Step 4: OI Change Analysis (vs previous snapshot)
        # ---------------------------------------------------------
        oi_change_bias, price_oi_pattern = _analyze_oi_changes(
            index_oi_data, kite_obj, fetch_ohlc_fn
        )
        
        # ---------------------------------------------------------
        # Step 5: Store current snapshot for next comparison
        # ---------------------------------------------------------
        _oi_state["snapshots"] = index_oi_data
        
        # ---------------------------------------------------------
        # Step 6: Compute combined sentiment scores (Phase 2 redesign)
        # ---------------------------------------------------------
        bull_score = 0
        bear_score = 0
        score_breakdown = []
        
        # PCR level contribution (weight: 2-3 depending on strength)
        if pcr >= OI_PCR_STRONG_BULL:
            bull_score += OI_W_PCR_STRONG
            score_breakdown.append(f"PCR {pcr:.3f} strong bull +{OI_W_PCR_STRONG}")
        elif pcr_bias == "BULLISH":
            bull_score += OI_W_PCR_LEVEL
            score_breakdown.append(f"PCR {pcr:.3f} bull +{OI_W_PCR_LEVEL}")
        elif pcr <= OI_PCR_STRONG_BEAR:
            bear_score += OI_W_PCR_STRONG
            score_breakdown.append(f"PCR {pcr:.3f} strong bear +{OI_W_PCR_STRONG}")
        elif pcr_bias == "BEARISH":
            bear_score += OI_W_PCR_LEVEL
            score_breakdown.append(f"PCR {pcr:.3f} bear +{OI_W_PCR_LEVEL}")
        
        # PCR trend contribution (weight: 2)
        if pcr_trend == "RISING":
            bull_score += OI_W_PCR_TREND
            score_breakdown.append(f"PCR trend RISING +{OI_W_PCR_TREND} bull")
        elif pcr_trend == "FALLING":
            bear_score += OI_W_PCR_TREND
            score_breakdown.append(f"PCR trend FALLING +{OI_W_PCR_TREND} bear")
        
        # OI change pattern contribution (weight: 2-3)
        if price_oi_pattern == "LONG_BUILDUP":
            bull_score += OI_W_LONG_BUILDUP
            score_breakdown.append(f"LONG_BUILDUP +{OI_W_LONG_BUILDUP} bull")
        elif price_oi_pattern == "SHORT_COVERING":
            bull_score += OI_W_SHORT_COVERING
            score_breakdown.append(f"SHORT_COVERING +{OI_W_SHORT_COVERING} bull")
        elif price_oi_pattern == "SHORT_BUILDUP":
            bear_score += OI_W_SHORT_BUILDUP
            score_breakdown.append(f"SHORT_BUILDUP +{OI_W_SHORT_BUILDUP} bear")
        elif price_oi_pattern == "LONG_UNWINDING":
            bear_score += OI_W_LONG_UNWINDING
            score_breakdown.append(f"LONG_UNWINDING +{OI_W_LONG_UNWINDING} bear")
        
        # OI change directional bias (weight: 1)
        if oi_change_bias == "BULLISH":
            bull_score += OI_W_OI_CHANGE_BIAS
            score_breakdown.append(f"OI change bias bull +{OI_W_OI_CHANGE_BIAS}")
        elif oi_change_bias == "BEARISH":
            bear_score += OI_W_OI_CHANGE_BIAS
            score_breakdown.append(f"OI change bias bear +{OI_W_OI_CHANGE_BIAS}")
        
        # ---------------------------------------------------------
        # Step 6b: Strike Heatmap Integration
        # Check if heavy PE OI sits near/below spot (support wall = bullish)
        # Check if heavy CE OI sits near/above spot (resistance wall = bearish)
        # ---------------------------------------------------------
        for name, oi_data in index_oi_data.items():
            spot = oi_data.get("spot", 0)
            step = 50 if name == "NIFTY" else 100
            if spot <= 0:
                continue
            
            max_put_strike = oi_data.get("max_put_strike", 0)
            max_call_strike = oi_data.get("max_call_strike", 0)
            max_put_oi = oi_data.get("max_put_oi", 0)
            max_call_oi = oi_data.get("max_call_oi", 0)
            
            # PE support wall: max PE OI strike is near or below spot (within 2 steps)
            if max_put_strike > 0 and max_put_oi > 0:
                if spot - max_put_strike <= step * 2 and max_put_strike <= spot:
                    bull_score += OI_W_HEATMAP_WALL
                    score_breakdown.append(
                        f"{name} PE wall {max_put_strike} near spot {spot:.0f} +{OI_W_HEATMAP_WALL} bull"
                    )
            
            # CE resistance wall: max CE OI strike is near or above spot (within 2 steps)
            if max_call_strike > 0 and max_call_oi > 0:
                if max_call_strike - spot <= step * 2 and max_call_strike >= spot:
                    bear_score += OI_W_HEATMAP_WALL
                    score_breakdown.append(
                        f"{name} CE wall {max_call_strike} near spot {spot:.0f} +{OI_W_HEATMAP_WALL} bear"
                    )
        
        # ---------------------------------------------------------
        # Step 7: Determine final sentiment (symmetric thresholds)
        # ---------------------------------------------------------
        if bull_score >= OI_BUILDUP_MIN_SIGNALS and bull_score > bear_score + 1:
            sentiment = "BULLISH"
        elif bear_score >= OI_BUILDUP_MIN_SIGNALS and bear_score > bull_score + 1:
            sentiment = "BEARISH"
        else:
            sentiment = "NEUTRAL"
        
        # Build details string
        breakdown_str = " | ".join(score_breakdown) if score_breakdown else "no signals"
        details = (
            f"PCR={pcr} ({pcr_bias}) | Trend={pcr_trend} | "
            f"Pattern={price_oi_pattern} | "
            f"Call OI={total_call_oi:,} Put OI={total_put_oi:,} | "
            f"Bull={bull_score} Bear={bear_score} | "
            f"Breakdown: {breakdown_str}"
        )
        
        # Update state
        _oi_state.update({
            "sentiment": sentiment,
            "pcr_bias": pcr_bias,
            "pcr_trend": pcr_trend,
            "oi_change_bias": oi_change_bias,
            "price_oi_pattern": price_oi_pattern,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "last_update": now,
            "details": details,
            "score_breakdown": score_breakdown,
        })
        
        logger.info(f"📊 OI SENTIMENT: {sentiment} | {details}")
        
        return _oi_state
        
    except Exception as e:
        logger.error(f"OI Sentiment update error: {e}")
        return _oi_state


# =====================================================
# INTERNAL: Fetch OI for a single index
# =====================================================

def _get_nearest_expiry(instruments, index_name):
    """
    Find the nearest weekly expiry for the given index from instruments list.
    Returns a date object or None.
    """
    today = dt_date.today()
    expiries = set()
    for i in instruments:
        if i["name"] == index_name and i["instrument_type"] in ("CE", "PE"):
            exp = i["expiry"]
            if isinstance(exp, datetime):
                exp = exp.date()
            if exp >= today:
                expiries.add(exp)
    return min(expiries) if expiries else None


def _fetch_index_oi(kite_obj, index_symbol, index_name, step):
    """
    Fetch option chain OI for one index using instruments cache + kite.quote().
    Returns dict with call_oi, put_oi, per-strike data, and spot price.
    
    Uses the NFO instruments cache (shared with engine/options.py) to build
    correct tradingsymbols with expiry dates.
    """
    try:
        # Get spot price
        ltp_quote = kite_obj.ltp([index_symbol])
        if not ltp_quote or index_symbol not in ltp_quote:
            return None
        
        spot = ltp_quote[index_symbol]["last_price"]
        atm_strike = round(spot / step) * step
        
        # Load instruments from cache (shared with options.py)
        cache_path = cfg.OPT_CACHE_PKL
        instruments = None
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as f:
                    instruments = pickle.load(f)
            except Exception:
                pass
        
        if not instruments:
            try:
                instruments = kite_obj.instruments(exchange="NFO")
                with open(cache_path, "wb") as f:
                    pickle.dump(instruments, f)
            except Exception as e:
                logger.warning(f"OI: Failed to fetch NFO instruments: {e}")
                return None
        
        # Find nearest expiry
        target_expiry = _get_nearest_expiry(instruments, index_name)
        if not target_expiry:
            logger.warning(f"OI: No expiry found for {index_name}")
            return None
        
        # Build strike range: ±10 strikes around ATM
        target_strikes = set()
        for offset in range(-10, 11):
            target_strikes.add(int(atm_strike + offset * step))
        
        # Find matching tradingsymbols from instruments list
        symbols_to_query = []
        sym_strike_map = {}  # {"NFO:NIFTY26FEB25500CE": (25500, "CE")}
        
        for instr in instruments:
            if instr["name"] != index_name:
                continue
            if instr["instrument_type"] not in ("CE", "PE"):
                continue
            exp = instr["expiry"]
            if isinstance(exp, datetime):
                exp = exp.date()
            if exp != target_expiry:
                continue
            if instr["strike"] not in target_strikes:
                continue
            
            nfo_sym = f"NFO:{instr['tradingsymbol']}"
            symbols_to_query.append(nfo_sym)
            sym_strike_map[nfo_sym] = (int(instr["strike"]), instr["instrument_type"])
        
        if not symbols_to_query:
            logger.warning(f"OI: No option contracts found for {index_name} expiry {target_expiry}")
            return None
        
        # Batch query (max ~42 symbols per call, fits within Kite limits)
        try:
            quotes = kite_obj.quote(symbols_to_query)
        except Exception as e:
            logger.warning(f"OI batch quote failed for {index_name}: {e}")
            return None
        
        total_call_oi = 0
        total_put_oi = 0
        call_oi_by_strike = {}
        put_oi_by_strike = {}
        
        for sym, data in quotes.items():
            oi = data.get("oi", 0)
            if oi <= 0:
                continue
            
            strike, opt_type = sym_strike_map.get(sym, (None, None))
            if strike is None:
                continue
            
            if opt_type == "CE":
                total_call_oi += oi
                call_oi_by_strike[strike] = oi
            elif opt_type == "PE":
                total_put_oi += oi
                put_oi_by_strike[strike] = oi
        
        # Identify max OI strikes (resistance/support walls)
        max_call_strike = max(call_oi_by_strike, key=call_oi_by_strike.get) if call_oi_by_strike else atm_strike
        max_put_strike = max(put_oi_by_strike, key=put_oi_by_strike.get) if put_oi_by_strike else atm_strike
        max_call_oi = call_oi_by_strike.get(max_call_strike, 0)
        max_put_oi = put_oi_by_strike.get(max_put_strike, 0)
        
        return {
            "name": index_name,
            "spot": spot,
            "call_oi": total_call_oi,
            "put_oi": total_put_oi,
            "call_oi_by_strike": call_oi_by_strike,
            "put_oi_by_strike": put_oi_by_strike,
            "max_call_strike": max_call_strike,   # Resistance wall
            "max_put_strike": max_put_strike,      # Support wall
            "max_call_oi": max_call_oi,
            "max_put_oi": max_put_oi,
            "timestamp": datetime.now(_IST),
        }
        
    except Exception as e:
        err_str = str(e).lower()
        logger.error("OI fetch error for %s: %s", index_name, e)
        if "api_key" in err_str or "access_token" in err_str or "invalid" in err_str or "token" in err_str or "incorrect" in err_str:
            logger.error(
                "Kite auth failed for OI. Token may be expired or wrong. "
                "Run morning_login.bat (or zerodha_login.py); engine will pick up new token from Redis within 2 min, or restart engine."
            )
        return None


# =====================================================
# INTERNAL: PCR Trend Analysis
# =====================================================

def _compute_pcr_trend():
    """
    Analyzes PCR direction over recent history.
    
    Returns: "RISING" (bullish), "FALLING" (bearish), or "FLAT"
    """
    history = _oi_state["pcr_history"]
    
    if len(history) < 3:
        return "FLAT"
    
    # Compare latest 3 readings vs previous 3
    recent = [h[1] for h in list(history)[-3:]]
    older = [h[1] for h in list(history)[-6:-3]] if len(history) >= 6 else [h[1] for h in list(history)[:3]]
    
    avg_recent = sum(recent) / len(recent)
    avg_older = sum(older) / len(older)
    
    pct_change = (avg_recent - avg_older) / avg_older if avg_older > 0 else 0
    
    if pct_change > 0.05:    # PCR rose >5%
        return "RISING"
    elif pct_change < -0.05:  # PCR fell >5%
        return "FALLING"
    return "FLAT"


# =====================================================
# INTERNAL: OI Change + Price Direction Analysis
# =====================================================

def _analyze_oi_changes(current_data, kite_obj, fetch_ohlc_fn=None):
    """
    Compares current OI snapshot with previous snapshot.
    Correlates with price direction to classify:
    
    - LONG_BUILDUP:    Price UP + OI UP     (fresh bullish positions)
    - SHORT_COVERING:  Price UP + OI DOWN   (shorts exiting)
    - SHORT_BUILDUP:   Price DOWN + OI UP   (fresh bearish positions)
    - LONG_UNWINDING:  Price DOWN + OI DOWN (longs exiting)
    
    Also returns directional bias from net OI change direction.
    
    Returns: (oi_change_bias, price_oi_pattern)
    """
    prev_snapshots = _oi_state["snapshots"]
    
    if not prev_snapshots:
        return "NEUTRAL", "NONE"
    
    total_prev_call = 0
    total_prev_put = 0
    total_cur_call = 0
    total_cur_put = 0
    price_direction = 0  # +1 bullish, -1 bearish
    
    for name, cur in current_data.items():
        prev = prev_snapshots.get(name)
        if not prev:
            continue
        
        total_prev_call += prev["call_oi"]
        total_prev_put += prev["put_oi"]
        total_cur_call += cur["call_oi"]
        total_cur_put += cur["put_oi"]
        
        # Price direction from spot change
        if cur["spot"] > prev["spot"] * 1.0005:
            price_direction += 1
        elif cur["spot"] < prev["spot"] * 0.9995:
            price_direction -= 1
    
    # Also check underlying candle direction if fetch_ohlc is available
    if fetch_ohlc_fn and price_direction == 0:
        try:
            for sym_pair in cfg.OPT_UNDERLYINGS:
                ohlc = fetch_ohlc_fn(sym_pair["symbol"], "5minute", lookback=5)
                if ohlc and len(ohlc) >= 2:
                    if ohlc[-1]["close"] > ohlc[-2]["close"]:
                        price_direction += 1
                    elif ohlc[-1]["close"] < ohlc[-2]["close"]:
                        price_direction -= 1
        except Exception:
            pass
    
    # Net OI changes
    call_oi_change = total_cur_call - total_prev_call
    put_oi_change = total_cur_put - total_prev_put
    total_oi_change = (call_oi_change + put_oi_change)
    
    # Calculate change percentages
    total_prev = total_prev_call + total_prev_put
    oi_change_pct = abs(total_oi_change) / total_prev * 100 if total_prev > 0 else 0
    
    # OI change directional bias
    # Put OI growing faster than Call OI → bullish (support building)
    # Call OI growing faster than Put OI → bearish (ceiling building)
    if total_prev_call > 0 and total_prev_put > 0:
        call_change_pct = call_oi_change / total_prev_call * 100
        put_change_pct = put_oi_change / total_prev_put * 100
        
        if put_change_pct > call_change_pct + OI_CHANGE_THRESHOLD_PCT:
            oi_change_bias = "BULLISH"  # More put writing = support
        elif call_change_pct > put_change_pct + OI_CHANGE_THRESHOLD_PCT:
            oi_change_bias = "BEARISH"  # More call writing = resistance
        else:
            oi_change_bias = "NEUTRAL"
    else:
        oi_change_bias = "NEUTRAL"
    
    # OI expanding or contracting?
    oi_expanding = total_oi_change > 0
    price_up = price_direction > 0
    price_down = price_direction < 0
    
    # Classify price+OI pattern
    if price_up and oi_expanding:
        pattern = "LONG_BUILDUP"
    elif price_up and not oi_expanding:
        pattern = "SHORT_COVERING"
    elif price_down and oi_expanding:
        pattern = "SHORT_BUILDUP"
    elif price_down and not oi_expanding:
        pattern = "LONG_UNWINDING"
    else:
        pattern = "NONE"
    
    if oi_change_pct < OI_CHANGE_THRESHOLD_PCT:
        # Change too small to be meaningful
        pattern = "NONE"
        oi_change_bias = "NEUTRAL"
    
    return oi_change_bias, pattern


# =====================================================
# UTILITY: Reset state (for testing / new day)
# =====================================================

def reset_oi_state():
    """Reset all OI sentiment state. Call at start of new trading day."""
    _oi_state["snapshots"] = {}
    _oi_state["pcr_history"] = deque(maxlen=OI_PCR_HISTORY_SIZE)
    _oi_state["sentiment"] = "NEUTRAL"
    _oi_state["pcr_bias"] = "NEUTRAL"
    _oi_state["pcr_trend"] = "NEUTRAL"
    _oi_state["oi_change_bias"] = "NEUTRAL"
    _oi_state["price_oi_pattern"] = "NONE"
    _oi_state["bull_score"] = 0
    _oi_state["bear_score"] = 0
    _oi_state["last_update"] = None
    _oi_state["details"] = ""
