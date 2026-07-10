"""
services/pil/reference_data.py
==============================
Symbol → reference metadata (sector, industry, market-cap tier, theme, beta,
liquidity) for the exposure/risk analytics. This is a thin, side-effect-free
provider that REUSES existing platform data rather than inventing new maps:

  * sector: reuses engine.swing.SECTOR_MAP (a pure lookup dict) when importable;
    Momentum rows also carry their own `sector`, which callers pass through.
  * everything else: an optional bundled reference file
    `data/pil_symbol_reference.json` (symbol -> {industry, mcap_tier, theme,
    beta, liquidity_cr}). Absent keys degrade gracefully to sane defaults.

Nothing here touches an engine loop or triggers network I/O.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("pil.reference_data")

_ROOT = Path(__file__).resolve().parents[2]
_REF_PATH = Path(os.getenv("PIL_SYMBOL_REFERENCE", _ROOT / "data" / "pil_symbol_reference.json"))


def _clean(symbol: str) -> str:
    return (symbol or "").replace("NSE:", "").replace(" ", "").upper()


@lru_cache(maxsize=1)
def _sector_map() -> dict[str, str]:
    """Reuse the engine's sector map (pure dict). Guarded so a heavy/failed
    import never breaks PIL — falls back to whatever the bundled file provides."""
    try:
        from engine.swing import SECTOR_MAP  # pure lookup dict, no side effects
        return {k.upper(): v for k, v in SECTOR_MAP.items()}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[PIL] engine.swing SECTOR_MAP unavailable: %s", exc)
        return {}


@lru_cache(maxsize=1)
def _ref() -> dict[str, dict]:
    """Bundled per-symbol reference metadata. Optional; empty if the file
    is absent (all lookups then return graceful defaults)."""
    try:
        if _REF_PATH.exists():
            with open(_REF_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return {_clean(k): v for k, v in raw.items()}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[PIL] symbol reference load failed: %s", exc)
    return {}


def get_sector(symbol: str, fallback: str | None = None) -> str:
    """Sector for a symbol. `fallback` lets callers pass a row-level sector
    (e.g. Momentum positions store their own) which wins over the static map."""
    sym = _clean(symbol)
    if fallback:
        return fallback
    ref = _ref().get(sym, {})
    if ref.get("sector"):
        return ref["sector"]
    return _sector_map().get(sym, "Others")


def get_industry(symbol: str) -> str:
    return _ref().get(_clean(symbol), {}).get("industry") or get_sector(symbol)


def get_market_cap_tier(symbol: str) -> str:
    """LargeCap / MidCap / SmallCap. Defaults to MidCap when unknown."""
    return _ref().get(_clean(symbol), {}).get("mcap_tier") or "MidCap"


def get_theme(symbol: str) -> str:
    return _ref().get(_clean(symbol), {}).get("theme") or "Broad Market"


def get_beta(symbol: str) -> float:
    """Beta vs NIFTY. Defaults to 1.0 (market beta) when unknown."""
    v = _ref().get(_clean(symbol), {}).get("beta")
    try:
        return float(v) if v is not None else 1.0
    except (TypeError, ValueError):
        return 1.0


def get_liquidity_cr(symbol: str) -> float | None:
    """Average daily traded value in ₹Cr, if known (else None)."""
    v = _ref().get(_clean(symbol), {}).get("liquidity_cr")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def metadata(symbol: str, *, sector_fallback: str | None = None) -> dict:
    """One-shot bundle of every reference dimension for a symbol."""
    return {
        "symbol": _clean(symbol),
        "sector": get_sector(symbol, sector_fallback),
        "industry": get_industry(symbol),
        "mcap_tier": get_market_cap_tier(symbol),
        "theme": get_theme(symbol),
        "beta": get_beta(symbol),
        "liquidity_cr": get_liquidity_cr(symbol),
    }
