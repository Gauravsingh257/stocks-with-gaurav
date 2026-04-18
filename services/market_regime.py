"""
services/market_regime.py

Market regime detection for the stock selection engine.

Determines broad market condition (TRENDING_UP, TRENDING_DOWN, SIDEWAYS)
using NIFTY 50 daily data. This is separate from the intraday market_state_engine
which tracks real-time BOS/CHOCH events.

Used by idea_selector.py to adjust scoring weights based on market condition:
  - TRENDING_UP  → favor momentum & breakout stocks
  - TRENDING_DOWN → tighten filters, reduce position sizing
  - SIDEWAYS      → favor mean-reversion, tighten RR requirements

Data source: yfinance (^NSEI) — works after market hours too.
Cached with configurable TTL (default 4h).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger("services.market_regime")

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

CACHE_TTL_SECONDS = 4 * 3600   # Recompute every 4 hours
EMA_SHORT = 20                 # Short EMA period (days)
EMA_LONG = 50                  # Long EMA period (days)
ADX_PERIOD = 14                # ADX lookback
ADX_TREND_THRESHOLD = 25       # ADX above this = trending
LOOKBACK_DAYS = 100            # Bars to fetch


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketRegime:
    regime: str = "UNKNOWN"            # TRENDING_UP | TRENDING_DOWN | SIDEWAYS | UNKNOWN
    confidence: float = 0.0            # 0.0 - 1.0
    nifty_close: float = 0.0
    ema_short: float = 0.0
    ema_long: float = 0.0
    adx: float = 0.0
    trend_slope: float = 0.0           # EMA20 slope (% per day)
    computed_at: float = 0.0

    # Scoring adjustments for idea_selector
    swing_adjustments: dict = None      # type: ignore[assignment]
    longterm_adjustments: dict = None    # type: ignore[assignment]

    def __post_init__(self):
        if self.swing_adjustments is None:
            self.swing_adjustments = {}
        if self.longterm_adjustments is None:
            self.longterm_adjustments = {}


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

_cached_regime: MarketRegime | None = None


def _is_cache_valid() -> bool:
    if not _cached_regime:
        return False
    return (time.time() - _cached_regime.computed_at) < CACHE_TTL_SECONDS


# ──────────────────────────────────────────────────────────────────────────────
# Technical helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    """Simple EMA calculation."""
    if len(values) < period:
        return values[:]
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _compute_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Compute latest ADX value from OHLC data."""
    n = len(closes)
    if n < period + 1:
        return 0.0

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr_list: list[float] = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]
        plus_dm.append(max(high_diff, 0) if high_diff > low_diff else 0)
        minus_dm.append(max(low_diff, 0) if low_diff > high_diff else 0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)

    if len(tr_list) < period:
        return 0.0

    # Smoothed averages
    atr = sum(tr_list[:period]) / period
    plus_di_sum = sum(plus_dm[:period]) / period
    minus_di_sum = sum(minus_dm[:period]) / period

    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_di_sum = (plus_di_sum * (period - 1) + plus_dm[i]) / period
        minus_di_sum = (minus_di_sum * (period - 1) + minus_dm[i]) / period

    if atr == 0:
        return 0.0

    plus_di = (plus_di_sum / atr) * 100
    minus_di = (minus_di_sum / atr) * 100
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0.0

    dx = abs(plus_di - minus_di) / di_sum * 100
    return round(dx, 2)


# ──────────────────────────────────────────────────────────────────────────────
# Core detection
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_nifty_data() -> dict | None:
    """Fetch NIFTY 50 daily OHLC via yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("^NSEI")
        df = ticker.history(period=f"{LOOKBACK_DAYS}d")
        if df is None or df.empty or len(df) < EMA_LONG + 5:
            log.warning("[Regime] Insufficient NIFTY data: %d bars", len(df) if df is not None else 0)
            return None
        return {
            "closes": df["Close"].tolist(),
            "highs": df["High"].tolist(),
            "lows": df["Low"].tolist(),
        }
    except Exception:
        log.exception("[Regime] Failed to fetch NIFTY data")
        return None


def detect_regime(force: bool = False) -> MarketRegime:
    """
    Detect current market regime using NIFTY 50 daily data.
    Returns cached result if fresh enough.
    """
    global _cached_regime

    if not force and _is_cache_valid() and _cached_regime:
        return _cached_regime

    data = _fetch_nifty_data()
    if not data:
        regime = MarketRegime(regime="UNKNOWN", computed_at=time.time())
        _cached_regime = regime
        return regime

    closes = data["closes"]
    highs = data["highs"]
    lows = data["lows"]

    # Compute indicators
    ema_s = _ema(closes, EMA_SHORT)
    ema_l = _ema(closes, EMA_LONG)
    adx = _compute_adx(highs, lows, closes, ADX_PERIOD)

    current_close = closes[-1]
    current_ema_s = ema_s[-1] if ema_s else current_close
    current_ema_l = ema_l[-1] if ema_l else current_close

    # EMA slope — % change of EMA20 over last 5 days
    if len(ema_s) >= 6:
        slope = (ema_s[-1] - ema_s[-6]) / ema_s[-6] * 100 / 5
    else:
        slope = 0.0

    # Determine regime
    ema_bullish = current_ema_s > current_ema_l
    price_above_ema = current_close > current_ema_s
    is_trending = adx >= ADX_TREND_THRESHOLD

    if is_trending and ema_bullish and price_above_ema:
        regime_label = "TRENDING_UP"
        confidence = min(1.0, adx / 50)
    elif is_trending and not ema_bullish and not price_above_ema:
        regime_label = "TRENDING_DOWN"
        confidence = min(1.0, adx / 50)
    else:
        regime_label = "SIDEWAYS"
        confidence = 1.0 - min(1.0, adx / 50)

    # Compute scoring adjustments based on regime
    swing_adj: dict[str, float] = {}
    longterm_adj: dict[str, float] = {}

    if regime_label == "TRENDING_UP":
        # Favor momentum and breakout in uptrends
        swing_adj = {"momentum": 0.04, "breakout": 0.03, "trend": 0.03, "liquidity": -0.02}
        longterm_adj = {"momentum": 0.03, "growth": 0.02}
    elif regime_label == "TRENDING_DOWN":
        # Tighten everything — defensive
        swing_adj = {"momentum": -0.05, "breakout": -0.04, "trend": -0.03, "volume_expansion": -0.02}
        longterm_adj = {"quality": 0.04, "institutional_accumulation": 0.03, "growth": -0.03}
    else:  # SIDEWAYS
        # Reduce breakout weight (false breakouts), favor liquidity and mean-reversion
        swing_adj = {"breakout": -0.04, "liquidity": 0.03, "mtf_alignment": 0.03}
        longterm_adj = {"quality": 0.02, "institutional_accumulation": 0.02}

    regime = MarketRegime(
        regime=regime_label,
        confidence=round(confidence, 3),
        nifty_close=round(current_close, 2),
        ema_short=round(current_ema_s, 2),
        ema_long=round(current_ema_l, 2),
        adx=adx,
        trend_slope=round(slope, 4),
        computed_at=time.time(),
        swing_adjustments=swing_adj,
        longterm_adjustments=longterm_adj,
    )

    _cached_regime = regime
    log.info("[Regime] %s (confidence=%.2f, ADX=%.1f, slope=%.3f%%/day, NIFTY=%.0f)",
             regime_label, confidence, adx, slope, current_close)

    return regime


def get_regime_adjustments(horizon: str) -> dict[str, float]:
    """Get regime-based weight adjustments for a given horizon."""
    regime = detect_regime()
    if regime.regime == "UNKNOWN":
        return {}
    if horizon.upper() == "SWING":
        return regime.swing_adjustments
    return regime.longterm_adjustments


def get_regime_summary() -> dict:
    """Regime info for API consumption."""
    r = detect_regime()
    return {
        "regime": r.regime,
        "confidence": r.confidence,
        "nifty_close": r.nifty_close,
        "ema_short": r.ema_short,
        "ema_long": r.ema_long,
        "adx": r.adx,
        "trend_slope_pct_per_day": r.trend_slope,
        "swing_adjustments": r.swing_adjustments,
        "longterm_adjustments": r.longterm_adjustments,
    }
