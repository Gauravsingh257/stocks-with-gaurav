"""
engine/indicators.py — Pure indicator and helper functions.
Extracted from smc_mtf_engine_v4.py lines 649–870 (Phase 5).

All functions here are stateless (except SIMULATION_TIME for killzone).
"""

from datetime import datetime, time
import pandas as pd
import logging

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

import smc_detectors as smc

# ---------------------------------------------------------------------------
# Time / Killzone
# ---------------------------------------------------------------------------

SIMULATION_TIME = None  # Override for backtesting

def killzone_confidence():
    """
    E4: Time-weighted killzone confidence (0.0 - 1.0).
    Higher = better trading quality expected.
    """
    now = SIMULATION_TIME or datetime.now(_IST).time()

    if time(9, 15) <= now < time(10, 0):
        return 0.8
    if time(10, 0) <= now < time(11, 0):
        return 0.0
    if time(11, 0) <= now < time(13, 0):
        return 1.0
    return 0.0

def is_killzone():
    """Backward-compatible wrapper. Returns True if in a tradeable window."""
    return killzone_confidence() > 0.0

# ---------------------------------------------------------------------------
# Expiry Day
# ---------------------------------------------------------------------------

def is_expiry_day(symbol=None):
    """
    Returns True if today is a weekly expiry day for the given symbol.
    NIFTY 50 / FIN SERVICE: Thursday
    BANK NIFTY: Wednesday
    """
    today = datetime.now().weekday()
    if symbol:
        if "BANK" in symbol.upper():
            return today == 2
        if "NIFTY" in symbol.upper() or "FIN" in symbol.upper():
            return today == 3
    return today in (2, 3)

def expiry_day_risk_adjustment(symbol, sl, entry, atr):
    """Widen SL by 1.5x ATR buffer and halve position size on expiry days."""
    if not is_expiry_day(symbol):
        return sl, 1.0
    extra_buffer = atr * 0.15
    if entry > sl:
        adjusted_sl = sl - extra_buffer
    else:
        adjusted_sl = sl + extra_buffer
    logging.info(f"EXPIRY DAY: {symbol} SL widened {sl:.2f} -> {adjusted_sl:.2f}")
    return round(adjusted_sl, 2), 0.5

# ---------------------------------------------------------------------------
# SMC Zone Delegation
# ---------------------------------------------------------------------------

def is_discount_zone(candles, price):
    return smc.is_discount_zone(candles, price)

def is_premium_zone(candles, price):
    return smc.is_premium_zone(candles, price)

# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def calculate_atr(candles, period: int = 14):
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def index_atr_filter(candles, min_atr=12):
    """Filters low volatility chop for Nifty/Fin."""
    if not candles:
        return False
    return calculate_atr(candles) >= min_atr

# ---------------------------------------------------------------------------
# EMA / ADX
# ---------------------------------------------------------------------------

def calc_ema(values, period):
    if len(values) < period:
        return [0] * len(values)
    ema = [0] * (period - 1)
    sma = sum(values[:period]) / period
    ema.append(sma)
    multiplier = 2 / (period + 1)
    for i in range(period, len(values)):
        val = (values[i] - ema[-1]) * multiplier + ema[-1]
        ema.append(val)
    return ema

def calc_adx(candles, period=14):
    if len(candles) < period * 2:
        return 0
    try:
        df = pd.DataFrame(candles)
        df['h-l'] = df['high'] - df['low']
        df['h-pc'] = (df['high'] - df['close'].shift(1)).abs()
        df['l-pc'] = (df['low'] - df['close'].shift(1)).abs()
        df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)

        df['up'] = df['high'] - df['high'].shift(1)
        df['down'] = df['low'].shift(1) - df['low']

        df['+dm'] = 0.0
        df.loc[(df['up'] > df['down']) & (df['up'] > 0), '+dm'] = df['up']
        df['-dm'] = 0.0
        df.loc[(df['down'] > df['up']) & (df['down'] > 0), '-dm'] = df['down']

        a = 1 / period
        df['tr_s'] = df['tr'].ewm(alpha=a, adjust=False).mean()
        df['+dm_s'] = df['+dm'].ewm(alpha=a, adjust=False).mean()
        df['-dm_s'] = df['-dm'].ewm(alpha=a, adjust=False).mean()

        df['+di'] = 100 * (df['+dm_s'] / df['tr_s'])
        df['-di'] = 100 * (df['-dm_s'] / df['tr_s'])

        df['dx'] = 100 * (abs(df['+di'] - df['-di']) / (df['+di'] + df['-di']))
        df['adx'] = df['dx'].ewm(alpha=a, adjust=False).mean()

        val = df['adx'].iloc[-1]
        return val if not pd.isna(val) else 0
    except Exception as e:
        print(f"ADX calculation error: {e}")
        return 0

# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

def volume_expansion(candles, lookback: int = 20, multiplier: float = 1.3):
    if len(candles) < lookback + 1:
        return False
    avg_vol = sum(c["volume"] for c in candles[-lookback - 1:-1]) / lookback
    return candles[-1]["volume"] > avg_vol * multiplier

# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------

def is_liquid_stock(candles, min_turnover=750000, min_price=90):
    """Check if stock is liquid based on turnover and price."""
    if not candles:
        return False
    if candles[-1]['close'] < min_price:
        return False
    avg_turnover = sum(c['close'] * c['volume'] for c in candles[-5:]) / 5
    return avg_turnover > min_turnover

# ---------------------------------------------------------------------------
# Dynamic SL Buffer
# ---------------------------------------------------------------------------

def compute_dynamic_buffer(symbol, atr):
    """
    Dynamic SL Buffer based on Asset Volatility.
    GRID SEARCH (Feb 2026): buf=0.1 optimal (Sharpe=3.51, E=+0.2579R).
    Applied uniformly for indices; stocks use 0.25 safety margin.
    """
    if "BANK" in symbol:
        return atr * 0.1
    elif "NIFTY 50" in symbol:
        return atr * 0.1
    elif "FIN" in symbol:
        return atr * 0.3
    else:
        return atr * 0.25
