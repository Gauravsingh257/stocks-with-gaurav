"""
services/momentum_engine/research/trailing.py
==============================================
Trailing-stop methodologies for the backtest, behind a registry. A trail may
only RAISE the stop, never lower it (enforced by the simulator).

Signature: fn(bars, atr, params) -> proposed_stop | None
`bars` is the list of candles from entry up to (and including) the current bar.
"""

from __future__ import annotations

from typing import Callable

_TRAILS: dict[str, Callable[..., float | None]] = {}


def register(name: str):
    def deco(fn):
        _TRAILS[name] = fn
        return fn
    return deco


@register("none")
def none(bars: list[dict], atr: float, params: dict) -> float | None:
    return None


@register("atr_chandelier")
def atr_chandelier(bars: list[dict], atr: float, params: dict) -> float | None:
    k = float(params.get("k", 3.0))
    hh = max(float(b["high"]) for b in bars)
    return hh - k * atr


@register("ema")
def ema(bars: list[dict], atr: float, params: dict) -> float | None:
    n = int(params.get("n", 20))
    closes = [float(b["close"]) for b in bars]
    if len(closes) < n:
        return None
    kk = 2.0 / (n + 1.0)
    e = sum(closes[:n]) / n
    for v in closes[n:]:
        e = v * kk + e * (1 - kk)
    buf = float(params.get("buffer_pct", 1.0)) / 100.0
    return e * (1.0 - buf)


@register("structure")
def structure(bars: list[dict], atr: float, params: dict) -> float | None:
    """Trail to the most recent confirmed higher-low (swing low)."""
    look = int(params.get("lookback", 10))
    win = bars[-look:] if len(bars) >= look else bars
    if len(win) < 3:
        return None
    return min(float(b["low"]) for b in win)


def trail_stop(method: str, bars: list[dict], atr: float, params: dict) -> float | None:
    fn = _TRAILS.get(method, none)
    return fn(bars, atr, params or {})


def available() -> list[str]:
    return sorted(_TRAILS.keys())
