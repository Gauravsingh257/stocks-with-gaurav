"""
dashboard/backend/terminal_events.py

Phase 2 — AI Trade Opportunity Terminal: signal + event bus.

Three responsibilities:

1. **Signal normalization** — convert raw signal payloads from
   `signals:today:YYYY-MM-DD` and `engine:snapshot` into the standardized
   schema consumed by the /terminal frontend.
2. **Event ring** — Redis-backed bounded list (`terminal:events`) capped at
   ~50 entries for the discovery feed (new setup / sweep / trigger).
3. **Pub/Sub fan-out** — publish on `terminal:signals:pub` and
   `terminal:events:pub` channels so the WebSocket layer can push instantly
   to every connected client.

All Redis operations are best-effort and never raise — degrades gracefully
to in-memory mode if Redis is unavailable.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    _IST = timezone.utc  # type: ignore[assignment]

log = logging.getLogger("dashboard.terminal_events")

# ── Redis keys / channels ────────────────────────────────────────────────
EVENTS_LIST_KEY = "terminal:events"          # Redis list (LPUSH + LTRIM)
EVENTS_PUB_CHANNEL = "terminal:events:pub"   # pub/sub channel for events
SIGNALS_PUB_CHANNEL = "terminal:signals:pub" # pub/sub channel for new signals
EVENTS_RING_SIZE = 50

# In-memory fallback ring used when Redis is unavailable.
_mem_ring: deque[Dict[str, Any]] = deque(maxlen=EVENTS_RING_SIZE)
_mem_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────
# Redis helpers
# ─────────────────────────────────────────────────────────────────────────

def _r():
    """Best-effort Redis client — returns None if unavailable."""
    try:
        from dashboard.backend.cache import _get_redis
        return _get_redis()
    except Exception:
        return None


def _today_signals_key() -> str:
    return f"signals:today:{date.today().isoformat()}"


# ─────────────────────────────────────────────────────────────────────────
# Signal normalization
# ─────────────────────────────────────────────────────────────────────────

_SETUP_HINTS = (
    ("SETUP_A", "A"), ("SETUP-A", "A"),
    ("SETUP_B", "B"), ("SETUP-B", "B"),
    ("SETUP_C", "C"), ("SETUP-C", "C"),
    ("SETUP_D", "D"), ("SETUP-D", "D"),
    ("SETUP_E", "B"),  # legacy E maps to B-grade structural
)


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _derive_setup(strategy_name: Optional[str], setup: Any) -> str:
    if setup:
        s = str(setup).upper()
        m = next((c for c in s if c in "ABCD"), None)
        if m:
            return m
    if strategy_name:
        up = str(strategy_name).upper()
        for needle, code in _SETUP_HINTS:
            if needle in up:
                return code
    return "A"


def _grade_from_score(score: Any) -> str:
    s = _to_float(score)
    if s is None:
        return "B"
    # Engine confluence scores normally 0–10; tolerate 0–100.
    if s > 10:
        s = s / 10.0
    if s >= 8.5:
        return "A+"
    if s >= 7.0:
        return "A"
    if s >= 5.5:
        return "B"
    return "C"


def _direction(value: Any) -> str:
    if value is None:
        return "LONG"
    s = str(value).upper()
    if s in ("LONG", "BUY", "BULL", "BULLISH"):
        return "LONG"
    if s in ("SHORT", "SELL", "BEAR", "BEARISH"):
        return "SHORT"
    return s


def _rr(entry: Optional[float], stop: Optional[float], target: Optional[float]) -> Optional[float]:
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = abs(target - entry)
    return round(reward / risk, 2)


def _status_from_signal(raw: Dict[str, Any]) -> str:
    """Infer WAITING / TAPPED / TRIGGERED for a signal record."""
    status = raw.get("status")
    if status:
        return str(status).upper()
    result = (raw.get("result") or "").upper()
    if result in ("HIT", "WIN", "TARGET"):
        return "TARGET_HIT"
    if result in ("LOSS", "STOP", "SL"):
        return "STOP_HIT"
    if raw.get("triggered") or raw.get("entry_filled"):
        return "TRIGGERED"
    if raw.get("near_setup"):
        return "TAPPED"
    return "WAITING"


def _build_analysis(raw: Dict[str, Any], direction: str, setup: str) -> Dict[str, Any]:
    """Synthesize the standardized `analysis` block expected by the frontend."""
    tech = raw.get("technical_signals") or {}
    layer1 = bool(raw.get("layer1_pass") or raw.get("liquidity") or tech.get("liquidity_sweep"))
    layer2 = bool(raw.get("layer2_pass") or raw.get("structure") or tech.get("structure"))
    layer3 = bool(raw.get("layer3_pass") or tech.get("htf_bias"))
    fvg_present = bool(tech.get("fvg") or raw.get("fvg") or layer1)
    ob_present = bool(tech.get("order_block") or raw.get("ob") or layer2)
    htf_bias = tech.get("htf_bias") or ("LONG" if direction == "LONG" else "SHORT")
    structure = tech.get("structure") or ("BOS" if layer2 else "CHOCH")
    reason = (
        raw.get("reasoning_summary")
        or raw.get("reasoning")
        or raw.get("analysis")
        or ("Liquidity sweep + OB tap + FVG rejection" if (layer1 and ob_present) else "Setup forming — awaiting confirmation.")
    )
    return {
        "htf_bias": htf_bias,
        "structure": structure,
        "liquidity": layer1,
        "fvg": fvg_present,
        "ob": ob_present,
        "reason": reason,
        "setup_grade": setup,
    }


def normalize_signal(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert any engine signal payload to the public terminal schema."""
    if not isinstance(raw, dict):
        return {}
    symbol = raw.get("symbol") or raw.get("tradingsymbol") or "UNKNOWN"
    direction = _direction(raw.get("direction"))
    entry = _to_float(raw.get("entry"))
    stop = _to_float(raw.get("stop_loss") or raw.get("sl"))
    target = _to_float(raw.get("target1") or raw.get("target") or raw.get("target_1"))
    target2 = _to_float(raw.get("target2") or raw.get("target_2"))
    setup = _derive_setup(raw.get("strategy_name"), raw.get("setup"))
    confidence = _grade_from_score(raw.get("score") or raw.get("confidence_score") or raw.get("confidence"))
    rr_value = _to_float(raw.get("risk_reward")) or _rr(entry, stop, target)
    status = _status_from_signal(raw)
    analysis = _build_analysis(raw, direction, setup)
    return {
        "id": raw.get("signal_id") or raw.get("id") or f"{symbol}-{raw.get('timestamp', '')}",
        "symbol": str(symbol).upper(),
        "direction": direction,
        "entry": entry,
        "sl": stop,
        "target": target,
        "target2": target2,
        "rr": rr_value,
        "setup": setup,
        "confidence": confidence,
        "status": status,
        "score": _to_float(raw.get("score") or raw.get("confidence_score")),
        "strategy": raw.get("strategy_name"),
        "timestamp": raw.get("timestamp") or datetime.now(_IST).isoformat(timespec="seconds"),
        "analysis": analysis,
    }


# ─────────────────────────────────────────────────────────────────────────
# Reading signals from Redis
# ─────────────────────────────────────────────────────────────────────────

def read_today_signals(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return today's normalized signals (oldest first)."""
    key = f"signals:today:{date_str}" if date_str else _today_signals_key()
    r = _r()
    if r is None:
        return []
    try:
        raw_items: Iterable[str] = r.lrange(key, 0, -1) or []
    except Exception as exc:
        log.debug("read_today_signals lrange failed: %s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for item in raw_items:
        try:
            payload = json.loads(item) if isinstance(item, str) else item
        except (TypeError, ValueError):
            continue
        norm = normalize_signal(payload)
        if norm:
            out.append(norm)
    return out


def read_active_trades() -> List[Dict[str, Any]]:
    """Return engine snapshot's running trades, normalized."""
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot
        snap = get_engine_snapshot()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for t in snap.get("active_trades") or []:
        norm = normalize_signal(t)
        if not norm:
            continue
        # Active trades should mark TRIGGERED if not already terminal.
        if norm["status"] == "WAITING":
            norm["status"] = "TRIGGERED"
        out.append(norm)
    return out


def get_signal_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Most recent signal/active trade for a symbol — preferring active trades."""
    sym = symbol.strip().upper()
    for t in read_active_trades():
        if t.get("symbol") == sym:
            return t
    todays = read_today_signals()
    matches = [s for s in todays if s.get("symbol") == sym]
    return matches[-1] if matches else None


# ─────────────────────────────────────────────────────────────────────────
# Event ring + pub/sub
# ─────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(_IST).isoformat(timespec="seconds")


def publish_event(event_type: str, symbol: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Append to event ring and fan out via pub/sub.

    `event_type` examples: NEW_SETUP, LIQUIDITY_SWEEP, ENTRY_TRIGGER,
    TARGET_HIT, STOP_HIT.
    """
    event = {
        "type": event_type.upper(),
        "symbol": str(symbol).upper(),
        "time": _now_iso(),
        "ts": int(time.time()),
        "payload": payload or {},
    }
    # In-memory mirror — always works.
    with _mem_lock:
        _mem_ring.appendleft(event)

    r = _r()
    if r is None:
        return event
    try:
        r.lpush(EVENTS_LIST_KEY, json.dumps(event, default=str))
        r.ltrim(EVENTS_LIST_KEY, 0, EVENTS_RING_SIZE - 1)
        r.expire(EVENTS_LIST_KEY, 86400)  # 24h
        r.publish(EVENTS_PUB_CHANNEL, json.dumps(event, default=str))
    except Exception as exc:
        log.debug("publish_event redis push failed: %s", exc)
    return event


def publish_signal(raw_signal: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a freshly generated signal and broadcast it.

    Also auto-emits a NEW_SETUP discovery event so the feed stays in sync.
    """
    normalized = normalize_signal(raw_signal)
    if not normalized:
        return {}
    r = _r()
    if r is not None:
        try:
            r.publish(SIGNALS_PUB_CHANNEL, json.dumps(normalized, default=str))
        except Exception as exc:
            log.debug("publish_signal redis publish failed: %s", exc)
    publish_event(
        "NEW_SETUP",
        normalized.get("symbol", ""),
        {
            "direction": normalized.get("direction"),
            "setup": normalized.get("setup"),
            "confidence": normalized.get("confidence"),
            "rr": normalized.get("rr"),
        },
    )
    return normalized


def get_recent_events(limit: int = EVENTS_RING_SIZE) -> List[Dict[str, Any]]:
    """Return events newest-first, capped at ``limit``."""
    limit = max(1, min(limit, EVENTS_RING_SIZE))
    r = _r()
    if r is not None:
        try:
            raw_items = r.lrange(EVENTS_LIST_KEY, 0, limit - 1) or []
            out: List[Dict[str, Any]] = []
            for item in raw_items:
                try:
                    out.append(json.loads(item) if isinstance(item, str) else item)
                except (TypeError, ValueError):
                    continue
            if out:
                return out
        except Exception as exc:
            log.debug("get_recent_events lrange failed: %s", exc)
    with _mem_lock:
        return list(_mem_ring)[:limit]
