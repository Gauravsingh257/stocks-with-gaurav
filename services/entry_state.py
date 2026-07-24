"""
services/entry_state.py

Single, reusable entry-timing classifier (PR3): maps a recommendation's current
price vs its planned entry into one of four honest states, so the UI stops
showing a stock as "actionable" after it has already run.

  READY      price is in the entry zone — take it now.       (actionable)
  WATCH      price is approaching / awaiting a breakout.      (actionable)
  IN_MOTION  price already moved in our favour past entry.    (not actionable)
  MISSED     price ran too far past entry / toward target.    (not actionable)

Only READY and WATCH are actionable. Consolidates the ad-hoc `action_tag` /
reachability logic that was scattered across the research route, and is unit-
tested. Direction is inferred from geometry (long if the final target is above
entry), so it is correct for the long-only book and safe for any short data.

All thresholds are env-tunable; defaults mirror the historical reachability
bands so behaviour is familiar.
"""

from __future__ import annotations

import os

READY = "READY"
WATCH = "WATCH"
IN_MOTION = "IN_MOTION"
MISSED = "MISSED"

_ACTIONABLE = {READY, WATCH}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def is_actionable(state: str) -> bool:
    return state in _ACTIONABLE


def classify_entry_state(
    cmp: float | None,
    entry: float | None,
    stop: float | None = None,
    targets: list[float] | None = None,
    entry_type: str = "MARKET",
) -> dict:
    """Return {state, actionable, entry_gap_pct, favorable_move_r, progress_pct}.

    Thresholds (env-tunable):
      ENTRY_READY_ABOVE_PCT  (3.0)   CMP up to this % above entry still = READY
      ENTRY_READY_BELOW_PCT  (5.0)   CMP down to this % below entry still = READY
      ENTRY_INMOTION_R       (0.5)   favourable move (in R) past this = IN_MOTION
      ENTRY_MISSED_PROGRESS  (0.30)  fraction of the way to target past this = MISSED
      ENTRY_MISSED_GAP_PCT   (15.0)  CMP this % beyond entry (toward target) = MISSED
    """
    try:
        cmp = float(cmp) if cmp is not None else None
        entry = float(entry) if entry is not None else None
    except (TypeError, ValueError):
        cmp = entry = None
    if not cmp or not entry or entry <= 0:
        return {"state": WATCH, "actionable": True, "entry_gap_pct": None,
                "favorable_move_r": None, "progress_pct": None}

    tgts = [float(t) for t in (targets or []) if t is not None]
    final_target = (max(tgts) if tgts else None)
    is_long = (final_target is None) or (final_target >= entry)

    ready_above = _f("ENTRY_READY_ABOVE_PCT", 3.0)
    ready_below = _f("ENTRY_READY_BELOW_PCT", 5.0)
    inmotion_r = _f("ENTRY_INMOTION_R", 0.5)
    missed_prog = _f("ENTRY_MISSED_PROGRESS", 0.30)
    missed_gap = _f("ENTRY_MISSED_GAP_PCT", 15.0)

    # gap = signed % of CMP vs entry, oriented so +ve = "in favour / past entry".
    raw_gap = (cmp - entry) / entry * 100.0
    gap = raw_gap if is_long else -raw_gap

    risk = abs(entry - float(stop)) if stop else 0.0
    fav_r = None
    if risk > 0:
        fav_r = ((cmp - entry) / risk) if is_long else ((entry - cmp) / risk)

    progress = None
    if final_target is not None and abs(final_target - entry) > 0:
        progress = (cmp - entry) / (final_target - entry) if is_long else (entry - cmp) / (entry - final_target)

    # Decision order: MISSED → IN_MOTION → READY → WATCH.
    if (progress is not None and progress > missed_prog) or gap > missed_gap:
        state = MISSED
    elif fav_r is not None and fav_r > inmotion_r:
        state = IN_MOTION
    elif -ready_below <= gap <= ready_above:
        state = READY
    else:
        state = WATCH

    return {
        "state": state,
        "actionable": state in _ACTIONABLE,
        "entry_gap_pct": round(raw_gap, 2),
        "favorable_move_r": round(fav_r, 2) if fav_r is not None else None,
        "progress_pct": round(progress * 100, 1) if progress is not None else None,
    }
