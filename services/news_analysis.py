from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

log = logging.getLogger("services.news_analysis")


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
    data_source: str = "synthetic"  # "synthetic" until real news API integrated

    def as_factors(self) -> dict[str, Any]:
        return {
            "financial_news": round(self.financial_news, 3),
            "earnings_announcements": round(self.earnings_event_bias, 3),
            "sector_rotation": round(self.sector_rotation, 3),
            "macro_sentiment": round(self.macro_sentiment, 3),
            "sentiment_score": round(self.sentiment_score, 3),
            "data_source": self.data_source,
        }


def _weighted_score(parts: list[tuple[float, float]]) -> float:
    total_w = sum(w for _, w in parts) or 1.0
    return sum(v * w for v, w in parts) / total_w


def synthetic_disabled() -> bool:
    """PHASE 0 flag. When set, no synthetic sentiment is produced at all.

    The teardown established these hash values are not "neutral placeholders":
    `_stable_unit` returns a different number per ticker, so every symbol gets a
    different sentiment score derived from nothing but the spelling of its name —
    and that score carries 0.16 of the SWING rank weight. Neutral would be
    identical for everyone; this is noise with a rank ordering.
    """
    return os.getenv("PHASE0_NO_SYNTHETIC", "0").strip().lower() in ("1", "true", "yes", "on")


async def analyze_news_sentiment(symbols: list[str]) -> dict[str, SentimentSnapshot]:
    """
    Sentiment provider.

    PHASE 0: with `PHASE0_NO_SYNTHETIC=1` this returns an EMPTY map — sentiment is
    genuinely unavailable (no news API is wired), and callers renormalize it away
    rather than being handed fabricated numbers. Default OFF keeps the historical
    hash baseline so behaviour is byte-identical until the flag is flipped.

    IMPORTANT: Signal explainer must label these as "baseline estimate" not "analysis".
    """
    if synthetic_disabled():
        log.info(
            "Sentiment analysis: DISABLED for %d symbols (PHASE0_NO_SYNTHETIC=1) — "
            "no news provider is wired, so sentiment is reported as unavailable",
            len(symbols),
        )
        return {}

    log.info("Sentiment analysis: using synthetic baseline for %d symbols (no live news API)", len(symbols))
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
            data_source="synthetic",
        )
    return output
