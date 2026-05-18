"""
Setup-A graded quality scorer  —  Step 0 of the Setup-A improvement plan.

WHY THIS EXISTS
---------------
The improvement audit found Setup-A is a "parts detector" (bias/OB/FVG present)
rather than a "story validator" (liquidity swept -> structure reclaimed ->
enter at origin -> target next liquidity). The naive fix — stacking hard
AND-gates — multiplicatively collapses signal count (5 filters @ ~60% each
=> ~8% pass). That is the user's explicit, mathematically-correct concern.

THE DESIGN ANSWER (anti-starvation, by construction)
----------------------------------------------------
1. ADDITIVE score, never multiplicative gates. A setup missing one factor
   still fires if the rest are strong. Filters multiply (collapse); scores
   add (degrade gracefully).
2. ONE tunable threshold = the frequency dial. Fire when score >= MIN_SCORE.
   It is a smooth slider, not a cliff — and env-tunable with no redeploy.
3. TIERED output, never zero: A+/A -> high-conviction, B -> watchlist
   (near-miss), C -> discovery only. The funnel can never go silent.
4. Pure function. No I/O, no globals, no engine imports, no side effects.
   Not wired into the live path by anyone yet — importing this file changes
   nothing. Deterministic and unit-testable.

This module is INERT until Step 2 wires it in behind a default-OFF flag.
Nothing here touches the live index engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Optional

# --- Factor weights (sum = 100). Tunable later via env if needed; the point
# is the score is ADDITIVE, so removing any one factor degrades gracefully
# instead of zeroing the signal. -----------------------------------------
W_SWEEP_RECLAIM = 25   # sell/buy-side liquidity swept AND reclaimed (the story)
W_ORIGIN_OB = 20       # entry zone is the OB that BROKE structure, not nearest
W_FVG = 15             # a valid displacement FVG is present
W_REGIME = 15          # market regime / HTF bias aligned with the trade
W_RR = 15              # realistic reward:risk (graded, not a hard >=2 gate)
W_ENTRY_DISTANCE = 10  # CMP still close enough that the entry can realistically fill

TIER_CUTOFFS = (
    ("A+", 85),
    ("A", 70),
    ("B", 55),
    ("C", 40),
)  # below the lowest cutoff => "reject"


def _min_score() -> int:
    """The single frequency dial. Env-tunable, no redeploy. Default 70 (= 'A')."""
    try:
        v = int(os.getenv("SETUP_A_MIN_SCORE", "70"))
    except (TypeError, ValueError):
        v = 70
    return max(0, min(100, v))


def _near_miss_band() -> int:
    """Setups within this many points BELOW MIN_SCORE are surfaced as
    watchlist instead of discarded — the frequency floor / safety net so
    the funnel is never empty."""
    try:
        return max(0, int(os.getenv("SETUP_A_NEAR_MISS_BAND", "12")))
    except (TypeError, ValueError):
        return 12


@dataclass(slots=True)
class SetupAFeatures:
    """Real, in-engine-computable inputs (no hindsight, no look-ahead)."""
    sweep_detected: bool = False          # liquidity_engine.detect_liquidity_sweep
    sweep_reclaimed: bool = False         # price closed back through the swept level
    ob_is_origin: bool = False            # the OB initiated the structure break
    fvg_present: bool = False             # valid displacement FVG
    regime_aligned: bool = False          # MARKET_REGIME / HTF bias agrees
    rr: float = 0.0                       # reward:risk of the planned trade
    dist_from_cmp_atr: Optional[float] = None  # |cmp - entry| / ATR (fill realism)


@dataclass(slots=True)
class QualityResult:
    score: int
    tier: str                  # A+/A/B/C/reject
    fires: bool                # score >= MIN_SCORE  (auto-eligible)
    watchlist: bool            # near-miss band      (surfaced, not traded)
    min_score: int
    breakdown: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _rr_points(rr: float) -> float:
    """Graded RR contribution — NOT a hard rr>=2 gate (that gate alone is a
    big source of starvation). Full marks at 2.5R, partial below, never
    negative."""
    if rr <= 0:
        return 0.0
    if rr >= 2.5:
        return W_RR
    if rr >= 2.0:
        return W_RR * 0.85
    if rr >= 1.5:
        return W_RR * 0.6
    if rr >= 1.2:
        return W_RR * 0.35
    return W_RR * 0.15


def _distance_points(dist_atr: Optional[float]) -> float:
    """Closer CMP -> more likely the planned entry fills. Graded, not binary.
    Unknown distance is treated neutrally (half marks) so missing data does
    not silently starve the score."""
    if dist_atr is None:
        return W_ENTRY_DISTANCE * 0.5
    if dist_atr <= 0.5:
        return W_ENTRY_DISTANCE
    if dist_atr <= 1.0:
        return W_ENTRY_DISTANCE * 0.75
    if dist_atr <= 2.0:
        return W_ENTRY_DISTANCE * 0.45
    if dist_atr <= 3.0:
        return W_ENTRY_DISTANCE * 0.2
    return 0.0


def score_setup_a(features: SetupAFeatures) -> QualityResult:
    """Pure graded scorer. Additive by construction so it can never collapse
    signal count the way stacked hard gates do."""
    b: dict[str, float] = {}
    reasons: list[str] = []

    # Sweep + reclaim — the strongest single signal of the A+ story. Half
    # credit for a bare sweep without reclaim (still informative).
    if features.sweep_detected and features.sweep_reclaimed:
        b["sweep_reclaim"] = W_SWEEP_RECLAIM
        reasons.append("liquidity swept + reclaimed")
    elif features.sweep_detected:
        b["sweep_reclaim"] = W_SWEEP_RECLAIM * 0.5
        reasons.append("liquidity swept (no reclaim yet)")
    else:
        b["sweep_reclaim"] = 0.0

    b["origin_ob"] = W_ORIGIN_OB if features.ob_is_origin else 0.0
    if features.ob_is_origin:
        reasons.append("entry at structure-origin OB")

    b["fvg"] = W_FVG if features.fvg_present else 0.0
    if features.fvg_present:
        reasons.append("displacement FVG present")

    b["regime"] = W_REGIME if features.regime_aligned else 0.0
    if features.regime_aligned:
        reasons.append("regime/HTF aligned")

    b["rr"] = _rr_points(features.rr)
    b["entry_distance"] = _distance_points(features.dist_from_cmp_atr)

    score = int(round(sum(b.values())))

    tier = "reject"
    for name, cut in TIER_CUTOFFS:
        if score >= cut:
            tier = name
            break

    mn = _min_score()
    fires = score >= mn
    watchlist = (not fires) and (score >= mn - _near_miss_band())

    return QualityResult(
        score=score,
        tier=tier,
        fires=fires,
        watchlist=watchlist,
        min_score=mn,
        breakdown={k: round(v, 1) for k, v in b.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    # Self-test: demonstrate the frequency DIAL — proving the user's concern
    # ("too strict => zero trades") is a smooth, tunable knob, not a cliff.
    samples = [
        ("A+ textbook", SetupAFeatures(True, True, True, True, True, 2.6, 0.4)),
        ("strong, no reclaim", SetupAFeatures(True, False, True, True, True, 2.2, 0.8)),
        ("origin OB, no sweep", SetupAFeatures(False, False, True, True, True, 2.1, 0.6)),
        ("mid-rally FVG (the 23,499)", SetupAFeatures(False, False, False, True, True, 1.9, 2.4)),
        ("decent trend cont.", SetupAFeatures(False, False, True, True, True, 1.6, 1.2)),
        ("weak / chop", SetupAFeatures(False, False, False, False, False, 1.1, 3.5)),
        ("sweep only", SetupAFeatures(True, True, False, False, True, 1.4, 1.0)),
        ("good RR, far entry", SetupAFeatures(True, True, True, True, True, 3.0, 4.0)),
    ]
    for thr in (85, 70, 55, 40):
        os.environ["SETUP_A_MIN_SCORE"] = str(thr)
        fired = [n for n, f in samples if score_setup_a(f).fires]
        watch = [n for n, f in samples if score_setup_a(f).watchlist]
        print(f"MIN_SCORE={thr:>3}: fires {len(fired)}/{len(samples)} "
              f"watch {len(watch)}  -> {fired}")
    os.environ.pop("SETUP_A_MIN_SCORE", None)
    print("\nPer-sample detail @ default:")
    for n, f in samples:
        r = score_setup_a(f)
        print(f"  {n:<28} score={r.score:>3} tier={r.tier:<6} "
              f"fires={r.fires} watch={r.watchlist}")
