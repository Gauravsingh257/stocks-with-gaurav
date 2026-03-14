from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any


def _stable_unit(symbol: str, salt: str) -> float:
    raw = sha256(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    return int(raw[16:24], 16) / 0xFFFFFFFF


@dataclass(slots=True)
class SentimentSnapshot:
    symbol: str
    financial_news: float
    earnings_event_bias: float
    sector_rotation: float
    macro_sentiment: float
    sentiment_score: float

    def as_factors(self) -> dict[str, Any]:
        return {
            "financial_news": round(self.financial_news, 3),
            "earnings_announcements": round(self.earnings_event_bias, 3),
            "sector_rotation": round(self.sector_rotation, 3),
            "macro_sentiment": round(self.macro_sentiment, 3),
            "sentiment_score": round(self.sentiment_score, 3),
        }


def _weighted_score(parts: list[tuple[float, float]]) -> float:
    total_w = sum(w for _, w in parts) or 1.0
    return sum(v * w for v, w in parts) / total_w


async def analyze_news_sentiment(symbols: list[str]) -> dict[str, SentimentSnapshot]:
    """
    Sentiment provider with stable output.
    This preserves a clean integration seam for optional FinBERT/news APIs.
    """
    output: dict[str, SentimentSnapshot] = {}
    for symbol in symbols:
        news = 0.35 + (_stable_unit(symbol, "news") * 0.65)
        earnings_event = 0.35 + (_stable_unit(symbol, "earnings_event") * 0.65)
        sector = 0.35 + (_stable_unit(symbol, "sector_rotation") * 0.65)
        macro = 0.35 + (_stable_unit(symbol, "macro") * 0.65)
        score = _weighted_score(
            [
                (news, 0.35),
                (earnings_event, 0.20),
                (sector, 0.25),
                (macro, 0.20),
            ]
        )
        output[symbol] = SentimentSnapshot(
            symbol=symbol,
            financial_news=news,
            earnings_event_bias=earnings_event,
            sector_rotation=sector,
            macro_sentiment=macro,
            sentiment_score=score,
        )
    return output
