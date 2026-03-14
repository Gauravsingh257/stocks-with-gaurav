"""
Global Data Module
==================
Computes a global sentiment score from US markets, Asian markets,
and GIFT Nifty gap data.

All functions accept explicit data inputs for backtest compatibility.
No side-effect API calls — data must be passed in.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ─── THRESHOLDS ────────────────────────────────────────
US_BULLISH_THRESHOLD = 0.3    # % change above which US is bullish
US_BEARISH_THRESHOLD = -0.3   # % change below which US is bearish
ASIA_BULLISH_THRESHOLD = 0.2
ASIA_BEARISH_THRESHOLD = -0.2
GIFT_BULLISH_THRESHOLD = 0.3  # % gap up from prev close
GIFT_BEARISH_THRESHOLD = -0.3

# ─── WEIGHTS ───────────────────────────────────────────
US_WEIGHT = 0.40
ASIA_WEIGHT = 0.35
GIFT_WEIGHT = 0.25


def get_us_market_change(
    sp500_change_pct: float = 0.0,
    nasdaq_change_pct: float = 0.0,
    dow_change_pct: float = 0.0,
) -> Dict:
    """
    Compute US market sentiment from overnight index changes.

    Args:
        sp500_change_pct:  S&P 500 % change (e.g. 0.5 for +0.5%)
        nasdaq_change_pct: NASDAQ % change
        dow_change_pct:    Dow Jones % change

    Returns:
        {"us_bias": str, "us_avg_change": float}
    """
    avg_change = (sp500_change_pct + nasdaq_change_pct + dow_change_pct) / 3.0

    if avg_change >= US_BULLISH_THRESHOLD:
        bias = "BULLISH"
    elif avg_change <= US_BEARISH_THRESHOLD:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {"us_bias": bias, "us_avg_change": round(avg_change, 4)}


def get_asia_market_change(
    nikkei_change_pct: float = 0.0,
    hangseng_change_pct: float = 0.0,
    sgx_change_pct: float = 0.0,
) -> Dict:
    """
    Compute Asian market sentiment from morning session data.

    Args:
        nikkei_change_pct:   Nikkei 225 % change
        hangseng_change_pct: Hang Seng % change
        sgx_change_pct:      SGX Nifty / Straits Times % change

    Returns:
        {"asia_bias": str, "asia_avg_change": float}
    """
    avg_change = (nikkei_change_pct + hangseng_change_pct + sgx_change_pct) / 3.0

    if avg_change >= ASIA_BULLISH_THRESHOLD:
        bias = "BULLISH"
    elif avg_change <= ASIA_BEARISH_THRESHOLD:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {"asia_bias": bias, "asia_avg_change": round(avg_change, 4)}


def get_gift_nifty_gap(
    gift_nifty_price: float = 0.0,
    prev_nifty_close: float = 0.0,
) -> Dict:
    """
    Compute GIFT Nifty gap as percentage of previous close.

    Args:
        gift_nifty_price: Current GIFT Nifty indicative price
        prev_nifty_close: Previous day Nifty 50 closing price

    Returns:
        {"gift_bias": str, "gift_gap_pct": float}
    """
    if prev_nifty_close <= 0:
        return {"gift_bias": "NEUTRAL", "gift_gap_pct": 0.0}

    gap_pct = ((gift_nifty_price - prev_nifty_close) / prev_nifty_close) * 100.0

    if gap_pct >= GIFT_BULLISH_THRESHOLD:
        bias = "BULLISH"
    elif gap_pct <= GIFT_BEARISH_THRESHOLD:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {"gift_bias": bias, "gift_gap_pct": round(gap_pct, 4)}


def compute_global_score(
    sp500_change_pct: float = 0.0,
    nasdaq_change_pct: float = 0.0,
    dow_change_pct: float = 0.0,
    nikkei_change_pct: float = 0.0,
    hangseng_change_pct: float = 0.0,
    sgx_change_pct: float = 0.0,
    gift_nifty_price: float = 0.0,
    prev_nifty_close: float = 0.0,
) -> Dict:
    """
    Combine all global signals into a single weighted score (0-100).

    Weights: US=40%, Asia=35%, GIFT Nifty=25%

    Score mapping:
        0   = maximum bearish
        50  = neutral
        100 = maximum bullish

    Returns:
        {
            "global_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
            "global_score": int (0-100),
            "us": {...},
            "asia": {...},
            "gift": {...},
        }
    """
    us = get_us_market_change(sp500_change_pct, nasdaq_change_pct, dow_change_pct)
    asia = get_asia_market_change(nikkei_change_pct, hangseng_change_pct, sgx_change_pct)
    gift = get_gift_nifty_gap(gift_nifty_price, prev_nifty_close)

    # Convert each component to a 0-100 sub-score
    # We clamp the % change to [-2, +2] and map linearly to [0, 100]
    def pct_to_score(pct: float, clamp: float = 2.0) -> float:
        clamped = max(-clamp, min(clamp, pct))
        return ((clamped + clamp) / (2 * clamp)) * 100.0

    us_score = pct_to_score(us["us_avg_change"])
    asia_score = pct_to_score(asia["asia_avg_change"])
    gift_score = pct_to_score(gift["gift_gap_pct"])

    global_score = int(round(
        us_score * US_WEIGHT
        + asia_score * ASIA_WEIGHT
        + gift_score * GIFT_WEIGHT
    ))
    global_score = max(0, min(100, global_score))

    # Determine overall bias
    if global_score >= 60:
        bias = "BULLISH"
    elif global_score <= 40:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    result = {
        "global_bias": bias,
        "global_score": global_score,
        "us": us,
        "asia": asia,
        "gift": gift,
    }

    logger.info(
        f"[GLOBAL] bias={bias} score={global_score} | "
        f"US={us['us_avg_change']}% Asia={asia['asia_avg_change']}% "
        f"GIFT gap={gift['gift_gap_pct']}%"
    )

    return result
