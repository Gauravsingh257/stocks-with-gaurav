from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.fundamental_analysis import FundamentalSnapshot
from services.news_analysis import SentimentSnapshot
from services.technical_scanner import TechnicalSnapshot


@dataclass(slots=True)
class FactorRow:
    """One symbol's factor vector.

    PHASE 0: `factors` may be PARTIAL. A factor whose source data is unavailable
    is absent from the dict rather than present with a fabricated value, and
    `available_groups` records which classes of data actually backed this row so
    the scorer can renormalize instead of ranking a real stock against noise.
    """

    symbol: str
    factors: dict[str, float]
    technical_score: float
    fundamental_score: float | None
    sentiment_score: float | None
    liquidity_score: float
    available_groups: tuple[str, ...] = ("technical", "fundamental", "sentiment")

    def has(self, group: str) -> bool:
        return group in self.available_groups

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "factors": self.factors,
            "technical_score": self.technical_score,
            "fundamental_score": self.fundamental_score,
            "sentiment_score": self.sentiment_score,
            "liquidity_score": self.liquidity_score,
            "available_groups": list(self.available_groups),
        }


def build_factor_row(
    symbol: str,
    technical: TechnicalSnapshot,
    fundamental: FundamentalSnapshot | None,
    sentiment: SentimentSnapshot | None,
) -> FactorRow:
    """Assemble the factor vector from whatever real data exists.

    `technical` is required — without bars there is no row to build. Fundamental
    and sentiment are optional; when absent their factors are simply not emitted.
    """
    groups: list[str] = ["technical"]
    factors: dict[str, float] = {
        # Technical
        "trend": technical.trend_structure,
        "momentum": (technical.rsi_momentum + technical.macd_momentum) / 2,
        "breakout": (technical.fvg_quality + technical.order_block_quality) / 2,
        "mtf_alignment": technical.mtf_alignment,
        # Liquidity
        "liquidity": technical.liquidity_score,
        "volume_expansion": technical.volume_expansion,
    }

    if fundamental is not None:
        groups.append("fundamental")
        factors.update({
            "growth": (fundamental.revenue_growth + fundamental.earnings_growth) / 2,
            "quality": (fundamental.roce + fundamental.roe + fundamental.management_quality) / 3,
            "balance_sheet": fundamental.debt_quality,
            "institutional_accumulation": fundamental.institutional_accumulation,
        })

    if sentiment is not None:
        groups.append("sentiment")
        factors.update({
            "news_sentiment": sentiment.financial_news,
            "sector_rotation": sentiment.sector_rotation,
            "macro_sentiment": sentiment.macro_sentiment,
        })

    return FactorRow(
        symbol=symbol,
        factors=factors,
        technical_score=technical.technical_score,
        fundamental_score=fundamental.fundamental_score if fundamental else None,
        sentiment_score=sentiment.sentiment_score if sentiment else None,
        liquidity_score=technical.liquidity_score,
        available_groups=tuple(groups),
    )
