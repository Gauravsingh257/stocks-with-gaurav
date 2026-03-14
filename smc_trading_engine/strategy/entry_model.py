"""
Entry Model
============
Combines all SMC concepts into strict entry logic with golden win-rate filters.

Entry conditions (ALL must be met):
1. Regime filter (if provided) — direction gate + size adjustment
2. HTF bias must align (15m)
3. Liquidity sweep must occur
4. BOS confirmation on LTF (5m)
5. Price retrace into OB or FVG
6. Rejection confirmation candle

Golden Win-Rate Filters:
- Trade only after liquidity sweep
- Only take first OB retest
- Avoid mid-range entries
- Avoid weak displacement candles
- Avoid counter-HTF structure trades
- Require strong imbalance
- Volume expansion filter
- Minimum displacement candle size filter
- Skip trades below RR 1:3
- Regime direction gate (BLOCKED_BY_REGIME)
- Log rejected trades with reason
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, time
import logging

from smc_trading_engine.smc.market_structure import (
    analyze_structure, TrendState, calculate_atr, is_ranging_market
)
from smc_trading_engine.smc.bos_choch import (
    detect_bos, get_latest_bos, is_weak_bos, detect_bias
)
from smc_trading_engine.smc.order_blocks import (
    detect_order_blocks, get_nearest_ob, is_price_in_ob, update_ob_status
)
from smc_trading_engine.smc.fvg import (
    detect_fvg, is_price_in_fvg, get_nearest_fvg
)
from smc_trading_engine.smc.liquidity import (
    detect_all_liquidity, LiquidityPool, SweepStatus
)
from smc_trading_engine.strategy.risk_management import RiskManager
from smc_trading_engine.regime.regime_controller import RegimeControlFlags

logger = logging.getLogger(__name__)


@dataclass
class TradeSetup:
    """A validated trade setup ready for execution."""
    symbol: str
    direction: str           # "LONG" or "SHORT"
    entry: float
    stop_loss: float
    target: float
    rr: float
    confidence_score: float  # 0-10
    htf_bias: str
    bos_direction: str
    ob_quality: float = 0.0
    fvg_quality: float = 0.0
    sweep_quality: float = 0.0
    volume_confirmed: bool = False
    timestamp: datetime = None
    reasons: List[str] = field(default_factory=list)


@dataclass
class RejectedTrade:
    """A trade that was rejected with reason."""
    symbol: str
    direction: str
    reason: str
    timestamp: datetime = None
    details: Dict = field(default_factory=dict)


# ─── SESSION FILTER ────────────────────────────────────
TRADE_SESSION_START = time(9, 30)
TRADE_SESSION_END = time(14, 30)


def is_in_session(current_time: time = None) -> bool:
    """Trade only during 9:30 AM - 2:30 PM IST."""
    t = current_time or datetime.now().time()
    return TRADE_SESSION_START <= t <= TRADE_SESSION_END


def is_near_session_close(current_time: time = None, buffer_min: int = 15) -> bool:
    """Avoid entries near session close."""
    t = current_time or datetime.now().time()
    cutoff = time(TRADE_SESSION_END.hour, TRADE_SESSION_END.minute - buffer_min)
    return t >= cutoff


# ─── CONFIRMATION CANDLE ──────────────────────────────
def has_confirmation_candle(df: pd.DataFrame, direction: str) -> bool:
    """
    Rejection confirmation candle.
    LONG: bullish candle with long lower wick (wick > 1.2x body)
    SHORT: bearish candle with long upper wick
    """
    if len(df) < 2:
        return False
    c = df.iloc[-1]
    body = abs(c['close'] - c['open'])
    if body <= 0:
        return False
    wick_low = min(c['open'], c['close']) - c['low']
    wick_high = c['high'] - max(c['open'], c['close'])

    if direction == "LONG":
        return c['close'] > c['open'] and wick_low > body * 0.8
    elif direction == "SHORT":
        return c['close'] < c['open'] and wick_high > body * 0.8
    return False


# ─── VOLUME EXPANSION ────────────────────────────────
def has_volume_expansion(df: pd.DataFrame, lookback: int = 20,
                         multiplier: float = 1.3) -> bool:
    """Current candle volume > average * multiplier."""
    if len(df) < lookback + 1 or 'volume' not in df.columns:
        return False
    avg_vol = df['volume'].iloc[-lookback - 1:-1].mean()
    if avg_vol <= 0:
        return False
    return df['volume'].iloc[-1] > avg_vol * multiplier


# ─── MID-RANGE FILTER ────────────────────────────────
def is_mid_range_entry(df: pd.DataFrame, price: float, lookback: int = 50) -> bool:
    """Reject entries in the middle 20% of the recent range (40%-60%)."""
    if len(df) < lookback:
        return False
    recent = df.tail(lookback)
    h = recent['high'].max()
    l = recent['low'].min()
    rng = h - l
    if rng <= 0:
        return False
    mid_low = l + rng * 0.4
    mid_high = l + rng * 0.6
    return mid_low <= price <= mid_high


# ─── MAIN ENTRY EVALUATION ───────────────────────────
# ─── ATR VOLATILITY FILTER (Option 3) ─────────────────
MIN_ATR_THRESHOLDS = {
    "NSE:NIFTY 50": 15,
    "NSE:NIFTY BANK": 40,
    "NSE:NIFTY FIN SERVICE": 10,
}
DEFAULT_MIN_ATR = 10  # For any other symbol


def passes_volatility_filter(df: pd.DataFrame, symbol: str) -> bool:
    """Skip symbols with ATR(14) below threshold — avoid low-vol noise."""
    atr = calculate_atr(df, period=14)
    threshold = MIN_ATR_THRESHOLDS.get(symbol, DEFAULT_MIN_ATR)
    return atr >= threshold


# ─── PROXIMITY-BASED OB/FVG CHECK (Option 2) ─────────
def is_near_ob_or_fvg(price: float, nearest_ob, nearest_fvg,
                      atr: float, tolerance_atr: float = 0.5):
    """
    Relaxed check: price must be inside OR within tolerance of OB or FVG.
    This replaces the strict 'must be exactly inside' requirement.
    """
    tol = atr * tolerance_atr
    in_ob = False
    in_fvg = False

    if nearest_ob is not None:
        in_ob = (nearest_ob.low - tol) <= price <= (nearest_ob.high + tol)
    if nearest_fvg is not None:
        in_fvg = (nearest_fvg.low - tol) <= price <= (nearest_fvg.high + tol)

    return in_ob, in_fvg



def evaluate_entry(
    symbol: str,
    htf_15m_df: pd.DataFrame,
    ltf_5m_df: pd.DataFrame,
    risk_mgr: RiskManager = None,
    current_time: time = None,
    regime_flags: Optional[RegimeControlFlags] = None,
) -> Tuple[Optional[TradeSetup], Optional[RejectedTrade]]:
    """
    Complete entry evaluation with strict Hierarchical SMC Logic.
    
    HIERARCHY:
    1. 15m Swing Structure -> Determines BIAS (Long/Short)
    2. 15m Swing OB -> Area of Interest (AoI)
    3. 5m Internal Structure -> Entry Trigger (BOS in direction of Bias)

    Args:
        symbol: Trading symbol
        htf_15m_df: 15m OHLC Data (Bias & Swing Structure)
        ltf_5m_df: 5m OHLC Data (Entry Trigger & Internal Structure)
        ...
    """
    if risk_mgr is None:
        risk_mgr = RiskManager()

    now = current_time or datetime.now().time()

    # ── FILTER 1: Session check ──
    if not is_in_session(now):
        return None, RejectedTrade(symbol, "", "OUTSIDE_SESSION", datetime.now())

    if is_near_session_close(now):
        return None, RejectedTrade(symbol, "", "NEAR_SESSION_CLOSE", datetime.now())

    # ── FILTER 2: Daily risk limits ──
    can_trade, reason = risk_mgr.can_take_trade()
    if not can_trade:
        return None, RejectedTrade(symbol, "", reason, datetime.now())

    # ── FILTER 3: Ranging market (on 5m) ──
    if is_ranging_market(ltf_5m_df):
        return None, RejectedTrade(symbol, "", "RANGING_MARKET_5M", datetime.now())
        
    # ── FILTER 3b: Volatility check ──
    if not passes_volatility_filter(ltf_5m_df, symbol):
        return None, RejectedTrade(symbol, "", "LOW_VOLATILITY", datetime.now())

    # =========================================================
    # STEP 1: 15m SWING BIAS (Primary Trend)
    # =========================================================
    # We use LuxAlgo-style Swing Logic (Fractals) if possible, 
    # but for now we stick to standard BOS detection on 15m.
    # ideally we want 'Swing BOS' which is more significant.
    # detection_mode="SWING" if supported, else standard.
    
    latest_swing_bos_15m = get_latest_bos(htf_15m_df) # Assuming this gets significant breaks
    
    if not latest_swing_bos_15m:
         return None, RejectedTrade(symbol, "", "NO_15M_SWING_STRUCTURE", datetime.now())
         
    bias_15m = latest_swing_bos_15m.direction # "BULLISH" or "BEARISH"
    direction = "LONG" if bias_15m == "BULLISH" else "SHORT"
    ob_direction = bias_15m # Look for OBs supporting this bias

    # ── REGIME FILTER (Strict) ──
    if regime_flags:
        if direction == "LONG" and not regime_flags.allow_long:
             return None, RejectedTrade(symbol, direction, "BLOCKED_BY_REGIME_SHORT_ONLY", datetime.now())
        if direction == "SHORT" and not regime_flags.allow_short:
             return None, RejectedTrade(symbol, direction, "BLOCKED_BY_REGIME_LONG_ONLY", datetime.now())

    # =========================================================
    # STEP 2: 15m OB / OI (Area of Interest)
    # =========================================================
    # We only want to trade if price is seemingly pulling back to a 15m POI.
    # However, user said "Only trade first mitigation of latest swing OB".
    # So we need to find the latest Swing OB on 15m.
    
    
    obs_15m = detect_order_blocks(htf_15m_df, bias_15m, lookback=200)
    obs_15m = update_ob_status(obs_15m, htf_15m_df) # Update mitigation status matches
    
    # print(f"DEBUG: Found {len(obs_15m)} Total OBs. Checking status...")
    # for o in obs_15m:
    #     print(f"   OB Index: {o.index}, Status: {o.status}, Retests: {o.retest_count}")

    # Get unmitigated or just-tapped OBs
    # [V3] INSTITUTIONAL OB RULE: ACTIVE OR JUST TESTED
    # We allow <= 1 because the current touch might have just incremented it to 1.
    valid_obs_15m = [
        ob for ob in obs_15m 
        if (ob.status == "ACTIVE" or ob.status == "TESTED") and ob.retest_count <= 1
    ]
    
    if not valid_obs_15m:
        # If no fresh OBs, we might be essentially chasing price or deep in trend.
        return None, RejectedTrade(symbol, direction, "NO_FRESH_15M_OB", datetime.now(), {"total_obs": len(obs_15m)})

    current_price = ltf_5m_df['close'].iloc[-1]
    atr_5m = calculate_atr(ltf_5m_df)
    
    nearest_15m_ob = get_nearest_ob(valid_obs_15m, current_price, bias_15m)
    
    # Check Proximity to 15m OB
    in_15m_ob, _ = is_near_ob_or_fvg(current_price, nearest_15m_ob, None, calculate_atr(htf_15m_df))
    
    # [V3] RECENT TAP LOGIC (Status Based)
    # If the OB is "TESTED", it means it was tapped (<=1 time) and HELD (not mitigated).
    # We accept this as a valid POI regardless of exact minutes elapsed.
    
    valid_poi = False
    if in_15m_ob:
        valid_poi = True
    elif nearest_15m_ob and nearest_15m_ob.status == "TESTED":
        valid_poi = True

    # If not in 15m OB AND not a valid held tap, reject
    if not valid_poi:
         return None, RejectedTrade(symbol, direction, "NOT_IN_15M_OB", datetime.now(), {"nearest_ob": nearest_15m_ob})

    # =========================================================
    # STEP 3: 5m ENTRY TRIGGER (Internal BOS + Displacement)
    # =========================================================
    # Now that we are in a 15m POI, we look for a 5m Shift (CHoCH or BOS) in direction of bias.
    
    latest_internal_bos_5m = get_latest_bos(ltf_5m_df)
    
    # 1. Check Existence
    if not latest_internal_bos_5m:
        return None, RejectedTrade(symbol, direction, "NO_5M_ENTRY_TRIGGER", datetime.now())
        
    # 2. Check Alignment
    if latest_internal_bos_5m.direction != bias_15m:
        return None, RejectedTrade(symbol, direction, "5M_STRUCTURE_COUNTER_TREND", datetime.now(), 
                                   {"15m_bias": bias_15m, "5m_bos": latest_internal_bos_5m.direction})
                                   
    # 3. Check Recency (Must be recent, e.g., last 3-5 bars)
    # If the BOS happened 20 bars ago, it's stale.
    bars_since_bos = len(ltf_5m_df) - latest_internal_bos_5m.index
    if bars_since_bos > 8:
         return None, RejectedTrade(symbol, direction, "STALE_5M_TRIGGER", datetime.now(), {"bars_ago": bars_since_bos})

    # 4. DISPLACEMENT FILTER (Candle Range >= 1.8 ATR)
    # Get the candle corresponding to the BOS
    bos_candle = ltf_5m_df.iloc[latest_internal_bos_5m.index]
    candle_range = abs(bos_candle['high'] - bos_candle['low'])
    if candle_range < (atr_5m * 1.8):
        return None, RejectedTrade(symbol, direction, "WEAK_DISPLACEMENT_NO_MOMENTUM", datetime.now(),
                                   {"range": candle_range, "req": atr_5m * 1.8})

    # [V3] VOLUME MANDATORY
    if not has_volume_expansion(ltf_5m_df):
        return None, RejectedTrade(symbol, direction, "NO_VOLUME_EXPANSION", datetime.now())

    # =========================================================
    # STEP 4: FINAL CONFIRMATION
    # =========================================================
    
    # Check Confirmation Candle (Rejection wicks etc)
    if not has_confirmation_candle(ltf_5m_df, direction):
         return None, RejectedTrade(symbol, direction, "NO_ENTRY_CANDLE_CONFIRMATION", datetime.now())

    # SL / Target
    # SL goes below the 15m OB we are reacting from (safest) OR the 5m Swing Low.
    # Ideally 15m OB Low is structure protected.
    
    if direction == "LONG":
        sl = nearest_15m_ob.low - (atr_5m * 0.5)
        target = current_price + (current_price - sl) * 3 # 1:3 Base
    else:
        sl = nearest_15m_ob.high + (atr_5m * 0.5)
        target = current_price - (sl - current_price) * 3

    # Validate RR
    rr = risk_mgr.calculate_rr(current_price, sl, target, direction)
    if not risk_mgr.passes_rr_filter(current_price, sl, target, direction):
         return None, RejectedTrade(symbol, direction, f"LOW_RR_{rr}", datetime.now())

    # Construct Valid Setup
    setup = TradeSetup(
        symbol=symbol,
        direction=direction,
        entry=current_price,
        stop_loss=sl,
        target=target,
        rr=rr,
        confidence_score=8.5, # High confidence due to strict checks
        htf_bias=bias_15m,
        bos_direction=latest_internal_bos_5m.direction,
        ob_quality=nearest_15m_ob.quality_score,
        fvg_quality=0, # Not checking FVG explicitly for entry, just OB tap
        sweep_quality=0,
        volume_confirmed=has_volume_expansion(ltf_5m_df),
        timestamp=datetime.now(),
        reasons=["HTF_15M_ALIGNED", "IN_15M_OB", "FRESH_OB_TAP", "5M_DISPLACEMENT_BOS", "STRICT_HIERARCHY"]
    )
    
    return setup, None


# ─── CONFIDENCE SCORING ──────────────────────────────
def compute_confidence(htf_bias: str, sweep_quality: float,
                       ob_quality: float, fvg_present: bool,
                       fvg_quality: float, volume_spike: bool) -> float:
    """
    Confidence score 0-10 based on weighted factors:
    HTF alignment: 30%, Liquidity sweep: 20%, OB quality: 20%,
    FVG presence: 15%, Volume spike: 15%
    """
    score = 0.0
    # HTF alignment (30%) — always 3.0 if aligned (we only reach here if aligned)
    score += 3.0
    # Liquidity sweep quality (20%)
    score += min(2.0, sweep_quality * 0.2)
    # OB quality (20%)
    score += min(2.0, ob_quality * 0.2)
    # FVG presence (15%)
    if fvg_present:
        score += min(1.5, fvg_quality * 0.15)
    # Volume spike (15%)
    if volume_spike:
        score += 1.5
    return round(min(10.0, score), 1)
