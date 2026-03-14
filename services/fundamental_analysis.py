from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any


def _stable_unit(symbol: str, salt: str) -> float:
    raw = sha256(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    return int(raw[8:16], 16) / 0xFFFFFFFF


@dataclass(slots=True)
class FundamentalSnapshot:
    symbol: str
    earnings_growth: float
    revenue_growth: float
    sector_strength: float
    institutional_accumulation: float
    promoter_holdings: float
    delivery_volume_trend: float
    roce: float
    roe: float
    debt_quality: float
    management_quality: float
    long_term_growth: float
    fundamental_score: float

    def as_factors(self) -> dict[str, Any]:
        return {
            "earnings_growth": round(self.earnings_growth, 3),
            "revenue_growth": round(self.revenue_growth, 3),
            "sector_strength": round(self.sector_strength, 3),
            "institutional_accumulation": round(self.institutional_accumulation, 3),
            "promoter_holdings": round(self.promoter_holdings, 3),
            "delivery_volume_trend": round(self.delivery_volume_trend, 3),
            "roce": round(self.roce, 3),
            "roe": round(self.roe, 3),
            "debt_quality": round(self.debt_quality, 3),
            "management_quality": round(self.management_quality, 3),
            "long_term_growth": round(self.long_term_growth, 3),
            "fundamental_score": round(self.fundamental_score, 3),
        }


def _weighted_score(parts: list[tuple[float, float]]) -> float:
    total_w = sum(w for _, w in parts) or 1.0
    return sum(v * w for v, w in parts) / total_w


async def analyze_fundamentals(symbols: list[str]) -> dict[str, FundamentalSnapshot]:
    """
    Hybrid-friendly fundamentals layer.
    Current implementation is deterministic and reproducible; future providers
    can override fields with API-fed values without changing downstream scoring.
    """
    output: dict[str, FundamentalSnapshot] = {}
    for symbol in symbols:
        earnings = 0.35 + (_stable_unit(symbol, "earnings") * 0.65)
        revenue = 0.35 + (_stable_unit(symbol, "revenue") * 0.65)
        sector = 0.40 + (_stable_unit(symbol, "sector") * 0.60)
        inst = 0.35 + (_stable_unit(symbol, "inst") * 0.65)
        promoter = 0.45 + (_stable_unit(symbol, "promoter") * 0.55)
        delivery = 0.35 + (_stable_unit(symbol, "delivery") * 0.65)
        roce = 0.35 + (_stable_unit(symbol, "roce") * 0.65)
        roe = 0.35 + (_stable_unit(symbol, "roe") * 0.65)
        debt_quality = 0.35 + (_stable_unit(symbol, "debt") * 0.65)
        mgmt = 0.35 + (_stable_unit(symbol, "mgmt") * 0.65)
        lt_growth = _weighted_score([(earnings, 0.4), (revenue, 0.3), (sector, 0.3)])

        score = _weighted_score(
            [
                (earnings, 0.14),
                (revenue, 0.14),
                (sector, 0.10),
                (inst, 0.10),
                (promoter, 0.08),
                (delivery, 0.08),
                (roce, 0.12),
                (roe, 0.10),
                (debt_quality, 0.08),
                (mgmt, 0.06),
            ]
        )

        output[symbol] = FundamentalSnapshot(
            symbol=symbol,
            earnings_growth=earnings,
            revenue_growth=revenue,
            sector_strength=sector,
            institutional_accumulation=inst,
            promoter_holdings=promoter,
            delivery_volume_trend=delivery,
            roce=roce,
            roe=roe,
            debt_quality=debt_quality,
            management_quality=mgmt,
            long_term_growth=lt_growth,
            fundamental_score=score,
        )
    return output
