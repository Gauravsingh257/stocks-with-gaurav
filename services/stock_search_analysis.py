"""
On-demand stock analysis for the Research Center global NSE search.

This module intentionally reuses existing research primitives where possible:
- NSE universe loading from services.universe_manager
- CMP resolution from services.price_resolver
- SMC trade levels from services.research_levels
- fundamentals from services.fundamental_analysis
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import pandas as pd

from engine.indicators import calculate_atr
from engine.swing import detect_daily_fvg, detect_daily_ob, detect_daily_structure
from services.fundamental_analysis import analyze_fundamentals
from services.price_resolver import resolve_cmp
from services.research_levels import (
    build_longterm_trade_levels,
    build_swing_trade_levels,
    daily_candles_to_weekly,
    df_to_candles,
)
from services.universe_manager import load_nse_universe

log = logging.getLogger("services.stock_search_analysis")

_SUGGESTION_TTL_SEC = 1800
_analysis_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_ANALYSIS_TTL_SEC = 300


def normalize_symbol(symbol: str) -> str:
    clean = (symbol or "").strip().upper()
    clean = clean.replace(".NS", "").replace("NSE:", "")
    clean = "".join(ch for ch in clean if ch.isalnum() or ch in ("-", "&", " "))
    return clean.strip()


def nse_symbol(symbol: str) -> str:
    clean = normalize_symbol(symbol)
    return clean if clean.startswith("NSE:") else f"NSE:{clean}"


@lru_cache(maxsize=1)
def _universe_cache_marker() -> float:
    return time.time()


def _load_symbols() -> list[str]:
    # Cache at the function level without stale process-global data structures.
    _universe_cache_marker()
    universe = load_nse_universe(target_size=1800)
    return sorted({s.replace("NSE:", "").upper() for s in universe.symbols if s})


def stock_suggestions(query: str, limit: int = 10) -> list[dict[str, str]]:
    q = normalize_symbol(query)
    if not q:
        return []
    symbols = _load_symbols()
    prefix = [s for s in symbols if s.startswith(q)]
    contains = [s for s in symbols if q in s and not s.startswith(q)]
    picks = (prefix + contains)[:limit]
    return [{"symbol": s, "name": s, "exchange": "NSE"} for s in picks]


def _fetch_ohlc(symbol: str, days: int = 420) -> pd.DataFrame | None:
    try:
        import yfinance as yf  # noqa: PLC0415

        clean = normalize_symbol(symbol)
        df = yf.Ticker(f"{clean}.NS").history(period=f"{days}d")
        if df is None or df.empty:
            return None
        out = df.reset_index().rename(
            columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        return out[["date", "open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    except Exception as exc:
        log.warning("OHLC fetch failed for %s: %s", symbol, exc)
        return None


def _fundamentals(symbol: str) -> dict[str, Any]:
    try:
        snap = asyncio.run(analyze_fundamentals([symbol]))
        fs = snap.get(symbol) or snap.get(nse_symbol(symbol)) or next(iter(snap.values()), None)
        if not fs:
            return {}
        return {
            "score": round(float(fs.fundamental_score) * 100, 1),
            "pe_ratio": fs.raw_pe,
            "roe_pct": fs.raw_roe_pct,
            "roce_pct": fs.raw_roce_pct,
            "revenue_growth_pct": fs.raw_revenue_growth_pct,
            "debt_equity": fs.raw_debt_equity,
            "market_cap_cr": fs.raw_market_cap_cr,
            "promoter_pct": fs.raw_promoter_pct,
            "sector": fs.sector,
            "industry": fs.industry,
            "data_source": fs.data_source,
        }
    except Exception as exc:
        log.warning("fundamentals failed for %s: %s", symbol, exc)
        return {}


def _cmp(symbol: str, fallback: float | None) -> tuple[float | None, str, int | None]:
    try:
        data = resolve_cmp([symbol], scan_cmp_map={symbol: fallback} if fallback else None)
        row = data.get(symbol) or data.get(nse_symbol(symbol)) or data.get(normalize_symbol(symbol))
        if row:
            return float(row["price"]), str(row.get("source", "unknown")), int(row.get("age_sec", 0))
    except Exception as exc:
        log.debug("CMP resolution failed for %s: %s", symbol, exc)
    return fallback, "ohlc_close" if fallback is not None else "unknown", None


def _zones(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    try:
        ob = detect_daily_ob(candles, "LONG")
        if ob:
            zones.append({"type": "Order Block", "bottom": round(float(ob[0]), 2), "top": round(float(ob[1]), 2)})
        fvg = detect_daily_fvg(candles, "LONG")
        if fvg:
            zones.append({"type": "Fair Value Gap", "bottom": round(float(fvg[0]), 2), "top": round(float(fvg[1]), 2)})
        structure, info = detect_daily_structure(candles)
        if info and info.get("level"):
            zones.append({"type": f"Structure {structure}", "level": round(float(info["level"]), 2)})
    except Exception as exc:
        log.debug("SMC zone detection failed: %s", exc)
    return zones


def _criteria_not_met(candles: list[dict[str, Any]], setup_ok: bool) -> list[str]:
    if setup_ok:
        return []
    misses: list[str] = []
    try:
        if not detect_daily_ob(candles, "LONG"):
            misses.append("No valid order block")
        if not detect_daily_fvg(candles, "LONG"):
            misses.append("No liquidity sweep")
        structure, _ = detect_daily_structure(candles)
        if structure not in ("BOS", "CHOCH"):
            misses.append("No BOS confirmation")
    except Exception:
        pass
    return misses or ["No valid order block", "No liquidity sweep", "No BOS confirmation"]


def _recommendation(confidence: float, setup_ok: bool) -> str:
    if setup_ok and confidence >= 75:
        return "Strong Buy"
    if confidence >= 50:
        return "Watchlist"
    return "Avoid"


def _confidence(
    *,
    setup_ok: bool,
    rr: float,
    smc_meta: dict[str, Any] | None,
    fundamentals: dict[str, Any],
    candles: list[dict[str, Any]],
) -> float:
    score = 30.0
    if setup_ok:
        score += 25.0
    score += min(max(rr, 0), 4) * 6
    if smc_meta:
        raw = float(smc_meta.get("score", 0))
        score += min(raw / 12 * 20, 20)
    if candles:
        recent = candles[-20:]
        avg_vol = sum(float(c.get("volume", 0)) for c in recent) / max(len(recent), 1)
        if avg_vol > 500_000:
            score += 8
        elif avg_vol > 100_000:
            score += 4
    score += min(float(fundamentals.get("score") or 0) * 0.13, 13)
    return round(max(0, min(100, score)), 1)


def _fallback_levels(cmp_value: float | None, candles: list[dict[str, Any]]) -> tuple[list[float] | None, float | None, float | None, float]:
    if not cmp_value:
        return None, None, None, 0.0
    atr = calculate_atr(candles, 14) if len(candles) >= 20 else cmp_value * 0.03
    atr = max(atr, cmp_value * 0.025)
    entry_low = round(cmp_value - atr * 1.2, 2)
    entry_high = round(cmp_value - atr * 0.3, 2)
    stop = round(entry_low - atr * 1.5, 2)
    target = round(cmp_value + atr * 2.5, 2)
    risk = max(entry_high - stop, 0.01)
    reward = max(target - entry_high, 0.0)
    return [entry_low, entry_high], stop, target, round(reward / risk, 2)


def analyze_stock(symbol: str) -> dict[str, Any]:
    clean = normalize_symbol(symbol)
    if not clean:
        raise ValueError("symbol is required")
    full_symbol = f"NSE:{clean}"

    cached = _analysis_cache.get(full_symbol)
    if cached and time.time() - cached[0] < _ANALYSIS_TTL_SEC:
        return cached[1]

    df = _fetch_ohlc(clean)
    candles = df_to_candles(df)
    if not df is None and candles:
        last_close = float(candles[-1]["close"])
    else:
        last_close = None
    cmp_value, cmp_source, cmp_age_sec = _cmp(full_symbol, last_close)
    nifty_df = _fetch_ohlc("NIFTY 50")
    nifty_daily = df_to_candles(nifty_df)
    fundamentals = _fundamentals(full_symbol)

    levels = None
    horizon = "SWING"
    if df is not None and len(candles) >= 30:
        levels = build_swing_trade_levels(full_symbol, df, nifty_daily)
    if levels is None and df is not None and len(candles) >= 60:
        lt = build_longterm_trade_levels(full_symbol, df, nifty_daily)
        if lt:
            entry, stop, targets, long_target, entry_zone, setup, meta = lt
            levels = (entry, stop, targets or [long_target], setup, meta, entry_zone)
            horizon = "LONGTERM"

    setup_ok = levels is not None
    entry_zone: list[float] | None = None
    stop_loss: float | None = None
    target: float | None = None
    rr = 0.0
    setup_type = "No Valid SMC Setup"
    smc_meta: dict[str, Any] | None = None

    if levels:
        entry = float(levels[0])
        stop_loss = round(float(levels[1]), 2)
        targets = [float(t) for t in levels[2]]
        target = round(float(targets[-1]), 2) if targets else None
        setup_type = str(levels[3])
        smc_meta = levels[4] if isinstance(levels[4], dict) else None
        if len(levels) >= 6 and isinstance(levels[5], list):
            entry_zone = [round(float(levels[5][0]), 2), round(float(levels[5][1]), 2)]
        else:
            entry_zone = [round(entry * 0.995, 2), round(entry * 1.005, 2)]
        risk = abs(entry - stop_loss)
        reward = abs((target or entry) - entry)
        rr = round(reward / max(risk, 0.01), 2)
    else:
        entry_zone, stop_loss, target, rr = _fallback_levels(cmp_value, candles)

    confidence = _confidence(
        setup_ok=setup_ok,
        rr=rr,
        smc_meta=smc_meta,
        fundamentals=fundamentals,
        candles=candles,
    )
    criteria_not_met = _criteria_not_met(candles, setup_ok)
    recommendation = _recommendation(confidence, setup_ok)
    reason = (
        "Strong demand zone, confirmed SMC levels, and acceptable risk/reward align for a research setup."
        if setup_ok
        else "No valid SMC setup is confirmed yet. Use this as a watchlist candidate until order block, liquidity sweep, and BOS confirmation improve."
    )
    if smc_meta and smc_meta.get("reasons"):
        reason = " ".join(str(x) for x in smc_meta["reasons"][:3])

    result = {
        "symbol": clean,
        "name": clean,
        "exchange": "NSE",
        "cmp": round(float(cmp_value), 2) if cmp_value is not None else None,
        "cmp_source": cmp_source,
        "cmp_age_sec": cmp_age_sec,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "target": target,
        "risk_reward": rr,
        "confidence_score": confidence,
        "setup_type": setup_type,
        "horizon": horizon,
        "recommendation": recommendation,
        "reason": reason,
        "criteria_not_met": criteria_not_met,
        "smc_zones": _zones(candles),
        "fundamentals": fundamentals,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _analysis_cache[full_symbol] = (time.time(), result)
    return result
