from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from services.data_quality import evaluate_symbol_quality
from services.factor_pipeline import FactorRow, build_factor_row
from services.fundamental_analysis import analyze_fundamentals
from services.news_analysis import analyze_news_sentiment
from services.reasoning_engine import generate_evidence_reasoning
from services.signal_explainer import extract_longterm_signals, extract_swing_signals
from services.technical_scanner import scan_technical
from services.universe_manager import UniverseSnapshot, load_nse_universe


Horizon = Literal["SWING", "LONGTERM"]


@dataclass(slots=True)
class RankedIdea:
    symbol: str
    rank: int
    rank_score: float
    confidence_score: float
    entry_price: float
    stop_loss: float
    targets: list[float]
    setup: str
    expected_holding_period: str
    technical_signals: dict[str, str]
    fundamental_signals: dict[str, str]
    sentiment_signals: dict[str, str]
    technical_factors: dict[str, float]
    fundamental_factors: dict[str, float]
    sentiment_factors: dict[str, float]
    reasoning: str
    fair_value_estimate: float | None = None
    entry_zone: list[float] | None = None
    long_term_target: float | None = None
    risk_factors: list[str] | None = None


@dataclass(slots=True)
class RankingResult:
    horizon: Horizon
    universe: UniverseSnapshot
    scanned: int
    quality_passed: int
    ranked_candidates: int
    ideas: list[RankedIdea]


def _stable_unit(symbol: str, salt: str) -> float:
    raw = sha256(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    return int(raw[:8], 16) / 0xFFFFFFFF


def _percentile(values: list[float]) -> list[float]:
    if not values:
        return []
    indexed = sorted((v, i) for i, v in enumerate(values))
    out = [0.0] * len(values)
    n = max(len(values) - 1, 1)
    for rank, (_, i) in enumerate(indexed):
        out[i] = rank / n
    return out


def _score_candidates(rows: list[FactorRow], horizon: Horizon) -> list[tuple[FactorRow, float]]:
    if not rows:
        return []

    tech = _percentile([r.technical_score for r in rows])
    fund = _percentile([r.fundamental_score for r in rows])
    sent = _percentile([r.sentiment_score for r in rows])
    liq = _percentile([r.liquidity_score for r in rows])
    trend = _percentile([r.factors["trend"] for r in rows])
    growth = _percentile([r.factors["growth"] for r in rows])
    quality = _percentile([r.factors["quality"] for r in rows])

    scored: list[tuple[FactorRow, float]] = []
    for idx, row in enumerate(rows):
        if horizon == "SWING":
            score = (
                (0.30 * tech[idx])
                + (0.16 * trend[idx])
                + (0.16 * sent[idx])
                + (0.14 * liq[idx])
                + (0.12 * fund[idx])
                + (0.12 * growth[idx])
            )
        else:
            score = (
                (0.30 * fund[idx])
                + (0.20 * growth[idx])
                + (0.18 * quality[idx])
                + (0.14 * tech[idx])
                + (0.10 * sent[idx])
                + (0.08 * liq[idx])
            )
        scored.append((row, score))
    return scored


def _build_trade_levels(symbol: str, horizon: Horizon) -> tuple[float, float, list[float], float, list[float] | None]:
    base = round(100 + (_stable_unit(symbol, "base") * 3000), 2)
    if horizon == "SWING":
        entry = round(base * (0.99 + (_stable_unit(symbol, "entry") * 0.02)), 2)
        stop = round(entry * (0.94 + (_stable_unit(symbol, "sl") * 0.02)), 2)
        t1 = round(entry * (1.05 + (_stable_unit(symbol, "t1") * 0.03)), 2)
        t2 = round(entry * (1.10 + (_stable_unit(symbol, "t2") * 0.06)), 2)
        return entry, stop, [t1, t2], t2, None

    entry_low = round(base * (0.92 + (_stable_unit(symbol, "lt_el") * 0.04)), 2)
    entry_high = round(base * (0.97 + (_stable_unit(symbol, "lt_eh") * 0.02)), 2)
    entry = entry_low
    stop = round(entry_low * 0.86, 2)
    target = round(base * (1.25 + (_stable_unit(symbol, "lt_tg") * 0.35)), 2)
    return entry, stop, [target], target, [entry_low, entry_high]


async def generate_rankings(horizon: Horizon, top_k: int = 10, target_universe: int = 1800) -> RankingResult:
    universe = load_nse_universe(target_universe)
    symbols = universe.symbols
    if not symbols:
        return RankingResult(horizon, universe, 0, 0, 0, [])

    tech = await scan_technical(symbols)
    fund = await analyze_fundamentals(symbols)
    sent = await analyze_news_sentiment(symbols)

    candidate_rows: list[FactorRow] = []
    evidence_map: dict[str, tuple[dict[str, str], dict[str, str], dict[str, str], str]] = {}
    quality_passed = 0

    for symbol in symbols:
        q = evaluate_symbol_quality(symbol, tech[symbol], fund[symbol], sent[symbol])
        if not q.passed:
            continue
        quality_passed += 1

        row = build_factor_row(symbol, tech[symbol], fund[symbol], sent[symbol])
        candidate_rows.append(row)

        if horizon == "SWING":
            evidence = extract_swing_signals(symbol, tech[symbol], fund[symbol], sent[symbol])
            setup = "WEEKLY_CROSS_SECTIONAL_SWING"
        else:
            evidence = extract_longterm_signals(symbol, tech[symbol], fund[symbol], sent[symbol])
            setup = "WEEKLY_CROSS_SECTIONAL_LONGTERM"

        reasoning, factors_used = generate_evidence_reasoning(
            symbol=symbol,
            technical_signals=evidence.technical_signals,
            fundamental_signals=evidence.fundamental_signals,
            sentiment_signals=evidence.sentiment_signals,
            min_factors=3,
            max_factors=6,
        )
        if len(factors_used) < 3:
            continue
        evidence_map[symbol] = (
            evidence.technical_signals,
            evidence.fundamental_signals,
            evidence.sentiment_signals,
            setup,
        )

    scored = _score_candidates([r for r in candidate_rows if r.symbol in evidence_map], horizon)
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = scored[:top_k]

    ideas: list[RankedIdea] = []
    for rank, (row, rank_score) in enumerate(selected, start=1):
        symbol = row.symbol
        technical_signals, fundamental_signals, sentiment_signals, setup = evidence_map[symbol]
        reasoning, _ = generate_evidence_reasoning(
            symbol=symbol,
            technical_signals=technical_signals,
            fundamental_signals=fundamental_signals,
            sentiment_signals=sentiment_signals,
            min_factors=3,
            max_factors=6,
        )
        entry, stop, targets, long_target, entry_zone = _build_trade_levels(symbol, horizon)
        confidence = round(rank_score * 100, 2)
        ideas.append(
            RankedIdea(
                symbol=symbol,
                rank=rank,
                rank_score=round(rank_score, 6),
                confidence_score=confidence,
                entry_price=entry,
                stop_loss=stop,
                targets=targets,
                setup=setup,
                expected_holding_period="1-8 weeks" if horizon == "SWING" else "6-24 months",
                technical_signals=technical_signals,
                fundamental_signals=fundamental_signals,
                sentiment_signals=sentiment_signals,
                technical_factors={k: round(v, 4) for k, v in row.factors.items() if k in ("trend", "momentum", "breakout", "mtf_alignment", "liquidity", "volume_expansion")},
                fundamental_factors={k: round(v, 4) for k, v in row.factors.items() if k in ("growth", "quality", "balance_sheet", "institutional_accumulation")},
                sentiment_factors={k: round(v, 4) for k, v in row.factors.items() if k in ("news_sentiment", "sector_rotation", "macro_sentiment")},
                reasoning=reasoning,
                fair_value_estimate=round(long_target * 0.9, 2) if horizon == "LONGTERM" else None,
                entry_zone=entry_zone,
                long_term_target=long_target if horizon == "LONGTERM" else None,
                risk_factors=["Earnings miss risk", "Macro sentiment reversal", "Liquidity contraction"] if horizon == "LONGTERM" else None,
            )
        )

    return RankingResult(
        horizon=horizon,
        universe=universe,
        scanned=len(symbols),
        quality_passed=quality_passed,
        ranked_candidates=len(scored),
        ideas=ideas,
    )


def run_weekly_rankings(top_k: int = 10, target_universe: int = 1800) -> tuple[RankingResult, RankingResult]:
    swing = asyncio.run(generate_rankings("SWING", top_k=top_k, target_universe=target_universe))
    longterm = asyncio.run(generate_rankings("LONGTERM", top_k=top_k, target_universe=target_universe))
    return swing, longterm
