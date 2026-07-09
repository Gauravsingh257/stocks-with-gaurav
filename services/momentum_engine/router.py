"""
services/momentum_engine/router.py
===================================
Phase 6 — the deterministic engine router.

SMC ALWAYS has first priority. That rule is enforced structurally: the Momentum
Engine only ever sees candidates the SMC materializer already rejected (the
candidate_feed consumes the SMC structural-reject stream), so a stock for which
SMC produced a valid actionable trade never reaches this engine.

The router's remaining, deterministic job is the REGIME gate: momentum
continuation only operates in the configured regimes (never in a downtrend). The
per-stock SMC-first guarantee + the portfolio-level regime gate together decide
when the momentum engine may act.
"""

from __future__ import annotations

import logging

from .config import cfg, regimes_allowed

log = logging.getLogger("services.momentum_engine.router")


def current_regime() -> str:
    """Best-effort market regime label. Returns 'UNKNOWN' if unavailable (which
    is treated as NOT allowed — fail safe: no momentum entries without a
    confirmed favourable regime)."""
    try:
        from services.market_regime import detect_regime
        r = detect_regime()
        return str(getattr(r, "regime", "UNKNOWN") or "UNKNOWN").upper()
    except Exception as exc:
        log.debug("regime detect failed: %s", exc)
        return "UNKNOWN"


def regime_allows(regime: str | None = None) -> tuple[bool, str]:
    """Deterministic: is momentum permitted in the current regime?"""
    c = cfg()
    if not c["MOMENTUM_REGIME_GATE_ENABLED"]:
        return True, (regime or "GATE_OFF")
    reg = (regime or current_regime()).upper()
    return (reg in regimes_allowed()), reg
