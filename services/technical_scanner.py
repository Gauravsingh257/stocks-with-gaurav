from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any


def _stable_unit(symbol: str, salt: str) -> float:
    raw = sha256(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    return int(raw[:8], 16) / 0xFFFFFFFF


@dataclass(slots=True)
class TechnicalSnapshot:
    symbol: str
    trend_structure: float
    volume_expansion: float
    liquidity_sweep: float
    order_block_quality: float
    fvg_quality: float
    rsi_momentum: float
    macd_momentum: float
    mtf_alignment: float
    liquidity_score: float
    technical_score: float

    def as_factors(self) -> dict[str, Any]:
        return {
            "trend_structure": round(self.trend_structure, 3),
            "volume_expansion": round(self.volume_expansion, 3),
            "liquidity_sweep": round(self.liquidity_sweep, 3),
            "order_block_quality": round(self.order_block_quality, 3),
            "fvg_quality": round(self.fvg_quality, 3),
            "rsi_momentum": round(self.rsi_momentum, 3),
            "macd_momentum": round(self.macd_momentum, 3),
            "mtf_alignment_1d_4h_1h": round(self.mtf_alignment, 3),
            "liquidity_score": round(self.liquidity_score, 3),
            "technical_score": round(self.technical_score, 3),
        }


def _weighted_score(parts: list[tuple[float, float]]) -> float:
    total_w = sum(w for _, w in parts) or 1.0
    return sum(v * w for v, w in parts) / total_w


async def scan_technical(symbols: list[str]) -> dict[str, TechnicalSnapshot]:
    """
    Deterministic technical scanner.
    Uses stable hashing to produce reproducible scores while keeping the
    provider interface pluggable for live market data upgrades.
    """
    output: dict[str, TechnicalSnapshot] = {}
    for symbol in symbols:
        trend = 0.45 + (_stable_unit(symbol, "trend") * 0.55)
        volume = 0.40 + (_stable_unit(symbol, "volume") * 0.60)
        sweep = 0.35 + (_stable_unit(symbol, "sweep") * 0.65)
        ob = 0.35 + (_stable_unit(symbol, "ob") * 0.65)
        fvg = 0.35 + (_stable_unit(symbol, "fvg") * 0.65)
        rsi = 0.30 + (_stable_unit(symbol, "rsi") * 0.70)
        macd = 0.30 + (_stable_unit(symbol, "macd") * 0.70)
        mtf = 0.40 + (_stable_unit(symbol, "mtf") * 0.60)
        liquidity = 0.50 + (_stable_unit(symbol, "liq") * 0.50)

        score = _weighted_score(
            [
                (trend, 0.18),
                (volume, 0.14),
                (sweep, 0.10),
                (ob, 0.14),
                (fvg, 0.12),
                (rsi, 0.10),
                (macd, 0.10),
                (mtf, 0.12),
            ]
        )

        output[symbol] = TechnicalSnapshot(
            symbol=symbol,
            trend_structure=trend,
            volume_expansion=volume,
            liquidity_sweep=sweep,
            order_block_quality=ob,
            fvg_quality=fvg,
            rsi_momentum=rsi,
            macd_momentum=macd,
            mtf_alignment=mtf,
            liquidity_score=liquidity,
            technical_score=score,
        )
    return output
