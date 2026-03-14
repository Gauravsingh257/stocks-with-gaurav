from __future__ import annotations

from dataclasses import dataclass

from services.fundamental_analysis import FundamentalSnapshot
from services.news_analysis import SentimentSnapshot
from services.technical_scanner import TechnicalSnapshot


@dataclass(slots=True)
class QualityGateResult:
    symbol: str
    passed: bool
    score: float
    reasons: list[str]


def evaluate_symbol_quality(
    symbol: str,
    technical: TechnicalSnapshot,
    fundamental: FundamentalSnapshot,
    sentiment: SentimentSnapshot,
) -> QualityGateResult:
    reasons: list[str] = []
    score = 0.0

    liquidity = technical.liquidity_score
    if liquidity < 0.52:
        reasons.append("low_liquidity")
    else:
        score += 0.35

    if technical.volume_expansion < 0.45:
        reasons.append("weak_volume")
    else:
        score += 0.20

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

    return QualityGateResult(
        symbol=symbol,
        passed=score >= 0.65,
        score=round(score, 4),
        reasons=reasons,
    )
