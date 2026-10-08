"""
services/telegram_health.py — record what Telegram actually did with each send.

Why this exists (2026-10-09 audit): from ~2026-09-29 every message to the MTF
Alerts channel failed with ``PEER_ID_INVALID`` (the bot account was frozen by
Telegram — ``getChatMember`` returns ``FROZEN_METHOD_INVALID``), and nothing
noticed for ten days:

* the engine only recorded failures for *signals*, into a 24h-TTL list, so the
  hourly heartbeat failures were invisible and the signal failures expired;
* the web paths called ``requests.post`` and logged "sent" without reading the
  response at all.

``sendChatAction`` still returns ``ok`` on a frozen bot, so there is no silent
probe — the only truth is the outcome of real ``sendMessage`` calls. Every send
path therefore reports its outcome here, keyed by destination chat, in Redis
(shared by the engine and web containers). ``get_health()`` turns that into a
per-destination verdict that ``/api/system/telegram-health`` serves and the
``Telegram Health`` GitHub Action fails on — Telegram cannot be trusted to
report its own outage.

Success means HTTP 200 **and** ``{"ok": true}`` in the body. Anything else is a
failure carrying Telegram's own ``description``. Never raises.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEST_KEY_PREFIX = "telegram:health:dest:"
DESTS_SET_KEY = "telegram:health:dests"
EVENTS_KEY = "telegram:health:failures"
RETENTION_SEC = 14 * 86400
EVENTS_MAX = 200

# A destination is FAILING once this many consecutive sends failed with no
# success in between. One send already includes the caller's own retries, so 2
# means two independent messages were lost — not a single network blip.
FAILING_AFTER = 2


@dataclass
class SendResult:
    ok: bool
    status_code: int | None = None
    error: str | None = None
    message_id: int | None = None


def classify_response(status_code: int | None, body) -> SendResult:
    """Interpret a Telegram Bot API response. ``body`` is the parsed JSON (or None)."""
    payload = body if isinstance(body, dict) else {}
    if status_code == 200 and payload.get("ok") is True:
        result = payload.get("result")
        mid = result.get("message_id") if isinstance(result, dict) else None
        return SendResult(ok=True, status_code=status_code, message_id=mid)
    desc = payload.get("description") or (f"HTTP {status_code}" if status_code else "no response")
    return SendResult(ok=False, status_code=status_code, error=str(desc)[:300])


def result_from_response(resp) -> SendResult:
    """Classify a ``requests.Response``."""
    try:
        body = resp.json()
    except Exception:
        body = None
    res = classify_response(getattr(resp, "status_code", None), body)
    if not res.ok and body is None:
        text = (getattr(resp, "text", "") or "")[:200]
        res.error = f"HTTP {res.status_code}: {text}" if text else res.error
    return res


def _get_redis():
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis as redis_lib

        return redis_lib.from_url(url, decode_responses=True, socket_connect_timeout=5)
    except Exception:
        return None


def record_send_result(source: str, chat_id, result: SendResult) -> None:
    """Persist one send outcome for ``chat_id``. Best-effort; never raises."""
    if not chat_id:
        return
    chat = str(chat_id).strip()
    now = time.time()
    if not result.ok:
        log.error("[TelegramHealth] send FAILED source=%s chat=%s error=%s", source, mask_chat(chat), result.error)
    r = _get_redis()
    if r is None:
        return
    try:
        key = DEST_KEY_PREFIX + chat
        pipe = r.pipeline(transaction=True)
        pipe.sadd(DESTS_SET_KEY, chat)
        pipe.expire(DESTS_SET_KEY, RETENTION_SEC)
        pipe.hset(key, mapping={"chat_id": chat, "last_source": source, "last_attempt_ts": now})
        if result.ok:
            pipe.hset(key, mapping={"last_ok_ts": now, "consecutive_failures": 0})
            pipe.hincrby(key, "total_ok", 1)
        else:
            pipe.hset(key, mapping={"last_fail_ts": now, "last_error": result.error or "unknown",
                                    "last_fail_source": source})
            pipe.hincrby(key, "consecutive_failures", 1)
            pipe.hincrby(key, "total_fail", 1)
            pipe.rpush(EVENTS_KEY, json.dumps({"ts": now, "source": source, "chat": mask_chat(chat),
                                               "error": result.error}, default=str))
            pipe.ltrim(EVENTS_KEY, -EVENTS_MAX, -1)
            pipe.expire(EVENTS_KEY, RETENTION_SEC)
        pipe.expire(key, RETENTION_SEC)
        pipe.execute()
    except Exception as e:
        log.debug("record_send_result failed: %s", e)


def send_message(token: str, chat_id, text: str, *, source: str, parse_mode: str | None = "HTML",
                 disable_web_page_preview: bool | None = None, timeout: float = 10) -> SendResult:
    """POST sendMessage, classify the real response and record it. Never raises."""
    if not token or not chat_id:
        return SendResult(ok=False, error="telegram_not_configured")
    body: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if disable_web_page_preview is not None:
        body["disable_web_page_preview"] = disable_web_page_preview
    try:
        import requests

        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body, timeout=timeout)
        res = result_from_response(resp)
    except Exception as exc:
        res = SendResult(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
    record_send_result(source, chat_id, res)
    return res


def mask_chat(chat_id) -> str:
    s = str(chat_id or "")
    return s if len(s) <= 6 else f"{s[:4]}…{s[-4:]}"


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def destination_status(h: dict) -> str:
    fails = int(_f(h.get("consecutive_failures")) or 0)
    last_ok, last_fail = _f(h.get("last_ok_ts")), _f(h.get("last_fail_ts"))
    if fails >= FAILING_AFTER:
        return "FAILING"
    if last_fail and (last_ok is None or last_fail > last_ok):
        return "DEGRADED"
    if last_ok:
        return "OK"
    return "UNKNOWN"


def get_health(now: float | None = None) -> dict:
    """Per-destination verdict from recorded outcomes. Never raises."""
    now = now or time.time()
    r = _get_redis()
    if r is None:
        return {"available": False, "ok": None, "reason": "redis_unavailable", "destinations": []}
    try:
        dests = []
        for chat in sorted(r.smembers(DESTS_SET_KEY) or []):
            h = r.hgetall(DEST_KEY_PREFIX + chat) or {}
            if not h:
                continue
            last_ok, last_fail = _f(h.get("last_ok_ts")), _f(h.get("last_fail_ts"))
            dests.append({
                "chat": mask_chat(chat),
                "status": destination_status(h),
                "consecutive_failures": int(_f(h.get("consecutive_failures")) or 0),
                "last_error": h.get("last_error"),
                "last_source": h.get("last_source"),
                "last_fail_source": h.get("last_fail_source"),
                "last_ok_age_h": round((now - last_ok) / 3600, 1) if last_ok else None,
                "last_fail_age_h": round((now - last_fail) / 3600, 1) if last_fail else None,
                "total_ok": int(_f(h.get("total_ok")) or 0),
                "total_fail": int(_f(h.get("total_fail")) or 0),
            })
        recent = []
        for raw in (r.lrange(EVENTS_KEY, -10, -1) or []):
            with contextlib.suppress(Exception):
                recent.append(json.loads(raw))
        failing = [d["chat"] for d in dests if d["status"] == "FAILING"]
        return {
            "available": True,
            "ok": not failing,
            "failing": failing,
            "destinations": dests,
            "recent_failures": recent,
            "checked_at": now,
            "rule": f"FAILING after {FAILING_AFTER} consecutive failed sends; success = HTTP 200 and ok:true",
        }
    except Exception as e:
        return {"available": False, "ok": None, "reason": f"error: {e}", "destinations": []}
