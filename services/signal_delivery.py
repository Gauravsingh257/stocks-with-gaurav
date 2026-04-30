"""
services/signal_delivery.py

Reliable signal → Telegram delivery pipeline for the live engine.

Responsibilities:
  - Redis-backed signal queue  (RPUSH / BLPOP pattern for zero-loss delivery)
  - Per-cycle diagnostics      (signal counts, rejection reasons → Redis)
  - Delivery confirmation      (telegram:last_sent_signal, timestamp)
  - No-signal heartbeat        (30 min silence → heartbeat message)
  - Retry worker               (background thread drains queue with retries)
  - Debug data                 (last N signals/sends/failures for /api/debug/signals)

IMPORTANT: This module must be safe to import from both the engine process
(Railway worker) and the dashboard process (Railway web). Redis is the only
shared state.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

log = logging.getLogger("services.signal_delivery")

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

_IST = ZoneInfo("Asia/Kolkata")

# --- Redis keys (sidecar; no clash with existing snapshot keys) ------------
QUEUE_KEY = "telegram:signal_queue"
LAST_SENT_SIGNAL_KEY = "telegram:last_sent_signal"
LAST_SENT_TS_KEY = "telegram:last_sent_timestamp"
LAST_HEARTBEAT_TS_KEY = "telegram:last_heartbeat_ts"
CYCLE_DIAG_KEY = "telegram:cycle_diagnostics"
DELIVERY_LOG_KEY = "telegram:delivery_log"
FAILURE_LOG_KEY = "telegram:failure_log"

QUEUE_ITEM_TTL_SEC = 86400
DELIVERY_LOG_MAX = 50
FAILURE_LOG_MAX = 50
HEARTBEAT_INTERVAL_SEC = int(os.getenv("SIGNAL_HEARTBEAT_INTERVAL_SEC", "1800"))
RETRY_DELAY_SEC = int(os.getenv("SIGNAL_RETRY_DELAY_SEC", "10"))
MAX_RETRIES = int(os.getenv("SIGNAL_MAX_RETRIES", "3"))


def _get_redis():
    try:
        import redis as redis_lib
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            return None
        return redis_lib.from_url(url, decode_responses=True, socket_connect_timeout=5)
    except Exception:
        return None


def _now_ist_iso() -> str:
    return datetime.now(_IST).isoformat()


# --- 1. Per-cycle diagnostics ------------------------------------------------

def record_cycle_diagnostics(
    *,
    signals_generated: int = 0,
    signal_details: list[dict] | None = None,
    rejection_reasons: list[str] | None = None,
    zero_signal_reason: str = "",
) -> None:
    """Called once per engine scan cycle to store what happened."""
    r = _get_redis()
    if r is None:
        return
    diag = {
        "ts": _now_ist_iso(),
        "signals_generated_count": signals_generated,
        "signal_details": (signal_details or [])[:20],
        "rejection_reasons": (rejection_reasons or [])[:50],
        "zero_signal_reason": zero_signal_reason,
    }
    try:
        r.setex(CYCLE_DIAG_KEY, 3600, json.dumps(diag, default=str))
    except Exception as e:
        log.debug("record_cycle_diagnostics failed: %s", e)


# --- 2. Signal queue --------------------------------------------------------

def enqueue_signal(
    message: str,
    signal_id: str | None = None,
    signal_meta: dict | None = None,
    chat_id: str | None = None,
) -> bool:
    """Push a signal into the Redis queue for the delivery worker."""
    r = _get_redis()
    if r is None:
        log.warning("enqueue_signal: Redis unavailable — signal may be lost")
        return False
    item = {
        "message": message,
        "signal_id": signal_id,
        "signal_meta": signal_meta or {},
        "chat_id": chat_id,
        "enqueued_at": _now_ist_iso(),
        "retries": 0,
    }
    try:
        r.rpush(QUEUE_KEY, json.dumps(item, default=str))
        r.expire(QUEUE_KEY, QUEUE_ITEM_TTL_SEC)
        return True
    except Exception as e:
        log.warning("enqueue_signal failed: %s", e)
        return False


def dequeue_signal() -> dict | None:
    """Pop the oldest signal from the queue (non-blocking)."""
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.lpop(QUEUE_KEY)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        log.debug("dequeue_signal failed: %s", e)
        return None


def requeue_signal(item: dict) -> None:
    """Put a failed item back at the end of the queue with incremented retry count."""
    item["retries"] = int(item.get("retries") or 0) + 1
    item["last_retry_at"] = _now_ist_iso()
    r = _get_redis()
    if r is None:
        return
    try:
        r.rpush(QUEUE_KEY, json.dumps(item, default=str))
    except Exception:
        pass


def queue_length() -> int:
    r = _get_redis()
    if r is None:
        return -1
    try:
        return int(r.llen(QUEUE_KEY) or 0)
    except Exception:
        return -1


# --- 3. Delivery confirmation ------------------------------------------------

def record_delivery_success(signal_id: str | None, message_preview: str) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        ts = _now_ist_iso()
        pipe = r.pipeline(transaction=True)
        pipe.setex(LAST_SENT_SIGNAL_KEY, 86400, json.dumps({
            "signal_id": signal_id,
            "preview": message_preview[:300],
            "sent_at": ts,
        }, default=str))
        pipe.setex(LAST_SENT_TS_KEY, 86400, ts)
        pipe.rpush(DELIVERY_LOG_KEY, json.dumps({
            "signal_id": signal_id,
            "preview": message_preview[:200],
            "sent_at": ts,
        }, default=str))
        pipe.ltrim(DELIVERY_LOG_KEY, -DELIVERY_LOG_MAX, -1)
        pipe.expire(DELIVERY_LOG_KEY, 86400)
        pipe.execute()
    except Exception as e:
        log.debug("record_delivery_success failed: %s", e)


def record_delivery_failure(signal_id: str | None, error: str, message_preview: str) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        pipe = r.pipeline(transaction=True)
        pipe.rpush(FAILURE_LOG_KEY, json.dumps({
            "signal_id": signal_id,
            "error": error[:500],
            "preview": message_preview[:200],
            "failed_at": _now_ist_iso(),
        }, default=str))
        pipe.ltrim(FAILURE_LOG_KEY, -FAILURE_LOG_MAX, -1)
        pipe.expire(FAILURE_LOG_KEY, 86400)
        pipe.execute()
    except Exception as e:
        log.debug("record_delivery_failure failed: %s", e)


# --- 4. Heartbeat -----------------------------------------------------------

_last_heartbeat_check: float = 0.0


def maybe_send_heartbeat(telegram_send_fn) -> bool:
    """If no signal has been sent for HEARTBEAT_INTERVAL_SEC, send a heartbeat.

    Returns True if heartbeat was sent.
    """
    global _last_heartbeat_check
    now = time.time()
    if now - _last_heartbeat_check < 60:
        return False
    _last_heartbeat_check = now

    r = _get_redis()
    if r is None:
        return False
    try:
        last_ts_raw = r.get(LAST_SENT_TS_KEY)
        last_hb_raw = r.get(LAST_HEARTBEAT_TS_KEY)

        last_event = 0.0
        for raw in (last_ts_raw, last_hb_raw):
            if raw:
                try:
                    dt = datetime.fromisoformat(raw)
                    last_event = max(last_event, dt.timestamp())
                except Exception:
                    pass

        if last_event == 0.0:
            last_event = now - HEARTBEAT_INTERVAL_SEC - 1

        if now - last_event >= HEARTBEAT_INTERVAL_SEC:
            minutes = int((now - last_event) / 60)
            ist_now = datetime.now(_IST).strftime("%H:%M")
            msg = (
                f"💓 <b>Engine heartbeat</b> — {ist_now} IST\n"
                f"No trading signals in the last {minutes} min.\n"
                f"Engine is active and scanning normally."
            )
            try:
                sent = telegram_send_fn(msg)
            except Exception as e:
                log.debug("heartbeat send failed: %s", e)
                sent = False
            if sent:
                r.setex(LAST_HEARTBEAT_TS_KEY, 86400, _now_ist_iso())
            return bool(sent)
    except Exception as e:
        log.debug("maybe_send_heartbeat error: %s", e)
    return False


# --- 5. Delivery worker (background thread) ---------------------------------

_worker_started = False
_worker_lock = threading.Lock()


def _delivery_worker(telegram_send_signal_fn):
    """Drain the Redis signal queue. Retries failed items up to MAX_RETRIES."""
    while True:
        try:
            item = dequeue_signal()
            if item is None:
                time.sleep(2)
                continue

            msg = item.get("message", "")
            sid = item.get("signal_id")
            meta = item.get("signal_meta") or {}
            chat = item.get("chat_id")
            retries_done = int(item.get("retries") or 0)

            try:
                sent = telegram_send_signal_fn(
                    msg,
                    signal_id=sid,
                    chat_id=chat,
                    signal_meta=meta,
                )
                if sent is None:
                    sent = True
            except Exception as exc:
                sent = False
                log.warning("queue worker send failed: %s", exc)

            if sent or sent is None:
                record_delivery_success(sid, msg)
            else:
                if retries_done < MAX_RETRIES:
                    log.info(
                        "queue worker: will retry signal %s (attempt %d/%d) in %ds",
                        sid, retries_done + 1, MAX_RETRIES, RETRY_DELAY_SEC,
                    )
                    time.sleep(RETRY_DELAY_SEC)
                    requeue_signal(item)
                else:
                    err = f"exhausted {MAX_RETRIES} retries"
                    log.error("queue worker: signal %s LOST — %s", sid, err)
                    record_delivery_failure(sid, err, msg)
        except Exception as loop_exc:
            log.error("delivery worker loop error: %s", loop_exc)
            time.sleep(5)


def start_delivery_worker(telegram_send_signal_fn) -> None:
    """Start the background delivery thread (idempotent)."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    t = threading.Thread(
        target=_delivery_worker,
        args=(telegram_send_signal_fn,),
        daemon=True,
        name="signal_delivery_worker",
    )
    t.start()
    log.info("signal delivery worker started")


# --- 6. Debug data (read by /api/debug/signals) ------------------------------

def get_signal_debug() -> dict[str, Any]:
    """Collect last N signals, sends, failures, queue length, heartbeat info."""
    r = _get_redis()
    if r is None:
        return {"redis_available": False}

    out: dict[str, Any] = {"redis_available": True}

    try:
        out["queue_length"] = int(r.llen(QUEUE_KEY) or 0)
    except Exception:
        out["queue_length"] = -1

    try:
        raw = r.get(LAST_SENT_SIGNAL_KEY)
        out["last_sent_signal"] = json.loads(raw) if raw else None
    except Exception:
        out["last_sent_signal"] = None

    try:
        out["last_sent_timestamp"] = r.get(LAST_SENT_TS_KEY)
    except Exception:
        out["last_sent_timestamp"] = None

    try:
        out["last_heartbeat_ts"] = r.get(LAST_HEARTBEAT_TS_KEY)
    except Exception:
        out["last_heartbeat_ts"] = None

    try:
        raw = r.get(CYCLE_DIAG_KEY)
        out["last_cycle_diagnostics"] = json.loads(raw) if raw else None
    except Exception:
        out["last_cycle_diagnostics"] = None

    for label, key, limit in (
        ("recent_deliveries", DELIVERY_LOG_KEY, DELIVERY_LOG_MAX),
        ("recent_failures", FAILURE_LOG_KEY, FAILURE_LOG_MAX),
    ):
        try:
            items = r.lrange(key, -limit, -1)
            out[label] = [json.loads(i) for i in (items or [])]
        except Exception:
            out[label] = []

    out["checked_at"] = _now_ist_iso()
    return out
