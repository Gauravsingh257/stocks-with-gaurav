"""
services/signal_delivery.py

Reliable signal → Telegram delivery pipeline for the live engine.

Responsibilities:
  - Redis-backed signal queue  (RPUSH / LPOP — drained each engine cycle + optional BG worker)
  - Per-cycle diagnostics      (signal counts, rejection reasons → Redis)
  - Delivery confirmation      (telegram:last_sent_signal, delivery_log)
  - No-signal heartbeat        (silence → heartbeat message)
  - Watchdog                   (stale queue → forced drain; optional worker restart)
  - No-setup Telegram report (throttled when signals_generated == 0)
  - Daily pipeline smoke ping  (09:20 IST)
  - Debug data                 (/api/system/debug/signals)

IMPORTANT: Safe to import from engine (Railway worker) and dashboard (Railway web).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, time as dt_time
from typing import Any, Callable

log = logging.getLogger("services.signal_delivery")

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

_IST = ZoneInfo("Asia/Kolkata")

# --- Redis keys --------------------------------------------------------------
QUEUE_KEY = "telegram:signal_queue"
LAST_SENT_SIGNAL_KEY = "telegram:last_sent_signal"
LAST_SENT_TS_KEY = "telegram:last_sent_timestamp"
LAST_HEARTBEAT_TS_KEY = "telegram:last_heartbeat_ts"
CYCLE_DIAG_KEY = "telegram:cycle_diagnostics"
DELIVERY_LOG_KEY = "telegram:delivery_log"
FAILURE_LOG_KEY = "telegram:failure_log"
LAST_QUEUE_PROCESS_TS_KEY = "telegram:last_queue_process_ts"
DELIVERY_WATCHDOG_TS_KEY = "telegram:delivery_watchdog_ts"
DELIVERY_EVENTS_ZSET = "telegram:delivery_events_ts"
NO_SETUP_LAST_TS_KEY = "telegram:last_no_setup_report_ts"

QUEUE_ITEM_TTL_SEC = 86400
DELIVERY_LOG_MAX = 50
FAILURE_LOG_MAX = 50
HEARTBEAT_INTERVAL_SEC = int(os.getenv("SIGNAL_HEARTBEAT_INTERVAL_SEC", "1800"))
RETRY_DELAY_SEC = int(os.getenv("SIGNAL_RETRY_DELAY_SEC", "10"))
MAX_RETRIES = int(os.getenv("SIGNAL_MAX_RETRIES", "3"))
NO_SETUP_REPORT_INTERVAL_SEC = int(os.getenv("NO_SETUP_REPORT_INTERVAL_SEC", "900"))
SIGNAL_DRAIN_MAX_ITEMS = int(os.getenv("SIGNAL_DRAIN_MAX_ITEMS", "25"))
SIGNAL_VERIFY_AFTER_ENQUEUE_SEC = float(os.getenv("SIGNAL_VERIFY_AFTER_ENQUEUE_SEC", "5"))
SIGNAL_DEDUPE_WINDOW_SEC = float(os.getenv("SIGNAL_DEDUPE_WINDOW_SEC", "900"))
WORKER_ALIVE_MAX_AGE_SEC = float(os.getenv("SIGNAL_WORKER_ALIVE_MAX_AGE_SEC", "600"))
WATCHDOG_ALIVE_MAX_AGE_SEC = float(os.getenv("SIGNAL_WATCHDOG_ALIVE_MAX_AGE_SEC", "180"))
WATCHDOG_INTERVAL_SEC = float(os.getenv("SIGNAL_WATCHDOG_INTERVAL_SEC", "45"))
QUEUE_STALE_SEC = float(os.getenv("SIGNAL_QUEUE_STALE_SEC", "120"))

TelegramSendSig = Callable[..., Any]


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


def format_no_setup_report_message(*, scanned: int, data_ok: int, reason: str) -> str:
    rsn = (reason or "").strip() or "—"
    return (
        "<b>Scan Complete — No setups found</b>\n\n"
        f"Scanned: {scanned} symbols\n"
        f"Data OK: {data_ok}\n"
        f"Reason: {rsn}"
    )


def _touch_last_queue_process_ts() -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(LAST_QUEUE_PROCESS_TS_KEY, 86400, _now_ist_iso())
    except Exception as e:
        log.debug("touch_last_queue_process_ts failed: %s", e)


def _touch_delivery_watchdog_ts() -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(DELIVERY_WATCHDOG_TS_KEY, 86400, _now_ist_iso())
    except Exception as e:
        log.debug("_touch_delivery_watchdog_ts failed: %s", e)


def _iso_age_seconds(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_IST)
        return max(0.0, time.time() - dt.timestamp())
    except Exception:
        return None


def delivery_log_contains_signal_id(signal_id: str | None, max_age_sec: float = 86400.0) -> bool:
    if not signal_id:
        return False
    r = _get_redis()
    if r is None:
        return False
    try:
        items = r.lrange(DELIVERY_LOG_KEY, -DELIVERY_LOG_MAX, -1) or []
        now_ts = time.time()
        for raw in reversed(items):
            row = json.loads(raw)
            if row.get("signal_id") != signal_id:
                continue
            sent_at = row.get("sent_at")
            if not sent_at:
                return True
            age = _iso_age_seconds(str(sent_at))
            if age is None:
                return True
            return age <= max_age_sec
    except Exception as e:
        log.debug("delivery_log_contains_signal_id failed: %s", e)
    return False


# --- 1. Per-cycle diagnostics ----------------------------------------------


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


# --- 2. Signal queue ---------------------------------------------------------


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


def enqueue_signal_verified(
    message: str,
    telegram_send_signal_fn: TelegramSendSig,
    *,
    signal_id: str | None = None,
    signal_meta: dict | None = None,
    chat_id: str | None = None,
    verify_timeout_sec: float | None = None,
) -> bool:
    """
    Enqueue then confirm delivery_log contains signal_id within verify window by
    draining the queue synchronously. On enqueue failure or verify timeout,
    sends via telegram_send_signal_fn directly.
    """
    timeout = SIGNAL_VERIFY_AFTER_ENQUEUE_SEC if verify_timeout_sec is None else verify_timeout_sec
    ok = enqueue_signal(message, signal_id=signal_id, signal_meta=signal_meta, chat_id=chat_id)
    if not ok:
        if signal_id and delivery_log_contains_signal_id(signal_id, max_age_sec=SIGNAL_DEDUPE_WINDOW_SEC):
            log.info("enqueue_signal_verified: enqueue failed but delivery_log OK sid=%s", signal_id)
            return True
        log.warning("enqueue_signal_verified: enqueue failed — direct send sid=%s", signal_id)
        try:
            sent = telegram_send_signal_fn(
                message,
                signal_id=signal_id,
                chat_id=chat_id,
                signal_meta=signal_meta,
            )
            return bool(sent)
        except Exception as exc:
            log.error("enqueue_signal_verified direct send failed: %s", exc)
            return False

    deadline = time.time() + timeout
    iterations = max(1, int(timeout / 0.35) + 2)
    for _ in range(iterations):
        if signal_id and delivery_log_contains_signal_id(signal_id, max_age_sec=SIGNAL_DEDUPE_WINDOW_SEC):
            return True
        drain_signal_queue_cycle(telegram_send_signal_fn, max_items=min(15, SIGNAL_DRAIN_MAX_ITEMS))
        if signal_id and delivery_log_contains_signal_id(signal_id, max_age_sec=SIGNAL_DEDUPE_WINDOW_SEC):
            return True
        if time.time() >= deadline:
            break
        time.sleep(0.35)

    if signal_id and delivery_log_contains_signal_id(signal_id, max_age_sec=SIGNAL_DEDUPE_WINDOW_SEC):
        return True

    log.warning(
        "enqueue_signal_verified: delivery_log timeout (%.1fs) — direct send sid=%s",
        timeout,
        signal_id,
    )
    try:
        sent = telegram_send_signal_fn(
            message,
            signal_id=signal_id,
            chat_id=chat_id,
            signal_meta=signal_meta,
        )
        return bool(sent)
    except Exception as exc:
        log.error("enqueue_signal_verified fallback send failed: %s", exc)
        return False


# --- 3. Delivery confirmation ----------------------------------------------


def _record_send_metric() -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        ts = time.time()
        r.zadd(DELIVERY_EVENTS_ZSET, {str(uuid.uuid4()): ts})
        r.expire(DELIVERY_EVENTS_ZSET, 7200)
        r.zremrangebyscore(DELIVERY_EVENTS_ZSET, 0, ts - 7200)
    except Exception as e:
        log.debug("_record_send_metric failed: %s", e)


def record_delivery_success(signal_id: str | None, message_preview: str) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        ts = _now_ist_iso()
        pipe = r.pipeline(transaction=True)
        pipe.setex(
            LAST_SENT_SIGNAL_KEY,
            86400,
            json.dumps(
                {"signal_id": signal_id, "preview": message_preview[:300], "sent_at": ts},
                default=str,
            ),
        )
        pipe.setex(LAST_SENT_TS_KEY, 86400, ts)
        pipe.rpush(
            DELIVERY_LOG_KEY,
            json.dumps(
                {"signal_id": signal_id, "preview": message_preview[:200], "sent_at": ts},
                default=str,
            ),
        )
        pipe.ltrim(DELIVERY_LOG_KEY, -DELIVERY_LOG_MAX, -1)
        pipe.expire(DELIVERY_LOG_KEY, 86400)
        pipe.execute()
        _record_send_metric()
    except Exception as e:
        log.debug("record_delivery_success failed: %s", e)


def record_delivery_failure(signal_id: str | None, error: str, message_preview: str) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        pipe = r.pipeline(transaction=True)
        pipe.rpush(
            FAILURE_LOG_KEY,
            json.dumps(
                {
                    "signal_id": signal_id,
                    "error": error[:500],
                    "preview": message_preview[:200],
                    "failed_at": _now_ist_iso(),
                },
                default=str,
            ),
        )
        pipe.ltrim(FAILURE_LOG_KEY, -FAILURE_LOG_MAX, -1)
        pipe.expire(FAILURE_LOG_KEY, 86400)
        pipe.execute()
    except Exception as e:
        log.debug("record_delivery_failure failed: %s", e)


# --- 4. Heartbeat ------------------------------------------------------------

_last_heartbeat_check: float = 0.0


def maybe_send_heartbeat(telegram_send_fn) -> bool:
    """If no signal has been sent for HEARTBEAT_INTERVAL_SEC, send a heartbeat."""
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


# --- 5. No-setup report & daily pipeline check ------------------------------

_last_no_setup_local_ts: float = 0.0


def maybe_send_no_setup_report(
    telegram_send_fn,
    *,
    signals_generated: int,
    scanned_count: int,
    data_ok_count: int,
    zero_signal_reason: str,
) -> bool:
    """Throttled Telegram when no signals generated (Redis + local cooldown)."""
    if signals_generated != 0:
        return False

    global _last_no_setup_local_ts
    now = time.time()
    interval = NO_SETUP_REPORT_INTERVAL_SEC

    r = _get_redis()
    if r is not None:
        try:
            raw = r.get(NO_SETUP_LAST_TS_KEY)
            if raw:
                try:
                    last = float(raw)
                    if now - last < interval:
                        return False
                except ValueError:
                    pass
        except Exception:
            pass

    if now - _last_no_setup_local_ts < min(120.0, interval):
        return False

    msg = format_no_setup_report_message(
        scanned=scanned_count,
        data_ok=data_ok_count,
        reason=zero_signal_reason,
    )
    try:
        sent = bool(telegram_send_fn(msg))
    except Exception as e:
        log.warning("maybe_send_no_setup_report send failed: %s", e)
        sent = False

    if sent:
        _last_no_setup_local_ts = now
        if r is not None:
            try:
                r.setex(NO_SETUP_LAST_TS_KEY, interval + 60, str(now))
            except Exception:
                pass
    return sent


def maybe_send_daily_pipeline_check(telegram_send_fn) -> bool:
    """Once per IST calendar day shortly after 09:20 — confirms pipeline alive."""
    global _daily_pipeline_local_day
    now = datetime.now(_IST)
    tnow = now.time()
    window_start = dt_time(9, 20)
    window_end = dt_time(9, 26)
    if not (window_start <= tnow < window_end):
        return False

    day_str = now.date().isoformat()
    r = _get_redis()
    day_key = f"telegram:pipeline_daily:{day_str}"
    allow_send = False
    if r is not None:
        try:
            allow_send = bool(r.set(day_key, _now_ist_iso(), nx=True, ex=86400 * 2))
        except Exception:
            allow_send = _daily_pipeline_local_day != day_str
    else:
        allow_send = _daily_pipeline_local_day != day_str

    if not allow_send:
        return False

    msg = "<b>System check — signal pipeline active</b>"
    try:
        ok = bool(telegram_send_fn(msg))
        if ok:
            _daily_pipeline_local_day = day_str
        return ok
    except Exception as e:
        log.warning("daily pipeline check send failed: %s", e)
        return False


# --- 6. Queue dispatch (shared worker + cycle drain) -------------------------


def _dispatch_queued_item(
    item: dict,
    telegram_send_signal_fn: TelegramSendSig,
    *,
    retry_sleep_sec: float | None,
) -> str:
    """
    Returns:
      skipped_dup — already in delivery_log (no Telegram call)
      sent — telegram_send_signal succeeded
      requeued — failed, put back with delay optional
      dead — retries exhausted
    """
    msg = item.get("message", "")
    sid = item.get("signal_id")
    meta = item.get("signal_meta") or {}
    chat = item.get("chat_id")
    retries_done = int(item.get("retries") or 0)

    if sid and delivery_log_contains_signal_id(sid, max_age_sec=SIGNAL_DEDUPE_WINDOW_SEC):
        log.info("signal_delivery: skip queue duplicate sid=%s", sid)
        return "skipped_dup"

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
        log.warning("queue dispatch send failed: %s", exc)

    if sent:
        return "sent"

    if retries_done < MAX_RETRIES:
        log.info(
            "signal_delivery: requeue sid=%s attempt %d/%d",
            sid,
            retries_done + 1,
            MAX_RETRIES,
        )
        if retry_sleep_sec and retry_sleep_sec > 0:
            time.sleep(retry_sleep_sec)
        requeue_signal(item)
        return "requeued"

    err = f"exhausted {MAX_RETRIES} retries"
    log.error("signal_delivery: signal %s LOST — %s", sid, err)
    record_delivery_failure(sid, err, msg)
    return "dead"


def drain_signal_queue_cycle(
    telegram_send_signal_fn: TelegramSendSig,
    max_items: int | None = None,
) -> dict[str, Any]:
    """
    Drain up to max_items from the queue (main engine cycle).
    Logs queue depth and Telegram sends for observability.
    """
    if max_items is None:
        max_items = SIGNAL_DRAIN_MAX_ITEMS

    ql_before = queue_length()
    messages_sent = 0
    skipped_dup = 0
    requeued = 0
    dead = 0

    for _ in range(max(0, max_items)):
        item = dequeue_signal()
        if item is None:
            break
        outcome = _dispatch_queued_item(item, telegram_send_signal_fn, retry_sleep_sec=None)
        if outcome == "sent":
            messages_sent += 1
        elif outcome == "skipped_dup":
            skipped_dup += 1
        elif outcome == "requeued":
            requeued += 1
        elif outcome == "dead":
            dead += 1

    ql_after = queue_length()
    _touch_last_queue_process_ts()

    stats = {
        "queue_length_before": ql_before,
        "queue_length_after": ql_after,
        "messages_sent_this_cycle": messages_sent,
        "skipped_duplicate_this_cycle": skipped_dup,
        "requeued_this_cycle": requeued,
        "dead_this_cycle": dead,
    }
    log.info(
        "[signal_delivery] drain_cycle before=%s after=%s sent=%s skipped_dup=%s requeued=%s dead=%s",
        ql_before,
        ql_after,
        messages_sent,
        skipped_dup,
        requeued,
        dead,
    )
    return stats


# --- 7. Background worker + watchdog -----------------------------------------

_delivery_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()
_watchdog_thread: threading.Thread | None = None
_watchdog_lock = threading.Lock()
_daily_pipeline_local_day: str | None = None


def _delivery_worker_loop(telegram_send_signal_fn: TelegramSendSig) -> None:
    while True:
        try:
            item = dequeue_signal()
            if item is None:
                time.sleep(2)
                continue

            outcome = _dispatch_queued_item(
                item,
                telegram_send_signal_fn,
                retry_sleep_sec=float(RETRY_DELAY_SEC),
            )
            if outcome == "requeued":
                continue
        except Exception as loop_exc:
            log.error("delivery worker loop error: %s", loop_exc)
            time.sleep(5)


def start_delivery_worker(telegram_send_signal_fn: TelegramSendSig) -> None:
    """Optional background worker (enable with SIGNAL_BACKGROUND_QUEUE_WORKER=1)."""
    global _delivery_worker_thread
    if os.getenv("SIGNAL_BACKGROUND_QUEUE_WORKER", "0").strip() != "1":
        return
    with _worker_lock:
        if _delivery_worker_thread is not None and _delivery_worker_thread.is_alive():
            return
        _delivery_worker_thread = threading.Thread(
            target=_delivery_worker_loop,
            args=(telegram_send_signal_fn,),
            daemon=True,
            name="signal_delivery_worker",
        )
        _delivery_worker_thread.start()
        log.info("signal delivery background worker started")


def ensure_delivery_worker_alive(telegram_send_signal_fn: TelegramSendSig) -> None:
    """Restart background worker thread if enabled and dead."""
    if os.getenv("SIGNAL_BACKGROUND_QUEUE_WORKER", "0").strip() != "1":
        return
    global _delivery_worker_thread
    with _worker_lock:
        if _delivery_worker_thread is None or not _delivery_worker_thread.is_alive():
            log.warning("signal_delivery: restarting dead background worker thread")
            _delivery_worker_thread = threading.Thread(
                target=_delivery_worker_loop,
                args=(telegram_send_signal_fn,),
                daemon=True,
                name="signal_delivery_worker",
            )
            _delivery_worker_thread.start()


def _watchdog_loop(telegram_send_signal_fn: TelegramSendSig) -> None:
    while True:
        try:
            _touch_delivery_watchdog_ts()
            ensure_delivery_worker_alive(telegram_send_signal_fn)
            ql = queue_length()
            if ql > 0:
                r = _get_redis()
                stale = True
                if r is not None:
                    try:
                        raw = r.get(LAST_QUEUE_PROCESS_TS_KEY)
                        age = _iso_age_seconds(raw)
                        stale = age is None or age > QUEUE_STALE_SEC
                    except Exception:
                        stale = True
                if stale:
                    log.warning(
                        "[signal_delivery] watchdog: stale drain (queue=%s) — forcing drain",
                        ql,
                    )
                    drain_signal_queue_cycle(telegram_send_signal_fn, max_items=50)
        except Exception as e:
            log.debug("signal_delivery watchdog error: %s", e)
        time.sleep(WATCHDOG_INTERVAL_SEC)


def start_delivery_watchdog(telegram_send_signal_fn: TelegramSendSig) -> None:
    """Watchdog: restart BG worker if enabled; force drain if queue stale."""
    global _watchdog_thread
    with _watchdog_lock:
        if _watchdog_thread is not None and _watchdog_thread.is_alive():
            return
        _watchdog_thread = threading.Thread(
            target=_watchdog_loop,
            args=(telegram_send_signal_fn,),
            daemon=True,
            name="signal_delivery_watchdog",
        )
        _watchdog_thread.start()
        log.info("signal delivery watchdog started")


# --- 8. Debug ----------------------------------------------------------------


def get_signal_debug() -> dict[str, Any]:
    """Collect pipeline diagnostics for /api/system/debug/signals."""
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

    last_qp = None
    try:
        last_qp = r.get(LAST_QUEUE_PROCESS_TS_KEY)
        out["last_queue_process_ts"] = last_qp
    except Exception:
        out["last_queue_process_ts"] = None

    wd_ts = None
    try:
        wd_ts = r.get(DELIVERY_WATCHDOG_TS_KEY)
        out["last_delivery_watchdog_ts"] = wd_ts
    except Exception:
        out["last_delivery_watchdog_ts"] = None

    qp_age = _iso_age_seconds(last_qp)
    wd_age = _iso_age_seconds(wd_ts)
    out["worker_alive"] = bool(
        (wd_age is not None and wd_age <= WATCHDOG_ALIVE_MAX_AGE_SEC)
        or (qp_age is not None and qp_age <= WORKER_ALIVE_MAX_AGE_SEC)
    )

    bg_on = os.getenv("SIGNAL_BACKGROUND_QUEUE_WORKER", "0").strip() == "1"
    out["background_worker_enabled"] = bg_on
    try:
        thr = _delivery_worker_thread
        out["background_worker_thread_alive"] = bool(thr is not None and thr.is_alive())
    except Exception:
        out["background_worker_thread_alive"] = False

    try:
        now_ts = time.time()
        out["messages_sent_last_10min"] = int(
            r.zcount(DELIVERY_EVENTS_ZSET, now_ts - 600, now_ts + 1) or 0
        )
    except Exception:
        out["messages_sent_last_10min"] = -1

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
