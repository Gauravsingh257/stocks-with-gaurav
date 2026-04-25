from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

import pandas as pd

from data.ingestion import DataIngestion
from engine.indicators import calculate_atr
from engine.swing import detect_daily_fvg, detect_daily_ob, detect_daily_structure
from services.data_quality import evaluate_symbol_quality
from services.decision_engine import build_decision_output
from services.discovery_engine import DiscoveryCandidate, _compute_features, synthesize_swing_levels
from services.fundamental_analysis import analyze_fundamentals
from services.news_analysis import analyze_news_sentiment
from services.research_levels import (
    NIFTY_DAILY_SYMBOL,
    build_longterm_trade_levels,
    build_swing_trade_levels,
    df_to_candles,
)
from services.technical_scanner import scan_technical, snapshot_from_ohlc
from services.universe_manager import UniverseSnapshot, load_nse_universe
from utils.scoring import composite_score, score_from_discovery

log = logging.getLogger("services.validation_engine")

Horizon = Literal["SWING", "LONGTERM"]


@dataclass(slots=True)
class CoverageReport:
    total_universe: int
    available_universe: int
    scanned: int
    data_available: int
    missed: int
    coverage_percent: float
    missing_symbols: list[str] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class FunnelMetrics:
    total: int
    layer1_pass: int
    layer2_pass: int
    layer3_pass: int
    final_selected: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class LayerValidationRecord:
    scan_id: str
    horizon: Horizon
    symbol: str
    date: str
    cmp: float | None = None
    entry: float | None = None
    stop_loss: float | None = None
    targets: list[float] = field(default_factory=list)
    setup: str | None = None
    confidence_score: float = 0.0
    layer1_pass: bool = False
    layer2_pass: bool = False
    layer3_pass: bool = False
    final_selected: bool = False
    rejection_reason: list[str] = field(default_factory=list)
    discovery: dict | None = None
    quality: dict | None = None
    smc: dict | None = None
    score_breakdown: dict | None = None

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "horizon": self.horizon,
            "symbol": self.symbol,
            "date": self.date,
            "cmp": self.cmp,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "setup": self.setup,
            "confidence_score": self.confidence_score,
            "confidence": self.confidence_score,
            "layer1_pass": self.layer1_pass,
            "layer2_pass": self.layer2_pass,
            "layer3_pass": self.layer3_pass,
            "final_selected": self.final_selected,
            "rejection_reason": self.rejection_reason,
            "layer_details": {
                "discovery": self.discovery or {},
                "quality": self.quality or {},
                "smc": self.smc or {},
                "score_breakdown": self.score_breakdown or {},
            },
        }

    def to_trade_card(self) -> dict:
        target = self.targets[-1] if self.targets else None
        rr = 0.0
        if self.entry and self.stop_loss and target:
            risk = abs(self.entry - self.stop_loss)
            rr = round(abs(target - self.entry) / max(risk, 0.01), 2)
        return {
            "symbol": self.symbol,
            "setup": self.setup,
            "entry_price": self.entry,
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "risk_reward": rr,
            "confidence_score": self.confidence_score,
            "scan_cmp": self.cmp,
            "entry_type": (self.smc or {}).get("entry_type", "MARKET"),
            "expected_holding_period": "1-8 weeks" if self.horizon == "SWING" else "6-24 months",
            "layer1_pass": self.layer1_pass,
            "layer2_pass": self.layer2_pass,
            "layer3_pass": self.layer3_pass,
            "final_selected": self.final_selected,
            "rejection_reason": self.rejection_reason,
            "layer_details": self.to_dict()["layer_details"],
            "reasoning": _record_reasoning(self),
            "technical_signals": _record_technical_signals(self),
        }


@dataclass(slots=True)
class ValidationScanResult:
    scan_id: str
    horizon: Horizon
    universe: UniverseSnapshot
    records: list[LayerValidationRecord]
    selected: list[LayerValidationRecord]
    watchlist: list[LayerValidationRecord]
    fallback: list[LayerValidationRecord]
    coverage: CoverageReport
    funnel: FunnelMetrics
    logged_rows: int = 0

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "horizon": self.horizon,
            "coverage": self.coverage.to_dict(),
            "funnel": self.funnel.to_dict(),
            "final_trades": [r.to_trade_card() for r in self.selected],
            "watchlist": [r.to_trade_card() for r in self.watchlist],
            "selected": [r.to_trade_card() for r in self.selected],
            "fallback": [r.to_trade_card() for r in self.fallback],
            "records": [r.to_dict() for r in self.records],
            "logged_rows": self.logged_rows,
        }


def _today_label(as_of: str | date | datetime | None) -> str:
    if as_of is None:
        return date.today().isoformat()
    if isinstance(as_of, datetime):
        return as_of.date().isoformat()
    if isinstance(as_of, date):
        return as_of.isoformat()
    return str(as_of)[:10]


def _slice_to_date(df: pd.DataFrame | None, as_of: str | date | datetime | None) -> pd.DataFrame | None:
    if df is None or df.empty or as_of is None:
        return df
    cutoff = pd.Timestamp(_today_label(as_of))
    frame = df.copy()
    if "date" in frame.columns:
        dates = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_convert(None)
        return frame.loc[dates <= cutoff]
    if isinstance(frame.index, pd.DatetimeIndex):
        index = frame.index
        if index.tz is not None:
            index = index.tz_convert(None)
        return frame.loc[index <= cutoff]
    return frame


def _has_usable_ohlc(df: pd.DataFrame | None) -> bool:
    if df is None or df.empty or len(df) < 30:
        return False
    cols = {str(c).lower() for c in df.columns}
    return {"open", "high", "low", "close"}.issubset(cols) or "close" in cols


def _append_unique(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def _quality_reasons(raw_reasons: list[str]) -> list[str]:
    mapped: list[str] = []
    for reason in raw_reasons:
        lower = reason.lower()
        if "trend" in lower:
            _append_unique(mapped, "weak_trend")
        elif "volume" in lower or "liquid" in lower:
            _append_unique(mapped, "low_volume")
        elif "fundamental" in lower or "earnings" in lower or "pe" in lower or "market_cap" in lower:
            _append_unique(mapped, "weak_fundamentals")
        elif "sentiment" in lower:
            _append_unique(mapped, "weak_sentiment")
        elif "real" in lower or "synthetic" in lower:
            _append_unique(mapped, "missing_real_data")
        else:
            _append_unique(mapped, lower.replace(" ", "_"))
    return mapped or ["quality_gate"]


def _discovery_failure_reasons(candidate: DiscoveryCandidate | None, df: pd.DataFrame | None, min_turnover_cr: float) -> list[str]:
    if candidate is None:
        if not _has_usable_ohlc(df):
            return ["insufficient_history"]
        return ["no_momentum"]
    reasons: list[str] = []
    if candidate.avg_turnover_cr < min_turnover_cr:
        reasons.append("low_volume")
    if candidate.momentum_score < 35:
        reasons.append("no_momentum")
    if candidate.volume_score < 10:
        reasons.append("low_volume")
    if candidate.breakout_score < 25:
        reasons.append("weak_trend")
    return reasons


def _smc_failure_reasons(df: pd.DataFrame | None) -> list[str]:
    confirmation = _smc_confirmation(df)
    if confirmation.get("reason") == "insufficient_history":
        return ["insufficient_history"]
    missing = confirmation.get("missing") or []
    if missing:
        return list(missing)
    return ["smc_geometry_failed"]


def _smc_confirmation(df: pd.DataFrame | None) -> dict:
    candles = df_to_candles(df)
    if len(candles) < 30:
        return {
            "confirmation_score": 0.0,
            "tier": "REJECTED",
            "reason": "insufficient_history",
            "missing": ["insufficient_history"],
        }
    order_block = detect_daily_ob(candles, "LONG")
    liquidity = detect_daily_fvg(candles, "LONG")
    structure, structure_info = detect_daily_structure(candles)
    has_bos = structure in ("BULLISH_BOS", "BULLISH_CHOCH")
    score = 0.0
    missing: list[str] = []
    if order_block:
        score += 40.0
    else:
        missing.append("no_order_block")
    if has_bos:
        score += 30.0
    else:
        missing.append("no_BOS")
    if liquidity:
        score += 30.0
    else:
        missing.append("no_liquidity_sweep")
    if score > 70:
        tier = "HIGH_CONVICTION"
    elif score >= 60:
        tier = "CONFIRMED"
    elif score >= 40:
        tier = "NEAR_SETUP"
    else:
        tier = "REJECTED"
    return {
        "confirmation_score": score,
        "tier": tier,
        "order_block": [round(float(order_block[0]), 2), round(float(order_block[1]), 2)] if order_block else None,
        "liquidity_zone": [round(float(liquidity[0]), 2), round(float(liquidity[1]), 2)] if liquidity else None,
        "structure": structure,
        "structure_info": structure_info or {},
        "missing": missing,
    }


def _scored_smc_levels(symbol: str, df: pd.DataFrame | None, horizon: Horizon, confirmation: dict) -> tuple[float, float, list[float], str, dict] | None:
    candles = df_to_candles(df)
    if len(candles) < 30 or float(confirmation.get("confirmation_score", 0.0) or 0.0) < 40.0:
        return None
    close = float(candles[-1]["close"])
    atr = float(calculate_atr(candles, 14) or 0.0)
    if close <= 0 or atr <= 0:
        return None
    ob = confirmation.get("order_block") or None
    liquidity = confirmation.get("liquidity_zone") or None
    if ob:
        entry = round((float(ob[0]) + float(ob[1])) / 2.0, 2)
        entry_type = "LIMIT"
    elif liquidity:
        entry = round((float(liquidity[0]) + float(liquidity[1])) / 2.0, 2)
        entry_type = "LIMIT"
    else:
        entry = round(close, 2)
        entry_type = "MARKET"
    if abs(entry - close) / close > 0.30:
        entry = round(close - atr * 0.5, 2)
    recent_low = min(float(c["low"]) for c in candles[-20:])
    base_risk = max(atr * (2.2 if horizon == "LONGTERM" else 1.5), entry * 0.03)
    ob_floor = float(ob[0]) if ob else recent_low
    stop = min(entry - base_risk, ob_floor - atr * 0.25, recent_low - atr * 0.2)
    stop = round(stop, 2)
    if stop <= 0 or stop >= entry:
        stop = round(entry - base_risk, 2)
    if stop <= 0 or stop >= entry:
        return None
    risk = entry - stop
    target_mult = 3.5 if horizon == "LONGTERM" else 3.0
    targets = [round(entry + risk * 1.5, 2), round(entry + risk * target_mult, 2)]
    tier = str(confirmation.get("tier", "SCORED"))
    structure = str(confirmation.get("structure", "NEUTRAL"))
    setup = f"SMC_{horizon}_SCORE_{int(float(confirmation.get('confirmation_score', 0.0) or 0.0))}_{tier}_{structure}"
    meta = dict(confirmation)
    meta["score"] = round(float(confirmation.get("confirmation_score", 0.0) or 0.0) / 100.0 * 12.0, 2)
    meta["entry_type"] = entry_type
    meta["scored_smc"] = True
    meta["symbol"] = symbol
    return entry, stop, targets, setup, meta


def _smc_score(meta: dict | None, horizon: Horizon) -> float:
    if not meta:
        return 0.0
    max_score = 11.0 if horizon == "LONGTERM" else 12.0
    try:
        if "confirmation_score" in meta:
            return max(0.0, min(100.0, float(meta.get("confirmation_score", 0))))
        return max(0.0, min(100.0, float(meta.get("score", 0)) / max_score * 100.0))
    except Exception:
        return 0.0


def _record_reasoning(record: LayerValidationRecord) -> str:
    if record.final_selected:
        return (
            f"Selected after all 3 layers passed. Discovery score "
            f"{(record.discovery or {}).get('discovery_score', 0)}, quality score "
            f"{(record.quality or {}).get('score', 0)}, SMC score "
            f"{(record.smc or {}).get('score', 0)}."
        )
    return "Rejected: " + ", ".join(record.rejection_reason or ["no final setup"])


def _record_technical_signals(record: LayerValidationRecord) -> dict[str, str]:
    details = record.to_dict()["layer_details"]
    return {
        "layer_1_discovery": "pass" if record.layer1_pass else "fail",
        "layer_2_quality": "pass" if record.layer2_pass else "fail",
        "layer_3_smc": "pass" if record.layer3_pass else "fail",
        "rejection_reason": ", ".join(record.rejection_reason) or "none",
        "score_breakdown": str(details.get("score_breakdown", {})),
    }


async def _fetch_frames(symbols: list[str], source: str, days: int, as_of: str | date | datetime | None) -> dict[str, pd.DataFrame | None]:
    ingestion = DataIngestion(source=source)
    concurrency = max(1, int(os.getenv("VALIDATION_FETCH_CONCURRENCY", "8")))
    sem = asyncio.Semaphore(concurrency)
    loop = asyncio.get_running_loop()

    async def _one(symbol: str) -> tuple[str, pd.DataFrame | None]:
        async with sem:
            try:
                df = await loop.run_in_executor(
                    None,
                    lambda: ingestion.fetch_historical(symbol, interval="day", days=days),
                )
                return symbol, _slice_to_date(df, as_of)
            except Exception as exc:
                log.debug("validation fetch failed for %s: %s", symbol, exc)
                return symbol, None

    pairs = await asyncio.gather(*(_one(symbol) for symbol in symbols))
    return dict(pairs)


async def run_validation_scan(
    horizon: Horizon = "SWING",
    *,
    top_k: int = 10,
    target_universe: int = 2200,
    symbols: list[str] | None = None,
    source: str | None = None,
    as_of: str | date | datetime | None = None,
    min_turnover_cr: float = 1.0,
    log_scan: bool = True,
    historical_frames: dict[str, pd.DataFrame] | None = None,
) -> ValidationScanResult:
    """Run every symbol through Discovery, Quality, and SMC, then log each row.

    This is the auditable path for operator validation and historical backtests.
    A stock is `final_selected` only when all three layers pass. Fallback rows are
    returned separately for visibility and are never marked as final trades.
    """
    horizon = horizon.upper()  # type: ignore[assignment]
    if horizon not in ("SWING", "LONGTERM"):
        raise ValueError("horizon must be SWING or LONGTERM")

    universe = load_nse_universe(target_universe)
    scan_symbols = list(symbols or universe.symbols)
    scan_id = f"VAL-{horizon}-{_today_label(as_of)}-{uuid4().hex[:8]}"
    src = source or os.getenv("RESEARCH_DATA_SOURCE", "yfinance")
    days = int(os.getenv("VALIDATION_FETCH_DAYS", os.getenv("RESEARCH_FETCH_DAYS", "420")))
    as_of_label = _today_label(as_of)

    if historical_frames is None:
        frames = await _fetch_frames(scan_symbols, src, days, as_of)
    else:
        frames = {symbol: _slice_to_date(historical_frames.get(symbol), as_of) for symbol in scan_symbols}

    nifty_frames = await _fetch_frames([NIFTY_DAILY_SYMBOL], src, days, as_of)
    nifty_daily = df_to_candles(nifty_frames.get(NIFTY_DAILY_SYMBOL))

    technical_map = await scan_technical(scan_symbols)
    for symbol, df in frames.items():
        snap = snapshot_from_ohlc(symbol, df) if _has_usable_ohlc(df) else None
        if snap is not None:
            technical_map[symbol] = snap
    fundamental_map = await analyze_fundamentals(scan_symbols)
    sentiment_map = await analyze_news_sentiment(scan_symbols)

    layer1_min_score = float(os.getenv("VALIDATION_LAYER1_MIN_SCORE", "35"))
    records: list[LayerValidationRecord] = []
    no_data_symbols: list[str] = []

    for symbol in scan_symbols:
        df = frames.get(symbol)
        if not _has_usable_ohlc(df):
            no_data_symbols.append(symbol)
        record = LayerValidationRecord(scan_id=scan_id, horizon=horizon, symbol=symbol, date=as_of_label)

        candidate = _compute_features(symbol, df) if _has_usable_ohlc(df) else None
        if candidate is not None:
            record.cmp = candidate.cmp
            record.discovery = candidate.to_dict()
            record.layer1_pass = candidate.avg_turnover_cr >= min_turnover_cr and candidate.discovery_score >= layer1_min_score
        for reason in _discovery_failure_reasons(candidate, df, min_turnover_cr):
            if not record.layer1_pass:
                _append_unique(record.rejection_reason, reason)

        tech = technical_map.get(symbol)
        fund = fundamental_map.get(symbol)
        sent = sentiment_map.get(symbol)
        if tech is not None and fund is not None and sent is not None:
            quality = evaluate_symbol_quality(symbol, tech, fund, sent)
            record.layer2_pass = quality.passed
            record.quality = {
                "score": quality.score,
                "reasons": quality.reasons,
                "data_authenticity": quality.data_authenticity,
                "technical_score": round(float(getattr(tech, "technical_score", 0)) * 100, 2),
                "fundamental_score": round(float(getattr(fund, "fundamental_score", 0)) * 100, 2),
                "sentiment_score": round(float(getattr(sent, "sentiment_score", 0)) * 100, 2),
            }
            if not quality.passed:
                for reason in _quality_reasons(quality.reasons):
                    _append_unique(record.rejection_reason, reason)
        else:
            _append_unique(record.rejection_reason, "quality_data_unavailable")

        smc_confirmation = _smc_confirmation(df)
        record.smc = dict(smc_confirmation)
        levels = None
        if _has_usable_ohlc(df) and nifty_daily:
            if horizon == "SWING":
                levels = build_swing_trade_levels(symbol, df, nifty_daily)
                if levels:
                    entry, stop, targets, setup, meta = levels
                    strict_smc = bool(meta) and str(setup).startswith("SMC_SWING")
                    if strict_smc:
                        record.entry = float(entry)
                        record.stop_loss = float(stop)
                        record.targets = [float(t) for t in targets]
                        record.setup = str(setup)
                        merged_meta = dict(smc_confirmation)
                        merged_meta.update(dict(meta or {}))
                        merged_meta["confirmation_score"] = max(float(smc_confirmation.get("confirmation_score", 0.0) or 0.0), 70.0)
                        merged_meta["tier"] = "HIGH_CONVICTION" if merged_meta["confirmation_score"] > 70 else "CONFIRMED"
                        record.smc = merged_meta
            else:
                levels = build_longterm_trade_levels(symbol, df, nifty_daily)
                if levels:
                    entry, stop, targets, _long_target, _entry_zone, setup, meta = levels
                    strict_smc = bool(meta) and str(setup).startswith("SMC_LONGTERM")
                    if strict_smc:
                        record.entry = float(entry)
                        record.stop_loss = float(stop)
                        record.targets = [float(t) for t in targets]
                        record.setup = str(setup)
                        merged_meta = dict(smc_confirmation)
                        merged_meta.update(dict(meta or {}))
                        merged_meta["confirmation_score"] = max(float(smc_confirmation.get("confirmation_score", 0.0) or 0.0), 70.0)
                        merged_meta["tier"] = "HIGH_CONVICTION" if merged_meta["confirmation_score"] > 70 else "CONFIRMED"
                        record.smc = merged_meta
            if record.entry is None:
                scored_levels = _scored_smc_levels(symbol, df, horizon, smc_confirmation)
                if scored_levels:
                    entry, stop, targets, setup, meta = scored_levels
                    record.entry = float(entry)
                    record.stop_loss = float(stop)
                    record.targets = [float(t) for t in targets]
                    record.setup = str(setup)
                    record.smc = dict(meta)

        record.layer3_pass = float((record.smc or {}).get("confirmation_score", 0.0) or 0.0) >= 60.0
        if not record.layer3_pass:
            for reason in (record.smc or {}).get("missing", []) or _smc_failure_reasons(df):
                _append_unique(record.rejection_reason, reason)

        smc_score = _smc_score(record.smc, horizon)
        if record.discovery:
            score = score_from_discovery(record.discovery, smc=smc_score)
        else:
            trend = float(getattr(tech, "trend_structure", 0) or 0) * 100 if tech else 0.0
            volume = float(getattr(tech, "volume_expansion", 0) or 0) * 100 if tech else 0.0
            momentum = float(getattr(tech, "technical_score", 0) or 0) * 100 if tech else 0.0
            score = composite_score(trend=trend, volume=volume, momentum=momentum, smc=smc_score)
        record.score_breakdown = score.to_dict()
        record.confidence_score = score.composite
        if record.cmp is None and _has_usable_ohlc(df):
            candles = df_to_candles(df)
            if candles:
                record.cmp = float(candles[-1]["close"])
        record.final_selected = record.layer1_pass and record.layer2_pass and record.layer3_pass
        if record.final_selected:
            record.rejection_reason = []
        records.append(record)

    decisions = build_decision_output(records, limit=top_k)
    selected = list(decisions.final_trades)
    watchlist = list(decisions.watchlist)
    fallback = list(decisions.fallback)

    shortfall = max(0, int(target_universe) - len(scan_symbols))
    missed = shortfall + len(no_data_symbols)
    total_universe = universe.total_size if symbols is None and universe.total_size else int(target_universe)
    coverage = CoverageReport(
        total_universe=total_universe,
        available_universe=universe.total_size or universe.actual_size,
        scanned=len(scan_symbols),
        data_available=len(scan_symbols) - len(no_data_symbols),
        missed=missed,
        coverage_percent=round((len(scan_symbols) / max(total_universe, 1)) * 100, 2),
        missing_symbols=no_data_symbols[:100],
        sources=universe.sources,
    )
    funnel = FunnelMetrics(
        total=len(records),
        layer1_pass=sum(1 for r in records if r.layer1_pass),
        layer2_pass=sum(1 for r in records if r.layer2_pass),
        layer3_pass=sum(1 for r in records if r.layer3_pass),
        final_selected=sum(1 for r in records if r.final_selected),
    )

    logged_rows = 0
    if log_scan:
        try:
            from dashboard.backend.db import log_signals_scan
            log_rows = []
            for record in records:
                row = record.to_dict()
                row["coverage_report"] = coverage.to_dict()
                log_rows.append(row)
            logged_rows = log_signals_scan(log_rows)
        except Exception as exc:
            log.warning("signals_log write failed for %s: %s", scan_id, exc)

    log.info(
        "[%s] validation scan %s: total=%d l1=%d l2=%d l3=%d selected=%d logged=%d",
        horizon,
        scan_id,
        funnel.total,
        funnel.layer1_pass,
        funnel.layer2_pass,
        funnel.layer3_pass,
        funnel.final_selected,
        logged_rows,
    )
    return ValidationScanResult(
        scan_id=scan_id,
        horizon=horizon,
        universe=universe,
        records=records,
        selected=selected,
        watchlist=watchlist,
        fallback=fallback,
        coverage=coverage,
        funnel=funnel,
        logged_rows=logged_rows,
    )


def fallback_cards(records: list[LayerValidationRecord], limit: int = 10) -> list[dict]:
    """Return discovery-only fallback cards for display, never final selection."""
    cards: list[dict] = []
    for record in records:
        if not record.discovery:
            continue
        cand = DiscoveryCandidate(**record.discovery)
        card = synthesize_swing_levels(cand)
        card.update(
            {
                "final_selected": False,
                "fallback_only": True,
                "layer1_pass": record.layer1_pass,
                "layer2_pass": record.layer2_pass,
                "layer3_pass": record.layer3_pass,
                "rejection_reason": record.rejection_reason,
                "layer_details": record.to_dict()["layer_details"],
            }
        )
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards
