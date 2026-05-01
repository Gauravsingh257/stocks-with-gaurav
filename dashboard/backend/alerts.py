"""
dashboard/backend/alerts.py

Phase 3 — Alert dispatcher.

Lifecycle transitions that should generate a user-visible alert. Alerts are:
    1. Pushed to a Redis list `terminal:alerts` (capped, 24h TTL) — read by
       /api/alerts for the bell-icon dropdown in the UI.
    2. Republished on `terminal:events:pub` so connected /ws/trades clients
       get them instantly (already covered by the LIFECYCLE event; we add an
       explicit ALERT type for the bell icon).
    3. Optionally forwarded to Telegram for user-side notifications.

We deliberately do NOT use this for engine-side broadcast (that already has
its own delivery pipeline). This is read-side / per-user surfacing.

Loud transitions (alerted): WAITING→APPROACHING, *→TRIGGERED, *→TARGET_HIT,
                            *→STOP_HIT
Quiet transitions          : APPROACHING↔WAITING flicker, TRIGGERED↔RUNNING
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

log = logging.getLogger("dashboard.alerts")

ALERTS_LIST_KEY = "terminal:alerts"
ALERTS_TTL_SECONDS = 86_400
ALERTS_RING_SIZE = 100

_LOUD_TRANSITIONS = {
    "APPROACHING": ("Entry zone reached", "info"),
    "TRIGGERED": ("Trade triggered", "success"),
    "TARGET_HIT": ("Target hit", "success"),
    "STOP_HIT": ("Stop loss hit", "danger"),
}


def _r():
    try:
        from dashboard.backend.cache import _get_redis
        return _get_redis()
    except Exception:
        return None


def dispatch_alert(
    symbol: str,
    old_state: Optional[str],
    new_state: str,
    record: Dict[str, Any],
    cmp_value: Optional[float],
) -> None:
    """Publish + persist an alert if this transition is user-visible."""
    if new_state not in _LOUD_TRANSITIONS:
        return
    title, severity = _LOUD_TRANSITIONS[new_state]
    direction = (record.get("direction") or "LONG").lower()
    body = _build_body(symbol, direction, new_state, record, cmp_value)
    alert = {
        "id": f"alert-{symbol}-{new_state}-{int(time.time())}",
        "type": new_state,
        "severity": severity,
        "symbol": symbol,
        "title": f"{symbol} — {title}",
        "body": body,
        "from": old_state,
        "to": new_state,
        "cmp": cmp_value,
        "rr": record.get("rr"),
        "probability": record.get("probability"),
        "ts": int(time.time()),
    }

    r = _r()
    if r is not None:
        try:
            r.lpush(ALERTS_LIST_KEY, json.dumps(alert, default=str))
            r.ltrim(ALERTS_LIST_KEY, 0, ALERTS_RING_SIZE - 1)
            r.expire(ALERTS_LIST_KEY, ALERTS_TTL_SECONDS)
        except Exception as exc:
            log.debug("alerts list write failed: %s", exc)

    # Push as ALERT event into the existing pub/sub so live clients get a
    # bell-icon update without an extra fetch.
    try:
        from dashboard.backend.terminal_events import publish_event
        publish_event("ALERT", symbol, alert)
    except Exception as exc:
        log.debug("alert event publish failed: %s", exc)


def _build_body(
    symbol: str,
    direction: str,
    new_state: str,
    record: Dict[str, Any],
    cmp_value: Optional[float],
) -> str:
    entry = record.get("entry")
    target = record.get("target")
    sl = record.get("sl")
    rr = record.get("rr")
    if new_state == "APPROACHING":
        return (
            f"{symbol} ({direction}) is at entry zone — "
            f"CMP {_fmt(cmp_value)} vs entry {_fmt(entry)}. RR {_fmt_rr(rr)}."
        )
    if new_state == "TRIGGERED":
        return f"{symbol} ({direction}) triggered at {_fmt(entry)}. Target {_fmt(target)} / SL {_fmt(sl)}."
    if new_state == "TARGET_HIT":
        return f"{symbol} ({direction}) hit target at {_fmt(target)} — closed in profit ({_fmt_rr(rr)} R)."
    if new_state == "STOP_HIT":
        return f"{symbol} ({direction}) hit stop at {_fmt(sl)}."
    return f"{symbol} → {new_state}"


def _fmt(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:,.2f}" if v >= 100 else f"{v:.2f}"


def _fmt_rr(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:.2f}R"


def get_recent_alerts(limit: int = 50) -> list[Dict[str, Any]]:
    """Read most-recent alerts (newest first)."""
    r = _r()
    if r is None:
        return []
    try:
        items = r.lrange(ALERTS_LIST_KEY, 0, limit - 1) or []
    except Exception:
        return []
    out: list[Dict[str, Any]] = []
    for raw in items:
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out
