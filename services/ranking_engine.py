from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

from data.ingestion import DataIngestion
from services.data_quality import evaluate_symbol_quality
from services.factor_pipeline import FactorRow, build_factor_row
from services.fundamental_analysis import analyze_fundamentals
from services.news_analysis import analyze_news_sentiment
from services.reasoning_engine import generate_evidence_reasoning
from services.research_levels import (
    NIFTY_DAILY_SYMBOL,
    RESEARCH_POOL_MULT,
    build_longterm_trade_levels,
    build_swing_trade_levels,
    df_to_candles,
)
from services.signal_explainer import extract_longterm_signals, extract_swing_signals
from services.technical_scanner import scan_technical
from services.universe_manager import UniverseSnapshot, load_nse_universe

log = logging.getLogger("services.ranking_engine")

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


def _research_data_source() -> str:
    return os.getenv("RESEARCH_DATA_SOURCE", "yfinance")


def _research_fetch_days() -> int:
    return int(os.getenv("RESEARCH_FETCH_DAYS", "420"))


async def _fetch_daily_df(ingestion: DataIngestion, symbol: str) -> object:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: ingestion.fetch_historical(
            symbol,
            interval="day",
            days=_research_fetch_days(),
        ),
    )


async def _materialize_swing_idea(
    row: FactorRow,
    rank_score: float,
    evidence_map: dict[str, tuple[dict, dict, dict, str]],
    ingestion: DataIngestion,
    nifty_daily: list[dict],
) -> RankedIdea | None:
    symbol = row.symbol
    technical_signals, fundamental_signals, sentiment_signals, _base_setup = evidence_map[symbol]
    reasoning, _ = generate_evidence_reasoning(
        symbol=symbol,
        technical_signals=technical_signals,
        fundamental_signals=fundamental_signals,
        sentiment_signals=sentiment_signals,
        min_factors=3,
        max_factors=6,
    )
    daily_df = await _fetch_daily_df(ingestion, symbol)
    levels = build_swing_trade_levels(symbol, daily_df, nifty_daily)
    if not levels:
        log.debug("No OHLC swing levels for %s", symbol)
        return None
    entry, stop, targets, setup = levels
    entry_price = float(entry)
    stop_loss = float(stop)
    confidence = round(rank_score * 100, 2)
    return RankedIdea(
        symbol=symbol,
        rank=0,
        rank_score=round(rank_score, 6),
        confidence_score=confidence,
        entry_price=entry_price,
        stop_loss=stop_loss,
        targets=targets,
        setup=setup,
        expected_holding_period="1-8 weeks",
        technical_signals=technical_signals,
        fundamental_signals=fundamental_signals,
        sentiment_signals=sentiment_signals,
        technical_factors={
            k: round(v, 4)
            for k, v in row.factors.items()
            if k in ("trend", "momentum", "breakout", "mtf_alignment", "liquidity", "volume_expansion")
        },
        fundamental_factors={
            k: round(v, 4)
            for k, v in row.factors.items()
            if k in ("growth", "quality", "balance_sheet", "institutional_accumulation")
        },
        sentiment_factors={
            k: round(v, 4)
            for k, v in row.factors.items()
            if k in ("news_sentiment", "sector_rotation", "macro_sentiment")
        },
        reasoning=reasoning,
        fair_value_estimate=None,
        entry_zone=None,
        long_term_target=None,
        risk_factors=None,
    )


async def _materialize_longterm_idea(
    row: FactorRow,
    rank_score: float,
    evidence_map: dict[str, tuple[dict, dict, dict, str]],
    ingestion: DataIngestion,
) -> RankedIdea | None:
    symbol = row.symbol
    technical_signals, fundamental_signals, sentiment_signals, _base_setup = evidence_map[symbol]
    reasoning, _ = generate_evidence_reasoning(
        symbol=symbol,
        technical_signals=technical_signals,
        fundamental_signals=fundamental_signals,
        sentiment_signals=sentiment_signals,
        min_factors=3,
        max_factors=6,
    )
    daily_df = await _fetch_daily_df(ingestion, symbol)
    lt = build_longterm_trade_levels(daily_df)
    if not lt:
        log.debug("No OHLC long-term levels for %s", symbol)
        return None
    entry, stop, targets, long_target, entry_zone, setup = lt
    confidence = round(rank_score * 100, 2)
    return RankedIdea(
        symbol=symbol,
        rank=0,
        rank_score=round(rank_score, 6),
        confidence_score=confidence,
        entry_price=float(entry),
        stop_loss=float(stop),
        targets=targets,
        setup=setup,
        expected_holding_period="6-24 months",
        technical_signals=technical_signals,
        fundamental_signals=fundamental_signals,
        sentiment_signals=sentiment_signals,
        technical_factors={
            k: round(v, 4)
            for k, v in row.factors.items()
            if k in ("trend", "momentum", "breakout", "mtf_alignment", "liquidity", "volume_expansion")
        },
        fundamental_factors={
            k: round(v, 4)
            for k, v in row.factors.items()
            if k in ("growth", "quality", "balance_sheet", "institutional_accumulation")
        },
        sentiment_factors={
            k: round(v, 4)
            for k, v in row.factors.items()
            if k in ("news_sentiment", "sector_rotation", "macro_sentiment")
        },
        reasoning=reasoning,
        fair_value_estimate=round(long_target * 0.9, 2),
        entry_zone=entry_zone,
        long_term_target=long_target,
        risk_factors=["Earnings miss risk", "Macro sentiment reversal", "Liquidity contraction"],
    )


async def _collect_ideas_from_pool(
    horizon: Horizon,
    top_k: int,
    scored: list[tuple[FactorRow, float]],
    evidence_map: dict[str, tuple[dict, dict, dict, str]],
) -> list[RankedIdea]:
    """Walk ranked pool (wider than top_k), fetch OHLC per symbol until top_k ideas or pool exhausted."""
    pool_n = min(len(scored), max(top_k * RESEARCH_POOL_MULT, top_k + 5))
    pool = scored[:pool_n]
    ingestion = DataIngestion(source=_research_data_source())
    sem = asyncio.Semaphore(int(os.getenv("RESEARCH_FETCH_CONCURRENCY", "6")))

    nifty_daily: list[dict] = []
    if horizon == "SWING":
        nifty_df = await _fetch_daily_df(ingestion, NIFTY_DAILY_SYMBOL)
        nifty_daily = df_to_candles(nifty_df)

    ideas: list[RankedIdea] = []
    rank_counter = 1

    for row, rank_score in pool:
        if len(ideas) >= top_k:
            break
        async with sem:
            if horizon == "SWING":
                idea = await _materialize_swing_idea(
                    row, rank_score, evidence_map, ingestion, nifty_daily
                )
            else:
                idea = await _materialize_longterm_idea(row, rank_score, evidence_map, ingestion)
        if idea is None:
            continue
        ideas.append(replace(idea, rank=rank_counter))
        rank_counter += 1

    return ideas


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

    ideas = await _collect_ideas_from_pool(horizon, top_k, scored, evidence_map)

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
