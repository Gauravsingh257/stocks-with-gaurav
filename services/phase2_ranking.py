"""
services/phase2_ranking.py — PHASE 2: SMC becomes a ranking factor, not a gate.

WHY
---
The Phase 1/2 research measured, on 213,512 forward-labelled rows, that Layer 3
(SMC structure) as a HARD GATE costs the book: it discards ~27% of the surviving
SWING candidates and the ones it keeps do no better. Layer 1 carries essentially
all of the edge, and every reasonable ranking of the L1+L2 pool beat the gate on
20d, 60d and benchmark excess at an identical daily opportunity count.

So SMC stops rejecting stocks and starts ordering them.

THE WEIGHTS, AND WHY THEY ARE NOT THE SWEEP WINNER
--------------------------------------------------
A budget-matched sweep of eight weightings was bootstrapped over the 43 paired
scan days that carry outcomes. The result that matters is the one that says
*don't optimise*: every variant's interval against every other variant spans
zero, and no variant beat the current gate at 95% confidence either. The ranking
of the leaderboard is noise at this sample size.

Picking the top scorer would therefore be fitting 43 days. The defaults below are
the PRE-REGISTERED weights — chosen before the sweep ran, tied with the best on
every metric — so the choice carries no selection bias. Every weight is
env-tunable so they can be recalibrated on evidence later without a redeploy.

One directional finding worth carrying forward (not acted on here): across the
sweep, more SMC weight tracked slightly WORSE outcomes (SMC 0.40 was the weakest
variant on both 20d and excess). If that survives a larger sample, the right move
is to lower `PHASE2_W_SMC`, not to re-gate.

Flag: PHASE2_SMC_AS_SCORE (default OFF ⟹ the L3 hard gate behaves exactly as now).
"""

from __future__ import annotations

import os

# Factor -> default weight. Pre-registered; see the module docstring.
DEFAULT_WEIGHTS: dict[str, float] = {
    "momentum20": 0.35,
    "smc": 0.25,
    "quality": 0.20,
    "momentum50": 0.20,
}

_ENV = {
    "momentum20": "PHASE2_W_MOM20",
    "smc": "PHASE2_W_SMC",
    "quality": "PHASE2_W_QUALITY",
    "momentum50": "PHASE2_W_MOM50",
}


def smc_as_score_enabled() -> bool:
    """Master flag. OFF (default) ⟹ Layer 3 stays a hard gate, byte-identical."""
    return os.getenv("PHASE2_SMC_AS_SCORE", "0").strip().lower() in ("1", "true", "yes", "on")


def weights() -> dict[str, float]:
    """Live weights, env-overridable. Normalised so any set sums to 1.0 and a
    partial override cannot silently rescale the whole score."""
    out: dict[str, float] = {}
    for key, default in DEFAULT_WEIGHTS.items():
        try:
            out[key] = float(os.getenv(_ENV[key], str(default)))
        except (TypeError, ValueError):
            out[key] = default
    total = sum(out.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in out.items()}


def _zscores(values: list[float | None]) -> list[float]:
    """Cross-sectional z-score for ONE scan, missing treated as the mean (0).

    Scored within the day, not against history, because the question a ranking
    answers is "which of today's candidates is strongest" — an absolute
    threshold would drift with the market and quietly re-become a gate.
    """
    present = [v for v in values if v is not None]
    n = len(present)
    if n < 2:
        return [0.0] * len(values)
    mean = sum(present) / n
    var = sum((v - mean) ** 2 for v in present) / n
    sd = var ** 0.5
    if sd <= 0:
        return [0.0] * len(values)
    return [0.0 if v is None else (v - mean) / sd for v in values]


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Score and order one scan's candidates. Highest score first.

    Each candidate needs: `key` (anything hashable), and any of
    `momentum20`, `momentum50`, `smc`, `quality` — a missing factor scores at
    the cohort mean rather than at zero, so a data gap neither rewards nor
    punishes. Returns new dicts with `score` and `components`, input untouched.
    """
    if not candidates:
        return []

    w = weights()
    cols = {
        f: _zscores([c.get(f) for c in candidates])
        for f in ("momentum20", "smc", "quality", "momentum50")
    }

    scored = []
    for i, c in enumerate(candidates):
        components = {f: round(cols[f][i], 4) for f in cols}
        score = sum(w[f] * components[f] for f in cols)
        scored.append({**c, "score": round(score, 6), "components": components,
                       "weights": {k: round(v, 4) for k, v in w.items()}})
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored


def select_top(candidates: list[dict], budget: int) -> list[dict]:
    """The top `budget` candidates by score.

    `budget` is deliberately supplied by the caller as the count the existing
    hard gate would have produced, so switching the flag changes WHICH stocks
    are chosen and never HOW MANY — the daily opportunity count, and therefore
    everything downstream that depends on it, is unchanged.
    """
    if budget <= 0:
        return []
    return rank_candidates(candidates)[:budget]
