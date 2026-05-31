"""
services/entry_trigger_service.py
=================================
The watchlist entry-trigger state machine (skeleton — PR-B).

Lifecycle (per user requirement):
    WAITING → APPROACHING → ACTIONABLE → ARMED → TRIGGERED → ACTIVE
                                              → EXPIRED
    ACTIVE → (shared PositionTrackingService) → TARGET_HIT / STOP_HIT → CLOSED

Design notes:
  - PAPER trigger: an ARMED idea whose CMP enters [entry_low, entry_high]
    goes to TRIGGERED and NOTIFIES — it does NOT create a position. The user
    confirms (Buy Now) or the user's auto_entry preference promotes it.
  - This module is store-agnostic: it computes states + decides transitions;
    actual promotion delegates to the existing per-user position creation
    (reused, not duplicated).
  - PR-B ships this INERT: tick() is flag-gated by WATCHLIST_MONITOR_ENABLED
    (default OFF) and is not yet wired into the tracker loop or any API. PR-C
    adds the APIs, notifications, and loop wiring. Kept here so the state
    logic is reviewable now.

Pure functions (compute_zone_state, size_position) are import-safe and unit
testable without a DB.
"""

from __future__ import annotations

import logging
import math
import os

log = logging.getLogger("services.entry_trigger")

# Bands (config-driven; mirror validation_engine reachability semantics).
APPROACH_PCT = float(os.getenv("WATCHLIST_APPROACH_PCT", "5.0"))   # within 5% below zone
MISSED_PCT = float(os.getenv("WATCHLIST_MISSED_PCT", "15.0"))      # >15% above zone


def compute_zone_state(cmp: float, entry_low: float, entry_high: float) -> str:
    """Price-derived state for an UNARMED idea. Pure."""
    if cmp <= 0 or entry_low <= 0:
        return "WAITING"
    if entry_low <= cmp <= entry_high:
        return "ACTIONABLE"
    if cmp > entry_high:
        gap = (cmp - entry_high) / entry_high * 100.0
        return "MISSED" if gap > MISSED_PCT else "ACTIONABLE"
    # cmp below the zone
    gap = (entry_low - cmp) / entry_low * 100.0
    return "APPROACHING" if gap <= APPROACH_PCT else "WAITING"


def in_entry_zone(cmp: float, entry_low: float, entry_high: float) -> bool:
    return cmp > 0 and entry_low <= cmp <= entry_high


def size_position(capital: float | None, risk_percent: float | None,
                  entry_ref: float, stop_loss: float) -> int:
    """Risk-based quantity: floor(capital * risk% / per-share-risk). Pure.
    Returns 0 when inputs are missing/invalid (never raises)."""
    try:
        if not capital or not risk_percent:
            return 0
        per_share_risk = abs(entry_ref - stop_loss)
        if per_share_risk <= 0:
            return 0
        risk_amount = capital * (risk_percent / 100.0)
        return max(0, math.floor(risk_amount / per_share_risk))
    except Exception:
        return 0


def _enabled() -> bool:
    return os.getenv("WATCHLIST_MONITOR_ENABLED", "0").strip().lower() in {"1", "true", "yes"}


class EntryTriggerService:
    """Skeleton. Will: recompute zone states, fire paper-triggers on armed
    ideas entering their zone (notify), honor auto_entry to promote, and expire
    ideas past valid_until. PR-C wires this into the tracker loop + APIs."""

    name = "entry_trigger"

    def enabled(self) -> bool:
        return _enabled()

    def tick(self) -> dict:
        if not self.enabled():
            return {"status": "disabled"}
        # PR-C: load armed/active watchlist rows, fetch CMP, run transitions,
        # notify, promote per auto_entry, expire by valid_until. Intentionally
        # not implemented in PR-B (skeleton).
        log.debug("EntryTriggerService.tick (skeleton no-op)")
        return {"status": "noop", "note": "skeleton — wired in PR-C"}
