"""
services/momentum_engine
========================
Momentum Continuation Engine — a first-class, independent subsystem that
complements (never replaces) the production SMC Pullback Swing Engine.

It harvests the one proven gap in the SMC book: strong leaders that keep moving
without offering a pullback (e.g. LODHA). It consumes ONLY the SMC structural-
reject stream, is gated by market regime, ranks candidates by a transparent
Momentum Quality Score, and is INERT by default (MOMENTUM_ENGINE_ENABLED=0,
MOMENTUM_SHADOW_ONLY=1) so it cannot affect production until explicitly enabled.

Public interface (stable):
    from services.momentum_engine import evaluate_candidate, run, cfg
    from services.momentum_engine.models import MomentumCandidate, MomentumDecision

See docs/MOMENTUM_ENGINE_DESIGN.md for the full architecture.
"""

from __future__ import annotations

from .config import cfg, entry_models_enabled, regimes_allowed
from .engine import evaluate_candidate, run
from .models import (
    MomentumCandidate, EligibilityResult, EntrySignal,
    MomentumQualityScore, MomentumDecision,
)

__all__ = [
    "cfg", "entry_models_enabled", "regimes_allowed",
    "evaluate_candidate", "run",
    "MomentumCandidate", "EligibilityResult", "EntrySignal",
    "MomentumQualityScore", "MomentumDecision",
]
