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
from services.phase2_ranking import smc_as_score_enabled
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


# ── Signal-quality flags (Phase 2 — default OFF: behaviour unchanged) ─────────
# Max gap between the planned (OB/liquidity) entry and current price before the
# entry is pulled toward CMP. Historically hard-coded to 0.30 (30%), which let
# limit entries sit up to ~30% below price — orders that never fill on momentum
# names already at their highs (the live audit found 16/30 final ideas >15%
# past entry). Lower this (e.g. ENTRY_ANCHOR_MAX_GAP_PCT=10) to anchor entries
# to a fillable level. Default 30 reproduces the prior behaviour exactly.
ENTRY_ANCHOR_MAX_GAP_PCT = float(os.getenv("ENTRY_ANCHOR_MAX_GAP_PCT", "30")) / 100.0

# ── Phase 1 flags (all default OFF ⟹ behaviour identical to Phase 0) ─────────

def strict_funnel_enabled() -> bool:
    """`final_selected = L1 AND L2 AND L3`, and Layer 2 can no longer be forced
    to pass by a downstream SMC score. Default OFF."""
    return os.getenv("PHASE1_STRICT_FUNNEL", "0").strip().lower() in ("1", "true", "yes", "on")


def tight_entry_gap_enabled() -> bool:
    """Anchor planned entries within a fillable distance of price. Default OFF."""
    return os.getenv("PHASE1_TIGHT_ENTRY_GAP", "0").strip().lower() in ("1", "true", "yes", "on")


def entry_anchor_max_gap() -> float:
    """Max |entry − CMP| / CMP before the entry is pulled toward price.

    The historical 30% let a LIMIT entry sit a third of the way below a stock
    already running — an order that cannot fill. Measured on 1,016 selected
    symbols from one scan: only 51.6% were within ±5% of the plan and 10.1% were
    more than 15% past it. `PHASE1_ENTRY_GAP_PCT` (default 8) is the fillability
    target; it applies only when PHASE1_TIGHT_ENTRY_GAP is on, so the effect can
    be measured against the unchanged baseline.
    """
    if tight_entry_gap_enabled():
        return float(os.getenv("PHASE1_ENTRY_GAP_PCT", "8")) / 100.0
    return ENTRY_ANCHOR_MAX_GAP_PCT

# When enabled, cap the far target at the nearest prior swing-high resistance
# above entry (floored at 1.5R, so a clean breakout with no overhead supply
# keeps the full measured-move target). Default off → fixed 3.0R (LT 3.5R), so
# nothing changes until STRUCTURAL_TARGET_CAP=1 is set and reviewed.
STRUCTURAL_TARGET_CAP = os.getenv("STRUCTURAL_TARGET_CAP", "0").strip().lower() in {"1", "true", "yes"}


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
    near_setup: bool = False
    rejection_reason: list[str] = field(default_factory=list)
    discovery: dict | None = None
    quality: dict | None = None
    smc: dict | None = None
    score_breakdown: dict | None = None
    exceptionalism: dict | None = None   # EP2 verdict (score/threshold/qualifies/reason/breakdown)

    @property
    def section(self) -> str:
        smc_score = _smc_score(self.smc, self.horizon) / 10.0
        if smc_score >= 5.5:
            return "final"
        if smc_score >= 3.5:
            return "watchlist"
        return "discovery"

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
            "near_setup": self.near_setup,
            "section": self.section,
            "rejection_reason": self.rejection_reason,
            "exceptionalism": self.exceptionalism or {},
            "layer_details": {
                "discovery": self.discovery or {},
                "quality": self.quality or {},
                "smc": self.smc or {},
                "score_breakdown": self.score_breakdown or {},
                "exceptionalism": self.exceptionalism or {},
            },
        }

    def to_trade_card(self) -> dict:
        target = self.targets[-1] if self.targets else None
        rr = 0.0
        if self.entry and self.stop_loss and target:
            risk = abs(self.entry - self.stop_loss)
            rr = round(abs(target - self.entry) / max(risk, 0.01), 2)
        tier = _signal_tier_label(self)
        entry_distance_pct, reachability = _entry_reachability(self.cmp, self.entry)
        return {
            "symbol": self.symbol,
            "setup": self.setup,
            "entry_price": self.entry,
            "stop_loss": self.stop_loss,
            "targets": self.targets,
            "risk_reward": rr,
            "confidence_score": self.confidence_score,
            "scan_cmp": self.cmp,
            "entry_distance_pct": entry_distance_pct,
            "reachability": reachability,
            "entry_type": (self.smc or {}).get("entry_type", "MARKET"),
            "expected_holding_period": "1-8 weeks" if self.horizon == "SWING" else "6-24 months",
            "layer1_pass": self.layer1_pass,
            "layer2_pass": self.layer2_pass,
            "layer3_pass": self.layer3_pass,
            "final_selected": self.final_selected,
            "near_setup": self.near_setup,
            "signal_tier": tier,
            "potential_setup": tier == "WATCHLIST",
            "section": self.section,
            "rejection_reason": self.rejection_reason,
            "exceptionalism": (self.exceptionalism or {}).get("exceptionalism"),
            "exceptionalism_threshold": (self.exceptionalism or {}).get("threshold"),
            "exceptionalism_qualifies": (self.exceptionalism or {}).get("qualifies"),
            "exceptionalism_reason": (self.exceptionalism or {}).get("reason"),
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
    discovery: list[LayerValidationRecord]
    coverage: CoverageReport
    funnel: FunnelMetrics
    logged_rows: int = 0
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "horizon": self.horizon,
            "coverage": self.coverage.to_dict(),
            "funnel": self.funnel.to_dict(),
            "final_trades": [r.to_trade_card() for r in self.selected],
            "watchlist": [r.to_trade_card() for r in self.watchlist],
            "discovery": [r.to_trade_card() for r in self.discovery],
            "selected": [r.to_trade_card() for r in self.selected],
            "fallback": [r.to_trade_card() for r in self.discovery],
            "records": [r.to_dict() for r in self.records],
            "logged_rows": self.logged_rows,
            "diagnostics": dict(self.diagnostics),
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
            "partial_hits": 0,
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

    partial_hits = sum([bool(order_block), bool(liquidity), has_bos])
    # Allow 2-of-3 SMC legs to qualify as meaningful partial confluence (not all-or-nothing).
    if partial_hits >= 2 and score < 55.0:
        score = max(score, 48.0)
    elif partial_hits == 1 and score < 35.0:
        score = max(score, 30.0)

    if score >= 70:
        tier = "HIGH_CONVICTION"
    elif score >= 55:
        tier = "CONFIRMED"
    elif score >= 40 or partial_hits >= 2:
        tier = "NEAR_SETUP"
    elif score >= 28:
        tier = "PARTIAL"
    else:
        tier = "REJECTED"
    return {
        "confirmation_score": score,
        "tier": tier,
        "partial_hits": partial_hits,
        "order_block": [round(float(order_block[0]), 2), round(float(order_block[1]), 2)] if order_block else None,
        "liquidity_zone": [round(float(liquidity[0]), 2), round(float(liquidity[1]), 2)] if liquidity else None,
        "structure": structure,
        "structure_info": structure_info or {},
        "missing": missing,
    }


def _nearest_resistance_above(
    candles: list[dict], entry: float, left: int = 3, right: int = 3, lookback: int = 180
) -> float | None:
    """Nearest prior swing-high pivot strictly above `entry`.

    A pivot high is a bar whose high is the max of a +/- window around it
    (standard fractal pivot — fixed window, not tuned per symbol, so it does
    not curve-fit). Used to cap the far target at real overhead supply.
    Returns None when no pivot sits above entry (e.g. a clean breakout at
    all-time highs), so those setups keep the full measured-move target."""
    highs = [float(c["high"]) for c in candles[-lookback:]]
    n = len(highs)
    pivots: list[float] = []
    for i in range(left, n - right):
        h = highs[i]
        if h > entry and h == max(highs[i - left : i + right + 1]):
            pivots.append(h)
    return min(pivots) if pivots else None


def _scored_smc_levels(symbol: str, df: pd.DataFrame | None, horizon: Horizon, confirmation: dict) -> tuple[float, float, list[float], str, dict] | None:
    candles = df_to_candles(df)
    confirmation_score = float(confirmation.get("confirmation_score", 0.0) or 0.0)
    has_partial_zone = bool(confirmation.get("order_block") or confirmation.get("liquidity_zone"))
    partial_hits = int(confirmation.get("partial_hits") or 0)
    relaxed_entry = confirmation_score >= 15.0 or partial_hits >= 2 or (partial_hits >= 1 and has_partial_zone)
    if len(candles) < 30 or not relaxed_entry or not has_partial_zone:
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
    if abs(entry - close) / close > entry_anchor_max_gap():
        entry = round(close - atr * 0.5, 2)
    recent_low = min(float(c["low"]) for c in candles[-20:])
    base_risk = max(atr * (2.0 if horizon == "LONGTERM" else 1.3), entry * 0.03)
    ob_floor = float(ob[0]) if ob else recent_low
    stop = min(entry - base_risk, ob_floor - atr * 0.2, recent_low - atr * 0.15)
    stop = round(stop, 2)
    if stop <= 0 or stop >= entry:
        stop = round(entry - base_risk, 2)
    if stop <= 0 or stop >= entry:
        return None
    risk = entry - stop
    target_mult = 3.5 if horizon == "LONGTERM" else 3.0
    far = entry + risk * target_mult
    if STRUCTURAL_TARGET_CAP:
        res = _nearest_resistance_above(candles, entry)
        if res is not None:
            # Cap at the nearest overhead resistance, but never below a 1.5R
            # floor (don't neuter the trade); breakouts with no pivot above
            # entry keep the full measured-move target.
            far = max(entry + risk * 1.5, min(far, res))
    targets = [round(entry + risk * 1.5, 2), round(far, 2)]
    tier = str(confirmation.get("tier", "SCORED"))
    structure = str(confirmation.get("structure", "NEUTRAL"))
    setup = f"SMC_{horizon}_SCORE_{int(float(confirmation.get('confirmation_score', 0.0) or 0.0))}_{tier}_{structure}"
    meta = dict(confirmation)
    meta["score"] = round(confirmation_score / 10.0, 2)
    meta["near_setup"] = 5.0 <= meta["score"] < 6.0
    meta["entry_type"] = entry_type
    meta["scored_smc"] = True
    meta["symbol"] = symbol
    return entry, stop, targets, setup, meta


def _entry_reachability(cmp: float | None, entry: float | None) -> tuple[float | None, str]:
    """Classify how reachable the planned entry is from the current price.

    A recommendation whose limit entry sits far BELOW the current price is
    effectively dead — the order will not fill unless the stock reverses hard.
    This surfaces that state instead of showing an entry that "will never
    trigger" (the 2026-05 product complaint: 35% of the final book had CMP
    >15% above the planned entry).

    gap = (cmp - entry) / entry   (+ve = price already above the buy zone)
    Pre-committed bands (frozen — not swept):
      |gap| <= 5%      -> "actionable"   (trading at/near the entry)
      5% < gap <= 15%  -> "waiting"      (above entry; may pull back)
      gap > 15%        -> "unreachable"  (price ran away; hidden by default)
      gap < -5%        -> "pre_breakout" (entry above CMP; awaiting trigger)
    Returns (entry_distance_pct, reachability). Distance is None when inputs
    are missing/zero, with reachability "unknown" (UI shows it, no badge)."""
    if not cmp or not entry or entry <= 0:
        return None, "unknown"
    gap = (cmp - entry) / entry * 100.0
    if gap < -5.0:
        band = "pre_breakout"
    elif gap <= 5.0:
        band = "actionable"
    elif gap <= 15.0:
        band = "waiting"
    else:
        band = "unreachable"
    return round(gap, 2), band


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
    pending = ", ".join(record.rejection_reason or ["no final setup"])
    return "Pending confirmation: " + pending


def _record_technical_signals(record: LayerValidationRecord) -> dict[str, str]:
    details = record.to_dict()["layer_details"]
    return {
        "layer_1_discovery": "pass" if record.layer1_pass else "fail",
        "layer_2_quality": "pass" if record.layer2_pass else "fail",
        "layer_3_smc": "pass" if record.layer3_pass else "fail",
        "rejection_reason": ", ".join(record.rejection_reason) or "none",
        "score_breakdown": str(details.get("score_breakdown", {})),
    }


def _confirmation_smc_band(record: LayerValidationRecord) -> float:
    return _smc_score(record.smc, record.horizon) / 10.0


def _signal_tier_label(record: LayerValidationRecord) -> str:
    band = _confirmation_smc_band(record)
    if record.final_selected or band >= 5.5:
        return "STRONG_SETUP"
    if band >= 3.5 or getattr(record, "near_setup", False):
        return "MEDIUM_SETUP"
    return "WATCHLIST"


def _movement_fallback_score(record: LayerValidationRecord) -> float:
    """Rank stocks by liquidity + volatility/momentum when SMC is thin."""
    d = record.discovery or {}
    if not d:
        return float(record.confidence_score or 0)
    turn = float(d.get("avg_turnover_cr", 0) or 0)
    vol = float(d.get("volume_score", 0) or 0)
    mom = float(d.get("momentum_score", 0) or 0)
    br = float(d.get("breakout_score", 0) or 0)
    return min(turn, 40.0) * 1.25 + vol * 0.35 + mom * 0.35 + br * 0.25


def _unique_symbols_from_lists(*lists: list) -> set[str]:
    seen: set[str] = set()
    for lst in lists:
        for r in lst:
            sym = str(getattr(r, "symbol", "")).replace("NSE:", "").replace(".NS", "").upper()
            if sym:
                seen.add(sym)
    return seen


def ensure_minimum_discovery_outputs(
    records: list[LayerValidationRecord],
    selected: list[LayerValidationRecord],
    watchlist: list[LayerValidationRecord],
    discovery: list[LayerValidationRecord],
    *,
    min_unique: int = 5,
) -> tuple[list[LayerValidationRecord], list[LayerValidationRecord], list[LayerValidationRecord]]:
    """Guarantee at least ``min_unique`` symbols across final + watchlist + discovery (momentum fallback)."""
    sel = list(selected)
    wl = list(watchlist)
    disc = list(discovery)

    def count_unique() -> int:
        return len(_unique_symbols_from_lists(sel, wl, disc))

    if count_unique() >= min_unique:
        return sel, wl, disc

    used = _unique_symbols_from_lists(sel, wl, disc)
    ranked = sorted(
        [r for r in records if r.discovery],
        key=_movement_fallback_score,
        reverse=True,
    )
    for r in ranked:
        if count_unique() >= min_unique:
            break
        sym = str(r.symbol).replace("NSE:", "").replace(".NS", "").upper()
        if not sym or sym in used:
            continue
        disc.append(r)
        used.add(sym)
    return sel, wl, disc


async def _fetch_frames(symbols: list[str], source: str, days: int, as_of: str | date | datetime | None) -> dict[str, pd.DataFrame | None]:
    # PHASE 0: prefer the full-universe Kite snapshot published by the scanner
    # worker. It is the whole point of the flag — every symbol in one scan is
    # then compared on bars from the SAME provider fetched at the SAME moment,
    # instead of whichever random 52-90% of the universe yfinance answered for
    # today. Symbols missing from the snapshot still fall through to the
    # per-symbol path below, so coverage can only improve, never regress.
    prefetched: dict[str, pd.DataFrame | None] = {}
    try:
        from services.universe_ohlc import kite_ohlc_enabled, load_universe_frames

        if kite_ohlc_enabled():
            prefetched = {
                symbol: _slice_to_date(frame, as_of)  # type: ignore[arg-type]
                for symbol, frame in load_universe_frames(symbols).items()
            }
            if prefetched:
                log.info("[Phase0] universe snapshot supplied %d/%d symbols",
                         len(prefetched), len(symbols))
    except Exception as exc:
        log.warning("[Phase0] universe snapshot unavailable (%s) — per-symbol fetch", exc)
        prefetched = {}

    remaining = [s for s in symbols if s not in prefetched]
    if not remaining:
        return prefetched

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

    pairs = await asyncio.gather(*(_one(symbol) for symbol in remaining))
    return {**prefetched, **dict(pairs)}


def apply_exceptionalism_final_gate(records, soft_ceiling: int = 20):
    """Make `final_selected` authoritative to the exceptionalism-qualified set.

    The public decision feed (`_signals_log_row_to_decision_card` sections by
    `final_selected`) and portfolio promotion (`select_from_final_ideas` reads
    `final_selected`) both then honor the exceptionalism gate. Mutates each
    record in place; returns (selected, n_qualified). Only called when
    EXCEPTIONALISM_ENABLED — when off, `final_selected` stays SMC-based.
    """
    # PHASE 1: this gate runs LAST and rewrites `final_selected` on every record,
    # so without this intersection it silently discards the funnel decision made
    # earlier in the scan — PHASE1_STRICT_FUNNEL would be a no-op wherever
    # EXCEPTIONALISM_ENABLED is set (which is the case in production). Measured
    # on the logged corpus: 17.6% of SWING and 22.2% of LONGTERM final_selected
    # rows had FAILED Layer 1 and survived exactly this way.
    #
    # Exceptionalism still decides ranking and the ceiling; it just no longer
    # readmits a stock the funnel rejected. With the strict funnel off this is a
    # no-op and the gate behaves exactly as before.
    _strict = strict_funnel_enabled()
    # PHASE 2: when SMC is a score, the ranked+budgeted set has ALREADY been
    # written to final_selected by the caller. Intersecting with it here keeps
    # this gate from re-admitting a stock the ranking left out — the same way it
    # would otherwise silently void PHASE1_STRICT_FUNNEL.
    _phase2 = smc_as_score_enabled()
    qualified = [
        r for r in records
        if getattr(r, "entry", None) is not None
        and (getattr(r, "exceptionalism", None) or {}).get("qualifies")
        and (not _phase2 or r.final_selected)
        and (
            not _strict
            or _phase2
            or (r.layer1_pass and r.layer2_pass and r.layer3_pass)
        )
    ]
    qualified.sort(
        key=lambda r: float((getattr(r, "exceptionalism", None) or {}).get("exceptionalism") or 0.0),
        reverse=True,
    )
    selected = qualified[: max(0, int(soft_ceiling))]
    sel_ids = {id(r) for r in selected}
    for r in records:
        r.final_selected = id(r) in sel_ids
        if r.final_selected:
            r.rejection_reason = []
    return selected, len(qualified)


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
    disable_fallback_levels: bool = False,
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

    # Railway-safe sector strength: derive bands from the CONSTITUENT candles we
    # just fetched (yfinance NSE sector-index tickers are blocked on Railway, so
    # the index-based path returns all-"unknown" there). Populates the shared
    # Redis cache read by the governor / sector scoring / /api/market/state.
    # Gated on the sector features so nothing runs when they're all off
    # (byte-identical). Best-effort — never breaks the scan.
    try:
        from services.regime_governor import (
            governor_enabled,
            sector_diversification_enabled,
            sector_scoring_enabled,
        )
        from services.market_health import market_health_enabled
        from services.exceptionalism import exceptionalism_enabled, exceptionalism_shadow_enabled

        # yfinance NSE sector-index tickers are blocked on Railway, so the
        # CONSTITUENT-based sector strength (+ breadth) is the only reliable
        # source. It must refresh whenever ANY consumer needs sector/breadth —
        # the selection flags, the market-health rotation sub-score, OR the
        # exceptionalism SHADOW (so the calibration dataset carries clean sector
        # signal even before selection flags are enabled). Cheap: pure arithmetic
        # over candles we already fetched (no network). Never changes what the
        # feed serves — enforcement stays flag-gated below.
        _need_candles = (
            governor_enabled() or sector_scoring_enabled() or sector_diversification_enabled()
            or market_health_enabled() or exceptionalism_shadow_enabled() or exceptionalism_enabled()
        )
        if _need_candles:
            _cand_map = {s: df_to_candles(f) for s, f in frames.items() if _has_usable_ohlc(f)}
            if _cand_map:
                from services.sector_strength import compute_sector_strength_from_candles
                compute_sector_strength_from_candles(_cand_map)
                # Layer-1 breadth from the same candles (% above 50/200-DMA, adv/decline).
                from services.market_health import breadth_from_candles
                breadth_from_candles(_cand_map)
    except Exception as exc:
        log.debug("in-scan sector/breadth refresh skipped: %s", exc)

    # ── EP2 — Stock Exceptionalism context (computed ONCE per scan) ──
    # Shadow by default: every scanned stock gets an exceptionalism verdict logged
    # (signals_log) for later threshold calibration. Enforcement (filtering the
    # served feed) is separate and flag-gated; when EXCEPTIONALISM_ENABLED=0 the
    # served recommendations are byte-identical.
    _exc_active = False
    _exc_health = None
    _exc_nifty_ret20 = None
    _exc_strength = None
    try:
        from services.exceptionalism import exceptionalism_enabled, exceptionalism_shadow_enabled
        _exc_active = exceptionalism_enabled() or exceptionalism_shadow_enabled()
        if _exc_active:
            try:
                from services.market_health import compute_market_health
                _h = compute_market_health()
                _exc_health = _h.get("score") if _h.get("available") else None
            except Exception:
                _exc_health = None
            if nifty_daily and len(nifty_daily) >= 21:
                try:
                    _n0 = float(nifty_daily[-21]["close"]); _n1 = float(nifty_daily[-1]["close"])
                    _exc_nifty_ret20 = (_n1 - _n0) / _n0 * 100.0 if _n0 > 0 else None
                except Exception:
                    _exc_nifty_ret20 = None
            try:
                from services.sector_strength import compute_sector_strength
                _exc_strength = compute_sector_strength()
            except Exception:
                _exc_strength = None
    except Exception as exc:
        log.debug("exceptionalism context skipped: %s", exc)

    technical_map = await scan_technical(scan_symbols)
    for symbol, df in frames.items():
        snap = snapshot_from_ohlc(symbol, df) if _has_usable_ohlc(df) else None
        if snap is not None:
            technical_map[symbol] = snap
    fundamental_map = await analyze_fundamentals(scan_symbols)
    sentiment_map = await analyze_news_sentiment(scan_symbols)

    layer1_min_score = float(os.getenv("VALIDATION_LAYER1_MIN_SCORE", "30"))
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
        # PHASE 0 FIX: only TECHNICALS are mandatory here.
        #
        # This guard used to require all three snapshots. `evaluate_symbol_quality`
        # was made None-tolerant in Phase 0 (missing components redistribute their
        # weight) but this caller was not updated — so with PHASE0_NO_SYNTHETIC=1,
        # where `analyze_news_sentiment` correctly returns {} because no news API
        # is wired, `sent` was None for EVERY symbol, the quality gate was never
        # evaluated at all, and layer2_pass stayed False universally.
        #
        # Measured on a 400-symbol production sample: L2 collapsed 395 → 0 and,
        # with the strict funnel on, final_selected went 6 → 0 on both books.
        # A genuinely absent provider must degrade the score, not silently void
        # the entire layer.
        if tech is not None:
            quality = evaluate_symbol_quality(symbol, tech, fund, sent)
            record.layer2_pass = quality.passed
            record.quality = {
                "score": quality.score,
                "reasons": quality.reasons,
                "data_authenticity": quality.data_authenticity,
                "technical_score": round(float(getattr(tech, "technical_score", 0)) * 100, 2),
                "fundamental_score": (
                    round(float(getattr(fund, "fundamental_score", 0)) * 100, 2)
                    if fund is not None else None
                ),
                "sentiment_score": (
                    round(float(getattr(sent, "sentiment_score", 0)) * 100, 2)
                    if sent is not None else None
                ),
            }
            if not quality.passed:
                for reason in _quality_reasons(quality.reasons):
                    _append_unique(record.rejection_reason, reason)
        else:
            _append_unique(record.rejection_reason, "quality_data_unavailable")

        smc_confirmation = _smc_confirmation(df)
        record.smc = dict(smc_confirmation)
        levels = None
        has_symbol_ohlc = _has_usable_ohlc(df)
        if has_symbol_ohlc:
            if horizon == "SWING" and nifty_daily:
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
            elif horizon != "SWING" and nifty_daily:
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
            # F-ENGINE-XRAY isolation: when disable_fallback_levels is set
            # (backtest research only), the ungated _scored_smc_levels path
            # is skipped entirely so ONLY strict gated build_*_trade_levels
            # trades survive. Default False → production path unchanged.
            if record.entry is None and not disable_fallback_levels:
                scored_levels = _scored_smc_levels(symbol, df, horizon, smc_confirmation)
                if scored_levels:
                    entry, stop, targets, setup, meta = scored_levels
                    record.entry = float(entry)
                    record.stop_loss = float(stop)
                    record.targets = [float(t) for t in targets]
                    record.setup = str(setup)
                    record.smc = dict(meta)

        smc_band_score = _smc_score(record.smc, horizon) / 10.0
        record.layer3_pass = smc_band_score >= 5.0
        record.near_setup = 4.0 <= smc_band_score < 5.0
        if record.smc is not None:
            record.smc["near_setup"] = record.near_setup
        # PHASE 1: the SMC score used to retroactively force Layer 2 to "pass",
        # which is why 2,192 of 2,200 symbols showed as clearing the quality gate.
        # A downstream layer cannot vouch for an upstream one — that is what made
        # the funnel report impossible numbers (L3 passing 336 while L1 passed 137).
        if not strict_funnel_enabled() and smc_band_score >= 3.5:
            record.layer2_pass = True
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
        # PHASE 1: the funnel is now actually a funnel.
        #
        # Before: `final_selected = layer3_pass` — Layer 1 (discovery: momentum /
        # volume / liquidity) and Layer 2 (quality: market cap / PE / trend) were
        # computed, displayed and logged, but never consulted. Across the 84-day
        # corpus that let 72.2% of "final trade ideas" through having FAILED the
        # discovery layer — the one layer measured to carry predictive value
        # (+1.9pp median forward return).
        #
        # After: a stock must clear all three. A tradable plan is still required,
        # because a pick with no entry is not actionable.
        if smc_as_score_enabled():
            # PHASE 2: Layer 3 stops rejecting and starts ordering. Eligibility is
            # L1 AND L2 plus a tradable plan; the SMC band is folded into the
            # ranking score below and no longer decides admission on its own.
            # The final set is trimmed to the SAME count the L3 gate would have
            # produced (see the budget trim after this loop), so this changes
            # WHICH stocks are chosen and never HOW MANY.
            record.final_selected = bool(
                record.layer1_pass and record.layer2_pass and record.entry is not None
            )
            if not record.final_selected:
                if not record.layer1_pass:
                    _append_unique(record.rejection_reason, "failed_layer1_discovery")
                if not record.layer2_pass:
                    _append_unique(record.rejection_reason, "failed_layer2_quality")
                if record.entry is None:
                    _append_unique(record.rejection_reason, "no_tradable_entry")
        elif strict_funnel_enabled():
            record.final_selected = bool(
                record.layer1_pass and record.layer2_pass and record.layer3_pass
            )
            if not record.final_selected and record.layer3_pass:
                # Structure was fine; say which upstream layer actually rejected it
                # so the funnel report attributes the loss instead of hiding it.
                if not record.layer1_pass:
                    _append_unique(record.rejection_reason, "failed_layer1_discovery")
                if not record.layer2_pass:
                    _append_unique(record.rejection_reason, "failed_layer2_quality")
        else:
            record.final_selected = record.layer3_pass
        if record.final_selected:
            record.rejection_reason = []

        # EP2 — compute the exceptionalism verdict for EVERY scanned stock (shadow
        # dataset for later calibration). Cheap: get_sector is a static dict lookup.
        if _exc_active:
            try:
                from services.entry_state import classify_entry_state
                from services.exceptionalism import score_and_qualify
                from services.sector_strength import classify_symbol
                _sc = classify_symbol(symbol, _exc_strength) if _exc_strength else {"band": "unknown", "rel_20d_pct": None}
                _rr = None
                if record.entry and record.stop_loss and record.targets:
                    _risk = abs(float(record.entry) - float(record.stop_loss))
                    if _risk > 0:
                        _rr = abs(max(record.targets) - float(record.entry)) / _risk
                _es = classify_entry_state(record.cmp, record.entry, record.stop_loss, record.targets)
                record.exceptionalism = score_and_qualify(
                    discovery=record.discovery,
                    smc_band=_smc_score(record.smc, horizon) / 10.0,
                    rr=_rr,
                    nifty_ret20=_exc_nifty_ret20,
                    sector_rel20=_sc.get("rel_20d_pct"),
                    sector_band=_sc.get("band"),
                    entry_state=_es.get("state"),
                    market_health=_exc_health,
                )
            except Exception as exc:
                log.debug("exceptionalism compute failed for %s: %s", symbol, exc)
        records.append(record)

    # PHASE 2 — rank the eligible pool and trim to the gate's own budget.
    #
    # The count is taken from what L1+L2+L3 would have selected on THIS scan, so
    # the switch is a pure substitution: same number of ideas, chosen by score
    # instead of by an SMC pass/fail. Everything downstream (governor caps,
    # promotion room, the served feed) sees an unchanged shape.
    #
    # Scored within the scan, never against a fixed threshold — an absolute cut
    # would drift with the market and quietly become a gate again.
    phase2_selected: set[int] = set()
    if smc_as_score_enabled():
        try:
            from services.phase2_ranking import select_top

            gate_budget = sum(
                1 for r in records
                if r.layer1_pass and r.layer2_pass and r.layer3_pass and r.entry is not None
            )
            eligible = [r for r in records if r.final_selected]
            ranked = select_top(
                [{
                    "key": id(r),
                    "momentum20": (r.discovery or {}).get("momentum_20d_pct"),
                    "momentum50": (r.discovery or {}).get("momentum_50d_pct"),
                    "smc": _smc_score(r.smc, horizon),
                    "quality": (r.quality or {}).get("score"),
                } for r in eligible],
                gate_budget,
            )
            phase2_selected = {row["key"] for row in ranked}
            by_key = {row["key"]: row for row in ranked}
            for r in records:
                keep = id(r) in phase2_selected
                if r.final_selected and not keep:
                    _append_unique(r.rejection_reason, "below_rank_budget")
                r.final_selected = keep
                if keep:
                    r.rejection_reason = []
                    if r.smc is not None:
                        row = by_key[id(r)]
                        r.smc["phase2_score"] = row["score"]
                        r.smc["phase2_components"] = row["components"]
            log.info(
                "[%s][PHASE2] SMC as score: %d eligible (L1+L2+entry) → %d selected "
                "(budget from L1+L2+L3 = %d)",
                horizon, len(eligible), len(phase2_selected), gate_budget,
            )
        except Exception as exc:
            log.warning("[%s][PHASE2] ranking failed (%s) — leaving funnel result", horizon, exc)

    # PR1 — Regime Governor. When enabled, the graduated exposure gate replaces
    # the "always show >=5" force-fill: quantity becomes an output of quality
    # (0/1/2/5/10 all valid). When DISABLED nothing new runs on this path
    # (no classify / no network) ⟹ byte-identical to prior behaviour.
    governor_on = False
    state_result = None
    try:
        from services.regime_governor import governor_enabled
        governor_on = governor_enabled()
        if governor_on:
            from services.regime_governor import classify_market_state
            state_result = classify_market_state()
    except Exception as exc:
        log.debug("regime governor classify skipped: %s", exc)
        governor_on = False

    # Governor-defensive states suppress the decision-engine backfill so watchlist
    # / discovery are not manufactured to fill the page.
    allow_backfill = True
    if governor_on and state_result is not None:
        from services.regime_governor import BEAR, CORRECTION, SIDEWAYS
        allow_backfill = state_result.state not in (SIDEWAYS, CORRECTION, BEAR)

    decisions = build_decision_output(records, limit=top_k, allow_backfill=allow_backfill)
    selected = list(decisions.final_trades)
    watchlist = list(decisions.watchlist)
    discovery = list(decisions.discovery)

    if governor_on and state_result is not None:
        from services.regime_governor import apply_to_records, get_policy
        smc_band_of = lambda r: _smc_score(getattr(r, "smc", None), horizon) / 10.0
        policy = get_policy(state_result.state)
        selected, _sel_diag = apply_to_records(selected, horizon, smc_band_of, state_result)
        # Watchlist/discovery are informational tiers — cap them to the policy
        # ceiling but do not force-fill (no ensure_minimum in governor mode).
        watchlist = watchlist[: policy.max_ideas]
        discovery = discovery[: policy.max_ideas]
    else:
        min_unique_out = max(5, min(int(os.getenv("DISCOVERY_MIN_UNIQUE_SYMBOLS", "5")), top_k))
        selected, watchlist, discovery = ensure_minimum_discovery_outputs(
            records,
            selected,
            watchlist,
            discovery,
            min_unique=min_unique_out,
        )

    # PR2 — sector diversification on the final (selected) bucket (flag-gated,
    # independent of the governor). Disabled ⟹ no-op / byte-identical.
    try:
        from services.regime_governor import (
            enforce_sector_diversification,
            sector_diversification_enabled,
        )

        if sector_diversification_enabled():
            selected, _div = enforce_sector_diversification(
                selected, symbol_of=lambda r: getattr(r, "symbol", "")
            )
    except Exception as exc:
        log.debug("feed sector diversification skipped: %s", exc)

    # EP2 — Exceptionalism as the PRIMARY gate (flag-gated). When enabled, the
    # served `selected` is rebuilt from every stock with a tradable plan that
    # clears required_exceptionalism(market_health) — count is emergent, and an
    # exceptional stock surfaces even if its sector is not leading (override).
    # When disabled, `selected` is exactly what the governor/legacy path produced
    # (byte-identical). Watchlist/discovery are left as the context tiers.
    try:
        from services.exceptionalism import exceptionalism_enabled as _exc_on
        if _exc_on():
            soft_ceiling = int(os.getenv("EXCEPTIONALISM_SOFT_CEILING", "20"))
            selected, _n_qual = apply_exceptionalism_final_gate(records, soft_ceiling)
            overrides = [r.symbol for r in selected if (r.exceptionalism or {}).get("reason") == "exceptional_override"]
            if overrides:
                log.info("[EP2] exceptional overrides surfaced (lagging sector): %s", overrides)
            log.info(
                "[EP2] exceptionalism gate: %d qualified → %d final_selected (health=%s)",
                _n_qual, len(selected), _exc_health,
            )
    except Exception as exc:
        log.debug("exceptionalism enforcement skipped: %s", exc)

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

    sig_total = len(selected) + len(watchlist) + len(discovery)
    reason_empty = ""
    if sig_total == 0:
        reason_empty = "no_discoverable_symbols_after_layer1_and_momentum_fallback"

    diagnostics = {
        "total_stocks_scanned": len(scan_symbols),
        "total_filtered": funnel.layer1_pass,
        "layer2_survivors": funnel.layer2_pass,
        "layer3_survivors": funnel.layer3_pass,
        "strict_final_signals": funnel.final_selected,
        "signals_generated": sig_total,
        "reason_for_zero_signals": reason_empty if sig_total == 0 else "",
    }
    # Attach the regime-governor exposure block to diagnostics ONLY when the
    # governor is enforcing (keeps the flag-OFF path free of extra network /
    # byte-identical). The Research UI reads market state from the dedicated
    # GET /api/market/state endpoint, which computes on demand regardless.
    diagnostics["governor_enforced"] = governor_on
    if governor_on and state_result is not None:
        try:
            from services.regime_governor import exposure_state
            diagnostics["market_state"] = state_result.state
            diagnostics["exposure"] = exposure_state(state_result)
        except Exception as exc:
            log.debug("exposure_state attach skipped: %s", exc)

    log.info(
        "[%s] validation scan %s: total=%d l1=%d l2=%d l3=%d selected=%d logged=%d sig_out=%d",
        horizon,
        scan_id,
        funnel.total,
        funnel.layer1_pass,
        funnel.layer2_pass,
        funnel.layer3_pass,
        funnel.final_selected,
        logged_rows,
        sig_total,
    )
    return ValidationScanResult(
        scan_id=scan_id,
        horizon=horizon,
        universe=universe,
        records=records,
        selected=selected,
        watchlist=watchlist,
        discovery=discovery,
        coverage=coverage,
        funnel=funnel,
        logged_rows=logged_rows,
        diagnostics=diagnostics,
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
