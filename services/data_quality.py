from __future__ import annotations

import os
from dataclasses import dataclass

from services.fundamental_analysis import FundamentalSnapshot
from services.news_analysis import SentimentSnapshot
from services.technical_scanner import TechnicalSnapshot

# ── Hard filters (instant reject) ────────────────────────────────
MIN_MARKET_CAP_CR = float(os.getenv("RESEARCH_MIN_MCAP_CR", "300"))       # ₹300 Cr minimum
MIN_PRICE = float(os.getenv("RESEARCH_MIN_PRICE", "50"))                   # ₹50 minimum (no penny stocks)
MAX_PRICE = float(os.getenv("RESEARCH_MAX_PRICE", "50000"))                # ₹50,000 max
MIN_AVG_VOLUME = float(os.getenv("RESEARCH_MIN_AVG_VOLUME", "50000"))      # 50k shares/day
# When "0" — allow stocks with partial/synthetic fundamentals through the quality gate.
# This broadens the universe significantly in markets where yfinance coverage is sparse.
REQUIRE_REAL_FUNDAMENTALS = os.getenv("RESEARCH_REQUIRE_REAL_DATA", "0") != "0"

# ── Quality tier thresholds ───────────────────────────────────────
# Score range 0.0–1.0 → tier label surfaced to frontend/ranking.
# Elite≥0.85, Strong≥0.75, Good≥0.65, Watchlist≥0.55, pass≥0.45
SCORE_ELITE = float(os.getenv("RESEARCH_SCORE_ELITE", "0.85"))
SCORE_STRONG = float(os.getenv("RESEARCH_SCORE_STRONG", "0.75"))
SCORE_GOOD = float(os.getenv("RESEARCH_SCORE_GOOD", "0.65"))
SCORE_WATCHLIST = float(os.getenv("RESEARCH_SCORE_WATCHLIST", "0.55"))
SCORE_PASS = float(os.getenv("RESEARCH_SCORE_PASS", "0.45"))


def quality_tier(score: float) -> str:
    if score >= SCORE_ELITE:
        return "Elite"
    if score >= SCORE_STRONG:
        return "Strong"
    if score >= SCORE_GOOD:
        return "Good"
    if score >= SCORE_WATCHLIST:
        return "Watchlist"
    return "Below"


@dataclass(slots=True)
class QualityGateResult:
    symbol: str
    passed: bool
    score: float
    reasons: list[str]
    data_authenticity: str  # "real", "partial", "synthetic"
    tier: str = "Below"


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
            return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity="real", tier="Below")

    # PE / earnings hard filter (only when real data available)
    if fundamental.raw_pe is not None:
        if fundamental.raw_pe < 0:
            reasons.append("negative_earnings")
            return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity="real", tier="Below")
        if fundamental.raw_pe > 200:
            reasons.append(f"extreme_pe({fundamental.raw_pe:.0f})")
            return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity="real", tier="Below")

    # Data authenticity check
    data_auth = "real"
    if fundamental.data_source == "hash":
        data_auth = "synthetic"
        if REQUIRE_REAL_FUNDAMENTALS:
            reasons.append("no_real_fundamental_data")
            return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity="synthetic", tier="Below")
    elif fundamental.raw_pe is None and fundamental.raw_roe_pct is None:
        data_auth = "partial"

    # Downtrend filter: only reject stocks in extreme downtrend (trend_structure < 0.25)
    # Was 0.35 — loosened to let more consolidating/recovering stocks through.
    if technical.trend_structure < 0.25:
        reasons.append("extreme_downtrend")
        return QualityGateResult(symbol=symbol, passed=False, score=0.0, reasons=reasons, data_authenticity=data_auth, tier="Below")

    # ── Soft scoring ──────────────────────────────────────────────
    liquidity = technical.liquidity_score
    if liquidity < 0.40:
        reasons.append("low_liquidity")
    else:
        score += 0.30

    if technical.volume_expansion < 0.35:
        reasons.append("weak_volume")
    else:
        score += 0.15

    if fundamental.fundamental_score < 0.30:
        reasons.append("weak_fundamentals")
    else:
        score += 0.25

    if sentiment.sentiment_score < 0.25:
        reasons.append("weak_sentiment")
    else:
        score += 0.10

    if technical.technical_score >= 0.40:
        score += 0.10

    # Bonus: real fundamental data with healthy metrics
    if fundamental.data_source == "yfinance" and fundamental.raw_roe_pct is not None:
        if fundamental.raw_roe_pct >= 10:
            score += 0.10

    tier = quality_tier(score)
    return QualityGateResult(
        symbol=symbol,
        passed=score >= SCORE_PASS,
        score=round(score, 4),
        reasons=reasons,
        data_authenticity=data_auth,
        tier=tier,
    )
