"""
dashboard/backend/services/morning_brief.py
============================================
Sprint 1 · Feature 4 — Telegram Morning Brief.

One concise, high-value morning summary pushed to the platform's Telegram
channel each trading morning. Reuse-first: it composes the message from the
EXISTING deterministic brief builder (`build_daily_market_brief`) and delivers
via the EXISTING best-effort sender (`services.pil.notify.send_telegram`).
No new market logic, no new bot.

Gated OFF by default (MORNING_BRIEF_ENABLED). It is outward-facing (posts to a
channel), so the owner opts in explicitly — mirrors the PIL Telegram pattern.
Runs as a single daemon thread that idles cheaply until the flag is on, and
sends at most once per weekday at MORNING_BRIEF_HOUR IST.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

log = logging.getLogger("dashboard.morning_brief")
_IST = timezone(timedelta(hours=5, minutes=30))

_started = False
_lock = threading.Lock()
_TICK_SECONDS = 300


def _enabled() -> bool:
    return os.getenv("MORNING_BRIEF_ENABLED", "0").strip() in ("1", "true", "True")


def _hour() -> int:
    try:
        return int(os.getenv("MORNING_BRIEF_HOUR", "8"))
    except ValueError:
        return 8


def _link() -> str:
    return os.getenv("MORNING_BRIEF_LINK", "https://stockswithgaurav.com/command")


def _esc(s: Any) -> str:
    """Minimal HTML escape (send_telegram uses parse_mode=HTML)."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_brief_message(brief: Dict[str, Any]) -> str:
    """Compose the concise Telegram message from a daily-brief payload.

    Kept deliberately short — a morning brief earns its buzz by being scannable
    in five seconds, then linking to the Command Center for depth.
    """
    now = datetime.now(_IST)
    regime = _esc(brief.get("regime") or "unknown")
    syms: List[str] = [s for s in (brief.get("top_discovery_symbols") or []) if s][:6]

    # Prefer the human narrative line; fall back to the deterministic sections.
    narrative = ""
    nsec = brief.get("narrative_sections") or []
    if nsec and isinstance(nsec[0], dict):
        narrative = str(nsec[0].get("body") or "")
    if not narrative:
        for sec in brief.get("sections") or []:
            if isinstance(sec, dict) and sec.get("title") == "Regime":
                narrative = str(sec.get("body") or "")
                break

    lines: List[str] = []
    lines.append(f"🌅 <b>Morning Brief</b> · {now.strftime('%a %d %b')}")
    lines.append(f"Market mood: <b>{regime}</b>")
    if narrative:
        lines.append("")
        lines.append(_esc(narrative.strip()))
    if syms:
        lines.append("")
        lines.append("📋 <b>On the radar:</b> " + ", ".join(_esc(s) for s in syms))
    lines.append("")
    lines.append(f'👉 <a href="{_esc(_link())}">Open your Command Center</a>')
    trust = brief.get("trust_note")
    if trust:
        lines.append("")
        lines.append(f"<i>{_esc(trust)}</i>")
    return "\n".join(lines)


def build_and_send() -> bool:
    """Build today's brief and push it once. Returns True on a successful send.

    Safe to call manually (e.g. an admin trigger) — it does not consult the
    once-per-day guard; the scheduler owns dedupe.
    """
    try:
        from dashboard.backend.services.command_center_service import build_daily_market_brief
        brief = build_daily_market_brief(user_id=None)
    except Exception as exc:
        log.error("[MorningBrief] brief build failed: %s", exc)
        return False
    try:
        from services.pil.notify import send_telegram
        return send_telegram(format_brief_message(brief))
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("[MorningBrief] send failed: %s", exc)
        return False


def _loop() -> None:
    last_sent = None  # YYYY-MM-DD
    log.info("[MorningBrief] loop started (idles until MORNING_BRIEF_ENABLED=1)")
    while True:
        try:
            if _enabled():
                now = datetime.now(_IST)
                today = now.date().isoformat()
                # Weekdays only, at/after the configured hour, at most once/day.
                if now.weekday() < 5 and now.hour >= _hour() and last_sent != today:
                    if build_and_send():
                        last_sent = today
                        log.info("[MorningBrief] sent for %s", today)
                    else:
                        # Don't hammer on failure; retry next tick, but mark the
                        # day so a misconfigured channel doesn't spin — only mark
                        # on success above. Here we simply wait for the next tick.
                        log.debug("[MorningBrief] send returned False; will retry next tick")
        except Exception as exc:  # never let the loop die
            log.error("[MorningBrief] tick error: %s", exc)
        time.sleep(_TICK_SECONDS)


def start_morning_brief_scheduler() -> None:
    """Start the morning-brief thread once. Safe to call unconditionally from the
    lifespan — the loop idles until MORNING_BRIEF_ENABLED=1."""
    global _started
    with _lock:
        if _started:
            return
        t = threading.Thread(target=_loop, daemon=True, name="morning-brief")
        t.start()
        _started = True
