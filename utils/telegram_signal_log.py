"""
Persist Telegram-delivered trading signals to ai_learning signal_log (SQLite).

Called after a successful Bot API send so deduped/skipped signals are not logged.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

_IST = ZoneInfo("Asia/Kolkata")


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_score(v: Any) -> float | None:
    x = _coerce_float(v)
    if x is None:
        return None
    return x


def _strip_html(t: str) -> str:
    if not t:
        return ""
    s = re.sub(r"<[^>]+>", " ", t)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_signal_record(
    message: str,
    signal_id: str | None,
    signal_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Merge Telegram HTML + optional engine metadata into one row for signal_log.
    """
    meta = dict(signal_meta or {})
    now = datetime.now(_IST)
    ts_str = meta.get("timestamp") or now.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    sym = meta.get("symbol") or meta.get("tradingsymbol")
    direction = meta.get("direction")
    if isinstance(direction, str):
        direction = direction.upper() if direction else None

    strategy = (
        meta.get("strategy_name")
        or meta.get("setup")
        or meta.get("signal_kind")
        or ""
    )

    entry = _coerce_float(meta.get("entry"))
    sl = _coerce_float(meta.get("stop_loss") or meta.get("sl"))
    t1 = _coerce_float(meta.get("target1") or meta.get("target"))
    t2 = _coerce_float(meta.get("target2"))
    score = _coerce_score(meta.get("score") or meta.get("smc_score"))
    conf = _coerce_score(meta.get("confidence"))
    result = meta.get("result")
    if isinstance(result, str):
        result = result.upper() if result else None
    pnl_r = _coerce_float(meta.get("pnl_r") or meta.get("exit_r"))

    payload: dict[str, Any] = {
        "signal_id": signal_id or f"SIG-{now.timestamp():.0f}",
        "timestamp": ts_str,
        "symbol": sym,
        "direction": direction,
        "strategy_name": strategy if isinstance(strategy, str) else str(strategy),
        "entry": entry,
        "stop_loss": sl,
        "target1": t1,
        "target2": t2,
        "score": score,
        "confidence": conf,
        "result": result,
        "pnl_r": pnl_r,
        "signal_kind": meta.get("signal_kind") or "",
        "delivery_channel": meta.get("delivery_channel") or "telegram",
        "delivery_format": meta.get("delivery_format") or "text",
        "telegram_html": message,
        "telegram_text": _strip_html(message)[:4000],
    }
    # Merge remaining meta (excluding keys we already flattened)
    skip = {
        "timestamp", "symbol", "tradingsymbol", "direction", "strategy_name", "setup",
        "entry", "stop_loss", "sl", "target1", "target", "target2", "score", "smc_score",
        "confidence", "result", "pnl_r", "exit_r", "signal_kind", "delivery_channel",
        "delivery_format",
    }
    for k, v in meta.items():
        if k not in skip and k not in payload:
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = str(v)
    return payload


def persist_telegram_signal(
    message: str,
    signal_id: str | None,
    signal_meta: dict[str, Any] | None = None,
) -> None:
    """Append one row to signal_log. Swallows errors so Telegram delivery is never blocked."""
    try:
        from ai_learning.data.trade_store import TradeStore

        rec = build_signal_record(message, signal_id, signal_meta)
        TradeStore().log_signal(rec)
    except Exception as exc:
        logger.warning("signal_log persist failed: %s", exc)
