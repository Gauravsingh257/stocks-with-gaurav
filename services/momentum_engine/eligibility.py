"""
services/momentum_engine/eligibility.py
=========================================
Phase 3 — hard eligibility gates. Given the metrics dict (from metrics.py) and
the live config, decide whether a candidate is a tradeable momentum LEADER (not
a chase, not a chop, not a trap). Pure + fully configurable.

Order matters only for readability; ALL failing gates are reported so the audit
log explains exactly why a name was dropped. A single failure => not eligible.
"""

from __future__ import annotations

from typing import Any

from .config import cfg
from .models import EligibilityResult


def evaluate(metrics: dict[str, Any]) -> EligibilityResult:
    c = cfg()
    f: list[str] = []
    m = metrics

    # Leadership: relative strength vs NIFTY.
    rs = m.get("rs_20d")
    if rs is None:
        f.append("rs_unknown")
    elif rs < c["MOM_MIN_RS_20D"]:
        f.append(f"rs_below_min({rs:.1f}<{c['MOM_MIN_RS_20D']:.0f})")

    # Primary uptrend.
    if c["MOM_REQUIRE_ABOVE_200DMA"] and not m.get("above_200dma"):
        f.append("below_200dma")

    # Liquidity + price floor (momentum needs depth; no microcap traps).
    if m.get("turnover_cr") is not None and m["turnover_cr"] < c["MOM_MIN_TURNOVER_CR"]:
        f.append(f"illiquid({m['turnover_cr']:.1f}Cr<{c['MOM_MIN_TURNOVER_CR']:.0f})")
    if m.get("last", 0) < c["MOM_MIN_PRICE"]:
        f.append("below_min_price")

    # Trend quality (orderly, not erratic).
    if m.get("trend_quality", 0) < c["MOM_MIN_TREND_QUALITY"]:
        f.append(f"low_trend_quality({m.get('trend_quality')}<{c['MOM_MIN_TREND_QUALITY']})")

    # Extension / anti-chase: not already stretched far above EMA20.
    if m.get("extension_atr", 0) > c["MOM_MAX_EXTENSION_ATR"]:
        f.append(f"over_extended_atr({m['extension_atr']:.1f}>{c['MOM_MAX_EXTENSION_ATR']:.0f})")
    if m.get("extension_pct", 0) > c["MOM_MAX_EXTENSION_PCT"]:
        f.append(f"over_extended_pct({m['extension_pct']:.1f}>{c['MOM_MAX_EXTENSION_PCT']:.0f})")

    # Climax / exhaustion.
    if m.get("consecutive_up_days", 0) >= c["MOM_CLIMAX_MAX_UP_DAYS"]:
        f.append(f"climax_up_days({m['consecutive_up_days']}>= {c['MOM_CLIMAX_MAX_UP_DAYS']})")
    ap = m.get("atr_percentile")
    if ap is not None and ap >= c["MOM_CLIMAX_ATR_PCTL"]:
        f.append(f"volatility_exhaustion(atr_pctl={ap})")

    # Gap trap.
    if abs(m.get("gap_pct", 0)) > c["MOM_MAX_GAP_PCT"]:
        f.append(f"gap_trap({m['gap_pct']:.1f}%)")

    return EligibilityResult(
        passed=(len(f) == 0),
        failures=tuple(f),
        metrics={k: m.get(k) for k in (
            "rs_20d", "above_200dma", "turnover_cr", "trend_quality",
            "extension_atr", "extension_pct", "consecutive_up_days",
            "atr_percentile", "gap_pct", "volume_ratio", "pos52",
        )},
    )
