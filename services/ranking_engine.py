from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

from data.ingestion import DataIngestion
from engine.swing import detect_daily_fvg, detect_daily_ob, detect_daily_structure
from services.data_quality import evaluate_symbol_quality
from services.factor_pipeline import FactorRow, build_factor_row
from services.fundamental_analysis import analyze_fundamentals
from services.news_analysis import analyze_news_sentiment
from services.reasoning_engine import generate_evidence_reasoning
from services.research_levels import (
    NIFTY_DAILY_SYMBOL,
    RESEARCH_POOL_MULT,
    atr_fallback_levels,
    build_longterm_trade_levels,
    build_longterm_watchlist_fallback,
    build_swing_trade_levels,
    df_to_candles,
)
from services.signal_explainer import extract_longterm_signals, extract_swing_signals
from services.technical_scanner import scan_technical
from services.universe_manager import UniverseSnapshot, load_nse_universe
from services.validation_engine import _scored_smc_levels, _smc_confirmation

log = logging.getLogger("services.ranking_engine")

Horizon = Literal["SWING", "LONGTERM"]


def _shadow_log_regime_sector(horizon: str, ideas: list) -> None:
    """G2-2 read-only shadow: compute the regime + sector verdict each pick
    WOULD get under the canonical mandatory pre-gate, log it, and persist to
    Redis `shadow:regime_sector:{horizon}:{date}`. Never mutates `ideas`."""
    from datetime import date as _date

    from services.market_regime import detect_regime
    from services.sector_strength import classify_symbol, compute_sector_strength

    reg = detect_regime()
    # Canonical rule: longs OK in TRENDING_UP/SIDEWAYS; TRENDING_DOWN blocks.
    # UNKNOWN does not block (no data ≠ bad regime) — logged as unknown.
    regime_blocks = reg.regime == "TRENDING_DOWN"
    strength = compute_sector_strength()

    rows = []
    killed_regime = killed_sector = would_survive = 0
    for idea in ideas or []:
        sym = getattr(idea, "symbol", None)
        if not sym:
            continue
        sc = classify_symbol(sym, strength)
        sector_blocks = sc["band"] == "lagging"  # only hard-lagging would be cut
        survives = not regime_blocks and not sector_blocks
        killed_regime += 1 if regime_blocks else 0
        killed_sector += 1 if (not regime_blocks and sector_blocks) else 0
        would_survive += 1 if survives else 0
        rows.append({
            "symbol": sym, "sector": sc["sector"], "sector_band": sc["band"],
            "sector_leading": sc["is_leading"],
            "regime_verdict": "block" if regime_blocks else "pass",
            "sector_verdict": "block" if sector_blocks else ("unknown" if sc["band"] == "unknown" else "pass"),
            "would_survive_gate": survives,
        })

    payload = {
        "scan_ts": time.time(),
        "horizon": horizon,
        "regime": {"label": reg.regime, "confidence": round(reg.confidence, 3), "blocks_longs": regime_blocks},
        "leading_sectors": strength.get("leading", []),
        "n_ideas": len(rows),
        "would_survive": would_survive,
        "killed_by_regime": killed_regime,
        "killed_by_sector": killed_sector,
        "picks": rows,
    }
    log.info(
        "[%s][G2-2 SHADOW] regime=%s(blocks=%s) ideas=%d would_survive=%d killed_regime=%d killed_sector=%d leading=%s",
        horizon, reg.regime, regime_blocks, len(rows), would_survive,
        killed_regime, killed_sector, strength.get("leading", []),
    )
    try:
        from dashboard.backend.cache import set as cache_set

        cache_set(
            f"shadow:regime_sector:{horizon}:{_date.today().isoformat()}",
            payload, ttl_seconds=7 * 86400,
        )
    except Exception:
        pass


def _ungated_fallback_allowed() -> bool:
    """PHASE G2-1: by default the ungated _scored_smc_levels fallback is
    DISABLED in the production recommendation path. F-ENGINE-XRAY proved it
    produced ~92% of trades at -0.39% expectancy (junk) because it has no
    rr>=2.5 / score>=7 gate. With it off, generate_rankings emits ONLY
    strict gated build_*_trade_levels ideas — fewer (often far fewer)
    recommendations, but real ones. "Zero qualifying = show zero" is the
    canonical, intended behaviour.

    Fully reversible: set RANKING_ALLOW_UNGATED_FALLBACK=1 to restore the
    old behaviour (or revert this commit). Default = gated only.
    """
    return os.getenv("RANKING_ALLOW_UNGATED_FALLBACK", "0").strip().lower() in ("1", "true", "yes")


def _empty_watchlist_fallback_enabled() -> bool:
    return os.getenv("RESEARCH_EMPTY_FALLBACK", "1").strip().lower() in ("1", "true", "yes")


def _watchlist_fallback_cap() -> int:
    return max(1, min(30, int(os.getenv("RESEARCH_FALLBACK_WATCHLIST_COUNT", "15"))))


def _log_swing_materialize_miss(symbol: str, daily_df: object) -> None:
    """Emit structured SMC gap tags when strict swing levels cannot be built."""
    try:
        candles = df_to_candles(daily_df)
        if len(candles) < 10:
            log.info("[SWING] %s materialize miss: insufficient_candles", symbol)
            return
        tags: list[str] = []
        if not detect_daily_ob(candles, "LONG"):
            tags.append("no_order_block")
        if not detect_daily_fvg(candles, "LONG"):
            tags.append("no_liquidity_sweep")
        st, _ = detect_daily_structure(candles)
        if st not in ("BOS", "CHOCH"):
            tags.append("no_BOS")
        log.info(
            "[SWING] %s materialize miss: %s",
            symbol,
            ",".join(tags) if tags else "filters_or_geometry",
        )
    except Exception as exc:
        log.debug("swing miss diagnostics failed for %s: %s", symbol, exc)


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
    sector: str | None = None


@dataclass(slots=True)
class RejectionRecord:
    """Why a single symbol was dropped during ranking. Surfaced via /api/research/scan-debug."""

    symbol: str
    stage: str   # one of: "quality", "reasoning", "materialize", "liquidity", "geometry", "freshness"
    reason: str

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "stage": self.stage, "reason": self.reason}


@dataclass(slots=True)
class RankingResult:
    horizon: Horizon
    universe: UniverseSnapshot
    scanned: int
    quality_passed: int
    ranked_candidates: int
    ideas: list[RankedIdea]
    rejections: list[RejectionRecord] | None = None
    fallback_used: bool = False
    fallback_ideas: list[RankedIdea] | None = None
    # Regime-governor context (additive; populated only when the governor is
    # enabled, else None ⟹ back-compat / byte-identical behaviour).
    market_state: str | None = None
    governor: dict | None = None


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


def _percentile_opt(values: list[float | None]) -> list[float | None]:
    """Percentile-rank only the rows that actually have this factor.

    A None stays None so `_score_candidates` can drop that term and renormalize,
    instead of the alternative failure modes: treating a data gap as a zero
    (which silently ranks a stock last for something nobody measured) or
    back-filling it with a hash (which is what Phase 0 exists to remove).
    """
    present = [(v, i) for i, v in enumerate(values) if v is not None]
    out: list[float | None] = [None] * len(values)
    if not present:
        return out
    present.sort(key=lambda pair: pair[0])
    n = max(len(present) - 1, 1)
    for rank, (_, i) in enumerate(present):
        out[i] = rank / n
    return out


def _score_candidates(rows: list[FactorRow], horizon: Horizon) -> list[tuple[FactorRow, float]]:
    if not rows:
        return []

    # PHASE 0: a FactorRow may be partial (sentiment/fundamentals genuinely
    # unavailable rather than hash-filled). Missing inputs percentile to None and
    # their weight is renormalized away per row below, so a stock is never ranked
    # against a fabricated number and never penalised for a data gap either.
    tech = _percentile([r.technical_score for r in rows])
    fund = _percentile_opt([r.fundamental_score for r in rows])
    sent = _percentile_opt([r.sentiment_score for r in rows])
    liq = _percentile([r.liquidity_score for r in rows])
    trend = _percentile([r.factors.get("trend", 0.0) for r in rows])
    growth = _percentile_opt([r.factors.get("growth") for r in rows])
    quality = _percentile_opt([r.factors.get("quality") for r in rows])

    # PR2 — sector-leadership multiplicative scoring (flag-gated). When enabled,
    # final_score = base_score * sector_multiplier so leaders float up and
    # laggards are heavily penalised BEFORE the final ranking sort. Disabled ⟹
    # base score unchanged (byte-identical).
    _sector_scoring = False
    _strength = None
    try:
        from services.regime_governor import sector_multiplier, sector_scoring_enabled
        _sector_scoring = sector_scoring_enabled()
        if _sector_scoring:
            from services.sector_strength import compute_sector_strength
            _strength = compute_sector_strength()
    except Exception as exc:
        log.debug("sector scoring unavailable: %s", exc)
        _sector_scoring = False

    scored: list[tuple[FactorRow, float]] = []
    for idx, row in enumerate(rows):
        if horizon == "SWING":
            terms = (
                (0.30, tech[idx]), (0.16, trend[idx]), (0.16, sent[idx]),
                (0.14, liq[idx]), (0.12, fund[idx]), (0.12, growth[idx]),
            )
        else:
            terms = (
                (0.30, fund[idx]), (0.20, growth[idx]), (0.18, quality[idx]),
                (0.14, tech[idx]), (0.10, sent[idx]), (0.08, liq[idx]),
            )
        # Renormalize over the terms this row actually has. With every factor
        # present the weights already sum to 1.0, so this is arithmetically
        # identical to the previous fixed-weight expression.
        present = [(w, v) for w, v in terms if v is not None]
        total_weight = sum(w for w, _ in present)
        score = (sum(w * v for w, v in present) / total_weight) if total_weight > 0 else 0.0
        if _sector_scoring:
            score *= sector_multiplier(getattr(row, "symbol", ""), _strength)
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
    # PHASE 0: serve from the scanner worker's full-universe Kite snapshot when
    # it exists, so the finalist pool is materialised on the same bars the
    # cross-sectional ranking was computed from. Falls through to the per-symbol
    # provider for anything the snapshot missed.
    try:
        from services.universe_ohlc import kite_ohlc_enabled, load_universe_frames

        if kite_ohlc_enabled():
            frame = load_universe_frames([symbol]).get(symbol)
            if frame is not None:
                return frame
    except Exception as exc:
        log.debug("[Phase0] snapshot lookup failed for %s: %s", symbol, exc)

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


def _scored_smc_signals(meta: dict, horizon: Horizon) -> tuple[dict[str, str], str]:
    score = float(meta.get("confirmation_score", 0.0) or 0.0)
    tier = str(meta.get("tier", "CONFIRMED"))
    structure = str(meta.get("structure", "NEUTRAL"))
    ob = meta.get("order_block")
    liquidity = meta.get("liquidity_zone")
    missing = meta.get("missing") or []
    ob_text = f"OB {ob[0]}-{ob[1]}" if isinstance(ob, list) and len(ob) == 2 else "OB not present"
    liq_text = f"liquidity/FVG {liquidity[0]}-{liquidity[1]}" if isinstance(liquidity, list) and len(liquidity) == 2 else "liquidity/FVG not present"
    label = "Daily" if horizon == "SWING" else "Higher-timeframe"
    tech = {
        "smc_score": f"SMC confirmation score: {score:.0f}/100 — {tier.replace('_', ' ').title()}.",
        "structure": f"{label} structure: {structure}.",
        "ob_liquidity": f"{ob_text}; {liq_text}.",
        "decision_layer": "Promoted by final decision engine; not a generic fallback.",
    }
    reason = (
        f"Final decision engine passed scored SMC ({score:.0f}/100): {structure}, {ob_text}, {liq_text}."
        + (f" Missing evidence: {', '.join(map(str, missing))}." if missing else "")
    )
    return tech, reason


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
        if _ungated_fallback_allowed():
            confirmation = _smc_confirmation(daily_df)
            levels = _scored_smc_levels(symbol, daily_df, "SWING", confirmation)
        if not levels:
            _log_swing_materialize_miss(symbol, daily_df)
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
        if smc_meta.get("scored_smc"):
            scored_tech, reasoning = _scored_smc_signals(smc_meta, "SWING")
            technical_signals = {**_hash_tech, **scored_tech}
        else:
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
        if _ungated_fallback_allowed():
            confirmation = _smc_confirmation(daily_df)
            scored = _scored_smc_levels(symbol, daily_df, "LONGTERM", confirmation)
            if scored:
                entry, stop, targets, setup, lt_meta = scored
                long_target = targets[-1] if targets else entry
                entry_zone = [entry, entry]
                lt = (entry, stop, targets, long_target, entry_zone, setup, lt_meta)
        if not lt:
            log.debug("No OHLC long-term levels for %s", symbol)
            return None
    entry, stop, targets, long_target, entry_zone, setup, lt_meta = lt

    # Use real signals if SMC scored; fall back to hash-based signals for ATR fallback
    if lt_meta:
        if lt_meta.get("scored_smc"):
            scored_tech, reasoning = _scored_smc_signals(lt_meta, "LONGTERM")
            technical_signals = {**_hash_tech, **scored_tech}
        else:
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
        # Inject raw fundamental values + sector for frontend display
        if fund_map and row.symbol in fund_map:
            snap = fund_map[row.symbol]
            enriched_ff = dict(idea.fundamental_factors)
            for attr in ("raw_pe", "raw_roe_pct", "raw_roce_pct", "raw_revenue_growth_pct",
                         "raw_debt_equity", "raw_market_cap_cr", "raw_promoter_pct"):
                val = getattr(snap, attr, None)
                if val is not None:
                    enriched_ff[attr] = round(float(val), 2)
            sector = getattr(snap, "sector", None) or getattr(snap, "industry", None)
            idea = replace(idea, rank=rank_counter, fundamental_factors=enriched_ff,
                           sector=sector)
        else:
            idea = replace(idea, rank=rank_counter)
        rank_counter += 1
        ideas.append(idea)

    return ideas


async def _collect_watchlist_fallback(
    horizon: Horizon,
    limit: int,
    scored: list[tuple[FactorRow, float]],
    evidence_map: dict[str, tuple[dict, dict, dict, str]],
    fund_map: dict | None,
    exclude: set[str],
    start_rank: int,
) -> list[RankedIdea]:
    """
    When strict SMC materialization yields fewer ideas than requested, add a small set of
    ATR / demand-zone watchlist rows so scans are not empty and users see near-setups.
    """
    if limit <= 0:
        return []
    ingestion = DataIngestion(source=_research_data_source())
    nifty_df = await _fetch_daily_df(ingestion, NIFTY_DAILY_SYMBOL)
    nifty_daily = df_to_candles(nifty_df)
    sem = asyncio.Semaphore(int(os.getenv("RESEARCH_FETCH_CONCURRENCY", "6")))
    out: list[RankedIdea] = []
    rank_counter = start_rank

    for row, rank_score in scored[: min(len(scored), 80)]:
        if len(out) >= limit:
            break
        if row.symbol in exclude:
            continue
        if row.symbol not in evidence_map:
            continue
        async with sem:
            daily_df = await _fetch_daily_df(ingestion, row.symbol)
        if not _passes_liquidity_filter(daily_df, row.symbol):
            continue
        candles = df_to_candles(daily_df)
        if len(candles) < 30:
            continue

        if horizon == "SWING":
            fb = atr_fallback_levels(row.symbol, candles, force_long=True)
            if not fb:
                continue
            entry, sl, targets, setup = fb
            if entry < 100:
                continue
            if "SHORT" in setup:
                continue
            _hash_tech, fundamental_signals, sentiment_signals, _base_setup = evidence_map[row.symbol]
            reasoning, _ = generate_evidence_reasoning(
                symbol=row.symbol,
                technical_signals=_hash_tech,
                fundamental_signals=fundamental_signals,
                sentiment_signals=sentiment_signals,
                min_factors=2,
                max_factors=5,
            )
            reasoning = (
                "[Watchlist / near setup] Strict SMC gates were not satisfied for this name; "
                "showing ATR pullback-style levels for monitoring only. "
                + reasoning
            )
            confidence = min(round(float(rank_score) * 48.0, 2), 48.0)
            entry_type = "LIMIT" if "PULLBACK" in setup else "MARKET"
            scan_cmp = float(candles[-1]["close"])
            idea = RankedIdea(
                symbol=row.symbol,
                rank=rank_counter,
                rank_score=round(rank_score * 0.55, 6),
                confidence_score=confidence,
                entry_price=float(entry),
                stop_loss=float(sl),
                targets=list(targets),
                setup=f"WATCHLIST_NEAR_{setup}",
                expected_holding_period="1-8 weeks",
                technical_signals={
                    **_hash_tech,
                    "watchlist_tier": "ATR near-setup — confirm SMC on chart before acting.",
                },
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
        else:
            lt = build_longterm_watchlist_fallback(row.symbol, daily_df, nifty_daily)
            if not lt:
                continue
            entry, stop, targets, long_target, entry_zone, setup_note, _ltm = lt
            if entry < 100:
                continue
            _hash_tech, fundamental_signals, sentiment_signals, _base_setup = evidence_map[row.symbol]
            reasoning, _ = generate_evidence_reasoning(
                symbol=row.symbol,
                technical_signals=_hash_tech,
                fundamental_signals=fundamental_signals,
                sentiment_signals=sentiment_signals,
                min_factors=2,
                max_factors=5,
            )
            reasoning = (
                "[Watchlist / near setup] Weekly SMC did not yield a primary long-term slot; "
                "demand-zone / ATR framing for tracking. "
                + reasoning
            )
            confidence = min(round(float(rank_score) * 46.0, 2), 46.0)
            scan_cmp = float(candles[-1]["close"])
            idea = RankedIdea(
                symbol=row.symbol,
                rank=rank_counter,
                rank_score=round(rank_score * 0.52, 6),
                confidence_score=confidence,
                entry_price=float(entry),
                stop_loss=float(stop),
                targets=list(targets),
                setup=setup_note,
                expected_holding_period="6-24 months",
                technical_signals={
                    **_hash_tech,
                    "watchlist_tier": "Long-term near-setup — weekly confirmation still required.",
                },
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
                fair_value_estimate=round(float(entry) + (float(long_target) - float(entry)) * 0.55, 2)
                if long_target
                else None,
                entry_zone=entry_zone,
                long_term_target=float(long_target) if long_target else None,
                risk_factors=None,
                entry_type="LIMIT",
                scan_cmp=scan_cmp,
            )

        if fund_map and row.symbol in fund_map:
            snap = fund_map[row.symbol]
            enriched_ff = dict(idea.fundamental_factors)
            for attr in (
                "raw_pe",
                "raw_roe_pct",
                "raw_roce_pct",
                "raw_revenue_growth_pct",
                "raw_debt_equity",
                "raw_market_cap_cr",
                "raw_promoter_pct",
            ):
                val = getattr(snap, attr, None)
                if val is not None:
                    enriched_ff[attr] = round(float(val), 2)
            sector = getattr(snap, "sector", None) or getattr(snap, "industry", None)
            idea = replace(idea, fundamental_factors=enriched_ff, sector=sector)

        out.append(idea)
        rank_counter += 1

    log.info(
        "[%s] watchlist fallback added %d near-setup row(s) (cap=%d)",
        horizon,
        len(out),
        limit,
    )
    return out


def _maybe_apply_alpha_v2_universe(symbols: list[str]) -> list[str]:
    """PHASE F3 shadow hook. Default OFF (ALPHA_V2 unset) → returns `symbols`
    unchanged, so the live scan path is byte-identical to pre-F3.

    When ALPHA_V2=1: intersect with the F2-cached quality universe. If the
    cache is cold or empty we deliberately return `symbols` unchanged — a
    missing quality cache must never empty the live scan.
    """
    try:
        from services.universe_quality import (
            alpha_v2_enabled,
            get_quality_universe_symbols_cached,
        )

        if not alpha_v2_enabled():
            return symbols
        keep = set(get_quality_universe_symbols_cached())
        if not keep:
            log.warning("[ALPHA_V2] quality universe cache empty — falling back to unfiltered")
            return symbols
        filtered = [s for s in symbols if s in keep]
        log.info("[ALPHA_V2] quality filter: %d → %d symbols", len(symbols), len(filtered))
        return filtered or symbols
    except Exception as exc:
        log.warning("[ALPHA_V2] universe hook failed (%s) — unfiltered fallback", exc)
        return symbols


async def generate_rankings(horizon: Horizon, top_k: int = 25, target_universe: int = 2200, exclude_symbols: list[str] | None = None) -> RankingResult:
    universe = load_nse_universe(target_universe)
    symbols = _maybe_apply_alpha_v2_universe(universe.symbols)
    # Exclude symbols already in active slots
    if exclude_symbols:
        excluded_set = set(exclude_symbols)
        symbols = [s for s in symbols if s not in excluded_set]
    if not symbols:
        return RankingResult(horizon, universe, 0, 0, 0, [], rejections=[])

    tech = await scan_technical(symbols)
    fund = await analyze_fundamentals(symbols)
    sent = await analyze_news_sentiment(symbols)

    candidate_rows: list[FactorRow] = []
    evidence_map: dict[str, tuple[dict[str, str], dict[str, str], dict[str, str], str]] = {}
    quality_passed = 0
    authenticity_map: dict[str, str] = {}
    rejections: list[RejectionRecord] = []

    for symbol in symbols:
        # PHASE 0: these maps can be PARTIAL when synthetic providers are off —
        # a symbol with no real data is simply absent, not hash-filled.
        t_snap, f_snap, s_snap = tech.get(symbol), fund.get(symbol), sent.get(symbol)
        if t_snap is None:
            rejections.append(RejectionRecord(symbol, "quality", "technical_data_unavailable"))
            continue

        q = evaluate_symbol_quality(symbol, t_snap, f_snap, s_snap)
        if not q.passed:
            reason_str = q.reasons[0] if q.reasons else "quality_gate"
            rejections.append(RejectionRecord(symbol, "quality", reason_str))
            continue
        quality_passed += 1

        row = build_factor_row(symbol, t_snap, f_snap, s_snap)
        candidate_rows.append(row)
        authenticity_map[symbol] = q.data_authenticity

        if horizon == "SWING":
            evidence = extract_swing_signals(symbol, t_snap, f_snap, s_snap)
            setup = "WEEKLY_CROSS_SECTIONAL_SWING"
        else:
            evidence = extract_longterm_signals(symbol, t_snap, f_snap, s_snap)
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
            rejections.append(RejectionRecord(symbol, "reasoning", f"only_{len(factors_used)}_factors"))
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

    fallback_ideas: list[RankedIdea] = []
    if _empty_watchlist_fallback_enabled() and len(ideas) < top_k and scored:
        need = min(_watchlist_fallback_cap(), top_k - len(ideas))
        if need > 0:
            fallback_ideas = await _collect_watchlist_fallback(
                horizon,
                need,
                scored,
                evidence_map,
                fund,
                exclude={i.symbol for i in ideas},
                start_rank=len(ideas) + 1,
            )
            if fallback_ideas:
                log.info(
                    "[%s] %d fallback near-setup row(s) retained for diagnostics only; not saved as final ideas",
                    horizon,
                    len(fallback_ideas),
                )

    if not ideas:
        log.warning(
            "[%s] No high-quality opportunities found. %d symbols scanned, %d passed quality, "
            "%d scored, but none materialized into valid trade levels.",
            horizon, len(symbols), quality_passed, len(scored),
        )

    # PHASE G2-2 — read-only regime+sector SHADOW. Records what a future
    # mandatory pre-gate WOULD do to these picks. Does NOT filter `ideas`.
    # Best-effort: any failure here must never affect the scan.
    try:
        _shadow_log_regime_sector(horizon, ideas)
    except Exception as exc:
        log.debug("[%s] regime/sector shadow log skipped: %s", horizon, exc)

    # PR1 — Regime Governor ENFORCEMENT. Flag-gated: when disabled this block is
    # a no-op and `ideas` is returned exactly as before (byte-identical). When
    # enabled, apply the graduated exposure gate (confidence/RR/sector caps).
    # `ideas` is already sorted best-first by rank_score above.
    market_state = None
    governor_diag = None
    try:
        from services.regime_governor import (
            apply_to_ideas,
            classify_market_state,
            exposure_state,
            governor_enabled,
        )

        state_result = classify_market_state()
        market_state = state_result.state
        if governor_enabled():
            before = len(ideas)
            ideas, diag = apply_to_ideas(ideas, horizon, state_result)
            governor_diag = diag.to_dict()
            governor_diag["exposure"] = exposure_state(state_result)
            log.info(
                "[%s][GOVERNOR] state=%s enforced: %d → %d ideas (killed conf=%d rr=%d sector=%d capped=%d)",
                horizon, market_state, before, len(ideas),
                diag.killed_confidence, diag.killed_rr, diag.killed_sector, diag.capped,
            )
    except Exception as exc:
        log.debug("[%s] regime governor skipped: %s", horizon, exc)

    # PR2 — sector diversification cap (max N per sector). Flag-gated and
    # independent of the governor. Applied last so it caps the final ordered
    # set. Disabled ⟹ no-op (byte-identical).
    try:
        from services.regime_governor import (
            enforce_sector_diversification,
            sector_diversification_enabled,
        )

        if sector_diversification_enabled():
            before = len(ideas)
            ideas, div_diag = enforce_sector_diversification(
                ideas, symbol_of=lambda i: getattr(i, "symbol", "")
            )
            governor_diag = governor_diag or {}
            governor_diag["diversification"] = div_diag
            log.info(
                "[%s][DIVERSIFY] %d → %d ideas (max %s/sector, dropped=%d)",
                horizon, before, len(ideas), div_diag.get("cap"), div_diag.get("dropped"),
            )
    except Exception as exc:
        log.debug("[%s] sector diversification skipped: %s", horizon, exc)

    return RankingResult(
        horizon=horizon,
        universe=universe,
        scanned=len(symbols),
        quality_passed=quality_passed,
        ranked_candidates=len(scored),
        ideas=ideas,
        rejections=rejections,
        fallback_used=bool(fallback_ideas),
        fallback_ideas=fallback_ideas,
        market_state=market_state,
        governor=governor_diag,
    )


def run_weekly_rankings(top_k: int = 10, target_universe: int = 2200) -> tuple[RankingResult, RankingResult]:
    swing = asyncio.run(generate_rankings("SWING", top_k=top_k, target_universe=target_universe))
    longterm = asyncio.run(generate_rankings("LONGTERM", top_k=top_k, target_universe=target_universe))
    return swing, longterm
