"""
services/momentum_classifier.py
================================
Phase-B portfolio-quality intelligence for the Momentum Portfolio:

  * classify(score)            -> ELITE | GOOD | WEAK | REPLACE   (configurable)
  * portfolio_quality(rows)    -> aggregate quality with a sector-concentration
                                  (diversification) penalty
  * best_replacement_target(candidate, active)
                               -> which ACTIVE holding, if any, to displace to
                                  MAXIMISE overall portfolio quality — considering
                                  score, sector diversification, and regime — not
                                  merely the lowest score. Returns None if no
                                  replacement improves the book by the configured
                                  margin (so the book is never churned pointlessly
                                  and never force-filled).

Pure functions (no I/O) → unit-testable. Diversification is used as the
production-tractable proxy for cross-holding correlation.
"""

from __future__ import annotations

from typing import Any

from services.momentum_engine.config import cfg


def classify(score: float | None) -> str:
    c = cfg()
    s = float(score or 0)
    if s >= c["MOM_CLASS_ELITE"]:
        return "ELITE"
    if s >= c["MOM_CLASS_GOOD"]:
        return "GOOD"
    if s >= c["MOM_CLASS_WEAK"]:
        return "WEAK"
    return "REPLACE"


def _sector_shares(rows: list[dict]) -> dict[str, float]:
    n = len(rows)
    if n == 0:
        return {}
    counts: dict[str, float] = {}
    for r in rows:
        sec = (r.get("sector") or "Others")
        counts[sec] = counts.get(sec, 0) + 1
    return {k: v / n for k, v in counts.items()}


def portfolio_quality(rows: list[dict]) -> dict[str, Any]:
    """Mean holding score minus a penalty for sector over-concentration. Higher
    is a better-diversified, higher-quality book."""
    c = cfg()
    active = [r for r in rows if r.get("status") == "ACTIVE"] or rows
    if not active:
        return {"quality": 0.0, "avg_score": 0.0, "sector_penalty": 0.0,
                "max_sector_share": 0.0, "n": 0, "sector_shares": {}}
    avg = sum(float(r.get("quality_score") or 0) for r in active) / len(active)
    shares = _sector_shares(active)
    max_share = max(shares.values()) if shares else 0.0
    over = max(0.0, max_share - c["MOM_MAX_SECTOR_SHARE"])
    penalty = over * c["MOM_SECTOR_CONCENTRATION_PENALTY"]  # excess share × penalty-per-unit
    return {"quality": round(avg - penalty, 2), "avg_score": round(avg, 2),
            "sector_penalty": round(penalty, 2), "max_sector_share": round(max_share, 2),
            "n": len(active), "sector_shares": {k: round(v, 2) for k, v in shares.items()}}


def best_replacement_target(candidate: dict, active: list[dict]) -> dict | None:
    """Return the ACTIVE holding to displace (or None). Chooses the swap that
    yields the greatest improvement in `portfolio_quality`, and only if that gain
    clears MOM_REPLACE_MIN_QUALITY_GAIN. Naturally prefers removing weak scores
    AND over-concentrated sectors."""
    c = cfg()
    active = [r for r in active if r.get("status") == "ACTIVE"]
    if not active:
        return None
    cand_row = {"quality_score": float(candidate.get("quality_score") or 0),
                "sector": candidate.get("sector") or "Others", "status": "ACTIVE"}
    base_q = portfolio_quality(active)["quality"]
    best = None
    best_gain = c["MOM_REPLACE_MIN_QUALITY_GAIN"]
    for h in active:
        swapped = [r for r in active if r["id"] != h["id"]] + [cand_row]
        gain = portfolio_quality(swapped)["quality"] - base_q
        if gain > best_gain:
            best_gain = gain
            best = h
    if best is not None:
        best = dict(best)
        best["_quality_gain"] = round(best_gain, 2)
    return best
