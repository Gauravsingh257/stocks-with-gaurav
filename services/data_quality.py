from __future__ import annotations

import os
from dataclasses import dataclass

from services.fundamental_analysis import FundamentalSnapshot
from services.news_analysis import SentimentSnapshot
from services.technical_scanner import TechnicalSnapshot

# ── Hard filters (instant reject) ────────────────────────────────
MIN_MARKET_CAP_CR = float(os.getenv("RESEARCH_MIN_MCAP_CR", "500"))      # ₹500 Cr minimum
MIN_PRICE = float(os.getenv("RESEARCH_MIN_PRICE", "50"))                  # ₹50 minimum (no penny stocks)
MAX_PRICE = float(os.getenv("RESEARCH_MAX_PRICE", "50000"))               # ₹50,000 max
MIN_AVG_VOLUME = float(os.getenv("RESEARCH_MIN_AVG_VOLUME", "100000"))    # 1 lakh shares/day
REQUIRE_REAL_FUNDAMENTALS = os.getenv("RESEARCH_REQUIRE_REAL_DATA", "1") != "0"


@dataclass(slots=True)
class QualityGateResult:
    symbol: str
    passed: bool
    score: float
    reasons: list[str]
    data_authenticity: str  # "real", "partial", "synthetic"


def evaluate_symbol_quality(
    symbol: str,
    technical: TechnicalSnapshot,
    fundamental: FundamentalSnapshot,
    sentiment: SentimentSnapshot,
) -> QualityGateResult:
    reasons: list[str] = []
    score = 0.0

    # ── Hard filters: reject immediately without scoring ──────────
    # Market cap filter (only when real data available)
    if fundamental.raw_market_cap_cr is not None:
        if fundamental.raw_market_cap_cr < MIN_MARKET_CAP_CR:
            reasons.append(f"market_cap_too_low({fundamental.raw_market_cap_cr:.0f}Cr<{MIN_MARKET_CAP_CR:.0f}Cr)")
            return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity="real")

    # Data authenticity check: reject if only hash/synthetic data
    data_auth = "real"
    if fundamental.data_source == "hash":
        data_auth = "synthetic"
        if REQUIRE_REAL_FUNDAMENTALS:
            reasons.append("no_real_fundamental_data")
            return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity="synthetic")
    elif fundamental.raw_pe is None and fundamental.raw_roe_pct is None:
        data_auth = "partial"

    # Downtrend filter: reject stocks in strong downtrend (trend_structure < 0.35)
    if technical.trend_structure < 0.35:
        reasons.append("strong_downtrend")
        return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity=data_auth)

    # ── Soft scoring ──────────────────────────────────────────────
    liquidity = technical.liquidity_score
    if liquidity < 0.52:
        reasons.append("low_liquidity")
    else:
        score += 0.30

    if technical.volume_expansion < 0.45:
        reasons.append("weak_volume")
    else:
        score += 0.15

    if fundamental.fundamental_score < 0.40:
        reasons.append("weak_fundamentals")
    else:
        score += 0.25

    if sentiment.sentiment_score < 0.30:
        reasons.append("weak_sentiment")
    else:
        score += 0.10

    if technical.technical_score >= 0.45:
        score += 0.10

    # Bonus: real fundamental data with healthy metrics
    if fundamental.data_source == "yfinance" and fundamental.raw_roe_pct is not None:
        if fundamental.raw_roe_pct >= 12:
            score += 0.10

    return QualityGateResult(
        symbol=symbol,
        passed=score >= 0.65,
        score=round(score, 4),
        reasons=reasons,
        data_authenticity=data_auth,
    )
