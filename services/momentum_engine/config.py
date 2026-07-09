"""
services/momentum_engine/config.py
==================================
Central configuration for the Momentum Continuation Engine.

Every threshold and flag is read LIVE from the environment (so changes take
effect without a redeploy, like services/risk_engine.py). Defaults are chosen so
the engine is INERT and SAFE out of the box:

  * MOMENTUM_ENGINE_ENABLED defaults to "0" (OFF) — the engine evaluates nothing
    until explicitly turned on, and even then only in shadow until wired.

Nothing here imports heavy modules; it is import-safe and unit-testable.
"""

from __future__ import annotations

import os
from typing import Any


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def cfg() -> dict[str, Any]:
    """Live configuration snapshot. Also used verbatim in decision logs."""
    return {
        # ── Master + mode flags (independently reversible) ──
        "MOMENTUM_ENGINE_ENABLED": _flag("MOMENTUM_ENGINE_ENABLED", "0"),   # default OFF
        "MOMENTUM_SHADOW_ONLY": _flag("MOMENTUM_SHADOW_ONLY", "1"),         # generate signals only, never promote
        "MOMENTUM_REGIME_GATE_ENABLED": _flag("MOMENTUM_REGIME_GATE_ENABLED", "1"),

        # ── Eligibility gates (Phase 3) ──
        "MOM_MIN_RS_20D": _f("MOM_MIN_RS_20D", 5.0),        # RS vs NIFTY over 20d must exceed this (%)
        "MOM_REQUIRE_ABOVE_200DMA": _flag("MOM_REQUIRE_ABOVE_200DMA", "1"),
        "MOM_MIN_TURNOVER_CR": _f("MOM_MIN_TURNOVER_CR", 5.0),   # ₹Cr/day liquidity floor (stricter than SMC — momentum needs depth)
        "MOM_MIN_PRICE": _f("MOM_MIN_PRICE", 50.0),
        "MOM_MIN_TREND_QUALITY": _f("MOM_MIN_TREND_QUALITY", 0.55),  # 0..1 smoothness of advance
        "MOM_MIN_VOLUME_RATIO": _f("MOM_MIN_VOLUME_RATIO", 1.3),     # breakout-day vol / 20d avg
        "MOM_MAX_EXTENSION_ATR": _f("MOM_MAX_EXTENSION_ATR", 4.0),   # reject if > N ATR above the base/EMA20 (anti-chase)
        "MOM_MAX_EXTENSION_PCT": _f("MOM_MAX_EXTENSION_PCT", 12.0),  # or > this % above EMA20
        "MOM_CLIMAX_MAX_UP_DAYS": _i("MOM_CLIMAX_MAX_UP_DAYS", 6),   # reject after N consecutive big up-days (exhaustion)
        "MOM_CLIMAX_ATR_PCTL": _f("MOM_CLIMAX_ATR_PCTL", 0.90),      # reject if ATR% in top decile of its own 1y range
        "MOM_MAX_GAP_PCT": _f("MOM_MAX_GAP_PCT", 6.0),              # reject breakouts that are a > this% opening gap (gap trap)
        "MOM_MAX_BASE_PROXIMITY_PCT": _f("MOM_MAX_BASE_PROXIMITY_PCT", 6.0),  # entry must be within this % of the breakout pivot (base proximity / anti-FOMO)
        "MOM_MIN_BARS": _i("MOM_MIN_BARS", 60),

        # ── Entry models (Phase 4) ──
        "MOM_ENTRY_MODELS": os.getenv("MOM_ENTRY_MODELS", "vcp,breakout,shallow_pullback"),
        "MOM_VCP_MAX_BASE_ATR_PCT": _f("MOM_VCP_MAX_BASE_ATR_PCT", 3.0),   # base must be tight: ATR% below this
        "MOM_VCP_BASE_LOOKBACK": _i("MOM_VCP_BASE_LOOKBACK", 15),          # bars forming the contraction
        "MOM_BREAKOUT_LOOKBACK": _i("MOM_BREAKOUT_LOOKBACK", 50),          # N-day high for the pivot
        "MOM_SHALLOW_PULLBACK_MAX_PCT": _f("MOM_SHALLOW_PULLBACK_MAX_PCT", 8.0),  # dip <= this% (too shallow for SMC OB/FVG)
        "MOM_TRIGGER_BUFFER_PCT": _f("MOM_TRIGGER_BUFFER_PCT", 0.3),       # arm trigger this % above the pivot

        # ── Ranking (Phase 5) — weights (normalised at use; configurable) ──
        "MOM_W_RS": _f("MOM_W_RS", 0.30),
        "MOM_W_BREAKOUT": _f("MOM_W_BREAKOUT", 0.20),
        "MOM_W_VOLUME": _f("MOM_W_VOLUME", 0.15),
        "MOM_W_TREND_QUALITY": _f("MOM_W_TREND_QUALITY", 0.15),
        "MOM_W_VCP_TIGHTNESS": _f("MOM_W_VCP_TIGHTNESS", 0.10),
        "MOM_W_SECTOR": _f("MOM_W_SECTOR", 0.10),
        "MOM_EXTENSION_PENALTY": _f("MOM_EXTENSION_PENALTY", 15.0),  # points subtracted per ATR of over-extension

        # ── Router (Phase 6) ──
        "MOM_REGIMES_ALLOWED": os.getenv("MOM_REGIMES_ALLOWED", "TRENDING_UP,SIDEWAYS"),
        # (SIDEWAYS kept for early breakouts; disable via env to be stricter. TRENDING_DOWN never allowed.)

        # ── Independent Momentum PORTFOLIO ──
        "MOMENTUM_PORTFOLIO_ENABLED": _flag("MOMENTUM_PORTFOLIO_ENABLED", "0"),  # master for the live independent book
        "MOMENTUM_MAX_POSITIONS": _i("MOMENTUM_MAX_POSITIONS", 20),
        "MOMENTUM_ALLOCATION_PCT": _f("MOMENTUM_ALLOCATION_PCT", 0.0),
        "MOMENTUM_ALLOW_DUP_EXPOSURE": _flag("MOMENTUM_ALLOW_DUP_EXPOSURE", "0"),  # skip names already ACTIVE in Swing
        "MOMENTUM_REPLACEMENT_MIN_EDGE": _f("MOMENTUM_REPLACEMENT_MIN_EDGE", 5.0),  # new score must beat weakest by this
        # risk / sizing profile (INDEPENDENT of the Swing risk engine)
        "MOMENTUM_NOTIONAL_CAPITAL": _f("MOMENTUM_NOTIONAL_CAPITAL", 1_000_000.0),
        "MOMENTUM_RISK_PER_TRADE_PCT": _f("MOMENTUM_RISK_PER_TRADE_PCT", 1.0),
        # exit engine profile
        "MOMENTUM_STOP_METHOD": os.getenv("MOMENTUM_STOP_METHOD", "hybrid"),
        "MOMENTUM_STOP_K": _f("MOMENTUM_STOP_K", 1.5),
        "MOMENTUM_STOP_MAX_PCT": _f("MOMENTUM_STOP_MAX_PCT", 10.0),
        "MOMENTUM_TRAIL_METHOD": os.getenv("MOMENTUM_TRAIL_METHOD", "atr_chandelier"),
        "MOMENTUM_TRAIL_K": _f("MOMENTUM_TRAIL_K", 3.0),
        "MOMENTUM_TRAIL_EMA_N": _i("MOMENTUM_TRAIL_EMA_N", 20),
        "MOMENTUM_TRAIL_STRUCT_LOOKBACK": _i("MOMENTUM_TRAIL_STRUCT_LOOKBACK", 10),
        "MOMENTUM_BREAKEVEN_R": _f("MOMENTUM_BREAKEVEN_R", 1.0),
        "MOMENTUM_MAX_LOSS_PCT": _f("MOMENTUM_MAX_LOSS_PCT", 12.0),   # hard backstop
        "MOMENTUM_TIME_EXIT_DAYS": _i("MOMENTUM_TIME_EXIT_DAYS", 40),
        "MOMENTUM_PENDING_MAX_DAYS": _i("MOMENTUM_PENDING_MAX_DAYS", 7),
        # ── Classification + quality-optimizing replacement (Phase B) ──
        "MOM_CLASS_ELITE": _f("MOM_CLASS_ELITE", 78.0),
        "MOM_CLASS_GOOD": _f("MOM_CLASS_GOOD", 64.0),
        "MOM_CLASS_WEAK": _f("MOM_CLASS_WEAK", 50.0),
        "MOM_MAX_SECTOR_SHARE": _f("MOM_MAX_SECTOR_SHARE", 0.34),      # diversification target
        "MOM_SECTOR_CONCENTRATION_PENALTY": _f("MOM_SECTOR_CONCENTRATION_PENALTY", 20.0),
        "MOM_REPLACE_MIN_QUALITY_GAIN": _f("MOM_REPLACE_MIN_QUALITY_GAIN", 2.0),
        # candidate pipeline
        "MOMENTUM_TRACKER_ENABLED": _flag("MOMENTUM_TRACKER_ENABLED", "0"),  # scheduler loop
        "MOMENTUM_CANDIDATE_LIMIT": _i("MOMENTUM_CANDIDATE_LIMIT", 60),
    }


def entry_models_enabled() -> list[str]:
    raw = cfg()["MOM_ENTRY_MODELS"]
    return [m.strip().lower() for m in str(raw).split(",") if m.strip()]


def regimes_allowed() -> set[str]:
    raw = cfg()["MOM_REGIMES_ALLOWED"]
    return {r.strip().upper() for r in str(raw).split(",") if r.strip()}
