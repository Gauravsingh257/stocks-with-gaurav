"""
services/dashboard_sync.py
Fire-and-forget trade sync from engine worker to dashboard web service.

When a trade closes (main engine or OI-SC), POST it to the dashboard so
the journal/analytics pages show real data in production (Railway).

Uses DASHBOARD_URL + TRADES_SYNC_KEY env vars.
No-op if DASHBOARD_URL is not set (local dev backward compatible).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Optional

log = logging.getLogger("dashboard_sync")

_DASHBOARD_URL: Optional[str] = None
_SYNC_KEY: Optional[str] = None
_retry_queue: deque = deque(maxlen=200)
_retry_thread_started = False
_retry_lock = threading.Lock()


def _get_config():
    global _DASHBOARD_URL, _SYNC_KEY
    if _DASHBOARD_URL is None:
        _DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").strip().rstrip("/")
        _SYNC_KEY = os.getenv("TRADES_SYNC_KEY", "").strip()
    return _DASHBOARD_URL, _SYNC_KEY


def sync_trade_to_dashboard(trade_data: dict) -> None:
    """POST a single closed trade to dashboard. Non-blocking (daemon thread).
    trade_data keys: date, symbol, direction, setup, entry, exit_price, result, pnl_r
    """
    url, _ = _get_config()
    if not url:
        return
    t = threading.Thread(target=_post_trade, args=(trade_data,), daemon=True)
    t.start()


def _post_trade(trade_data: dict, is_retry: bool = False) -> bool:
    import requests

    url, key = _get_config()
    endpoint = f"{url}/api/journal/trade"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Sync-Key"] = key
    try:
        resp = requests.post(endpoint, json=trade_data, headers=headers, timeout=10)
        if resp.status_code in (200, 201):
            log.info(
                "Trade synced to dashboard: %s %s %sR",
                trade_data.get("symbol"),
                trade_data.get("result"),
                trade_data.get("pnl_r"),
            )
            return True
        log.warning("Dashboard sync HTTP %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("Dashboard sync failed: %s", e)

    if not is_retry:
        with _retry_lock:
            _retry_queue.append(trade_data)
        _ensure_retry_thread()
    return False


def _ensure_retry_thread():
    global _retry_thread_started
    if _retry_thread_started:
        return
    _retry_thread_started = True
    t = threading.Thread(target=_retry_loop, daemon=True, name="dashboard-sync-retry")
    t.start()


def _retry_loop():
    while True:
        time.sleep(60)
        with _retry_lock:
            if not _retry_queue:
                continue
            batch = list(_retry_queue)
            _retry_queue.clear()
        for trade in batch:
            if not _post_trade(trade, is_retry=True):
                with _retry_lock:
                    _retry_queue.append(trade)
