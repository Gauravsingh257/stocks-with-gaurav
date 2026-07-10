"""
services/pil/allocation.py
==========================
Capital allocation dashboard (Part 5). Compares the *current* capital split
across the three engines (from the reconstructed ledger portfolio values) against
configurable *target* weights, and computes drift, whether a rebalance is needed,
and the ₹ moves required to reach target.

Targets live in pil_config (alloc.SWING / alloc.LONGTERM / alloc.MOMENTUM) with
env/defaults as the fallback — editable at runtime, never touching an engine.
"""

from __future__ import annotations

from typing import Any

from services.pil import config as pil_config

_ENGINES = ("SWING", "LONGTERM", "MOMENTUM")


def compute(books: dict[str, dict]) -> dict[str, Any]:
    targets = pil_config.allocation_targets()
    th = pil_config.thresholds()
    max_drift = th["max_capital_drift"]

    values = {b: books.get(b, {}).get("portfolio_value", 0.0) for b in _ENGINES}
    total = sum(values.values()) or 1.0

    rows = []
    rebalance_needed = False
    for b in _ENGINES:
        cur_w = values[b] / total
        tgt_w = targets.get(b, 0.0)
        drift = cur_w - tgt_w
        target_value = tgt_w * total
        required_delta = target_value - values[b]  # +ve => add capital to this book
        if abs(drift) > max_drift:
            rebalance_needed = True
        rows.append({
            "book": b,
            "current_value": round(values[b], 2),
            "current_weight": round(cur_w, 4),
            "target_weight": round(tgt_w, 4),
            "deviation": round(drift, 4),
            "target_value": round(target_value, 2),
            "required_delta": round(required_delta, 2),
            "action": "ADD" if required_delta > 0 else ("TRIM" if required_delta < 0 else "HOLD"),
        })

    cash_to_add = round(sum(r["required_delta"] for r in rows if r["required_delta"] > 0), 2)
    # momentum over-allocation guard (Part 10 alert source)
    mom_w = next((r["current_weight"] for r in rows if r["book"] == "MOMENTUM"), 0.0)
    warnings = []
    if mom_w > th["max_momentum_alloc"]:
        warnings.append({"type": "MOMENTUM_ALLOC_HIGH", "severity": "WARN",
                         "message": f"Momentum allocation {mom_w*100:.0f}% exceeds cap {th['max_momentum_alloc']*100:.0f}%",
                         "value": round(mom_w, 4), "threshold": th["max_momentum_alloc"]})
    for r in rows:
        if abs(r["deviation"]) > max_drift:
            warnings.append({"type": "CAPITAL_DRIFT", "severity": "INFO",
                             "message": f"{r['book']} drifted {r['deviation']*100:+.0f}% from target",
                             "value": round(abs(r["deviation"]), 4), "threshold": max_drift})

    return {
        "total_value": round(total, 2),
        "targets": targets,
        "rows": rows,
        "rebalance_needed": rebalance_needed,
        "cash_required_to_rebalance": cash_to_add,
        "max_drift": max_drift,
        "warnings": warnings,
    }


def set_targets(weights: dict[str, float]) -> dict[str, Any]:
    """Persist target weights (normalised). Returns the recomputed allocation."""
    from dashboard.backend.db import pil as pildb
    clean = {b.upper(): max(0.0, float(weights.get(b, weights.get(b.upper(), 0.0)))) for b in _ENGINES}
    total = sum(clean.values())
    if total <= 0:
        raise ValueError("target weights must sum to > 0")
    for b in _ENGINES:
        pildb.set_config(f"alloc.{b}", clean[b] / total)
    return pil_config.allocation_targets()
