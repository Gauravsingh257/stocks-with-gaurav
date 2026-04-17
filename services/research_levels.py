"""
OHLC-backed trade levels for AI Research Center.

Swing (long-only):
  - Primary: ``score_swing_candidate`` must return direction LONG with valid entry/SL/targets.
  - If SMC says SHORT (bearish structure), the symbol is **excluded** — we do not fabricate a
    conflicting "long" plan for the same name (that looked random vs the prior short plan).
  - Optional ATR fallback only when ``RESEARCH_SWING_ATR_FALLBACK=1`` and SMC returns None
    (failed quality gate), never as a substitute for an explicit SHORT signal.

Long-term (long-only):
  - Primary: ``score_longterm_candidate`` — weekly SMC analysis (weekly trend, structure,
    OB/FVG, RS, volume accumulation, 52W context).
  - Fallback to weekly demand-zone / ATR levels only when SMC returns None and
    ``RESEARCH_LONGTERM_ATR_FALLBACK=1``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

from engine.indicators import calculate_atr
from engine.swing import detect_weekly_trend, score_swing_candidate, score_longterm_candidate

log = logging.getLogger("services.research_levels")

NIFTY_DAILY_SYMBOL = os.getenv("RESEARCH_NIFTY_SYMBOL", "NSE:NIFTY 50")
RESEARCH_MAX_ENTRY_VS_CLOSE_PCT = float(os.getenv("RESEARCH_MAX_ENTRY_VS_CLOSE_PCT", "0.30"))
RESEARCH_POOL_MULT = max(3, int(os.getenv("RESEARCH_POOL_MULT", "15")))


def _swing_atr_fallback_enabled() -> bool:
    """
    Default ON: ATR fallback creates conservative long plans for stocks where SMC found nothing
    or returned SHORT. Disable with RESEARCH_SWING_ATR_FALLBACK=0.
    """
    return os.getenv("RESEARCH_SWING_ATR_FALLBACK", "1").strip().lower() not in ("0", "false", "no")


def df_to_candles(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Normalize DataIngestion DataFrame to list[dict] for engine.swing."""
    if df is None or df.empty:
        return []
    frame = df.copy()
    if "date" not in frame.columns:
        if frame.index.name in ("date", "Date") or isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index()
            frame.rename(columns={frame.columns[0]: "date"}, inplace=True)
    colmap = {c: c.lower() for c in frame.columns}
    frame = frame.rename(columns=colmap)
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(frame.columns)):
        return []
    out: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        out.append(
            {
                "date": row.get("date"),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]) if "volume" in frame.columns and pd.notna(row.get("volume")) else 0.0,
            }
        )
    return out


def daily_candles_to_weekly(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate daily OHLCV to weekly (Friday-aligned)."""
    if len(candles) < 5:
        return []
    pdf = pd.DataFrame(candles)
    pdf["date"] = pd.to_datetime(pdf["date"], errors="coerce")
    pdf = pdf.dropna(subset=["date"])
    if pdf.empty:
        return []
    pdf = pdf.set_index("date").sort_index()
    w = pdf.resample("W-FRI").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    w = w.dropna(subset=["close"])
    w = w.reset_index()
    return df_to_candles(w)


def entry_vs_close_sane(entry: float, close: float) -> bool:
    if close <= 0:
        return False
    return abs(entry - close) / close <= RESEARCH_MAX_ENTRY_VS_CLOSE_PCT


def _split_targets(direction: str, entry: float, target: float) -> tuple[float, float]:
    """Derive T1 (partial) and T2 (full SMC target) from single swing target."""
    if direction == "LONG":
        span = target - entry
        t1 = round(entry + span * 0.55, 2)
        t2 = round(target, 2)
    else:
        span = entry - target
        t1 = round(entry - span * 0.55, 2)
        t2 = round(target, 2)
    return t1, t2


def long_swing_geometry_ok(entry: float, stop: float, targets: list[float]) -> bool:
    """
    Research swing is long-only: stop below entry, all targets strictly above entry.
    """
    if entry <= 0 or stop >= entry:
        return False
    if not targets:
        return False
    for t in targets:
        if t <= entry:
            return False
    return True


def atr_fallback_levels(
    symbol: str,
    candles: list[dict[str, Any]],
    *,
    force_long: bool = False,
) -> tuple[float, float, list[float], str] | None:
    """When score_swing_candidate fails quality gates, use ATR / structure-neutral plan at CMP."""
    if len(candles) < 30:
        return None
    close = candles[-1]["close"]
    atr = calculate_atr(candles, 14)
    if atr <= 0 or close <= 0:
        return None
    window = min(20, len(candles))
    sma = sum(c["close"] for c in candles[-window:]) / window
    # Research swing stock picks are long-only — use force_long to avoid SHORT/CMP-below-SMA plans.
    if force_long:
        direction = "LONG"
    else:
        direction = "LONG" if close >= sma else "SHORT"
    min_risk = max(atr * 1.5, close * 0.03)
    if direction == "LONG":
        entry = round(close, 2)
        sl = round(entry - min_risk, 2)
        if sl >= entry:
            return None
        r = entry - sl
        t1 = round(entry + r * 1.5, 2)
        t2 = round(entry + r * 3.0, 2)
        setup = "ATR_FALLBACK_LONG"
    else:
        entry = round(close, 2)
        sl = round(entry + min_risk, 2)
        if sl <= entry:
            return None
        r = sl - entry
        t1 = round(entry - r * 1.5, 2)
        t2 = round(entry - r * 3.0, 2)
        setup = "ATR_FALLBACK_SHORT"
    if not entry_vs_close_sane(entry, close):
        return None
    return entry, sl, [t1, t2], setup


def build_swing_trade_levels(
    symbol: str,
    daily_df: pd.DataFrame,
    nifty_daily: list[dict[str, Any]],
) -> tuple[float, float, list[float], str, dict[str, Any] | None] | None:
    """
    Returns (entry, stop_loss, [t1, t2], setup_label, smc_meta) or None.

    Long-only list: we only emit levels when SMC agrees on LONG. If SMC says SHORT, we return
    None (symbol skipped for swing-long — no synthetic long to replace a bearish thesis).
    smc_meta is set only for real SMC LONG paths; ATR fallback (if enabled) sets smc_meta=None.
    """
    candles = df_to_candles(daily_df)
    if len(candles) < 30:
        return None
    close = candles[-1]["close"]
    weekly = daily_candles_to_weekly(candles)
    sw = score_swing_candidate(symbol, candles, weekly, nifty_daily)

    if not sw:
        if _swing_atr_fallback_enabled():
            fb = atr_fallback_levels(symbol, candles, force_long=True)
            if fb:
                entry, sl, targets, setup = fb
                return entry, sl, targets, setup, None
        return None

    direction = str(sw.get("direction") or "LONG")
    if direction != "LONG":
        if _swing_atr_fallback_enabled():
            log.info(
                "[research] %s SMC direction SHORT — using ATR fallback for long plan",
                symbol,
            )
            fb = atr_fallback_levels(symbol, candles, force_long=True)
            if fb:
                entry, sl, targets, setup = fb
                return entry, sl, targets, setup, None
        log.info(
            "[research] %s excluded from swing-long: SMC direction SHORT, no fallback",
            symbol,
        )
        return None

    entry = float(sw["entry"])
    sl = float(sw["sl"])
    target = float(sw["target"])
    entry_type = sw.get("entry_type", "MARKET")

    # Freshness gate: reject if CMP already moved >30% toward target (stale entry)
    total_move = abs(target - entry)
    if total_move > 0:
        progress = abs(close - entry) / total_move
        if progress > 0.30:
            log.info(
                "[research] %s entry stale: CMP %.2f already %.0f%% toward target (entry=%.2f target=%.2f) — skip",
                symbol, close, progress * 100, entry, target,
            )
            return None

    if not entry_vs_close_sane(entry, close):
        log.debug("Swing levels failed CMP sanity for %s", symbol)
        return None

    t1, t2 = _split_targets("LONG", entry, target)
    if not long_swing_geometry_ok(entry, sl, [t1, t2]):
        log.debug(
            "Swing LONG geometry invalid for %s (entry=%s sl=%s t=%s/%s) — skip",
            symbol, entry, sl, t1, t2,
        )
        return None

    wt = sw.get("weekly_trend", "?")
    ds = sw.get("daily_structure", "?")
    setup = f"SMC_SWING_{wt}_{ds}"
    return entry, sl, [t1, t2], setup, sw


def _find_weekly_demand_zone(weekly: list[dict[str, Any]], atr: float) -> tuple[float, float] | None:
    """Find the most recent weekly demand zone (swing low cluster) within 20% of CMP."""
    if len(weekly) < 8:
        return None
    close = weekly[-1]["close"]
    # Find recent weekly swing lows (local lows surrounded by higher lows)
    lows = []
    for i in range(2, len(weekly) - 1):
        if weekly[i]["low"] < weekly[i - 1]["low"] and weekly[i]["low"] < weekly[i + 1]["low"]:
            lows.append(weekly[i]["low"])
    if not lows:
        return None
    # Pick the nearest swing low that is below CMP and within 20%
    candidates = sorted([l for l in lows if l < close and (close - l) / close < 0.20], reverse=True)
    if not candidates:
        return None
    zone_low = candidates[0]
    zone_high = round(zone_low + atr, 2)
    return round(zone_low, 2), min(zone_high, round(close * 0.985, 2))


def _find_key_resistance(weekly: list[dict[str, Any]]) -> float | None:
    """Find resistance level (recent swing high above CMP for target projection)."""
    if len(weekly) < 8:
        return None
    close = weekly[-1]["close"]
    highs = []
    for i in range(2, len(weekly) - 1):
        if weekly[i]["high"] > weekly[i - 1]["high"] and weekly[i]["high"] > weekly[i + 1]["high"]:
            highs.append(weekly[i]["high"])
    # Nearest swing high above CMP up to 40% away
    candidates = sorted([h for h in highs if h > close and (h - close) / close < 0.40])
    return round(candidates[0], 2) if candidates else None


def _longterm_atr_fallback_enabled() -> bool:
    """
    Default OFF: ATR fallback creates generic long plans for stocks where weekly SMC found nothing.
    Enable only for testing with RESEARCH_LONGTERM_ATR_FALLBACK=1.
    """
    return os.getenv("RESEARCH_LONGTERM_ATR_FALLBACK", "0").strip().lower() in ("1", "true", "yes")


def build_longterm_trade_levels(
    symbol: str,
    daily_df: pd.DataFrame,
    nifty_daily: list[dict[str, Any]],
) -> tuple[float, float, list[float], float, list[float], str, dict[str, Any] | None] | None:
    """
    Long-horizon levels from weekly SMC structure analysis.

    Returns (entry, stop, [target], long_target, entry_zone, setup_label, longterm_meta) or None.
    longterm_meta is set only for real SMC paths; ATR fallback sets it to None.
    """
    candles = df_to_candles(daily_df)
    if len(candles) < 60:
        return None
    close = candles[-1]["close"]
    atr = calculate_atr(candles, 14)
    if close <= 0 or atr <= 0:
        return None
    weekly = daily_candles_to_weekly(candles)

    # Primary: weekly SMC analysis via score_longterm_candidate
    lt = score_longterm_candidate(symbol, candles, weekly, nifty_daily) if len(weekly) >= 20 else None

    if lt:
        entry = float(lt["entry"])
        sl = float(lt["sl"])
        target = float(lt["target"])
        rr = lt["rr"]

        # Freshness gate: reject if CMP already moved >30% toward target (stale entry)
        total_move = abs(target - entry)
        if total_move > 0:
            progress = abs(close - entry) / total_move
            if progress > 0.30:
                log.info(
                    "[research] %s longterm entry stale: CMP %.2f already %.0f%% toward target (entry=%.2f target=%.2f) — skip",
                    symbol, close, progress * 100, entry, target,
                )
                return None

        # Entry zone from weekly OB/FVG or ±ATR around entry
        w_ob = lt.get("weekly_ob")
        w_fvg = lt.get("weekly_fvg")
        if w_fvg:
            entry_zone = [round(w_fvg[0], 2), round(w_fvg[1], 2)]
        elif w_ob:
            entry_zone = [round(w_ob[0], 2), round(w_ob[1], 2)]
        else:
            entry_zone = [round(entry - atr, 2), round(entry + atr * 0.5, 2)]

        # Sanity: entry must relate to close
        if close > 0 and abs(entry - close) / close > 0.30:
            entry = round(close - 2 * atr, 2)

        wt = lt.get("weekly_trend", "?")
        ws = lt.get("weekly_structure", "?")
        setup_note = f"SMC_LONGTERM_{wt}_{ws}"
        return entry, sl, [target], target, entry_zone, setup_note, lt

    # Fallback: ATR-based levels (only if explicitly enabled)
    if not _longterm_atr_fallback_enabled():
        log.info("[research] %s excluded from longterm: weekly SMC returned no signal", symbol)
        return None

    # Legacy ATR fallback (same as before)
    wt = detect_weekly_trend(weekly) if len(weekly) >= 12 else "NEUTRAL"
    lookback = min(252, len(candles))
    hi52 = max(c["high"] for c in candles[-lookback:])
    lo52 = min(c["low"] for c in candles[-lookback:])
    demand_zone = _find_weekly_demand_zone(weekly, atr)
    if demand_zone:
        zone_low, zone_high = demand_zone
        entry = round((zone_low + zone_high) / 2, 2)
        entry_zone = [zone_low, zone_high]
        stop = round(zone_low - 1.5 * atr, 2)
    else:
        entry = round(close - 2 * atr, 2)
        entry_zone = [round(entry - atr, 2), round(entry + atr * 0.5, 2)]
        stop = round(entry - 2.5 * atr, 2)
    resistance = _find_key_resistance(weekly)
    min_target = round(close * 1.15, 2)
    long_target = max(resistance or 0, min_target)
    long_target = round(long_target, 2)
    risk = abs(entry - stop)
    reward = abs(long_target - entry)
    if risk <= 0 or reward / risk < 1.5:
        entry = round(close, 2)
        stop = round(close - 3 * atr, 2)
        long_target = round(close * 1.20, 2)
        entry_zone = [round(close - atr, 2), round(close, 2)]
    if close > 0 and abs(entry - close) / close > 0.30:
        entry = round(close - 2 * atr, 2)
    setup_note = f"LONGTERM_{wt}_52W({round((close-lo52)/lo52*100,0):.0f}%aboveLow)"
    return entry, stop, [long_target], long_target, entry_zone, setup_note, None
