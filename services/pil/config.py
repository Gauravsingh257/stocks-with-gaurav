"""
services/pil/config.py
======================
Live configuration for the Portfolio Intelligence Layer.

Every flag and threshold is read LIVE from the environment (like
services/momentum_engine/config.py and services/risk_engine.py), so changes take
effect without a redeploy. Defaults are chosen so PIL is INERT and SAFE out of
the box: PIL_ENABLED defaults to "0" — nothing mounts, nothing computes, the
platform behaves exactly as before.

A subset of values (book capital, allocation targets, thresholds) may be
overridden at runtime from the `pil_config` table; those overrides win over env.
This module is import-safe and has no heavy dependencies.
"""

from __future__ import annotations

import os
from typing import Any

from services.pil import BOOKS


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


# ── Master + sub flags ───────────────────────────────────────────────────────

def enabled() -> bool:
    """Master switch. When False, PIL mounts nothing and computes nothing.
    Defaults ON (live) — set PIL_ENABLED=0 to fully disable/rollback."""
    return _flag("PIL_ENABLED", "1")


def reports_enabled() -> bool:
    return enabled() and _flag("PIL_REPORTS_ENABLED", "1")


def alerts_enabled() -> bool:
    return enabled() and _flag("PIL_ALERTS_ENABLED", "1")


def telegram_enabled() -> bool:
    # Outward-facing (broadcasts to the Telegram channel). Enabled by owner
    # request; set PIL_TELEGRAM_ENABLED=0 to silence.
    return _flag("PIL_TELEGRAM_ENABLED", "1")


# ── Capital model (accounting layer) ─────────────────────────────────────────
# Independent configurable initial capital per book. Defaults: ₹10L / ₹10L / ₹5L.
# DB overrides (pil_config: capital.SWING etc.) take precedence over env.

_CAPITAL_ENV = {
    "SWING": "PIL_CAPITAL_SWING",
    "LONGTERM": "PIL_CAPITAL_LONGTERM",
    "MOMENTUM": "PIL_CAPITAL_MOMENTUM",
}
_CAPITAL_DEFAULT = {
    "SWING": 1_000_000.0,
    "LONGTERM": 1_000_000.0,
    "MOMENTUM": 500_000.0,
}

# Risk-free rate used for Sharpe/Sortino (annualised, decimal). India ~6.5%.
def risk_free_rate() -> float:
    return _f("PIL_RISK_FREE_RATE", 0.065)


def _db_overrides() -> dict[str, str]:
    """Best-effort read of runtime overrides; never raises (import-safe)."""
    try:
        from dashboard.backend.db.pil import get_all_config
        return get_all_config()
    except Exception:
        return {}


def book_capital(book: str) -> float:
    """Initial ₹ capital for a book. DB override > env > default."""
    book = book.upper()
    ov = _db_overrides().get(f"capital.{book}")
    if ov is not None:
        try:
            return float(ov)
        except (TypeError, ValueError):
            pass
    return _f(_CAPITAL_ENV.get(book, ""), _CAPITAL_DEFAULT.get(book, 0.0))


def all_book_capital() -> dict[str, float]:
    return {b: book_capital(b) for b in BOOKS}


def combined_capital() -> float:
    return sum(all_book_capital().values())


# ── Allocation targets (Part 5) ──────────────────────────────────────────────
# Default target weights across books. DB overrides (alloc.SWING ...) win.
_ALLOC_DEFAULT = {"SWING": 0.60, "LONGTERM": 0.25, "MOMENTUM": 0.15}


def allocation_targets() -> dict[str, float]:
    ov = _db_overrides()
    out: dict[str, float] = {}
    for b in BOOKS:
        raw = ov.get(f"alloc.{b}")
        if raw is not None:
            try:
                out[b] = float(raw)
                continue
            except (TypeError, ValueError):
                pass
        out[b] = _f(f"PIL_ALLOC_TARGET_{b}", _ALLOC_DEFAULT.get(b, 0.0))
    total = sum(out.values())
    if total > 0:  # normalise so weights sum to 1
        out = {b: round(v / total, 6) for b, v in out.items()}
    return out


# ── Exposure / risk thresholds (Parts 2, 10) ─────────────────────────────────

def thresholds() -> dict[str, float]:
    """Warning thresholds (fractions of the combined portfolio unless noted)."""
    return {
        "max_sector_share": _f("PIL_MAX_SECTOR_SHARE", 0.30),
        "max_single_stock": _f("PIL_MAX_SINGLE_STOCK", 0.10),
        "max_top10_share": _f("PIL_MAX_TOP10_SHARE", 0.60),
        "max_drawdown_warn": _f("PIL_MAX_DD_WARN", 0.15),   # |DD| warn level
        "min_diversification": _f("PIL_MIN_DIVERSIFICATION", 0.40),  # 0..1
        "max_capital_drift": _f("PIL_MAX_CAPITAL_DRIFT", 0.10),  # allocation drift
        "min_engine_expectancy": _f("PIL_MIN_ENGINE_EXPECTANCY", 0.0),  # R
        "max_correlation": _f("PIL_MAX_CORRELATION", 0.80),   # engine correlation spike
        "min_liquidity_cr": _f("PIL_MIN_LIQUIDITY_CR", 1.0),  # ₹Cr/day per name
        "max_momentum_alloc": _f("PIL_MAX_MOMENTUM_ALLOC", 0.25),
    }


def cfg() -> dict[str, Any]:
    """Full live snapshot — used verbatim in report/debug payloads."""
    return {
        "PIL_ENABLED": enabled(),
        "PIL_REPORTS_ENABLED": reports_enabled(),
        "PIL_ALERTS_ENABLED": alerts_enabled(),
        "PIL_TELEGRAM_ENABLED": telegram_enabled(),
        "capital": all_book_capital(),
        "combined_capital": combined_capital(),
        "allocation_targets": allocation_targets(),
        "risk_free_rate": risk_free_rate(),
        "thresholds": thresholds(),
    }
