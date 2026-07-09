"""
services/momentum_engine/entry_models.py
==========================================
Phase 4 — the approved momentum entry models, behind a REGISTRY so future models
can be added without editing this file's existing logic (open/closed principle):

    @register("my_model")
    def my_model(metrics, candles, c) -> EntrySignal | None: ...

Priority order (config MOM_ENTRY_MODELS): vcp > breakout > shallow_pullback.
Each model returns an arm-on-tap plan (trigger above the base, structural stop)
or None. Shared gates (volume confirmation, base-proximity / anti-FOMO) are
applied centrally in detect_entry so every model inherits them.

Rejected-by-design ideas (earnings continuation, trend-acceleration, standalone
RS/volume) are intentionally NOT implemented.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .config import cfg, entry_models_enabled
from .models import EntrySignal

log = logging.getLogger("services.momentum_engine.entry_models")

_REGISTRY: dict[str, Callable[[dict, list[dict], dict], EntrySignal | None]] = {}


def register(name: str):
    def deco(fn):
        _REGISTRY[name.lower()] = fn
        return fn
    return deco


def _swing_low(candles: list[dict], lookback: int) -> float:
    win = candles[-lookback:] if len(candles) >= lookback else candles
    return min(float(c["low"]) for c in win)


@register("vcp")
def vcp(metrics: dict[str, Any], candles: list[dict], c: dict) -> EntrySignal | None:
    """Volatility-Contraction breakout: a TIGHT base near highs breaking out.
    The highest-quality, lowest-chase continuation."""
    base_atr = metrics.get("base_atr_pct")
    if base_atr is None or base_atr > c["MOM_VCP_MAX_BASE_ATR_PCT"]:
        return None  # base not tight enough
    base_high = metrics["base_high"]; base_low = metrics["base_low"]
    last = metrics["last"]
    # Price must be pressing the top of the tight base (not deep inside it).
    if last < base_high * 0.98:
        return None
    trigger = round(base_high * (1 + c["MOM_TRIGGER_BUFFER_PCT"] / 100.0), 2)
    return EntrySignal(
        model="vcp", trigger=trigger, stop=round(base_low, 2),
        base_low=base_low, base_high=base_high,
        reason=f"VCP: tight base ATR%={base_atr} breaking ₹{base_high:.2f}",
        metrics={"base_atr_pct": base_atr},
    )


@register("breakout")
def breakout(metrics: dict[str, Any], candles: list[dict], c: dict) -> EntrySignal | None:
    """Breakout continuation: reclaiming/breaking the N-day pivot high."""
    pivot = metrics.get("pivot_50") or metrics["base_high"]
    last = metrics["last"]
    if last < pivot * 0.98:
        return None  # not at the pivot yet
    stop = round(_swing_low(candles, 10), 2)
    if stop >= last:
        return None
    trigger = round(pivot * (1 + c["MOM_TRIGGER_BUFFER_PCT"] / 100.0), 2)
    return EntrySignal(
        model="breakout", trigger=trigger, stop=stop,
        base_low=metrics["base_low"], base_high=metrics["base_high"],
        reason=f"Breakout: {c['MOM_BREAKOUT_LOOKBACK']}d pivot ₹{pivot:.2f} reclaim",
        metrics={"pivot": pivot},
    )


@register("shallow_pullback")
def shallow_pullback(metrics: dict[str, Any], candles: list[dict], c: dict) -> EntrySignal | None:
    """Shallow-pullback continuation: a leader that dipped only a few % to the
    20-EMA (too shallow for SMC's deep OB/FVG) and is reclaiming."""
    ema20 = metrics.get("ema20")
    if not ema20:
        return None
    base_high = metrics["base_high"]; last = metrics["last"]
    dip_pct = (base_high - last) / base_high * 100.0 if base_high > 0 else 0.0
    if dip_pct <= 0 or dip_pct > c["MOM_SHALLOW_PULLBACK_MAX_PCT"]:
        return None  # not a shallow pullback (either extended or too deep → SMC's job)
    # Must be holding above / reclaiming the 20-EMA.
    if last < ema20 * 0.99:
        return None
    stop = round(min(_swing_low(candles, 8), ema20 * 0.98), 2)
    if stop >= last:
        return None
    trigger = round(base_high * (1 + c["MOM_TRIGGER_BUFFER_PCT"] / 100.0), 2)
    return EntrySignal(
        model="shallow_pullback", trigger=trigger, stop=stop,
        base_low=metrics["base_low"], base_high=base_high,
        reason=f"Shallow pullback {dip_pct:.1f}% to 20-EMA, reclaim ₹{base_high:.2f}",
        metrics={"dip_pct": round(dip_pct, 2)},
    )


def detect_entry(metrics: dict[str, Any], candles: list[dict]) -> EntrySignal | None:
    """Try enabled models in priority order; return the first valid plan that
    also clears the shared volume + base-proximity gates. Deterministic."""
    c = cfg()
    for name in entry_models_enabled():
        fn = _REGISTRY.get(name)
        if not fn:
            continue
        try:
            sig = fn(metrics, candles, c)
        except Exception as exc:
            log.debug("entry model %s failed: %s", name, exc)
            continue
        if not sig:
            continue
        # Shared gate 1 — volume confirmation.
        if metrics.get("volume_ratio", 0) < c["MOM_MIN_VOLUME_RATIO"]:
            continue
        # Shared gate 2 — base proximity / anti-FOMO: CMP must be near the
        # trigger, not already run far past it.
        prox = (metrics["last"] - sig.trigger) / sig.trigger * 100.0
        if prox > c["MOM_MAX_BASE_PROXIMITY_PCT"]:
            continue
        return sig
    return None


def available_models() -> list[str]:
    return sorted(_REGISTRY.keys())
