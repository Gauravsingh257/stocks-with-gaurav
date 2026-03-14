from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.fundamental_analysis import FundamentalSnapshot
from services.news_analysis import SentimentSnapshot
from services.technical_scanner import TechnicalSnapshot


@dataclass(slots=True)
class FactorRow:
    symbol: str
    factors: dict[str, float]
    technical_score: float
    fundamental_score: float
    sentiment_score: float
    liquidity_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "factors": self.factors,
            "technical_score": self.technical_score,
            "fundamental_score": self.fundamental_score,
            "sentiment_score": self.sentiment_score,
            "liquidity_score": self.liquidity_score,
        }


def build_factor_row(
    symbol: str,
    technical: TechnicalSnapshot,
    fundamental: FundamentalSnapshot,
    sentiment: SentimentSnapshot,
) -> FactorRow:
    factors = {
        # Technical
        "trend": technical.trend_structure,
        "momentum": (technical.rsi_momentum + technical.macd_momentum) / 2,
        "breakout": (technical.fvg_quality + technical.order_block_quality) / 2,
        "mtf_alignment": technical.mtf_alignment,
        # Fundamental
        "growth": (fundamental.revenue_growth + fundamental.earnings_growth) / 2,
        "quality": (fundamental.roce + fundamental.roe + fundamental.management_quality) / 3,
        "balance_sheet": fundamental.debt_quality,
        "institutional_accumulation": fundamental.institutional_accumulation,
        # Sentiment / flow
        "news_sentiment": sentiment.financial_news,
        "sector_rotation": sentiment.sector_rotation,
        "macro_sentiment": sentiment.macro_sentiment,
        # Liquidity
        "liquidity": technical.liquidity_score,
        "volume_expansion": technical.volume_expansion,
    }

    return FactorRow(
        symbol=symbol,
        factors=factors,
        technical_score=technical.technical_score,
        fundamental_score=fundamental.fundamental_score,
        sentiment_score=sentiment.sentiment_score,
        liquidity_score=technical.liquidity_score,
    )
