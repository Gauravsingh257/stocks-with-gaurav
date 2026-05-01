"""
dashboard/backend/lifecycle.py

Phase 3 — Trade Life-Cycle Tracker.

Watches the merged set of (active trades + today's signals) on a slow tick,
detects state transitions, and emits structured `LIFECYCLE` events into the
existing terminal event ring + pub/sub.

States
------
    WAITING       (signal published, price not yet near zone)
    APPROACHING   (price within ~0.4% of entry)
    TRIGGERED     (entry filled, trade open)
    RUNNING       (trade open and progressing — price between entry and target)
    TARGET_HIT    (target reached / closed in profit)
    STOP_HIT      (stop reached / closed in loss)

Why a separate tracker (not inside the engine)?
    The engine already labels its own transitions when it acts. This tracker
    catches *implied* transitions for read-side consumers (the terminal): for
    example a WAITING signal whose CMP crosses into the zone becomes
    APPROACHING, and a TRIGGERED trade whose CMP moves >50% toward target
    becomes RUNNING. Pure read-only — never writes back to engine state.

Public API
----------
    start_lifecycle_watcher()  → start the asyncio task (idempotent)
    stop_lifecycle_watcher()   → stop the task
    classify(record, cmp)      → return enriched lifecycle for one record
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

log = logging.getLogger("dashboard.lifecycle")

_TICK_SECONDS = 7  # tracker tick — trades don't move that fast on day-TF
_APPROACH_PCT = 0.004  # within 0.4% of entry counts as APPROACHING
_RUNNING_PROGRESS = 0.10  # >10% of the way to target = RUNNING

# Memory of last known state per symbol — keyed by symbol so we only emit
# transition events on actual changes.
_LAST_STATE: Dict[str, str] = {}

_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None


# ─────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────

def _last_price(rec: Dict[str, Any]) -> Optional[float]:
    """Best effort current market price for a normalized record."""
    for key in ("ltp", "cmp", "current_price", "last_price"):
        v = rec.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    # try fetching live price from cache module if available
    try:
        from dashboard.backend.cache import get_cached_ltp  # type: ignore[attr-defined]
        ltp = get_cached_ltp(rec.get("symbol", ""))
        if isinstance(ltp, (int, float)) and ltp > 0:
            return float(ltp)
    except Exception:
        pass
    # fall back to entry as a placeholder when nothing else known
    return rec.get("entry") if isinstance(rec.get("entry"), (int, float)) else None


def classify(rec: Dict[str, Any], cmp_value: Optional[float] = None) -> str:
    """Return one of WAITING / APPROACHING / TRIGGERED / RUNNING / TARGET_HIT / STOP_HIT."""
    if not isinstance(rec, dict):
        return "WAITING"
    raw_status = (rec.get("status") or "").upper()
    if raw_status in ("TARGET_HIT", "STOP_HIT"):
        return raw_status
    entry = rec.get("entry")
    sl = rec.get("sl")
    target = rec.get("target")
    direction = (rec.get("direction") or "LONG").upper()
    cmp_value = cmp_value if cmp_value is not None else _last_price(rec)

    # Already-triggered active trades
    if raw_status == "TRIGGERED":
        if cmp_value is None or not isinstance(target, (int, float)) or not isinstance(entry, (int, float)):
            return "TRIGGERED"
        if direction == "LONG":
            if isinstance(sl, (int, float)) and cmp_value <= sl:
                return "STOP_HIT"
            if cmp_value >= target:
                return "TARGET_HIT"
            progress = (cmp_value - entry) / max(target - entry, 1e-9)
        else:
            if isinstance(sl, (int, float)) and cmp_value >= sl:
                return "STOP_HIT"
            if cmp_value <= target:
                return "TARGET_HIT"
            progress = (entry - cmp_value) / max(entry - target, 1e-9)
        return "RUNNING" if progress >= _RUNNING_PROGRESS else "TRIGGERED"

    # Pre-trigger: WAITING vs APPROACHING
    if cmp_value is None or not isinstance(entry, (int, float)) or entry <= 0:
        return raw_status or "WAITING"
    distance = abs(cmp_value - entry) / entry
    if distance <= _APPROACH_PCT:
        return "APPROACHING"
    return "WAITING"


# ─────────────────────────────────────────────────────────────────────────
# Watcher
# ─────────────────────────────────────────────────────────────────────────

async def _tick() -> None:
    """One pass over active+today signals — emit transitions."""
    try:
        from dashboard.backend.terminal_events import (
            publish_event,
            read_active_trades,
            read_today_signals,
        )
    except Exception as exc:
        log.debug("lifecycle tick: terminal_events import failed: %s", exc)
        return

    seen: Dict[str, Dict[str, Any]] = {}
    try:
        for r in read_active_trades():
            sym = r.get("symbol")
            if sym:
                seen[sym] = r
        for r in read_today_signals():
            sym = r.get("symbol")
            if sym and sym not in seen:
                seen[sym] = r
    except Exception as exc:
        log.debug("lifecycle tick: read failed: %s", exc)
        return

    for sym, rec in seen.items():
        try:
            cmp_value = _last_price(rec)
            new_state = classify(rec, cmp_value)
            old_state = _LAST_STATE.get(sym)
            if old_state == new_state:
                continue
            _LAST_STATE[sym] = new_state
            if old_state is None:
                # first observation — only announce notable states
                if new_state in ("APPROACHING", "TRIGGERED", "RUNNING", "TARGET_HIT", "STOP_HIT"):
                    publish_event("LIFECYCLE", sym, _payload(rec, old_state, new_state, cmp_value))
                continue
            publish_event("LIFECYCLE", sym, _payload(rec, old_state, new_state, cmp_value))
            # Fire alert side-effects for "loud" transitions
            try:
                from dashboard.backend.alerts import dispatch_alert
                dispatch_alert(sym, old_state, new_state, rec, cmp_value)
            except Exception as exc:
                log.debug("alert dispatch failed: %s", exc)
        except Exception as exc:
            log.debug("lifecycle classify failed for %s: %s", sym, exc)


def _payload(rec: Dict[str, Any], old: Optional[str], new: str, cmp_value: Optional[float]) -> Dict[str, Any]:
    return {
        "from": old,
        "to": new,
        "symbol": rec.get("symbol"),
        "direction": rec.get("direction"),
        "entry": rec.get("entry"),
        "sl": rec.get("sl"),
        "target": rec.get("target"),
        "rr": rec.get("rr"),
        "probability": rec.get("probability"),
        "expected_outcome": rec.get("expected_outcome"),
        "cmp": cmp_value,
        "ts": int(time.time()),
    }


async def _run() -> None:
    log.info("lifecycle watcher running (tick=%ds)", _TICK_SECONDS)
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            await _tick()
        except Exception as exc:
            log.debug("lifecycle tick crashed: %s", exc)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("lifecycle watcher stopped")


def start_lifecycle_watcher() -> None:
    global _task, _stop_event
    if _task and not _task.done():
        return
    loop = asyncio.get_event_loop()
    _stop_event = asyncio.Event()
    _task = loop.create_task(_run(), name="lifecycle-watcher")


def stop_lifecycle_watcher() -> None:
    global _task
    if _stop_event:
        _stop_event.set()
    if _task and not _task.done():
        _task.cancel()
    _task = None
