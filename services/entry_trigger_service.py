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
        """One monitoring pass over all non-terminal watchlist ideas.

        Per idea:
          1. Expire if past valid_until → EXPIRED.
          2. Refresh CMP + recompute zone state (WAITING/APPROACHING/ACTIONABLE/
             MISSED) for non-armed ideas.
          3. If armed and CMP entered the zone → TRIGGERED (paper) + notify.
             Then, if the user's auto_entry is ON → promote immediately to a
             user_position (source=WATCHLIST_TRIGGER); else wait for Buy Now.
        Best-effort; never raises into the loop."""
        if not self.enabled():
            return {"status": "disabled"}
        try:
            from datetime import date
            from dashboard.backend.db.watchlist_monitor import (
                list_monitorable, update_watchlist_fields, log_watchlist_event,
                get_user_pref, promote_watchlist_to_user_position,
            )
            from services.trade_tracker import _fetch_cmp_batch, _is_market_hours

            if not _is_market_hours():
                return {"status": "off_hours"}

            ideas = list_monitorable()
            if not ideas:
                return {"status": "ok", "evaluated": 0}

            prices = _fetch_cmp_batch(list({i["symbol"] for i in ideas})) or {}
            today = date.today().isoformat()
            triggered = promoted = expired = 0

            for idea in ideas:
                wid = int(idea["id"]); uid = int(idea["user_id"])
                # 1) expiry
                vu = idea.get("valid_until")
                if vu and str(vu)[:10] < today and not idea.get("triggered"):
                    update_watchlist_fields(wid, status="EXPIRED")
                    log_watchlist_event(wid, uid, "EXPIRED")
                    expired += 1
                    continue

                cmp = prices.get(idea["symbol"])
                if cmp is None:
                    continue
                lo = float(idea["entry_low"]); hi = float(idea["entry_high"])
                in_zone = in_entry_zone(cmp, lo, hi)
                armed = bool(idea.get("armed"))

                if armed and not idea.get("triggered") and in_zone:
                    # PAPER trigger — notify, do NOT auto-create (unless auto_entry)
                    update_watchlist_fields(wid, status="TRIGGERED", triggered=1,
                                            trigger_time=today, cmp=cmp)
                    log_watchlist_event(wid, uid, "TRIGGERED",
                                        notes=f"CMP {cmp} entered [{lo},{hi}]")
                    _notify(idea, cmp, paper=True)
                    triggered += 1

                    override = idea.get("auto_entry_override")
                    auto = (bool(override) if override is not None
                            else bool(get_user_pref(uid).get("auto_entry")))
                    if auto:
                        pid = promote_watchlist_to_user_position(idea, cmp, "WATCHLIST_TRIGGER")
                        if pid:
                            _notify(idea, cmp, paper=False)
                            promoted += 1
                    continue

                # 2) refresh display state for non-triggered ideas
                if not idea.get("triggered"):
                    new_state = "ARMED" if armed else compute_zone_state(cmp, lo, hi)
                    prev = idea.get("status")
                    update_watchlist_fields(wid, status=new_state, cmp=cmp)
                    if new_state == "APPROACHING" and prev != "APPROACHING":
                        log_watchlist_event(wid, uid, "APPROACHING", notes=f"CMP {cmp}")
                        _notify_approaching(idea, cmp)

            return {"status": "ok", "evaluated": len(ideas),
                    "triggered": triggered, "promoted": promoted, "expired": expired}
        except Exception as exc:
            log.exception("EntryTriggerService.tick failed: %s", exc)
            return {"status": "error", "detail": str(exc)}


def _tg(msg: str) -> None:
    """Best-effort Telegram to the MTF Alerts channel (same creds the rest of
    the platform uses). Never raises."""
    try:
        import requests
        bot = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
        if not bot or not chat:
            return
        requests.post(f"https://api.telegram.org/bot{bot}/sendMessage",
                      json={"chat_id": chat, "text": msg, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=8)
    except Exception:
        pass


def _notify(idea: dict, cmp: float, paper: bool) -> None:
    sym = idea["symbol"].replace("NSE:", "")
    tgt = idea.get("target_1") or idea.get("target_2")
    if paper:
        _tg(f"\U0001f7e2 <b>ENTRY ZONE TAPPED</b> — {sym}\n"
            f"CMP ₹{cmp} entered [{idea['entry_low']}–{idea['entry_high']}]\n"
            f"Target ₹{tgt} · SL ₹{idea['stop_loss']}\n"
            f"<i>Buy Now or Ignore on the watchlist.</i>")
    else:
        _tg(f"✅ <b>ENTRY TRIGGERED</b> — {sym}\n"
            f"Auto-entered at ₹{cmp} · SL ₹{idea['stop_loss']} · T ₹{tgt}")


def _notify_approaching(idea: dict, cmp: float) -> None:
    sym = idea["symbol"].replace("NSE:", "")
    _tg(f"\U0001f7e1 <b>APPROACHING</b> — {sym}\n"
        f"CMP ₹{cmp} nearing entry [{idea['entry_low']}–{idea['entry_high']}]")
