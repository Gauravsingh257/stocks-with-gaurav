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
    entry_type: str = "MARKET"
    scan_cmp: float | None = None


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


# ── Liquidity filter on real OHLCV data ──────────────────────────
_MIN_AVG_VOLUME = int(os.getenv("RESEARCH_MIN_AVG_VOLUME", "50000"))  # 50k shares/day
_MIN_AVG_TURNOVER_CR = float(os.getenv("RESEARCH_MIN_AVG_TURNOVER_CR", "1.0"))  # ₹1 Cr daily turnover


def _passes_liquidity_filter(daily_df, symbol: str) -> bool:
    """Reject stocks with inadequate daily volume using real OHLCV data."""
    try:
        import pandas as pd
        if daily_df is None or not hasattr(daily_df, 'columns'):
            return True  # can't check, let it through
        vol_col = 'volume' if 'volume' in daily_df.columns else 'Volume' if 'Volume' in daily_df.columns else None
        close_col = 'close' if 'close' in daily_df.columns else 'Close' if 'Close' in daily_df.columns else None
        if vol_col is None or close_col is None:
            return True
        # Use last 20 trading days
        recent = daily_df.tail(20)
        if len(recent) < 5:
            return True
        avg_vol = float(recent[vol_col].mean())
        avg_close = float(recent[close_col].mean())
        avg_turnover_cr = (avg_vol * avg_close) / 1e7  # in crores
        if avg_vol < _MIN_AVG_VOLUME:
            log.debug("REJECT %s: avg_vol %.0f < %d", symbol, avg_vol, _MIN_AVG_VOLUME)
            return False
        if avg_turnover_cr < _MIN_AVG_TURNOVER_CR:
            log.debug("REJECT %s: avg_turnover %.2f Cr < %.1f Cr", symbol, avg_turnover_cr, _MIN_AVG_TURNOVER_CR)
            return False
        return True
    except Exception:
        return True  # fail open


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


def _real_swing_signals(smc: dict) -> tuple[dict[str, str], str]:
    """Build live technical signal strings from score_swing_candidate result dict."""
    reasons = smc.get("reasons", [])
    research = smc.get("research", [])
    rs = smc.get("rs", 0.0)
    vol_sig = smc.get("volume", "NEUTRAL")
    wt = smc.get("weekly_trend", "")
    ds = smc.get("daily_structure", "")
    direction = smc.get("direction", "LONG")
    score = smc.get("score", 0)
    breakdown = smc.get("breakdown", {})
    fund = smc.get("fundamentals", {})

    # Build technical signals from real SMC analysis
    tech = {
        "weekly_trend": f"Weekly trend: {wt} — {'Higher highs pattern across last 4 weeks' if 'BULL' in wt else 'Lower lows pattern, bearish pressure'}.",
        "daily_structure": f"Daily {ds} confirmed — price broke {'swing high (BOS)' if 'BOS' in ds else 'previous structure (CHoCH)'}, signalling {'bullish' if direction == 'LONG' else 'bearish'} intent.",
        "ob_fvg": "; ".join(r for r in reasons if "OB:" in r or "FVG:" in r) or "No OB/FVG zone identified in recent candles.",
        "relative_strength": f"RS vs NIFTY: {rs:+.1f}% over 10 days — {'outperforming index' if (direction=='LONG' and rs>2) or (direction=='SHORT' and rs<-2) else 'neutral vs index'}.",
        "volume": f"Volume profile: {vol_sig} — {('institutional buying visible' if vol_sig in ('ACCUMULATION','STRONG_ACCUMULATION') else 'distribution pressure' if vol_sig=='DISTRIBUTION' else 'normal volume')}.",
        "smc_score": f"SMC quality score: {score}/12 (OB/FVG: {breakdown.get('ob_fvg',0)}/2, RS: {breakdown.get('rs',0)}/2, Vol: {breakdown.get('vol',0)}/1).",
    }

    # Build reasoning from real research lines and reasons
    research_lines = [l for l in research if l] if research else []
    reason_lines = [r for r in reasons if r]
    full_reason = " | ".join(reason_lines[:4])
    if research_lines:
        full_reason += " | " + " | ".join(research_lines[:3])

    return tech, full_reason


def _real_longterm_signals(lt_meta: dict) -> tuple[dict[str, str], str]:
    """Build live technical signal strings from score_longterm_candidate result dict."""
    reasons = lt_meta.get("reasons", [])
    research = lt_meta.get("research", [])
    rs = lt_meta.get("rs", 0.0)
    wt = lt_meta.get("weekly_trend", "")
    ws = lt_meta.get("weekly_structure", "")
    w_vol = lt_meta.get("weekly_volume", "NEUTRAL")
    score = lt_meta.get("score", 0)
    breakdown = lt_meta.get("breakdown", {})
    w_ob = lt_meta.get("weekly_ob")
    w_fvg = lt_meta.get("weekly_fvg")
    hi52 = lt_meta.get("hi_52w", 0)
    lo52 = lt_meta.get("lo_52w", 0)
    pct_from_high = lt_meta.get("pct_from_high", 0)
    chg_1m = lt_meta.get("chg_1m", 0)
    chg_3m = lt_meta.get("chg_3m", 0)

    tech = {
        "weekly_trend": f"Weekly trend: {wt} — {'Strong institutional accumulation across multiple weeks' if 'STRONG' in wt else 'Consistent higher-highs formation' if 'BULL' in wt else 'Base formation in progress'}.",
        "weekly_structure": f"Weekly {ws} — {'structural breakout on higher timeframe confirms long-term direction' if 'BOS' in ws or 'CHOCH' in ws else 'consolidation phase, awaiting breakout'}.",
        "weekly_ob_fvg": (
            (f"Weekly OB: ₹{w_ob[0]:.0f}-{w_ob[1]:.0f} (institutional demand zone). " if w_ob else "")
            + (f"Weekly FVG: ₹{w_fvg[0]:.0f}-{w_fvg[1]:.0f} (unfilled gap, strong support)." if w_fvg else "")
        ) or "No weekly OB/FVG zones identified in recent structure.",
        "relative_strength": f"RS vs NIFTY (20D): {rs:+.1f}% — {'significantly outperforming the index' if rs > 8 else 'outperforming index' if rs > 3 else 'in line with index'}.",
        "weekly_volume": f"Weekly volume profile: {w_vol} — {('sustained institutional buying visible' if w_vol in ('ACCUMULATION', 'STRONG_ACCUMULATION') else 'distribution pressure' if w_vol == 'DISTRIBUTION' else 'normal volume pattern')}.",
        "52w_context": f"52W range: ₹{lo52:.0f}-₹{hi52:.0f} | {pct_from_high:.0f}% below 52W high.",
        "momentum": f"1M change: {chg_1m:+.1f}% | 3M change: {chg_3m:+.1f}%.",
        "smc_score": f"Weekly SMC quality score: {score}/11 (Trend: {breakdown.get('weekly_trend', 0)}/2, Structure: {breakdown.get('weekly_structure', 0)}/2, OB/FVG: {breakdown.get('weekly_ob_fvg', 0)}/2, RS: {breakdown.get('rs', 0)}/2).",
    }

    reason_lines = [r for r in reasons if r]
    research_lines = [l for l in research if l] if research else []
    full_reason = " | ".join(reason_lines[:5])
    if research_lines:
        full_reason += " | " + " | ".join(research_lines[:4])

    return tech, full_reason


async def _materialize_swing_idea(
    row: FactorRow,
    rank_score: float,
    evidence_map: dict[str, tuple[dict, dict, dict, str]],
    ingestion: DataIngestion,
    nifty_daily: list[dict],
) -> RankedIdea | None:
    symbol = row.symbol
    _hash_tech, fundamental_signals, sentiment_signals, _base_setup = evidence_map[symbol]
    daily_df = await _fetch_daily_df(ingestion, symbol)
    if not _passes_liquidity_filter(daily_df, symbol):
        return None
    levels = build_swing_trade_levels(symbol, daily_df, nifty_daily)
    if not levels:
        log.debug("No OHLC swing levels for %s", symbol)
        return None
    entry, stop, targets, setup, smc_meta = levels
    entry_price = float(entry)
    stop_loss = float(stop)
    if smc_meta:
        entry_type = smc_meta.get("entry_type", "MARKET")
        scan_cmp = float(smc_meta.get("cmp", 0)) if smc_meta.get("cmp") else None
    else:
        # ATR pullback: entry is below CMP → LIMIT order
        entry_type = "LIMIT" if "PULLBACK" in setup else "MARKET"
        scan_cmp = None

    # Use real signals if SMC scored; fall back to hash-based signals for ATR fallback
    if smc_meta:
        technical_signals, reasoning = _real_swing_signals(smc_meta)
    else:
        technical_signals = _hash_tech
        reasoning, _ = generate_evidence_reasoning(
            symbol=symbol,
            technical_signals=_hash_tech,
            fundamental_signals=fundamental_signals,
            sentiment_signals=sentiment_signals,
            min_factors=3,
            max_factors=6,
        )

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
        entry_type=entry_type,
        scan_cmp=scan_cmp,
    )


async def _materialize_longterm_idea(
    row: FactorRow,
    rank_score: float,
    evidence_map: dict[str, tuple[dict, dict, dict, str]],
    ingestion: DataIngestion,
    nifty_daily: list[dict],
) -> RankedIdea | None:
    symbol = row.symbol
    _hash_tech, fundamental_signals, sentiment_signals, _base_setup = evidence_map[symbol]
    daily_df = await _fetch_daily_df(ingestion, symbol)
    if not _passes_liquidity_filter(daily_df, symbol):
        return None
    lt = build_longterm_trade_levels(symbol, daily_df, nifty_daily)
    if not lt:
        log.debug("No OHLC long-term levels for %s", symbol)
        return None
    entry, stop, targets, long_target, entry_zone, setup, lt_meta = lt

    # Use real signals if SMC scored; fall back to hash-based signals for ATR fallback
    if lt_meta:
        technical_signals, reasoning = _real_longterm_signals(lt_meta)
        entry_type = lt_meta.get("entry_type", "MARKET")
        scan_cmp = lt_meta.get("cmp")
    else:
        technical_signals = _hash_tech
        reasoning, _ = generate_evidence_reasoning(
            symbol=symbol,
            technical_signals=_hash_tech,
            fundamental_signals=fundamental_signals,
            sentiment_signals=sentiment_signals,
            min_factors=3,
            max_factors=6,
        )
        entry_type = "MARKET"
        scan_cmp = None

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
        fair_value_estimate=round(float(entry) + (long_target - float(entry)) * 0.6, 2) if long_target and entry else None,
        entry_zone=entry_zone,
        long_term_target=long_target,
        risk_factors=None,
        entry_type=entry_type,
        scan_cmp=scan_cmp,
    )


async def _collect_ideas_from_pool(
    horizon: Horizon,
    top_k: int,
    scored: list[tuple[FactorRow, float]],
    evidence_map: dict[str, tuple[dict, dict, dict, str]],
    fund_map: dict | None = None,
) -> list[RankedIdea]:
    """Walk ranked pool (wider than top_k), fetch OHLC per symbol until top_k ideas or pool exhausted."""
    pool_n = min(len(scored), max(top_k * RESEARCH_POOL_MULT, top_k + 5))
    pool = scored[:pool_n]
    ingestion = DataIngestion(source=_research_data_source())
    sem = asyncio.Semaphore(int(os.getenv("RESEARCH_FETCH_CONCURRENCY", "6")))

    # Both swing and longterm need Nifty daily for relative strength
    nifty_df = await _fetch_daily_df(ingestion, NIFTY_DAILY_SYMBOL)
    nifty_daily: list[dict] = df_to_candles(nifty_df)

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
                idea = await _materialize_longterm_idea(
                    row, rank_score, evidence_map, ingestion, nifty_daily
                )
        if idea is None:
            continue
        # Skip penny stocks below ₹100
        if idea.entry_price < 100:
            continue
        # Inject raw fundamental values for frontend display
        if fund_map and row.symbol in fund_map:
            snap = fund_map[row.symbol]
            enriched_ff = dict(idea.fundamental_factors)
            for attr in ("raw_pe", "raw_roe_pct", "raw_roce_pct", "raw_revenue_growth_pct",
                         "raw_debt_equity", "raw_market_cap_cr", "raw_promoter_pct"):
                val = getattr(snap, attr, None)
                if val is not None:
                    enriched_ff[attr] = round(float(val), 2)
            idea = replace(idea, rank=rank_counter, fundamental_factors=enriched_ff)
        else:
            idea = replace(idea, rank=rank_counter)
        rank_counter += 1

    return ideas


async def generate_rankings(horizon: Horizon, top_k: int = 10, target_universe: int = 1800, exclude_symbols: list[str] | None = None) -> RankingResult:
    universe = load_nse_universe(target_universe)
    symbols = universe.symbols
    # Exclude symbols already in active slots
    if exclude_symbols:
        excluded_set = set(exclude_symbols)
        symbols = [s for s in symbols if s not in excluded_set]
    if not symbols:
        return RankingResult(horizon, universe, 0, 0, 0, [])

    tech = await scan_technical(symbols)
    fund = await analyze_fundamentals(symbols)
    sent = await analyze_news_sentiment(symbols)

    candidate_rows: list[FactorRow] = []
    evidence_map: dict[str, tuple[dict[str, str], dict[str, str], dict[str, str], str]] = {}
    quality_passed = 0
    authenticity_map: dict[str, str] = {}

    for symbol in symbols:
        q = evaluate_symbol_quality(symbol, tech[symbol], fund[symbol], sent[symbol])
        if not q.passed:
            continue
        quality_passed += 1

        row = build_factor_row(symbol, tech[symbol], fund[symbol], sent[symbol])
        candidate_rows.append(row)
        authenticity_map[symbol] = q.data_authenticity

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

    log.info(
        "[%s] Ranking pipeline: %d universe → %d quality pass → %d scored → materializing top %d",
        horizon, len(symbols), quality_passed, len(scored), top_k,
    )

    ideas = await _collect_ideas_from_pool(horizon, top_k, scored, evidence_map, fund_map=fund)

    if not ideas:
        log.warning(
            "[%s] No high-quality opportunities found. %d symbols scanned, %d passed quality, "
            "%d scored, but none materialized into valid trade levels.",
            horizon, len(symbols), quality_passed, len(scored),
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
