"""
engine/oi_short_covering.py — Strike-Level OI Short-Covering Detector
======================================================================
Detects aggressive OI drops on individual option strikes combined with
price appreciation — the hallmark of SHORT COVERING.

Theory (Institutional OI Interpretation):
──────────────────────────────────────────
  CE OI drops + CE price rises → Call writers covering (BULLISH for underlying)
  PE OI drops + PE price rises → Put writers covering (BEARISH for underlying)

Why this matters vs aggregate OI:
─────────────────────────────────
  The existing oi_sentiment.py tracks TOTAL call/put OI across ±10 strikes.
  A 70K OI drop on ONE strike (e.g. BANKNIFTY 61000CE) can produce a 12% option
  spike while aggregate OI barely moves (PCR changes <1%).

  This module tracks OI per-strike over time and correlates with option LTP
  to detect the SHORT COVERING pattern at the micro (strike) level.

Detection Conditions (scored, need >= minimum):
─────────────────────────────────────────────────
  1. OI DROP:    Strike OI decreased by >= 5% in rolling window
  2. PRICE RISE: Option LTP increased by >= 3% in same window
  3. VELOCITY:   OI dropping fast (>= 2% per reading)
  4. VOLUME:     Trading volume spike (> 1.3× average)
  5. SUSTAINED:  OI drop from intraday peak >= 8%

Trade Logic:
────────────
  CE short covering → underlying BULLISH → BUY CE
  PE short covering → underlying BEARISH → BUY PE

  SL: Recent swing low minus buffer
  Target: 2R minimum

Risk Controls:
──────────────
  • Max 1 short-covering trade per underlying per day
  • Must pass global risk_management.py daily limits
  • Respects circuit breaker + multi-day DD halt
  • Minimum score threshold of 5/10

Integration:
────────────
  Called from BankNiftySignalEngine.poll() alongside evaluate_signals().
  Manages its own per-strike OI history internally.
"""

import logging
import os
import pickle
import csv
from datetime import datetime, time, timedelta, date as dt_date
from collections import deque
from enum import Enum
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

from engine import config as cfg
from engine.expiry_manager import get_atm_strikes, get_target_expiries

# SMC structure detectors — used for confluence filtering
import smc_detectors as smc

logger = logging.getLogger(__name__)

# Workspace root (for CSV/DB paths)
_WORKSPACE = Path(__file__).resolve().parents[1]
_TRADE_LEDGER = _WORKSPACE / "trade_ledger_2026.csv"
_OI_SC_TRADES_FILE = _WORKSPACE / "oi_sc_active_trades.json"
_OI_SC_SNAPSHOT_FILE = _WORKSPACE / "oi_sc_snapshot.json"


# =====================================================
# CONFIGURATION (from engine/config.py)
# =====================================================
OI_SC_REFRESH_SECS = getattr(cfg, "OI_SC_REFRESH_SECS", 60)
OI_SC_HISTORY_SIZE = getattr(cfg, "OI_SC_HISTORY_SIZE", 60)
OI_SC_MIN_READINGS = getattr(cfg, "OI_SC_MIN_READINGS", 3)
OI_SC_ROLLING_WINDOW = getattr(cfg, "OI_SC_ROLLING_WINDOW", 5)
OI_SC_MIN_OI_DROP_PCT = getattr(cfg, "OI_SC_MIN_OI_DROP_PCT", 0.05)
OI_SC_MIN_PRICE_RISE_PCT = getattr(cfg, "OI_SC_MIN_PRICE_RISE_PCT", 0.03)
OI_SC_PEAK_DROP_PCT = getattr(cfg, "OI_SC_PEAK_DROP_PCT", 0.08)
OI_SC_VELOCITY_PCT = getattr(cfg, "OI_SC_VELOCITY_PCT", 0.02)
OI_SC_VOLUME_MULT = getattr(cfg, "OI_SC_VOLUME_MULT", 1.3)
OI_SC_MIN_SCORE = getattr(cfg, "OI_SC_MIN_SCORE", 5)
OI_SC_MAX_PER_UL_DAY = getattr(cfg, "OI_SC_MAX_PER_UL_DAY", 1)
OI_SC_SL_ATR_MULT = getattr(cfg, "OI_SC_SL_ATR_MULT", 1.2)
OI_SC_TARGET_RR = getattr(cfg, "OI_SC_TARGET_RR", 2.0)
OI_SC_ALERT_COOLDOWN_SECS = getattr(cfg, "OI_SC_ALERT_COOLDOWN_SECS", 300)
# Hard-block when 5m bias contradicts signal AND score (after all adjustments) <= this
OI_SC_BIAS_CONFLICT_HARD_BLOCK = getattr(cfg, "OI_SC_BIAS_CONFLICT_HARD_BLOCK", 5)
# Consecutive same-direction 5m candles that constitute exhaustion (suppress signal)
OI_SC_EXHAUSTION_CANDLES = getattr(cfg, "OI_SC_EXHAUSTION_CANDLES", 4)

# V4.3r — Bias/Entry 2-Layer System
OI_SC_BIAS_WINDOW_MINS = getattr(cfg, "OI_SC_BIAS_WINDOW_MINS", 30)
OI_SC_SIGNAL_LOCK_MINS = getattr(cfg, "OI_SC_SIGNAL_LOCK_MINS", 60)
OI_SC_MIN_RR = getattr(cfg, "OI_SC_MIN_RR", 1.5)
OI_SC_IMPULSE_ATR_MULT = getattr(cfg, "OI_SC_IMPULSE_ATR_MULT", 2.0)
OI_SC_RANGE_EXPANSION_BLOCK = getattr(cfg, "OI_SC_RANGE_EXPANSION_BLOCK", 1.5)
OI_SC_PULLBACK_RETRACE_PCT = getattr(cfg, "OI_SC_PULLBACK_RETRACE_PCT", 0.40)
OI_SC_SL_STRUCTURE_BUFFER = getattr(cfg, "OI_SC_SL_STRUCTURE_BUFFER", 0.2)
OI_SC_CONFIRMATION_WICK_PCT = getattr(cfg, "OI_SC_CONFIRMATION_WICK_PCT", 0.55)


# =====================================================
# STATE — Module-Level
# =====================================================

# Per-strike OI + LTP history
# Key: tradingsymbol e.g. "BANKNIFTY26MAR61000CE"
# Value: deque of (datetime, oi, ltp, volume)
_strike_history = {}

# Intraday peak OI per strike (for peak-drop detection)
_strike_peak_oi = {}

# Alerted strikes today (dedup)
_alerted_today = {}  # {underlying_date_opttype: datetime}

# Daily trade count per underlying
_daily_trade_count = {}  # {"BANKNIFTY_2026-02-26": count}

# Last scan timestamp
_last_scan_time = None

# Instruments cache (shared with other modules)
_instruments_cache = None

# Active OI SC trades being monitored for outcome
_oi_sc_active_trades = []  # list of {symbol, entry, sl, target, entry_time, ...}

# V4.3r — Bias/Entry 2-Layer State
_signal_locks = {}      # {(underlying, direction): expiry_datetime}
_active_biases = []     # bias objects awaiting pullback entry
_daily_atr_cache = {}   # {f"{underlying}_{date}": float}


# =====================================================
# V4.3r — MARKET PHASE ENUM
# =====================================================

class MarketPhase(Enum):
    OPENING = "OPENING"                # 09:15-09:45, unstable
    EARLY_TREND = "EARLY_TREND"        # directional, range building
    EXTENDED_TREND = "EXTENDED_TREND"  # range > 1.5x daily ATR, still impulsive
    CONSOLIDATION = "CONSOLIDATION"    # low range or stalled within trend
    DISTRIBUTION = "DISTRIBUTION"      # reversing from extreme
    LATE_SESSION = "LATE_SESSION"      # after 14:00, avoid new entries


# =====================================================
# V4.3r — SIGNAL LOCK SYSTEM
# =====================================================

def is_signal_locked(underlying: str, direction: str) -> bool:
    """Check if (underlying, direction) combo is locked."""
    key = (underlying, direction)
    if key not in _signal_locks:
        return False
    if datetime.now(_IST) >= _signal_locks[key]:
        del _signal_locks[key]
        return False
    return True


def register_signal_lock(underlying: str, direction: str):
    """Lock (underlying, direction) for OI_SC_SIGNAL_LOCK_MINS."""
    key = (underlying, direction)
    _signal_locks[key] = datetime.now(_IST) + timedelta(minutes=OI_SC_SIGNAL_LOCK_MINS)
    logger.info(
        "OI-SC lock: %s %s until %s",
        underlying, direction, _signal_locks[key].strftime("%H:%M")
    )


def _cleanup_expired_locks():
    """Remove stale locks."""
    now = datetime.now(_IST)
    expired = [k for k, v in _signal_locks.items() if now >= v]
    for k in expired:
        del _signal_locks[k]


# =====================================================
# V4.3r — MARKET PHASE DETECTION
# =====================================================

def _get_daily_atr(underlying: str, fetch_ohlc_fn) -> float:
    """Compute daily ATR from 1H candles (cached per day). Fallback: BN=550, NF=180."""
    today = datetime.now(_IST).date().isoformat()
    cache_key = f"{underlying}_{today}"
    if cache_key in _daily_atr_cache:
        return _daily_atr_cache[cache_key]

    try:
        idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"
        candles_1h = fetch_ohlc_fn(idx_sym, "60minute", 30)
        if candles_1h and len(candles_1h) >= 10:
            daily_atr = smc.calculate_atr(candles_1h, period=10) * 4
            if daily_atr > 0:
                _daily_atr_cache[cache_key] = daily_atr
                return daily_atr
    except Exception:
        pass

    fallback = {"NIFTY": 180, "BANKNIFTY": 550}
    val = fallback.get(underlying, 300)
    _daily_atr_cache[cache_key] = val
    return val


def detect_market_phase(underlying: str, fetch_ohlc_fn) -> MarketPhase:
    """Classify current market phase using time, range expansion, and structure."""
    now = datetime.now(_IST)
    minutes_since_open = (now.hour - 9) * 60 + now.minute - 15

    if minutes_since_open < 30:
        return MarketPhase.OPENING
    if now.hour >= 14:
        return MarketPhase.LATE_SESSION

    idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"
    try:
        candles = fetch_ohlc_fn(idx_sym, "5minute", 80)
        if not candles or len(candles) < 10:
            return MarketPhase.EARLY_TREND
    except Exception:
        return MarketPhase.EARLY_TREND

    today = now.date()
    today_candles = [c for c in candles if c["date"].date() == today]
    if len(today_candles) < 5:
        return MarketPhase.EARLY_TREND

    open_price = today_candles[0]["open"]
    day_high = max(c["high"] for c in today_candles)
    day_low = min(c["low"] for c in today_candles)
    day_range = day_high - day_low
    spot = today_candles[-1]["close"]
    daily_atr = _get_daily_atr(underlying, fetch_ohlc_fn)

    range_expansion = day_range / daily_atr if daily_atr > 0 else 0
    position = (spot - day_low) / day_range if day_range > 0 else 0.5

    n_recent = min(6, len(today_candles))
    recent = today_candles[-n_recent:]
    recent_move = recent[-1]["close"] - recent[0]["open"]
    day_move = spot - open_price

    atr_5m = smc.calculate_atr(today_candles) if len(today_candles) > 15 else daily_atr / 10

    # EXTENDED_TREND: range exceeds typical daily range
    if range_expansion >= OI_SC_RANGE_EXPANSION_BLOCK:
        # If recent 30-min move is small → consolidation within trend, allow entry
        if abs(recent_move) < atr_5m * 0.3:
            return MarketPhase.CONSOLIDATION
        return MarketPhase.EXTENDED_TREND

    # DISTRIBUTION: large range + price reversing from extreme
    if range_expansion > 0.8:
        reversing_from_high = (day_move > 0 and position < 0.65 and recent_move < 0)
        reversing_from_low = (day_move < 0 and position > 0.35 and recent_move > 0)
        if reversing_from_high or reversing_from_low:
            return MarketPhase.DISTRIBUTION

    # CONSOLIDATION: small range, no strong move
    if range_expansion < 0.4 and abs(recent_move) < atr_5m * 0.5:
        return MarketPhase.CONSOLIDATION

    return MarketPhase.EARLY_TREND


def is_phase_allowed(phase: MarketPhase, direction: str) -> tuple:
    """Return (allowed, reason) for OI-SC signal in given phase."""
    rules = {
        MarketPhase.OPENING: (False, "Opening phase (09:15-09:45) - unstable"),
        MarketPhase.EARLY_TREND: (True, "Early trend - allowed with pullback"),
        MarketPhase.EXTENDED_TREND: (False, "Range > %.1fx daily ATR" % OI_SC_RANGE_EXPANSION_BLOCK),
        MarketPhase.CONSOLIDATION: (True, "Consolidation - allowed"),
        MarketPhase.DISTRIBUTION: (False, "Distribution - prefer reversal setups"),
        MarketPhase.LATE_SESSION: (False, "After 14:00 - no new OI-SC entries"),
    }
    return rules.get(phase, (True, ""))


# =====================================================
# V4.3r — IMPULSE EXHAUSTION
# =====================================================

def is_impulse_exhausted(underlying: str, direction: str, fetch_ohlc_fn) -> tuple:
    """
    Block if 30-min move > OI_SC_IMPULSE_ATR_MULT * ATR OR 5+ consecutive candles.
    Returns (exhausted: bool, reason: str).
    """
    try:
        idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"
        candles = fetch_ohlc_fn(idx_sym, "5minute", 40)
        if not candles or len(candles) < 8:
            return False, ""

        today = datetime.now(_IST).date()
        today_candles = [c for c in candles if c["date"].date() == today]
        if len(today_candles) < 6:
            return False, ""

        atr = smc.calculate_atr(today_candles) if len(today_candles) > 15 else smc.calculate_atr(candles)
        if atr <= 0:
            return False, ""

        # Check 1: 30-min move magnitude
        recent_6 = today_candles[-6:]
        recent_move = recent_6[-1]["close"] - recent_6[0]["open"]
        threshold = atr * OI_SC_IMPULSE_ATR_MULT

        if direction == "BULLISH" and recent_move > threshold:
            return True, "30-min rally %.0fpts > %.0fpt threshold" % (recent_move, threshold)
        if direction == "BEARISH" and recent_move < -threshold:
            return True, "30-min drop %.0fpts > %.0fpt threshold" % (abs(recent_move), threshold)

        # Check 2: 5+ consecutive directional candles
        streak = 0
        for c in reversed(today_candles):
            if direction == "BULLISH" and c["close"] > c["open"]:
                streak += 1
            elif direction == "BEARISH" and c["close"] < c["open"]:
                streak += 1
            else:
                break
        if streak >= 5:
            color = "green" if direction == "BULLISH" else "red"
            return True, "%d consecutive %s candles" % (streak, color)

    except Exception:
        pass
    return False, ""


# =====================================================
# V4.3r — LAYER 2: ENTRY QUALIFICATION
# =====================================================

def _find_qualifying_zone(bias: dict, spot: float, today_candles: list) -> dict:
    """Find nearest qualifying zone (FVG/OB/swing) that price is inside. Returns dict or None."""
    direction = bias["direction"]
    zones = bias.get("frozen_zones", {})

    # Check FVG first (most precise)
    fvg_key = "fvg_long" if direction == "BULLISH" else "fvg_short"
    fvg = zones.get(fvg_key)
    if fvg and fvg[0] <= spot <= fvg[1]:
        return {"type": "FVG", "low": fvg[0], "high": fvg[1]}

    # Check OB second
    ob_key = "ob_long" if direction == "BULLISH" else "ob_short"
    ob = zones.get(ob_key)
    if ob and ob[0] <= spot <= ob[1]:
        return {"type": "OB", "low": ob[0], "high": ob[1]}

    # Check swing zone fallback
    swing = _build_swing_zone(direction, spot, today_candles)
    if swing and swing["low"] <= spot <= swing["high"]:
        return swing

    return None


def _build_swing_zone(direction: str, spot: float, today_candles: list) -> dict:
    """Build zone from most recent swing point. Returns dict or None."""
    if len(today_candles) < 10:
        return None
    try:
        atr = smc.calculate_atr(today_candles) if len(today_candles) > 15 else 0
        if atr <= 0:
            return None
        swing_highs, swing_lows = smc.detect_swing_points(today_candles, left=2, right=2)
        if direction == "BULLISH" and swing_lows:
            last_sl = swing_lows[-1][1]
            return {"type": "SWING", "low": last_sl - atr * 0.2, "high": last_sl + atr * 0.3}
        if direction == "BEARISH" and swing_highs:
            last_sh = swing_highs[-1][1]
            return {"type": "SWING", "low": last_sh - atr * 0.3, "high": last_sh + atr * 0.2}
    except Exception:
        pass
    return None


def _has_confirmation_candle(direction: str, today_candles: list) -> bool:
    """Last closed 5m candle must show rejection or structure hold."""
    if len(today_candles) < 3:
        return False
    last = today_candles[-2]  # last closed
    prev = today_candles[-3]
    candle_range = last["high"] - last["low"]
    if candle_range <= 0:
        return False

    if direction == "BULLISH":
        is_green = last["close"] > last["open"]
        higher_low = last["low"] > prev["low"]
        body_low = min(last["open"], last["close"])
        rejection = ((body_low - last["low"]) / candle_range) > OI_SC_CONFIRMATION_WICK_PCT
        return is_green or higher_low or rejection
    else:
        is_red = last["close"] < last["open"]
        lower_high = last["high"] < prev["high"]
        body_high = max(last["open"], last["close"])
        rejection = ((last["high"] - body_high) / candle_range) > OI_SC_CONFIRMATION_WICK_PCT
        return is_red or lower_high or rejection


def _qualifies_pullback_exception(bias: dict, spot: float, today_candles: list) -> bool:
    """
    Override phase block if price retraced >= OI_SC_PULLBACK_RETRACE_PCT of last impulse
    AND has confirmation candle.
    """
    direction = bias["direction"]
    n = min(12, len(today_candles))
    recent = today_candles[-n:]
    impulse_high = max(c["high"] for c in recent)
    impulse_low = min(c["low"] for c in recent)
    impulse_range = impulse_high - impulse_low
    if impulse_range <= 0:
        return False

    if direction == "BULLISH":
        retracement = (impulse_high - spot) / impulse_range
    else:
        retracement = (spot - impulse_low) / impulse_range

    if retracement < OI_SC_PULLBACK_RETRACE_PCT:
        return False
    return _has_confirmation_candle(direction, today_candles)


def is_near_key_level(direction: str, spot: float, today_candles: list) -> bool:
    """Block if entry is within 0.5x ATR of day high (BULLISH) or day low (BEARISH)."""
    day_high = max(c["high"] for c in today_candles)
    day_low = min(c["low"] for c in today_candles)
    try:
        atr = smc.calculate_atr(today_candles) if len(today_candles) > 15 else 0
    except Exception:
        atr = 0
    if atr <= 0:
        return False
    buffer = atr * 0.5
    if direction == "BULLISH":
        return (day_high - spot) < buffer
    return (spot - day_low) < buffer


# =====================================================
# V4.3r — RISK ENGINE (SL / TARGET / STRIKE)
# =====================================================

def _select_itm_strike(underlying: str, spot: float, opt_type: str) -> tuple:
    """Select 1-strike ITM. Returns (strike, step)."""
    step = 100 if underlying == "BANKNIFTY" else 50
    atm = round(spot / step) * step
    if opt_type == "CE":
        return int(atm - step), step
    return int(atm + step), step


def _estimate_delta(spot: float, strike: int, opt_type: str) -> float:
    """Estimate delta from moneyness (no live Greeks needed)."""
    if opt_type == "CE":
        moneyness = (spot - strike) / strike
    else:
        moneyness = (strike - spot) / strike

    if moneyness > 0.03:
        return 0.80
    if moneyness > 0.015:
        return 0.65
    if moneyness > 0.005:
        return 0.55
    if moneyness > -0.005:
        return 0.50
    if moneyness > -0.015:
        return 0.35
    if moneyness > -0.03:
        return 0.25
    return 0.15


def compute_trade_levels_v43(direction: str, entry_spot: float, zone: dict,
                              atr: float, bias: dict) -> dict:
    """
    V4.3r SL/target computation using hybrid structure+ATR stop loss.
    Returns trade levels dict or None if invalid.
    """
    underlying = bias["underlying"]
    opt_type = bias["opt_type"]

    # Hybrid SL: wider of structure-based and ATR-based
    if direction == "BULLISH":
        structure_sl = zone["low"] - (atr * OI_SC_SL_STRUCTURE_BUFFER)
        atr_sl = entry_spot - (atr * OI_SC_SL_ATR_MULT)
        sl_spot = min(structure_sl, atr_sl)  # wider = further from entry
    else:
        structure_sl = zone["high"] + (atr * OI_SC_SL_STRUCTURE_BUFFER)
        atr_sl = entry_spot + (atr * OI_SC_SL_ATR_MULT)
        sl_spot = max(structure_sl, atr_sl)

    risk_spot = abs(entry_spot - sl_spot)
    if risk_spot <= 0:
        return None

    # Target at 2R
    if direction == "BULLISH":
        target_spot = entry_spot + (risk_spot * OI_SC_TARGET_RR)
    else:
        target_spot = entry_spot - (risk_spot * OI_SC_TARGET_RR)

    actual_rr = abs(target_spot - entry_spot) / risk_spot
    if actual_rr < OI_SC_MIN_RR:
        return None

    # Strike selection: 1 ITM
    strike, step = _select_itm_strike(underlying, entry_spot, opt_type)
    delta = _estimate_delta(entry_spot, strike, opt_type)

    return {
        "entry_spot": round(entry_spot, 2),
        "sl_spot": round(sl_spot, 2),
        "target_spot": round(target_spot, 2),
        "risk_spot": round(risk_spot, 2),
        "rr": round(actual_rr, 2),
        "strike": strike,
        "delta_estimate": delta,
        "sl_method": "hybrid",
        "structure_sl": round(structure_sl, 2),
        "atr_sl": round(atr_sl, 2),
        "zone_type": zone["type"],
        "zone_low": round(zone["low"], 2),
        "zone_high": round(zone["high"], 2),
    }


# =====================================================
# V4.3r — BIAS WRAPPER + ENTRY MANAGER
# =====================================================

def _detect_oi_sc_bias(tradingsymbol, info, spot, fetch_ohlc_fn):
    """
    Layer 1: Detect OI-SC and create BIAS object (not a trade signal).
    Calls existing _detect_strike_short_covering() for scoring, then
    applies V4.3r gates (lock, phase, impulse). Returns bias dict or None.
    """
    underlying = info["underlying"]
    opt_type = info["opt_type"]
    direction = "BULLISH" if opt_type == "CE" else "BEARISH"

    # V4.3r gate 1: Signal lock
    if is_signal_locked(underlying, direction):
        return None

    # Run existing detection (all scoring, SMC adjustment, bias-conflict, etc.)
    signal = _detect_strike_short_covering(tradingsymbol, info, spot, fetch_ohlc_fn)
    if signal is None:
        return None

    # Structure alerts pass through unchanged (not a trade signal)
    if signal.get("signal_type") == "OI_SC_STRUCTURE_ALERT":
        return signal  # pass through for separate handling

    # V4.3r gate 2: Market phase
    try:
        phase = detect_market_phase(underlying, fetch_ohlc_fn)
        allowed, phase_reason = is_phase_allowed(phase, direction)
        if not allowed:
            # Check deep pullback exception
            idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"
            try:
                candles = fetch_ohlc_fn(idx_sym, "5minute", 40)
                today = datetime.now(_IST).date()
                today_candles = [c for c in candles if c["date"].date() == today] if candles else []
            except Exception:
                today_candles = []

            fake_bias = {"direction": direction}
            if today_candles and _qualifies_pullback_exception(fake_bias, spot, today_candles):
                logger.info(
                    "OI-SC phase %s overridden by pullback exception for %s %s",
                    phase.value, underlying, direction
                )
            else:
                logger.info(
                    "OI-SC BIAS blocked by phase %s: %s %s | %s",
                    phase.value, underlying, direction, phase_reason
                )
                return None
    except Exception as e:
        logger.debug("OI-SC phase check failed (non-fatal): %s", e)
        phase = MarketPhase.EARLY_TREND  # fail open

    # V4.3r gate 3: Impulse exhaustion
    try:
        exhausted, ex_reason = is_impulse_exhausted(underlying, direction, fetch_ohlc_fn)
        if exhausted:
            logger.info("OI-SC BIAS blocked (impulse exhausted): %s %s | %s", underlying, direction, ex_reason)
            return None
    except Exception:
        pass  # fail open

    # Freeze SMC zones from signal's structure data
    smc_data = signal.get("smc_structure", {})
    frozen_zones = {
        "fvg_long": smc_data.get("fvg_long"),
        "fvg_short": smc_data.get("fvg_short"),
        "ob_long": smc_data.get("ob_long"),
        "ob_short": smc_data.get("ob_short"),
        "atr": smc_data.get("atr", 0) if isinstance(smc_data, dict) else 0,
        "snapshot_time": datetime.now(_IST),
    }

    # Build bias object
    now = datetime.now(_IST)
    score = signal.get("score", 5)
    # Adaptive window: higher score = longer wait
    window = 45 if score >= 8 else (30 if score >= 7 else 20)

    bias = {
        "id": "BIAS-%s-%s-%s" % (underlying, direction, now.strftime("%H%M%S")),
        "underlying": underlying,
        "direction": direction,
        "opt_type": opt_type,
        "trigger_strike": info["strike"],
        "trigger_tradingsymbol": tradingsymbol,
        "trigger_spot": spot,
        "trigger_ltp": signal.get("current_ltp", 0),
        "score": score,
        "score_breakdown": signal.get("score_breakdown", {}),
        "trigger_time": now,
        "expiry_time": now + timedelta(minutes=window),
        "frozen_zones": frozen_zones,
        "phase_at_trigger": phase.value if isinstance(phase, MarketPhase) else str(phase),
        "oi_data": {
            "current_oi": signal.get("current_oi", 0),
            "peak_oi": signal.get("peak_oi", 0),
        },
        "original_signal": signal,  # keep full signal for original alert format
        "status": "WAITING_ENTRY",
        "entry_attempts": 0,
    }

    register_signal_lock(underlying, direction)
    return bias


def check_entry_for_bias(bias: dict, fetch_ohlc_fn, kite_obj) -> dict:
    """
    Layer 2: Check if current price qualifies for entry on this bias.
    Returns trade signal dict or None.
    """
    underlying = bias["underlying"]
    direction = bias["direction"]
    idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"

    # Get current spot
    try:
        ltp_data = kite_obj.ltp([idx_sym])
        spot = ltp_data[idx_sym]["last_price"]
    except Exception:
        return None

    # Get today's 5m candles
    try:
        candles = fetch_ohlc_fn(idx_sym, "5minute", 40)
        if not candles or len(candles) < 10:
            return None
        today = datetime.now(_IST).date()
        today_candles = [c for c in candles if c["date"].date() == today]
        if len(today_candles) < 5:
            return None
    except Exception:
        return None

    # RULE 1: Price must have pulled back from trigger
    trigger_spot = bias["trigger_spot"]
    if direction == "BULLISH" and spot >= trigger_spot:
        return None
    if direction == "BEARISH" and spot <= trigger_spot:
        return None

    # RULE 2: Price inside qualifying zone
    zone = _find_qualifying_zone(bias, spot, today_candles)
    if zone is None:
        if _qualifies_pullback_exception(bias, spot, today_candles):
            zone = _build_swing_zone(direction, spot, today_candles)
        if zone is None:
            return None

    # RULE 3: Confirmation candle
    # Relaxed for deep retrace (>60%) + high score (7+)
    if not _has_confirmation_candle(direction, today_candles):
        impulse_high = max(c["high"] for c in today_candles[-12:]) if len(today_candles) >= 12 else 0
        impulse_low = min(c["low"] for c in today_candles[-12:]) if len(today_candles) >= 12 else 0
        imp_range = impulse_high - impulse_low
        if imp_range > 0 and bias["score"] >= 7:
            if direction == "BULLISH":
                retrace = (impulse_high - spot) / imp_range
            else:
                retrace = (spot - impulse_low) / imp_range
            if retrace < 0.60:
                return None  # not deep enough to skip confirmation
        else:
            return None

    # RULE 4: Not too close to key level
    if is_near_key_level(direction, spot, today_candles):
        return None

    # RULE 5: Compute levels + RR check
    atr = bias["frozen_zones"].get("atr", 0)
    if not atr or atr <= 0:
        try:
            atr = smc.calculate_atr(today_candles) if len(today_candles) > 15 else smc.calculate_atr(candles)
        except Exception:
            return None
    if atr <= 0:
        return None

    levels = compute_trade_levels_v43(direction, spot, zone, atr, bias)
    if levels is None:
        return None
    if levels["rr"] < OI_SC_MIN_RR:
        return None

    # Build entry signal (compatible with existing _log_oi_sc_trade_entry format)
    now = datetime.now(_IST)
    opt_type = bias["opt_type"]
    strike = levels["strike"]
    trade_action = "BUY_%s" % opt_type

    return {
        "signal_type": "OI_SC_ENTRY",
        "tradingsymbol": bias["trigger_tradingsymbol"],
        "underlying": underlying,
        "strike": strike,
        "opt_type": opt_type,
        "spot": spot,
        "trade_action": trade_action,
        "underlying_bias": direction,
        "score": bias["score"],
        "score_breakdown": bias["score_breakdown"],
        "current_oi": bias["oi_data"].get("current_oi", 0),
        "peak_oi": bias["oi_data"].get("peak_oi", 0),
        "current_ltp": bias["trigger_ltp"],
        "trade_levels": {
            "entry": levels["entry_spot"],
            "sl": levels["sl_spot"],
            "target": levels["target_spot"],
            "risk": levels["risk_spot"],
            "rr": levels["rr"],
        },
        "bias_id": bias["id"],
        "entry_zone_type": zone["type"],
        "trigger_to_entry_mins": (now - bias["trigger_time"]).total_seconds() / 60,
        "phase_at_trigger": bias["phase_at_trigger"],
        "timestamp": now,
        "smc_structure": bias.get("frozen_zones", {}),
    }


def _manage_active_biases(fetch_ohlc_fn, kite_obj) -> list:
    """Check all active biases for entry qualification. Returns list of entry signals."""
    now = datetime.now(_IST)
    signals = []

    for bias in _active_biases:
        if bias["status"] != "WAITING_ENTRY":
            continue

        if now >= bias["expiry_time"]:
            bias["status"] = "EXPIRED"
            logger.info("Bias expired: %s (no entry in %d attempts)", bias["id"], bias["entry_attempts"])
            continue

        bias["entry_attempts"] += 1

        try:
            signal = check_entry_for_bias(bias, fetch_ohlc_fn, kite_obj)
            if signal:
                bias["status"] = "ENTRY_FOUND"
                signals.append(signal)
                logger.info("Bias %s -> ENTRY at %.2f (zone: %s)", bias["id"], signal["spot"], signal["entry_zone_type"])
        except Exception as e:
            logger.debug("Bias entry check failed for %s: %s", bias["id"], e)

    # Cleanup completed/expired
    _active_biases[:] = [b for b in _active_biases if b["status"] == "WAITING_ENTRY"]

    return signals


# =====================================================
# V4.3r — TELEGRAM FORMATTERS
# =====================================================

def _format_bias_alert(bias: dict) -> str:
    """Format a BIAS-only Telegram alert (no trade yet)."""
    underlying = bias["underlying"]
    direction = bias["direction"]
    score = bias["score"]
    spot = bias["trigger_spot"]
    opt_type = bias["opt_type"]
    strike = bias["trigger_strike"]
    window = int((bias["expiry_time"] - bias["trigger_time"]).total_seconds() / 60)

    tier = "HIGHEST" if score >= 8 else ("HIGH" if score >= 6 else "MEDIUM")
    dir_emoji = "BULLISH" if direction == "BULLISH" else "BEARISH"

    # Zone info
    zones = bias.get("frozen_zones", {})
    fvg_key = "fvg_long" if direction == "BULLISH" else "fvg_short"
    fvg = zones.get(fvg_key)
    ob_key = "ob_long" if direction == "BULLISH" else "ob_short"
    ob = zones.get(ob_key)

    zone_lines = []
    if fvg:
        zone_lines.append("  FVG: %.1f - %.1f" % (fvg[0], fvg[1]))
    if ob:
        zone_lines.append("  OB: %.1f - %.1f" % (ob[0], ob[1]))
    zone_text = "\n".join(zone_lines) if zone_lines else "  (scanning for zones)"

    ts = bias["trigger_time"].strftime("%H:%M:%S")

    msg = (
        "[PAPER] OI SHORT COVERING BIAS\n"
        "Score: %d/10 (%s)\n"
        "==============================\n\n"
        "%s %d %s\n"
        "Spot: %s\n"
        "Direction: %s\n\n"
        "WAITING FOR PULLBACK ENTRY\n"
        "Window: %d minutes\n\n"
        "ZONES TO WATCH:\n%s\n\n"
        "Time: %s"
    ) % (score, tier, underlying, strike, opt_type, "%.2f" % spot, dir_emoji, window, zone_text, ts)

    return msg


def _format_entry_alert(signal: dict) -> str:
    """Format a V4.3r ENTRY alert (trade signal from pullback)."""
    underlying = signal["underlying"]
    direction = signal["underlying_bias"]
    score = signal["score"]
    spot = signal["spot"]
    opt_type = signal["opt_type"]
    strike = signal.get("strike", 0)
    levels = signal.get("trade_levels", {})
    zone_type = signal.get("entry_zone_type", "?")
    mins = signal.get("trigger_to_entry_mins", 0)

    tier = "HIGHEST" if score >= 8 else ("HIGH" if score >= 6 else "MEDIUM")

    msg = (
        "[PAPER] OI-SC PULLBACK ENTRY\n"
        "Score: %d/10 (%s)\n"
        "==============================\n\n"
        "%s %d %s\n"
        "Spot: %.2f\n"
        "Direction: %s\n"
        "Entry Zone: %s\n"
        "Bias->Entry: %.0f min\n\n"
        "TRADE PLAN:\n"
        "Action: BUY %s %d %s\n"
        "Entry: %.2f\n"
        "SL: %.2f\n"
        "Target: %.2f (RR: %.1f)\n\n"
        "Time: %s"
    ) % (
        score, tier, underlying, strike, opt_type,
        spot, direction, zone_type, mins,
        underlying, strike, opt_type,
        levels.get("entry", 0), levels.get("sl", 0),
        levels.get("target", 0), levels.get("rr", 0),
        signal["timestamp"].strftime("%H:%M:%S"),
    )

    return msg


# =====================================================
# OI SC TRADE LOGGING & OUTCOME TRACKING
# =====================================================

def _log_oi_sc_trade_entry(signal: dict):
    """
    Log an OI SC signal as an active trade to be monitored.
    Stores entry, SL, target from the signal for later outcome tracking.
    """
    import json
    levels = signal.get("trade_levels", {})
    if not levels:
        return

    trade = {
        "symbol": signal["tradingsymbol"],
        "underlying": signal["underlying"],
        "opt_type": signal["opt_type"],
        "strike": signal["strike"],
        "direction": "LONG" if signal.get("trade_action", "").startswith("BUY") else "SHORT",
        "setup": "OI-SC",
        "entry": levels["entry"],
        "sl": levels["sl"],
        "target": levels["target"],
        "rr": levels.get("rr", 2.0),
        "score": signal["score"],
        "spot_at_entry": signal["spot"],
        "entry_time": signal["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ACTIVE",
        "peak_price": levels["entry"],
        "trough_price": levels["entry"],
    }
    _oi_sc_active_trades.append(trade)
    _persist_oi_sc_trades()
    logger.info(f"OI-SC TRADE LOGGED: {trade['symbol']} Entry={trade['entry']:.2f} SL={trade['sl']:.2f} Target={trade['target']:.2f}")


def _persist_oi_sc_trades():
    """Save active OI SC trades to disk."""
    import json
    try:
        with open(_OI_SC_TRADES_FILE, "w") as f:
            json.dump(_oi_sc_active_trades, f, default=str, indent=2)
    except Exception as e:
        logger.debug(f"Failed to persist OI SC trades: {e}")


def _persist_sc_snapshot(trade_signals: list, structure_alerts: list):
    """
    Persist strike history + qualified signals to a shared JSON file
    so the dashboard backend (separate process) can read them.
    """
    import json

    try:
        now = datetime.now(_IST)
        history_out = {}
        for symbol, readings in _strike_history.items():
            if not readings:
                continue
            latest = readings[-1]
            ts, oi, ltp, vol = latest

            oi_change_pct = 0.0
            price_change_pct = 0.0
            if len(readings) >= 3:
                old_oi = readings[-3][1]
                old_ltp = readings[-3][2]
                if old_oi > 0:
                    oi_change_pct = round((oi - old_oi) / old_oi * 100, 2)
                if old_ltp > 0:
                    price_change_pct = round((ltp - old_ltp) / old_ltp * 100, 2)

            is_sc = oi_change_pct < -3 and price_change_pct > 2
            opt_type = "CE" if "CE" in symbol else "PE" if "PE" in symbol else "?"
            underlying = (
                "BANKNIFTY" if symbol.startswith("BANKNIFTY")
                else "NIFTY" if symbol.startswith("NIFTY")
                else "?"
            )

            history_out[symbol] = {
                "symbol": symbol,
                "underlying": underlying,
                "opt_type": opt_type,
                "current_oi": oi,
                "current_ltp": round(ltp, 2),
                "oi_change_pct": oi_change_pct,
                "price_change_pct": price_change_pct,
                "volume": vol,
                "is_short_covering": is_sc,
                "score": min(10, max(0, int(abs(oi_change_pct) + price_change_pct))),
                "time": ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts),
                "readings_count": len(readings),
                "peak_oi": _strike_peak_oi.get(symbol, oi),
            }

        serializable_signals = []
        for sig in trade_signals:
            s = dict(sig)
            if "timestamp" in s and hasattr(s["timestamp"], "isoformat"):
                s["timestamp"] = s["timestamp"].isoformat()
            if "expiry" in s and hasattr(s.get("expiry"), "isoformat"):
                s["expiry"] = s["expiry"].isoformat()
            serializable_signals.append(s)

        serializable_alerts = []
        for alert in structure_alerts:
            a = dict(alert)
            if "timestamp" in a and hasattr(a["timestamp"], "isoformat"):
                a["timestamp"] = a["timestamp"].isoformat()
            serializable_alerts.append(a)

        snapshot = {
            "updated_at": now.isoformat(),
            "strike_history": history_out,
            "trade_signals": serializable_signals,
            "structure_alerts": serializable_alerts,
            "active_trades": _oi_sc_active_trades,
            "daily_trade_count": dict(_daily_trade_count),
        }

        with open(_OI_SC_SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, default=str, indent=2)

    except Exception as e:
        logger.debug(f"Failed to persist SC snapshot: {e}")


def _load_oi_sc_trades():
    """Load active OI SC trades from disk on startup."""
    import json
    global _oi_sc_active_trades
    if _OI_SC_TRADES_FILE.exists():
        try:
            with open(_OI_SC_TRADES_FILE, "r") as f:
                _oi_sc_active_trades = json.load(f)
            logger.info(f"Loaded {len(_oi_sc_active_trades)} active OI SC trades from disk")
        except Exception:
            _oi_sc_active_trades = []


def monitor_oi_sc_trades(kite_obj):
    """
    Check active OI SC trades for SL/target hits.
    Called from main engine loop alongside stock trade monitoring.
    Logs outcome to CSV + DB when trade closes.
    """
    if not _oi_sc_active_trades or not kite_obj:
        return

    # Build list of symbols to fetch quotes for
    active = [t for t in _oi_sc_active_trades if t["status"] == "ACTIVE"]
    if not active:
        return

    symbols = [f"NFO:{t['symbol']}" for t in active]
    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
            quotes = _ex.submit(kite_obj.quote, symbols).result(timeout=8)
    except Exception as e:
        logger.debug(f"OI SC trade monitor quote error: {e}")
        return

    closed = []
    for trade in active:
        key = f"NFO:{trade['symbol']}"
        if key not in quotes:
            continue

        ltp = quotes[key].get("last_price", 0)
        if ltp <= 0:
            continue

        # Track peak/trough for analysis
        trade["peak_price"] = max(trade.get("peak_price", ltp), ltp)
        trade["trough_price"] = min(trade.get("trough_price", ltp), ltp)

        entry = trade["entry"]
        sl = trade["sl"]
        target = trade["target"]
        is_long = trade["direction"] == "LONG"

        # Check SL hit
        sl_hit = (ltp <= sl) if is_long else (ltp >= sl)
        # Check target hit
        target_hit = (ltp >= target) if is_long else (ltp <= target)

        if sl_hit:
            trade["status"] = "CLOSED"
            trade["result"] = "LOSS"
            trade["exit_price"] = sl
            trade["pnl_r"] = -1.0
            trade["exit_time"] = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
            closed.append(trade)
        elif target_hit:
            trade["status"] = "CLOSED"
            trade["result"] = "WIN"
            trade["exit_price"] = target
            trade["pnl_r"] = trade.get("rr", 2.0)
            trade["exit_time"] = datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
            closed.append(trade)

    # Log closed trades
    for trade in closed:
        _log_oi_sc_closed_trade(trade)

    # Also expire trades from previous days (EOD auto-close)
    now = datetime.now(_IST)
    for trade in active:
        if trade["status"] != "ACTIVE":
            continue
        try:
            entry_dt = datetime.strptime(trade["entry_time"], "%Y-%m-%d %H:%M:%S")
            if entry_dt.date() < now.date():
                # Previous day trade — auto-close at last known price
                key = f"NFO:{trade['symbol']}"
                ltp = quotes.get(key, {}).get("last_price", trade["entry"])
                trade["status"] = "CLOSED"
                trade["exit_price"] = ltp
                if trade["direction"] == "LONG":
                    pnl_pts = ltp - trade["entry"]
                else:
                    pnl_pts = trade["entry"] - ltp
                risk_pts = abs(trade["entry"] - trade["sl"])
                trade["pnl_r"] = round(pnl_pts / risk_pts, 2) if risk_pts > 0 else 0
                trade["result"] = "WIN" if trade["pnl_r"] > 0 else "LOSS"
                trade["exit_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                _log_oi_sc_closed_trade(trade)
        except Exception:
            continue

    _persist_oi_sc_trades()


def _log_oi_sc_closed_trade(trade: dict):
    """Log a closed OI SC trade to CSV + dashboard DB."""
    trade_data = {
        "date": trade.get("entry_time", datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")),
        "symbol": f"NFO:{trade['symbol']}",
        "direction": trade["direction"],
        "setup": "OI-SC",
        "entry": trade["entry"],
        "exit_price": trade["exit_price"],
        "result": trade["result"],
        "pnl_r": trade["pnl_r"],
    }

    # Write to CSV
    try:
        file_exists = _TRADE_LEDGER.exists()
        with open(_TRADE_LEDGER, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "symbol", "direction", "setup", "entry", "exit_price", "result", "pnl_r"])
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade_data)
    except Exception as e:
        logger.error(f"Failed to log OI SC trade to CSV: {e}")

    # Sync to dashboard web service (Railway cross-container)
    try:
        from services.dashboard_sync import sync_trade_to_dashboard
        sync_trade_to_dashboard(trade_data)
        logger.info(f"OI-SC TRADE CLOSED: {trade['symbol']} {trade['result']} {trade['pnl_r']:+.2f}R")
    except Exception as e:
        logger.error(f"Failed to sync OI SC trade to dashboard: {e}")

    # Check if we've hit 30 OI-SC trades — trigger stats alert
    _check_oi_sc_milestone()


def _check_oi_sc_milestone():
    """After 30+ OI SC trades, log a milestone for review."""
    closed = [t for t in _oi_sc_active_trades if t.get("status") == "CLOSED"]
    if len(closed) == 30:
        logger.info("*** MILESTONE: 30 OI-SC trades recorded! Run stats analysis to evaluate edge. ***")


# Load persisted trades on module import
_load_oi_sc_trades()


# =====================================================
# PUBLIC API
# =====================================================

def scan_short_covering(kite_obj, telegram_fn=None, fetch_ohlc_fn=None):
    """
    V4.3r: 2-phase OI-SC scan.
    Phase 1: Detect new biases (OI-SC condition met, waiting for pullback entry).
    Phase 2: Check active biases for entry qualification (pullback + zone + confirmation).

    Returns:
        (entry_signals, structure_alerts)
    """
    global _last_scan_time

    now = datetime.now(_IST)

    if not _is_market_hours():
        return [], []

    if _last_scan_time:
        elapsed = (now - _last_scan_time).total_seconds()
        if elapsed < OI_SC_REFRESH_SECS:
            return [], []

    _last_scan_time = now

    if not kite_obj:
        return [], []

    _check_daily_reset()
    _cleanup_expired_locks()

    # === PHASE 1: Detect new biases ===
    new_biases = []
    structure_alerts = []

    for ul in cfg.OPT_UNDERLYINGS:
        sym = ul["symbol"]
        name = ul["name"]
        step = ul["step"]

        try:
            biases, alerts = _scan_underlying_v43(
                kite_obj, sym, name, step, fetch_ohlc_fn
            )
            new_biases.extend(biases)
            structure_alerts.extend(alerts)
        except Exception as e:
            logger.error("OI SC v43 scan error for %s: %s", name, e)

    # Add new biases to active list and send bias alerts
    for bias in new_biases:
        _active_biases.append(bias)
        if telegram_fn:
            try:
                # Send bias alert (uses original signal format for backward compat)
                original_sig = bias.get("original_signal")
                if original_sig:
                    msg = _format_alert(original_sig)
                else:
                    msg = _format_bias_alert(bias)
                telegram_fn(msg)
            except Exception:
                pass

    # === PHASE 2: Check active biases for entry ===
    entry_signals = _manage_active_biases(fetch_ohlc_fn, kite_obj)

    for sig in entry_signals:
        if telegram_fn:
            try:
                msg = _format_entry_alert(sig)
                telegram_fn(msg)
            except Exception:
                pass
        _log_oi_sc_trade_entry(sig)

    # Monitor existing trades for SL/target hits (unchanged)
    monitor_oi_sc_trades(kite_obj)

    if new_biases:
        logger.info("OI-SC v43: %d new bias(es) detected", len(new_biases))
    if entry_signals:
        logger.info("OI-SC v43: %d entry signal(s) from biases", len(entry_signals))
    if structure_alerts:
        logger.info("OI-SC: %d structure alert(s)", len(structure_alerts))

    _persist_sc_snapshot(entry_signals, structure_alerts)

    return entry_signals, structure_alerts


def get_strike_history(symbol=None):
    """Return current strike OI history (for debugging/testing)."""
    if symbol:
        return list(_strike_history.get(symbol, []))
    return {k: list(v) for k, v in _strike_history.items()}


def reset_state():
    """Reset all state — call at start of day or for testing."""
    global _last_scan_time
    _strike_history.clear()
    _strike_peak_oi.clear()
    _alerted_today.clear()
    _daily_trade_count.clear()
    _last_scan_time = None
    # V4.3r state
    _signal_locks.clear()
    _active_biases.clear()
    _daily_atr_cache.clear()
    logger.info("OI Short Covering: State reset (incl. V4.3r locks/biases)")


# =====================================================
# SMC STRUCTURE CONFLUENCE CHECK
# =====================================================
# Index-only: Cross-checks OI SC signals against 5-minute
# price structure (HTF bias, OB, FVG) so that signals
# contradicting visible structure are penalised/suppressed.
# =====================================================

# Cache structure result per underlying per day to avoid
# excessive API calls (refreshes every 5 minutes)
_smc_structure_cache = {}  # key: (underlying, date) → {data, ts}
_SMC_CACHE_TTL = 300       # seconds


def _check_smc_structure(underlying, spot, fetch_ohlc_fn):
    """
    Query 5-minute price structure for the underlying index.

    Returns dict:
        {
            "bias": "LONG" | "SHORT" | None,
            "ob_long": (low, high) | None,   # bullish OB zone
            "ob_short": (low, high) | None,   # bearish OB zone
            "fvg_long": (low, high) | None,
            "fvg_short": (low, high) | None,
            "price_at_bullish_ob": bool,
            "price_at_bearish_ob": bool,
            "price_at_bullish_fvg": bool,
            "price_at_bearish_fvg": bool,
        }
    Returns None on failure (non-fatal).
    """
    now = datetime.now(_IST)
    cache_key = (underlying, now.date().isoformat())

    # Serve from cache if fresh
    cached = _smc_structure_cache.get(cache_key)
    if cached and (now - cached["ts"]).total_seconds() < _SMC_CACHE_TTL:
        # Update spot proximity flags with latest spot price
        result = dict(cached["data"])
        result.update(_spot_proximity(result, spot))
        result["_spot"] = spot
        return result

    # Map underlying name to index symbol
    idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"

    try:
        candles = fetch_ohlc_fn(idx_sym, "5minute", 80)
        if not candles or len(candles) < 25:
            logger.debug(f"OI SC SMC: Not enough 5m candles for {underlying}")
            return None
    except Exception as e:
        logger.debug(f"OI SC SMC: Failed to fetch 5m candles for {underlying}: {e}")
        return None

    # Run SMC detectors
    # BUG FIX: Filter to TODAY's candles only for bias.
    # Using all 80 candles (400 min = multi-session) caused detect_htf_bias to
    # read yesterday's structure as LONG even when today is clearly bearish.
    today_date = now.date()
    today_candles = [c for c in candles if c["date"].date() == today_date]
    bias_candles = today_candles if len(today_candles) >= 10 else candles[-20:]
    _raw_bias = smc.get_ltf_structure_bias(bias_candles)  # returns BULLISH/BEARISH/NEUTRAL
    # Normalize to LONG/SHORT/None to match downstream checks
    bias = "LONG" if _raw_bias == "BULLISH" else ("SHORT" if _raw_bias == "BEARISH" else None)
    ob_long = smc.detect_order_block(candles, "LONG")
    ob_short = smc.detect_order_block(candles, "SHORT")
    fvg_long = smc.detect_fvg(candles, "LONG")
    fvg_short = smc.detect_fvg(candles, "SHORT")

    # Compute ATR for extension distance checks
    atr = smc.calculate_atr(candles)

    result = {
        "bias": bias,
        "ob_long": ob_long,
        "ob_short": ob_short,
        "fvg_long": fvg_long,
        "fvg_short": fvg_short,
        "atr": atr,
    }

    # Cache the zone data (proximity flags computed fresh each call)
    _smc_structure_cache[cache_key] = {"data": result, "ts": now}

    # Compute spot proximity with current spot
    result.update(_spot_proximity(result, spot))
    result["_spot"] = spot  # needed by _smc_score_adjustment for extension check

    logger.debug(
        f"OI SC SMC: {underlying} bias={bias} | "
        f"OB_L={ob_long} OB_S={ob_short} | "
        f"FVG_L={fvg_long} FVG_S={fvg_short} | "
        f"spot={spot:.1f}"
    )

    return result


def _spot_proximity(structure, spot):
    """Check if spot is currently sitting at/near any detected OB or FVG zone."""
    flags = {
        "price_at_bullish_ob": False,
        "price_at_bearish_ob": False,
        "price_at_bullish_fvg": False,
        "price_at_bearish_fvg": False,
    }

    def _at_zone(zone, price, buffer_pct=0.002):
        """Check if price is within or very near a zone (0.2% buffer)."""
        if zone is None:
            return False
        low, high = zone
        buf = (high - low) * 0.5 + (price * buffer_pct)
        return (low - buf) <= price <= (high + buf)

    flags["price_at_bullish_ob"] = _at_zone(structure.get("ob_long"), spot)
    flags["price_at_bearish_ob"] = _at_zone(structure.get("ob_short"), spot)
    flags["price_at_bullish_fvg"] = _at_zone(structure.get("fvg_long"), spot)
    flags["price_at_bearish_fvg"] = _at_zone(structure.get("fvg_short"), spot)

    return flags


def _smc_score_adjustment(opt_type, structure):
    """
    Compute score adjustment based on SMC structure vs OI SC direction.

    OI SC Direction:
      CE short covering → BULLISH → OI SC expects price to go UP
      PE short covering → BEARISH → OI SC expects price to go DOWN

    Confluence logic (mirrors manual SMC trader thinking):
      1. ZONE CONTRADICTION (strongest): Signal direction contradicts the
         zone price is sitting at → hard penalty (-3).
         e.g. PE (bearish) at a bullish OB = selling into demand = wrong.

      2. BIAS CONTRADICTION: 5m swing bias opposes signal direction → -2.

      3. EXTENSION PENALTY: Price is NOT near any supporting zone for the
         signal direction. A good OI SC entry should fire AT a zone, not
         floating in premium/discount with no structure support → -1.
         e.g. CE at 59135 but nearest bullish OB at 58586 (550pts away).

      4. CONFIRMATION: Direction aligns with structure → +1 to +2.

    Returns:
        (adjustment: int, reason: str)
    """
    if structure is None:
        return 0, ""

    bias = structure.get("bias")
    atr = structure.get("atr", 0)
    adj = 0
    reasons = []

    if opt_type == "CE":
        # CE SC = BULLISH signal (expects price to go UP)

        # ── ZONE CONTRADICTION (hard block) ──
        # BULLISH signal while SITTING at a bearish OB (supply zone) = death zone
        if structure.get("price_at_bearish_ob"):
            adj -= 3
            reasons.append("price AT bearish OB / supply zone (-3)")

        # ── BIAS CONTRADICTION ──
        if bias == "SHORT":
            adj -= 2
            reasons.append("5m bias SHORT vs CE-SC BULLISH (-2)")

        # ── BEARISH FVG PENALTY ──
        if structure.get("price_at_bearish_fvg"):
            adj -= 1
            reasons.append("price at bearish FVG (-1)")

        # ── EXTENSION PENALTY ──
        # CE is bullish → nearest support should be a bullish OB or bullish FVG
        # If price is far from any supporting zone, the entry has no structure anchor
        if atr > 0:
            has_support_nearby = (
                structure.get("price_at_bullish_ob") or
                structure.get("price_at_bullish_fvg")
            )
            if not has_support_nearby:
                # Check distance from nearest bullish zone
                dist = _distance_from_nearest_support(structure, "LONG", structure.get("_spot", 0))
                if dist > 2.0 * atr:
                    adj -= 2
                    reasons.append(f"extended {dist/atr:.1f}x ATR above nearest support (-2)")
                elif dist > 1.0 * atr:
                    adj -= 1
                    reasons.append(f"extended {dist/atr:.1f}x ATR above nearest support (-1)")

        # ── CONFIRMATION checks ──
        if bias == "LONG":
            adj += 1
            reasons.append("5m bias LONG confirms CE-SC (+1)")

        if structure.get("price_at_bullish_ob"):
            adj += 2
            reasons.append("price AT bullish OB / demand zone (+2)")
        elif structure.get("price_at_bullish_fvg"):
            adj += 1
            reasons.append("price at bullish FVG (+1)")

    elif opt_type == "PE":
        # PE SC = BEARISH signal (expects price to go DOWN)

        # ── ZONE CONTRADICTION (hard block) ──
        # BEARISH signal while SITTING at a bullish OB (demand zone) = buying zone
        if structure.get("price_at_bullish_ob"):
            adj -= 3
            reasons.append("price AT bullish OB / demand zone (-3)")

        # ── BIAS CONTRADICTION ──
        if bias == "LONG":
            adj -= 2
            reasons.append("5m bias LONG vs PE-SC BEARISH (-2)")

        # ── BULLISH FVG PENALTY ──
        if structure.get("price_at_bullish_fvg"):
            adj -= 1
            reasons.append("price at bullish FVG (-1)")

        # ── EXTENSION PENALTY ──
        # PE is bearish → nearest resistance should be a bearish OB or bearish FVG
        # If price is far below any resistance zone, entry has no structure anchor
        if atr > 0:
            has_resistance_nearby = (
                structure.get("price_at_bearish_ob") or
                structure.get("price_at_bearish_fvg")
            )
            if not has_resistance_nearby:
                dist = _distance_from_nearest_support(structure, "SHORT", structure.get("_spot", 0))
                if dist > 2.0 * atr:
                    adj -= 2
                    reasons.append(f"extended {dist/atr:.1f}x ATR below nearest resistance (-2)")
                elif dist > 1.0 * atr:
                    adj -= 1
                    reasons.append(f"extended {dist/atr:.1f}x ATR below nearest resistance (-1)")

        # ── CONFIRMATION checks ──
        if bias == "SHORT":
            adj += 1
            reasons.append("5m bias SHORT confirms PE-SC (+1)")

        if structure.get("price_at_bearish_ob"):
            adj += 2
            reasons.append("price AT bearish OB / supply zone (+2)")
        elif structure.get("price_at_bearish_fvg"):
            adj += 1
            reasons.append("price at bearish FVG (+1)")

    # Cap: bonus +2, penalty -5 (strong enough to kill even score-7 signals
    # when zone fully contradicts)
    adj = max(-5, min(2, adj))
    reason_str = " | ".join(reasons) if reasons else ""

    return adj, reason_str


def _distance_from_nearest_support(structure, direction, spot):
    """
    Calculate how far spot is from the nearest structural zone that would
    support the trade direction.

    For LONG: nearest bullish OB or bullish FVG below/at spot
    For SHORT: nearest bearish OB or bearish FVG above/at spot

    Returns distance in price points (0 if no zone found, to avoid false penalty).
    """
    if spot <= 0:
        return 0

    if direction == "LONG":
        zones = []
        if structure.get("ob_long"):
            zones.append(structure["ob_long"])
        if structure.get("fvg_long"):
            zones.append(structure["fvg_long"])
        if not zones:
            # No support zones detected — signal is floating
            # Return large distance to trigger penalty
            return float("inf")
        # Distance from spot to nearest zone midpoint (below spot)
        distances = []
        for z_low, z_high in zones:
            mid = (z_low + z_high) / 2
            if mid <= spot:
                distances.append(spot - mid)
            else:
                distances.append(abs(spot - mid))
        return min(distances) if distances else float("inf")

    elif direction == "SHORT":
        zones = []
        if structure.get("ob_short"):
            zones.append(structure["ob_short"])
        if structure.get("fvg_short"):
            zones.append(structure["fvg_short"])
        if not zones:
            return float("inf")
        distances = []
        for z_low, z_high in zones:
            mid = (z_low + z_high) / 2
            if mid >= spot:
                distances.append(mid - spot)
            else:
                distances.append(abs(spot - mid))
        return min(distances) if distances else float("inf")

    return 0


# =====================================================
# INTERNAL: Scan One Underlying
# =====================================================

def _scan_underlying(kite_obj, index_symbol, index_name, step, fetch_ohlc_fn):
    """
    Scan ATM±1 strikes for one underlying (e.g. BANKNIFTY).

    Steps:
        1. Get spot price → compute ATM±1 strikes
        2. Get target expiries (monthly for BANKNIFTY, weekly+monthly for NIFTY)
        3. Batch-query kite.quote() for OI + LTP
        4. Store in history
        5. Detect short-covering on each strike
        6. Return qualified signals
    """
    # 1. Get spot — wrapped in a hard 8s timeout so a hung Kite call never
    # freezes the TRADE_MONITOR loop and triggers the watchdog.
    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
            ltp_data = _ex.submit(kite_obj.ltp, [index_symbol]).result(timeout=8)
        if not ltp_data or index_symbol not in ltp_data:
            return [], []
        spot = ltp_data[index_symbol]["last_price"]
    except _cf.TimeoutError:
        logger.warning(f"OI SC: ltp() timed out for {index_name} — skipping scan")
        return [], []
    except Exception as e:
        logger.warning(f"OI SC: Failed to fetch {index_name} spot: {e}")
        return [], []

    # 2. ATM±1 strike selection (replaces ±5 range)
    ce_strikes, pe_strikes = get_atm_strikes(spot, step)
    ce_strike_set = set(ce_strikes)
    pe_strike_set = set(pe_strikes)

    instruments = _load_instruments(kite_obj)
    if not instruments:
        return []

    # Smart expiry selection (monthly for BANKNIFTY, weekly+monthly for NIFTY)
    target_expiry_info = get_target_expiries(instruments, index_name)
    if not target_expiry_info:
        logger.warning(f"OI SC: No expiry found for {index_name}")
        return []

    target_expiry_dates = set(e["expiry"] for e in target_expiry_info)

    # Map: NFO:symbol → metadata
    symbols_to_query = []
    sym_info_map = {}

    for instr in instruments:
        if instr["name"] != index_name:
            continue
        if instr["instrument_type"] not in ("CE", "PE"):
            continue
        exp = instr["expiry"]
        if isinstance(exp, datetime):
            exp = exp.date()
        if exp not in target_expiry_dates:
            continue

        opt_type = instr["instrument_type"]
        strike = instr["strike"]

        # ATM±1 filter: CE uses ce_strike_set, PE uses pe_strike_set
        if opt_type == "CE" and strike not in ce_strike_set:
            continue
        if opt_type == "PE" and strike not in pe_strike_set:
            continue

        nfo_sym = f"NFO:{instr['tradingsymbol']}"
        symbols_to_query.append(nfo_sym)
        sym_info_map[nfo_sym] = {
            "tradingsymbol": instr["tradingsymbol"],
            "strike": int(instr["strike"]),
            "opt_type": instr["instrument_type"],
            "token": instr["instrument_token"],
            "underlying": index_name,
            "expiry": exp,
        }

    if not symbols_to_query:
        return []

    # 3. Batch quote — hard 12s timeout; quote() fetches many symbols at once
    # and can hang on network issues, which would freeze the main engine loop.
    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
            quotes = _ex.submit(kite_obj.quote, symbols_to_query).result(timeout=12)
    except _cf.TimeoutError:
        logger.warning(f"OI SC: quote() timed out for {index_name} — skipping scan")
        return [], []
    except Exception as e:
        logger.warning(f"OI SC: Quote failed for {index_name}: {e}")
        return [], []

    now = datetime.now(_IST)
    signals = []

    # 4. Store history + 5. Detect
    for nfo_sym, data in quotes.items():
        info = sym_info_map.get(nfo_sym)
        if not info:
            continue

        oi = data.get("oi", 0)
        ltp = data.get("last_price", 0)
        volume = data.get("volume", 0)

        if oi <= 0 or ltp <= 0:
            continue

        tsym = info["tradingsymbol"]

        # Store in history
        if tsym not in _strike_history:
            _strike_history[tsym] = deque(maxlen=OI_SC_HISTORY_SIZE)

        _strike_history[tsym].append((now, oi, ltp, volume))

        # Update peak OI
        if tsym not in _strike_peak_oi or oi > _strike_peak_oi[tsym]:
            _strike_peak_oi[tsym] = oi

        # Detect short covering
        signal = _detect_strike_short_covering(
            tsym, info, spot, fetch_ohlc_fn
        )

        if signal:
            signals.append(signal)

    # Separate trade signals from structure alerts
    trade_signals = [s for s in signals if s.get("signal_type") == "OI_SHORT_COVERING"]
    structure_alerts = [s for s in signals if s.get("signal_type") == "OI_SC_STRUCTURE_ALERT"]

    return trade_signals, structure_alerts


def _scan_underlying_v43(kite_obj, index_symbol, index_name, step, fetch_ohlc_fn):
    """
    V4.3r scan: same OI history storage as _scan_underlying, but uses
    _detect_oi_sc_bias() to produce bias objects instead of trade signals.
    Returns (biases, structure_alerts).
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

    now = datetime.now(_IST)

    # 1. Get spot price (same logic as _scan_underlying)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(kite_obj.ltp, [index_symbol])
            ltp_data = fut.result(timeout=8)
        spot = ltp_data[index_symbol]["last_price"]
    except Exception as e:
        logger.warning("OI SC v43: spot fetch failed for %s: %s", index_name, e)
        return [], []

    # 2-3. ATM strikes + instruments + expiries (reuse existing helpers)
    try:
        atm_strikes = get_atm_strikes(spot, step)
    except Exception:
        return [], []

    global _instruments_cache
    if _instruments_cache is None:
        try:
            _instruments_cache = kite_obj.instruments("NFO")
        except Exception:
            return [], []

    try:
        target_expiries = get_target_expiries(index_name, _instruments_cache)
    except Exception:
        return [], []

    # 4. Build symbols to query
    symbols_to_query = []
    sym_info_map = {}
    for inst in _instruments_cache:
        if inst["name"] != index_name:
            continue
        if inst["instrument_type"] not in ("CE", "PE"):
            continue
        if inst["strike"] not in atm_strikes:
            continue
        if inst["expiry"] not in target_expiries:
            continue
        tsym = inst["tradingsymbol"]
        nfo_sym = "NFO:%s" % tsym
        symbols_to_query.append(nfo_sym)
        sym_info_map[nfo_sym] = {
            "tradingsymbol": tsym,
            "underlying": index_name,
            "opt_type": inst["instrument_type"],
            "strike": inst["strike"],
            "expiry": inst["expiry"],
        }

    if not symbols_to_query:
        return [], []

    # 5. Fetch quotes
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(kite_obj.quote, symbols_to_query)
            quotes = fut.result(timeout=12)
    except Exception as e:
        logger.warning("OI SC v43: quote fetch failed for %s: %s", index_name, e)
        return [], []

    # 6. Store history + detect biases
    biases = []
    structure_alerts = []

    for nfo_sym, data in quotes.items():
        info = sym_info_map.get(nfo_sym)
        if not info:
            continue
        oi = data.get("oi", 0)
        ltp = data.get("last_price", 0)
        volume = data.get("volume", 0)
        if oi <= 0 or ltp <= 0:
            continue

        tsym = info["tradingsymbol"]

        # Store in history (same as original)
        if tsym not in _strike_history:
            _strike_history[tsym] = deque(maxlen=OI_SC_HISTORY_SIZE)
        _strike_history[tsym].append((now, oi, ltp, volume))

        if tsym not in _strike_peak_oi or oi > _strike_peak_oi[tsym]:
            _strike_peak_oi[tsym] = oi

        # V4.3r detection: returns bias dict or structure alert or None
        result = _detect_oi_sc_bias(tsym, info, spot, fetch_ohlc_fn)

        if result:
            if result.get("signal_type") == "OI_SC_STRUCTURE_ALERT":
                structure_alerts.append(result)
            elif result.get("status") == "WAITING_ENTRY":
                biases.append(result)

    return biases, structure_alerts


# =====================================================
# MOMENTUM EXHAUSTION CHECK
# =====================================================

def _count_consecutive_candles(candles: list, direction: str) -> int:
    """
    Count how many of the most recent 5m candles are consecutive
    same-direction closes (direction='bull' = close > open, 'bear' = close < open).
    Only today's candles are considered.
    Returns the streak count (0 if candles is empty or direction not matched).
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    today_c = [c for c in candles if c["date"].date() == today]
    if not today_c:
        return 0
    streak = 0
    for c in reversed(today_c):
        o, cl = c.get("open", 0), c.get("close", 0)
        if direction == "bull" and cl > o:
            streak += 1
        elif direction == "bear" and cl < o:
            streak += 1
        else:
            break
    return streak


def _check_momentum_exhaustion(opt_type: str, fetch_ohlc_fn, underlying: str) -> tuple[bool, str]:
    """
    Return (exhausted: bool, reason: str).

    Exhaustion definition:
      CE (bullish signal) — 4+ consecutive green 5m candles ending at current price
      PE (bearish signal) — 4+ consecutive red 5m candles ending at current price

    When exhausted, the short-covering rally or flush is already priced in;
    the initiation already happened several candles ago.
    """
    try:
        idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"
        candles = fetch_ohlc_fn(idx_sym, "5minute", 40)
        if not candles or len(candles) < 5:
            return False, ""
        candle_dir = "bull" if opt_type == "CE" else "bear"
        streak = _count_consecutive_candles(candles, candle_dir)
        if streak >= OI_SC_EXHAUSTION_CANDLES:
            label = "green" if opt_type == "CE" else "red"
            reason = f"{streak} consecutive {label} 5m candles — rally exhausted, not initiating"
            return True, reason
    except Exception:
        pass
    return False, ""


# =====================================================
# CORE DETECTION: Per-Strike Short Covering
# =====================================================

def _detect_strike_short_covering(tradingsymbol, info, spot, fetch_ohlc_fn):
    """
    Check a single strike for short-covering pattern.

    Short Covering = OI dropping + Price rising (simultaneously).

    Returns signal dict or None.
    """
    history = _strike_history.get(tradingsymbol)
    if not history or len(history) < OI_SC_MIN_READINGS:
        return None

    underlying = info["underlying"]
    opt_type = info["opt_type"]
    strike = info["strike"]
    now = datetime.now(_IST)

    # Dedup check
    alert_key = f"{underlying}_{opt_type}_{strike}_{now.date().isoformat()}"
    if alert_key in _alerted_today:
        elapsed = (now - _alerted_today[alert_key]).total_seconds()
        if elapsed < OI_SC_ALERT_COOLDOWN_SECS:
            return None

    # Per-underlying daily tracking (cap removed by user request)
    day_key = f"{underlying}_{now.date().isoformat()}"

    # -------------------------------------------------
    # Sub-condition checks
    # -------------------------------------------------
    readings = list(history)

    # Extract parallel lists
    oi_readings = [(r[0], r[1]) for r in readings]
    ltp_readings = [(r[0], r[2]) for r in readings]
    vol_readings = [(r[0], r[3]) for r in readings]

    rolling_oi_drop = check_rolling_oi_drop(oi_readings)
    price_rise = check_price_rise(ltp_readings)
    peak_drop = check_peak_oi_drop(oi_readings, tradingsymbol)
    velocity = check_oi_velocity(oi_readings)
    volume_ok = check_volume_spike(vol_readings)

    # Gate: Must have BOTH OI drop AND price rise
    has_oi_signal = rolling_oi_drop or peak_drop or velocity
    if not has_oi_signal:
        return None
    if not price_rise:
        return None

    # -------------------------------------------------
    # Score calculation (out of 10)
    # -------------------------------------------------
    score = 0
    breakdown = {}

    # OI Drop magnitude
    if rolling_oi_drop:
        drop_pct = rolling_oi_drop["drop_pct"]
        if drop_pct >= 20:
            score += 3; breakdown["oi_drop"] = 3
        elif drop_pct >= 10:
            score += 2; breakdown["oi_drop"] = 2
        else:
            score += 1; breakdown["oi_drop"] = 1

    # Price Rise magnitude
    if price_rise:
        rise_pct = price_rise["rise_pct"]
        if rise_pct >= 10:
            score += 3; breakdown["price_rise"] = 3
        elif rise_pct >= 5:
            score += 2; breakdown["price_rise"] = 2
        else:
            score += 1; breakdown["price_rise"] = 1

    # Velocity (fast OI drop)
    if velocity:
        score += 2; breakdown["velocity"] = 2

    # Peak OI drop (sustained)
    if peak_drop:
        score += 1; breakdown["peak_drop"] = 1

    # Volume spike
    if volume_ok:
        score += 1; breakdown["volume"] = 1

    # -------------------------------------------------
    # Score gate
    # -------------------------------------------------
    if score < OI_SC_MIN_SCORE:
        return None

    # -------------------------------------------------
    # Spot direction validation
    # Penalize signals that contradict underlying price trend.
    # Uses TWO checks:
    #   1) Daily: spot vs previous close (big picture)
    #   2) Intraday: is spot recovering? (option price rise IS the evidence)
    # If the option price is rising strongly (>5%), the SC is real even on a
    # red day — reduce penalty to -1 so strong signals (7+) still pass.
    # -------------------------------------------------
    spot_details = []  # collect spot-penalty messages here (merged into details later)
    try:
        from kiteconnect import KiteConnect
        from config.kite_auth import get_api_key, get_access_token
        _api_key = get_api_key()
        _token = get_access_token()
        if _api_key and _token:
            _kite = KiteConnect(api_key=_api_key)
            _kite.set_access_token(_token)
            _idx_sym = "NSE:NIFTY 50" if underlying == "NIFTY" else "NSE:NIFTY BANK"
            _ohlc = _kite.ohlc([_idx_sym])
            if _idx_sym in _ohlc:
                _prev_close = _ohlc[_idx_sym].get("ohlc", {}).get("close", 0)
                if _prev_close > 0:
                    _spot_chg_pct = (spot - _prev_close) / _prev_close * 100

                    # Check intraday momentum: if price_rise is strong (>5%),
                    # the SC is likely real even on a red day — reduce penalty
                    _strong_intraday = price_rise and price_rise.get("rise_pct", 0) >= 5.0
                    _penalty = -1 if _strong_intraday else -2

                    # CE short covering (bullish signal) during heavy selling
                    if opt_type == "CE" and _spot_chg_pct < -1.0:
                        score += _penalty
                        breakdown["spot_penalty"] = _penalty
                        if _strong_intraday:
                            spot_details.append(f"CAUTION: Spot {_spot_chg_pct:+.1f}% vs prev close (reduced penalty: intraday recovery +{price_rise['rise_pct']:.1f}%)")
                        else:
                            spot_details.append(f"WARN: Spot {_spot_chg_pct:+.1f}% vs prev close")
                    # PE short covering (bearish signal) during strong buying
                    elif opt_type == "PE" and _spot_chg_pct > 1.0:
                        score += _penalty
                        breakdown["spot_penalty"] = _penalty
                        if _strong_intraday:
                            spot_details.append(f"CAUTION: Spot {_spot_chg_pct:+.1f}% vs prev close (reduced penalty: intraday reversal +{price_rise['rise_pct']:.1f}%)")
                        else:
                            spot_details.append(f"WARN: Spot {_spot_chg_pct:+.1f}% vs prev close")
    except Exception:
        pass  # Non-critical, proceed without penalty

    # Re-check score after spot penalty
    if score < OI_SC_MIN_SCORE:
        return None

    # -------------------------------------------------
    # SMC STRUCTURE CONFLUENCE (5-minute)
    # Cross-check OI SC direction against price structure.
    # Penalize signals that fire away from structure zones or
    # contradict the 5m swing-based bias.
    # -------------------------------------------------
    smc_structure = None
    smc_adj = 0
    smc_reason = ""
    try:
        smc_structure = _check_smc_structure(underlying, spot, fetch_ohlc_fn)
        if smc_structure:
            smc_adj, smc_reason = _smc_score_adjustment(opt_type, smc_structure)
            if smc_adj != 0:
                score += smc_adj
                breakdown["smc_structure"] = smc_adj
                logger.info(
                    f"OI SC SMC: {tradingsymbol} adj={smc_adj:+d} | {smc_reason}"
                )
    except Exception as e:
        logger.debug(f"OI SC SMC: structure check failed for {underlying}: {e}")

    # Re-check score after SMC structure adjustment
    if score < OI_SC_MIN_SCORE:
        logger.info(
            f"OI SC BLOCKED by SMC structure: {tradingsymbol} "
            f"score {score}/10 (adj {smc_adj:+d}) | {smc_reason}"
        )
        # ─── STRUCTURE CONTRADICTION ALERT ───
        # When OI SC is blocked because structure contradicts, generate
        # an informational alert pointing the trader to the CORRECT
        # direction based on the structural zone.
        if smc_structure and smc_adj < 0:
            return _build_structure_alert(
                tradingsymbol, info, spot, opt_type, score,
                breakdown, smc_structure, smc_adj, smc_reason,
                rolling_oi_drop, price_rise, peak_drop, velocity, volume_ok
            )
        return None

    # -------------------------------------------------
    # BIAS-CONFLICT HARD FILTER
    # Even if score technically passes, kill signals where the 5m bias
    # directly opposes the OI SC direction and confidence is low.
    # Soft penalty (-2) already applied in _smc_score_adjustment; this
    # hard-blocks the borderline cases where score just scraped past min.
    # -------------------------------------------------
    if smc_structure:
        _bias = smc_structure.get("bias")
        _bias_conflicts = (
            (opt_type == "CE" and _bias == "SHORT") or
            (opt_type == "PE" and _bias == "LONG")
        )
        if _bias_conflicts and score <= OI_SC_BIAS_CONFLICT_HARD_BLOCK:
            logger.info(
                f"OI SC HARD-BLOCKED (bias conflict): {tradingsymbol} "
                f"5m bias={_bias} contradicts {opt_type}-SC | score={score}/10"
            )
            return None

    # -------------------------------------------------
    # MOMENTUM EXHAUSTION CHECK
    # Suppress signals that fire after 4+ consecutive same-direction
    # candles — the short-covering move is already priced in.
    # -------------------------------------------------
    try:
        _exhausted, _ex_reason = _check_momentum_exhaustion(opt_type, fetch_ohlc_fn, underlying)
        if _exhausted:
            logger.info(
                f"OI SC SUPPRESSED (exhaustion): {tradingsymbol} | {_ex_reason}"
            )
            return None
    except Exception:
        pass  # non-fatal, don't block signal on check failure

    # -------------------------------------------------
    # Direction
    # -------------------------------------------------
    if opt_type == "CE":
        underlying_bias = "BULLISH"
        trade_action = "BUY_CE"
    else:
        underlying_bias = "BEARISH"
        trade_action = "BUY_PE"

    current_oi = oi_readings[-1][1]
    current_ltp = ltp_readings[-1][1]
    peak_oi = _strike_peak_oi.get(tradingsymbol, current_oi)

    # Build details
    details = []
    if rolling_oi_drop:
        details.append(
            f"OI dropped {rolling_oi_drop['drop_pct']:.1f}% "
            f"({rolling_oi_drop['from_oi']:,} → {rolling_oi_drop['to_oi']:,})"
        )
    if peak_drop:
        details.append(
            f"Peak OI drop: {peak_drop['drop_pct']:.1f}% from {peak_drop['peak_oi']:,}"
        )
    if price_rise:
        details.append(
            f"Price +{price_rise['rise_pct']:.1f}% "
            f"({price_rise['from_ltp']:.2f} → {price_rise['to_ltp']:.2f})"
        )
    if velocity:
        details.append(f"Velocity: {velocity['avg_rate_pct']:.1f}%/reading")
    if volume_ok:
        details.append("Volume spike confirmed")

    # Append spot-penalty messages (collected earlier)
    details.extend(spot_details)

    # Append SMC structure info
    if smc_reason:
        details.append(f"SMC: {smc_reason}")

    # Mark alerted
    _alerted_today[alert_key] = now
    _daily_trade_count[day_key] = _daily_trade_count.get(day_key, 0) + 1

    # Compute trade levels
    trade_levels = _compute_trade_levels(current_ltp, ltp_readings)

    logger.info(
        f"⚡ OI SHORT COVERING: {tradingsymbol} | "
        f"Score {score}/10 | {underlying_bias} | "
        f"OI: {current_oi:,} (peak {peak_oi:,}) | "
        f"LTP: {current_ltp:.2f} | "
        f"{' | '.join(details)}"
    )

    return {
        "signal_type": "OI_SHORT_COVERING",
        "tradingsymbol": tradingsymbol,
        "underlying": underlying,
        "strike": strike,
        "opt_type": opt_type,
        "spot": spot,
        "trade_action": trade_action,
        "underlying_bias": underlying_bias,
        "score": min(score, 10),
        "score_breakdown": breakdown,
        "current_oi": current_oi,
        "peak_oi": peak_oi,
        "current_ltp": current_ltp,
        "oi_drop": rolling_oi_drop,
        "price_rise": price_rise,
        "peak_drop": peak_drop,
        "velocity": velocity,
        "volume_confirmed": volume_ok,
        "details": " | ".join(details),
        "trade_levels": trade_levels,
        "expiry": info.get("expiry"),
        "timestamp": now,
        "smc_structure": {
            "bias": smc_structure.get("bias") if smc_structure else None,
            "timeframe": "5m",
            "score_adj": smc_adj,
            "reason": smc_reason,
            "ob_long": smc_structure.get("ob_long") if smc_structure else None,
            "ob_short": smc_structure.get("ob_short") if smc_structure else None,
            "fvg_long": smc_structure.get("fvg_long") if smc_structure else None,
            "fvg_short": smc_structure.get("fvg_short") if smc_structure else None,
            "at_bullish_ob": smc_structure.get("price_at_bullish_ob", False) if smc_structure else False,
            "at_bearish_ob": smc_structure.get("price_at_bearish_ob", False) if smc_structure else False,
            "at_bullish_fvg": smc_structure.get("price_at_bullish_fvg", False) if smc_structure else False,
            "at_bearish_fvg": smc_structure.get("price_at_bearish_fvg", False) if smc_structure else False,
        },
    }


# =====================================================
# STRUCTURE CONTRADICTION ALERT BUILDER
# =====================================================

def _build_structure_alert(tradingsymbol, info, spot, opt_type, score,
                           breakdown, smc_structure, smc_adj, smc_reason,
                           rolling_oi_drop, price_rise, peak_drop, velocity, volume_ok):
    """
    Build an informational alert when OI SC is blocked by SMC structure.

    Instead of silently dropping the signal, we flip the direction and
    alert the trader: "OI SC detected X, but structure says watch for Y".

    Returns dict with signal_type = "OI_SC_STRUCTURE_ALERT".
    """
    underlying = info["underlying"]
    strike = info["strike"]
    now = datetime.now(_IST)

    # Determine the structural direction to watch
    bias = smc_structure.get("bias")
    ob_long = smc_structure.get("ob_long")
    ob_short = smc_structure.get("ob_short")
    fvg_long = smc_structure.get("fvg_long")
    fvg_short = smc_structure.get("fvg_short")

    # Figure out what the structure is saying
    watch_direction = None
    watch_zone = None
    watch_zone_type = None

    if opt_type == "PE":
        # PE SC was bearish, but structure blocked it
        # Structure likely says LONG (bullish OB, or LONG bias)
        if smc_structure.get("price_at_bullish_ob") and ob_long:
            watch_direction = "LONG"
            watch_zone = ob_long
            watch_zone_type = "Bullish OB"
        elif bias == "LONG":
            watch_direction = "LONG"
            if ob_long:
                watch_zone = ob_long
                watch_zone_type = "Bullish OB"
            elif fvg_long:
                watch_zone = fvg_long
                watch_zone_type = "Bullish FVG"
    elif opt_type == "CE":
        # CE SC was bullish, but structure blocked it
        # Structure likely says SHORT (bearish OB, or SHORT bias, or extended)
        if smc_structure.get("price_at_bearish_ob") and ob_short:
            watch_direction = "SHORT"
            watch_zone = ob_short
            watch_zone_type = "Bearish OB"
        elif bias == "SHORT":
            watch_direction = "SHORT"
            if ob_short:
                watch_zone = ob_short
                watch_zone_type = "Bearish OB"
            elif fvg_short:
                watch_zone = fvg_short
                watch_zone_type = "Bearish FVG"
        # Extension case — too far from support
        if watch_direction is None and not smc_structure.get("price_at_bullish_ob"):
            # Price is extended above support
            watch_direction = "WAIT"  # Wait for pullback
            if ob_long:
                watch_zone = ob_long
                watch_zone_type = "Bullish OB (pullback target)"

    # If we couldn't determine a clear flip, still issue a caution alert
    if watch_direction is None:
        watch_direction = "CAUTION"

    logger.info(
        f"📋 OI SC STRUCTURE ALERT: {tradingsymbol} | "
        f"OI SC {opt_type}-SC blocked (score {score}/10, adj {smc_adj:+d}) | "
        f"Watch: {watch_direction} | Zone: {watch_zone_type} {watch_zone}"
    )

    return {
        "signal_type": "OI_SC_STRUCTURE_ALERT",
        "tradingsymbol": tradingsymbol,
        "underlying": underlying,
        "strike": strike,
        "opt_type": opt_type,
        "spot": spot,
        "original_score": score - smc_adj,  # score before SMC
        "adjusted_score": score,
        "smc_adj": smc_adj,
        "smc_reason": smc_reason,
        "watch_direction": watch_direction,
        "watch_zone": watch_zone,
        "watch_zone_type": watch_zone_type,
        "smc_structure": {
            "bias": bias,
            "timeframe": "5m",
            "ob_long": ob_long,
            "ob_short": ob_short,
            "fvg_long": fvg_long,
            "fvg_short": fvg_short,
            "at_bullish_ob": smc_structure.get("price_at_bullish_ob", False),
            "at_bearish_ob": smc_structure.get("price_at_bearish_ob", False),
            "at_bullish_fvg": smc_structure.get("price_at_bullish_fvg", False),
            "at_bearish_fvg": smc_structure.get("price_at_bearish_fvg", False),
        },
        "oi_drop": rolling_oi_drop,
        "price_rise": price_rise,
        "timestamp": now,
    }


def _format_structure_alert(alert):
    """
    Build Telegram message for structure contradiction alert.
    Tells the trader what zone to watch and on which timeframe.
    """
    s = alert
    underlying = s["underlying"]
    strike = s["strike"]
    opt_type = s["opt_type"]
    tf = s["smc_structure"].get("timeframe", "5m")

    # What OI SC originally wanted
    oi_direction = "BULLISH (CE-SC)" if opt_type == "CE" else "BEARISH (PE-SC)"

    # Zones info with TF
    zone_lines = []
    smc = s["smc_structure"]
    if smc.get("ob_long"):
        low, high = smc["ob_long"]
        at = " ← PRICE HERE" if smc.get("at_bullish_ob") else ""
        zone_lines.append(f"  📗 Bullish OB ({tf}): {low:.1f} - {high:.1f}{at}")
    if smc.get("ob_short"):
        low, high = smc["ob_short"]
        at = " ← PRICE HERE" if smc.get("at_bearish_ob") else ""
        zone_lines.append(f"  📕 Bearish OB ({tf}): {low:.1f} - {high:.1f}{at}")
    if smc.get("fvg_long"):
        low, high = smc["fvg_long"]
        at = " ← PRICE HERE" if smc.get("at_bullish_fvg") else ""
        zone_lines.append(f"  📗 Bullish FVG ({tf}): {low:.1f} - {high:.1f}{at}")
    if smc.get("fvg_short"):
        low, high = smc["fvg_short"]
        at = " ← PRICE HERE" if smc.get("at_bearish_fvg") else ""
        zone_lines.append(f"  📕 Bearish FVG ({tf}): {low:.1f} - {high:.1f}{at}")

    zone_text = "\n".join(zone_lines) if zone_lines else "  No active zones detected"

    bias = smc.get("bias") or "UNCLEAR"

    # Watch direction explanation
    watch = s["watch_direction"]
    watch_zone = s.get("watch_zone")
    watch_type = s.get("watch_zone_type", "")

    if watch == "LONG" and watch_zone:
        watch_text = (
            f"🟢 <b>WATCH FOR LONG</b>\n"
            f"Price is at {watch_type} ({tf}): {watch_zone[0]:.1f} - {watch_zone[1]:.1f}\n"
            f"If rejection candle confirms → LONG entry"
        )
    elif watch == "SHORT" and watch_zone:
        watch_text = (
            f"🔴 <b>WATCH FOR SHORT</b>\n"
            f"Price is at {watch_type} ({tf}): {watch_zone[0]:.1f} - {watch_zone[1]:.1f}\n"
            f"If rejection candle confirms → SHORT entry"
        )
    elif watch == "WAIT" and watch_zone:
        watch_text = (
            f"⏳ <b>WAIT FOR PULLBACK</b>\n"
            f"Price is extended. Wait for pullback to:\n"
            f"{watch_type} ({tf}): {watch_zone[0]:.1f} - {watch_zone[1]:.1f}"
        )
    else:
        watch_text = (
            f"⚠️ <b>CAUTION — STRUCTURE UNCLEAR</b>\n"
            f"OI SC signal contradicted by price structure.\n"
            f"Wait for clearer setup."
        )

    # OI data
    oi_info = ""
    if s.get("oi_drop"):
        oi_info += f"  OI: -{s['oi_drop']['drop_pct']:.1f}%"
    if s.get("price_rise"):
        oi_info += f"  LTP: +{s['price_rise']['rise_pct']:.1f}%"

    msg = (
        f"📋 <b>OI SC STRUCTURE ALERT</b>\n"
        f"{'=' * 30}\n\n"
        f"<b>{underlying} {strike} {opt_type}</b>\n"
        f"📊 Spot: {s['spot']:.0f}\n"
        f"OI SC detected: {oi_direction} (Score {s['original_score']}/10)\n"
        f"❌ <b>BLOCKED</b> by SMC structure (adj {s['smc_adj']:+d} → {s['adjusted_score']}/10)\n"
        f"Reason: {s['smc_reason']}\n\n"
        f"<b>5m STRUCTURE ({tf} chart):</b>\n"
        f"  Bias: {bias}\n"
        f"{zone_text}\n\n"
        f"{watch_text}\n\n"
        f"<i>Open {tf} chart on {underlying} to verify.</i>\n"
        f"Time: {s['timestamp'].strftime('%H:%M:%S')}"
    )

    return msg


# =====================================================
# SUB-CONDITIONS
# =====================================================

def check_rolling_oi_drop(oi_readings, threshold_pct=None, window=None):
    """
    Check if OI dropped >= threshold in last `window` readings.

    Args:
        oi_readings: list of (timestamp, oi_value)
        threshold_pct: float, e.g. 0.05 for 5%
        window: int

    Returns:
        dict with {drop_pct, from_oi, to_oi, window} or None
    """
    threshold = threshold_pct if threshold_pct is not None else OI_SC_MIN_OI_DROP_PCT
    win = window if window is not None else OI_SC_ROLLING_WINDOW

    if len(oi_readings) < win:
        return None

    recent_oi = oi_readings[-1][1]
    earlier_oi = oi_readings[-win][1]

    if earlier_oi <= 0:
        return None

    drop_pct = (earlier_oi - recent_oi) / earlier_oi

    if drop_pct >= threshold:
        return {
            "drop_pct": round(drop_pct * 100, 1),
            "from_oi": earlier_oi,
            "to_oi": recent_oi,
            "window": win,
        }

    return None


def check_price_rise(ltp_readings, threshold_pct=None, window=None):
    """
    Check if LTP rose >= threshold in last `window` readings.

    Args:
        ltp_readings: list of (timestamp, ltp)
        threshold_pct: float, e.g. 0.03 for 3%

    Returns:
        dict with {rise_pct, from_ltp, to_ltp, window} or None
    """
    threshold = threshold_pct if threshold_pct is not None else OI_SC_MIN_PRICE_RISE_PCT
    win = window if window is not None else OI_SC_ROLLING_WINDOW

    if len(ltp_readings) < win:
        return None

    current_ltp = ltp_readings[-1][1]
    earlier_ltp = ltp_readings[-win][1]

    if earlier_ltp <= 0:
        return None

    rise_pct = (current_ltp - earlier_ltp) / earlier_ltp

    if rise_pct >= threshold:
        return {
            "rise_pct": round(rise_pct * 100, 1),
            "from_ltp": earlier_ltp,
            "to_ltp": current_ltp,
            "window": win,
        }

    return None


def check_peak_oi_drop(oi_readings, tradingsymbol=None, threshold_pct=None):
    """
    Check if OI dropped >= threshold from intraday peak.

    Args:
        oi_readings: list of (timestamp, oi_value)
        tradingsymbol: str, to look up peak from _strike_peak_oi
        threshold_pct: float, e.g. 0.08 for 8%

    Returns:
        dict with {drop_pct, peak_oi, current_oi} or None
    """
    threshold = threshold_pct if threshold_pct is not None else OI_SC_PEAK_DROP_PCT

    if len(oi_readings) < 2:
        return None

    # Use tracked peak if available, else compute from readings
    if tradingsymbol and tradingsymbol in _strike_peak_oi:
        peak_oi = _strike_peak_oi[tradingsymbol]
    else:
        peak_oi = max(r[1] for r in oi_readings)

    current_oi = oi_readings[-1][1]

    if peak_oi <= 0:
        return None

    drop_pct = (peak_oi - current_oi) / peak_oi

    if drop_pct >= threshold:
        return {
            "drop_pct": round(drop_pct * 100, 1),
            "peak_oi": peak_oi,
            "current_oi": current_oi,
        }

    return None


def check_oi_velocity(oi_readings, velocity_pct=None, window=None):
    """
    Check speed of OI decline — average % drop per consecutive reading.

    Returns:
        dict with {avg_rate_pct, rates, window} or None
    """
    vel_threshold = velocity_pct if velocity_pct is not None else OI_SC_VELOCITY_PCT
    win = window if window is not None else OI_SC_ROLLING_WINDOW

    if len(oi_readings) < win:
        return None

    recent = oi_readings[-win:]
    rates = []

    for i in range(1, len(recent)):
        prev_oi = recent[i - 1][1]
        curr_oi = recent[i][1]

        if prev_oi <= 0:
            continue

        rate = (prev_oi - curr_oi) / prev_oi
        rates.append(rate)

    if not rates:
        return None

    avg_rate = sum(rates) / len(rates)

    # All rates must be positive (OI consistently dropping)
    if any(r <= 0 for r in rates):
        return None

    if avg_rate >= vel_threshold:
        return {
            "avg_rate_pct": round(avg_rate * 100, 1),
            "rates": [round(r * 100, 1) for r in rates],
            "window": win,
        }

    return None


def check_volume_spike(vol_readings, multiplier=None, lookback=5):
    """
    Check if current volume > multiplier × rolling average.

    Returns:
        True if volume spike detected, False otherwise.
    """
    mult = multiplier if multiplier is not None else OI_SC_VOLUME_MULT

    if len(vol_readings) < lookback + 1:
        return False

    current_vol = vol_readings[-1][1]
    prev_vols = [r[1] for r in vol_readings[-(lookback + 1):-1]]

    avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0

    if avg_vol <= 0:
        return current_vol > 0

    return current_vol > avg_vol * mult


# =====================================================
# TRADE LEVELS
# =====================================================

def _compute_trade_levels(entry_price, ltp_readings):
    """
    Compute entry, SL, target for short-covering trade.

    SL: Recent low of the option price (from last N readings)
    Target: 2R from entry
    """
    entry = entry_price
    rr = OI_SC_TARGET_RR

    # SL: lowest LTP in recent window
    recent_ltps = [r[1] for r in ltp_readings[-OI_SC_ROLLING_WINDOW:]]
    swing_low = min(recent_ltps) if recent_ltps else entry * 0.95

    # Buffer: 1% below swing low
    sl = round(swing_low * 0.99, 2)

    # Safety: SL must be below entry
    if sl >= entry:
        sl = round(entry * 0.95, 2)

    risk = entry - sl
    if risk <= 0:
        risk = entry * 0.05
        sl = round(entry - risk, 2)

    target = round(entry + (risk * rr), 2)

    return {
        "entry": round(entry, 2),
        "sl": sl,
        "target": target,
        "risk": round(risk, 2),
        "rr": rr,
    }


# =====================================================
# ALERT FORMATTING
# =====================================================

def _format_alert(signal):
    """
    Build Telegram-ready alert message for short-covering signal.
    """
    s = signal
    underlying = s["underlying"]
    strike = s["strike"]
    opt_type = s["opt_type"]
    score = s["score"]
    bias = s["underlying_bias"]
    levels = s["trade_levels"]

    # Confidence level
    if score >= 8:
        emoji = "🔴"
        confidence = "HIGHEST"
    elif score >= 6:
        emoji = "🟠"
        confidence = "HIGH"
    else:
        emoji = "🟡"
        confidence = "MEDIUM"

    # Direction explanation
    if opt_type == "CE":
        explanation = (
            "CALL writers are panic-covering their short positions.\n"
            "OI dropping + Premium rising = SHORT COVERING.\n"
            "This creates buying pressure on the underlying → BULLISH."
        )
        action = f"BUY {underlying} {strike} CE"
    else:
        explanation = (
            "PUT writers are panic-covering their short positions.\n"
            "OI dropping + Premium rising = SHORT COVERING.\n"
            "This creates selling pressure on the underlying → BEARISH."
        )
        action = f"BUY {underlying} {strike} PE"

    # Score breakdown
    bd = s["score_breakdown"]
    bd_lines = []
    for k, v in bd.items():
        bd_lines.append(f"  {k}: {v:+d}" if v < 0 else f"  {k}: +{v}")
    breakdown_text = "\n".join(bd_lines)

    # Details
    detail_text = "\n".join(f"  • {d}" for d in s["details"].split(" | "))

    # SMC structure confluence section
    smc_info = s.get("smc_structure", {})
    smc_section = ""
    if smc_info and (smc_info.get("bias") or smc_info.get("score_adj")):
        tf = smc_info.get("timeframe", "5m")
        smc_lines = []
        if smc_info.get("bias"):
            smc_lines.append(f"  Bias ({tf}): {smc_info['bias']}")
        if smc_info.get("ob_long"):
            low, high = smc_info["ob_long"]
            at = " ← AT ZONE" if smc_info.get("at_bullish_ob") else ""
            smc_lines.append(f"  📗 Bullish OB ({tf}): {low:.1f} - {high:.1f}{at}")
        if smc_info.get("ob_short"):
            low, high = smc_info["ob_short"]
            at = " ← AT ZONE" if smc_info.get("at_bearish_ob") else ""
            smc_lines.append(f"  📕 Bearish OB ({tf}): {low:.1f} - {high:.1f}{at}")
        if smc_info.get("fvg_long"):
            low, high = smc_info["fvg_long"]
            at = " ← AT ZONE" if smc_info.get("at_bullish_fvg") else ""
            smc_lines.append(f"  📗 Bullish FVG ({tf}): {low:.1f} - {high:.1f}{at}")
        if smc_info.get("fvg_short"):
            low, high = smc_info["fvg_short"]
            at = " ← AT ZONE" if smc_info.get("at_bearish_fvg") else ""
            smc_lines.append(f"  📕 Bearish FVG ({tf}): {low:.1f} - {high:.1f}{at}")
        adj = smc_info.get("score_adj", 0)
        if adj != 0:
            smc_lines.append(f"  Score adj: {adj:+d}")
        if smc_lines:
            smc_section = f"\n<b>SMC STRUCTURE ({tf} chart):</b>\n" + "\n".join(smc_lines) + "\n"

    msg = (
        f"{emoji} <b>OI SHORT COVERING DETECTED</b>\n"
        f"Score: <b>{score}/10</b> ({confidence})\n"
        f"{'=' * 30}\n\n"
        f"<b>{underlying} {strike} {opt_type}</b>\n"
        f"📊 Spot: {s['spot']:.0f}\n"
        f"💰 Option LTP: {s['current_ltp']:.2f}\n"
        f"📉 OI: {s['current_oi']:,} (peak: {s['peak_oi']:,})\n\n"
        f"<b>PATTERN:</b>\n{detail_text}\n\n"
        f"<b>WHAT IT MEANS:</b>\n"
        f"<i>{explanation}</i>\n\n"
        f"<b>SCORE BREAKDOWN:</b>\n{breakdown_text}\n"
        f"{smc_section}\n"
        f"<b>UNDERLYING BIAS:</b> {bias}\n\n"
        f"📋 <b>TRADE PLAN:</b>\n"
        f"Action: <b>{action}</b>\n"
        f"Entry: <b>{levels['entry']:.2f}</b>\n"
        f"SL: <b>{levels['sl']:.2f}</b>\n"
        f"Target: <b>{levels['target']:.2f}</b> (RR: {levels['rr']:.1f})\n\n"
        f"Time: {s['timestamp'].strftime('%H:%M:%S')}"
    )

    return msg


# =====================================================
# INTERNAL UTILITIES
# =====================================================

def _is_market_hours():
    """Check if within valid scanning time (09:45 - 15:15).
    
    Skips first 30 min after open (09:15-09:45) because:
    - Opening OI movements are noisy (auto-squaring, adjustments)
    - Gap-down/up days produce false signals from stale OI
    - Need genuine price discovery before OI patterns are reliable
    """
    now = datetime.now(_IST).time()
    return time(9, 45) <= now <= time(15, 15)


def _check_daily_reset():
    """Reset state on new trading day."""
    global _last_scan_time

    today = datetime.now(_IST).date().isoformat()

    # Check if any alerted key is from a previous day
    stale_keys = [
        k for k in _alerted_today
        if today not in k
    ]

    # Also check signal locks for stale date keys
    if not stale_keys:
        for key in list(_signal_locks.keys()):
            if hasattr(key, '__iter__') and len(key) >= 2:
                # Lock keys are (underlying, direction) — check expiry time
                expiry = _signal_locks.get(key)
                if expiry and expiry.date().isoformat() != today:
                    stale_keys.append(str(key))
                    break

    if stale_keys:
        reset_state()


def _load_instruments(kite_obj):
    """Load NFO instruments from cache (shared with options.py)."""
    global _instruments_cache

    cache_path = cfg.OPT_CACHE_PKL
    if _instruments_cache:
        return _instruments_cache

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                _instruments_cache = pickle.load(f)
            if len(_instruments_cache) > 100:
                return _instruments_cache
        except Exception:
            pass

    try:
        _instruments_cache = kite_obj.instruments(exchange="NFO")
        with open(cache_path, "wb") as f:
            pickle.dump(_instruments_cache, f)
        return _instruments_cache
    except Exception as e:
        logger.warning(f"OI SC: Failed to fetch instruments: {e}")
        return None
