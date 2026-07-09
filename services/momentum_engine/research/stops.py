"""
services/momentum_engine/research/stops.py
============================================
Initial-stop methodologies for the backtest, behind a registry so each is
independently measurable and new ones add without editing existing logic.

Signature: fn(fill, base_low, atr, params) -> stop_price (below fill).
"""

from __future__ import annotations

from typing import Callable

_STOPS: dict[str, Callable[..., float]] = {}


def register(name: str):
    def deco(fn):
        _STOPS[name] = fn
        return fn
    return deco


@register("structural")
def structural(fill: float, base_low: float, atr: float, params: dict) -> float:
    """Stop at the breakout base low (the SMC-adjacent, structure-first choice)."""
    return base_low


@register("atr_multiple")
def atr_multiple(fill: float, base_low: float, atr: float, params: dict) -> float:
    k = float(params.get("k", 2.5))
    return fill - k * atr


@register("pct_cap")
def pct_cap(fill: float, base_low: float, atr: float, params: dict) -> float:
    pct = float(params.get("max_stop_pct", 8.0))
    return fill * (1.0 - pct / 100.0)


@register("hybrid")
def hybrid(fill: float, base_low: float, atr: float, params: dict) -> float:
    """Structure-based, floored by an ATR minimum and CAPPED by a max stop %
    (the risk_engine philosophy applied to momentum)."""
    k = float(params.get("k", 1.5))
    max_pct = float(params.get("max_stop_pct", 10.0))
    atr_floor = fill - k * atr
    stop = max(base_low, atr_floor)          # not tighter than ATR-min
    cap = fill * (1.0 - max_pct / 100.0)     # not wider than max %
    return max(stop, cap)


def initial_stop(method: str, fill: float, base_low: float, atr: float, params: dict) -> float:
    fn = _STOPS.get(method, structural)
    s = fn(fill, base_low, atr, params or {})
    return min(s, fill * 0.999)              # always below fill


def available() -> list[str]:
    return sorted(_STOPS.keys())
