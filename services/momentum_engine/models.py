"""
services/momentum_engine/models.py
==================================
Immutable data models passed between the Momentum Engine stages. Kept as plain
dataclasses (no behaviour, no I/O) so every stage is pure and unit-testable and
the whole decision is trivially serialisable for the audit log.

Stage flow:
    MomentumCandidate            (Phase 2: from the SMC-reject stream)
      -> EligibilityResult       (Phase 3)
      -> EntrySignal             (Phase 4)
      -> MomentumQualityScore    (Phase 5)
      -> MomentumDecision        (final, logged)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(slots=True, frozen=True)
class MomentumCandidate:
    """A stock Discovery liked that SMC rejected for a STRUCTURAL reason
    (no_BOS / no_liquidity_sweep / no_order_block) — the only source the
    Momentum Engine is allowed to consume."""
    symbol: str
    horizon: str
    scan_date: str
    cmp: float
    discovery_score: float | None = None
    breakout_score: float | None = None
    smc_rejection_reasons: tuple[str, ...] = field(default_factory=tuple)
    source: str = "smc_reject_stream"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class EligibilityResult:
    passed: bool
    failures: tuple[str, ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failures": list(self.failures), "metrics": self.metrics}


@dataclass(slots=True, frozen=True)
class EntrySignal:
    """The concrete plan produced by one entry model. `trigger` is an arm-on-tap
    breakout level (LIMIT/stop-buy above the base); `stop` is the structural
    invalidation; `base_low` anchors trailing + failed-breakout logic."""
    model: str                 # "vcp" | "breakout" | "shallow_pullback"
    trigger: float
    stop: float
    base_low: float
    base_high: float
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class MomentumQualityScore:
    score: float               # 0..100 (post-penalty, clamped)
    components: dict[str, float]
    penalties: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class MomentumDecision:
    """The full, logged outcome for one candidate. `stage` records how far it got
    so the audit trail explains every accept/reject."""
    symbol: str
    horizon: str
    accepted: bool
    stage: str                 # "router" | "eligibility" | "entry" | "ranked"
    reason: str
    quality_score: float | None = None
    entry: dict[str, Any] | None = None
    eligibility: dict[str, Any] | None = None
    ranking: dict[str, Any] | None = None
    regime: str | None = None
    flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
