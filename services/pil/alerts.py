"""
services/pil/alerts.py
======================
Intelligent portfolio alerts (Part 10). A stateful rule engine that consolidates
the threshold breaches surfaced by the exposure / allocation / health / metrics
layers into a single deduplicated, self-clearing alert stream:

  SECTOR_OVERWEIGHT · SINGLE_STOCK_CONCENTRATION · TOP10_CONCENTRATION ·
  LOW_DIVERSIFICATION · CORRELATION_SPIKE · LIQUIDITY_WARNING ·
  MOMENTUM_ALLOC_HIGH · CAPITAL_DRIFT · LARGE_DRAWDOWN ·
  ENGINE_UNDERPERFORMANCE · HEALTH_DROP

evaluate() records currently-firing alerts (dedup by book+type while active),
clears the ones that have resolved, and returns the newly-fired ones so the
caller can push them to Telegram. Descriptive only — never gates a trade.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("pil.alerts")


def _signals() -> list[dict]:
    """Compute the full set of currently-firing alert signals."""
    from services.pil import accounting, exposure, allocation, health, metrics, config as pil_config
    th = pil_config.thresholds()
    books = accounting.reconstruct_all()
    met = metrics.metrics_all(books)

    signals: list[dict] = []

    # exposure + allocation already emit structured warnings
    exp = exposure.compute(books)
    alloc = allocation.compute(books)
    for w in exp["warnings"]:
        signals.append({"book": "COMBINED", **w})
    for w in alloc["warnings"]:
        signals.append({"book": "COMBINED", **w})

    # large drawdown per book
    for b in ("SWING", "LONGTERM", "MOMENTUM", "COMBINED"):
        dd = met[b]["max_drawdown_pct"]
        if abs(dd) > th["max_drawdown_warn"] * 100:
            signals.append({"book": b, "type": "LARGE_DRAWDOWN", "severity": "WARN",
                            "message": f"{b} drawdown {dd:.1f}% exceeds {th['max_drawdown_warn']*100:.0f}%",
                            "value": round(abs(dd) / 100, 4), "threshold": th["max_drawdown_warn"]})

    # engine underperformance (negative expectancy or PF < 1 with a real sample)
    for b in ("SWING", "LONGTERM", "MOMENTUM"):
        m = met[b]
        if m["closed_trades"] >= 5 and (m["expectancy_pct"] < th["min_engine_expectancy"] or
                                        (0 < m["profit_factor"] < 1.0)):
            signals.append({"book": b, "type": "ENGINE_UNDERPERFORMANCE", "severity": "WARN",
                            "message": f"{b} underperforming: expectancy {m['expectancy_pct']:+.1f}%, PF {m['profit_factor']}",
                            "value": m["expectancy_pct"], "threshold": th["min_engine_expectancy"]})

    # portfolio health drop (RED)
    hlth = health.compute(books)
    for b in ("SWING", "LONGTERM", "MOMENTUM", "COMBINED"):
        if hlth[b]["status"] == "RED":
            signals.append({"book": b, "type": "HEALTH_DROP", "severity": "CRITICAL",
                            "message": f"{b} health RED ({hlth[b]['overall']}) — weakest: {hlth[b]['worst_factor']}",
                            "value": hlth[b]["overall"], "threshold": 45})

    return signals


def evaluate(notify: bool = False) -> dict[str, Any]:
    """Reconcile firing signals with the stored alert state. Returns fired/cleared."""
    from dashboard.backend.db import pil as pildb

    signals = _signals()
    current_keys = {(s["book"], s["type"]) for s in signals}
    existing = {(a["book"], a["type"]) for a in pildb.get_alerts(active_only=True)}

    # record all currently-firing (record_alert dedups while active)
    for s in signals:
        pildb.record_alert(s["book"], s["type"], s["message"], severity=s.get("severity", "WARN"),
                           value=s.get("value"), threshold=s.get("threshold"))

    # clear alerts that no longer fire
    cleared = existing - current_keys
    for book, type_ in cleared:
        pildb.clear_alert(book, type_)

    newly = current_keys - existing
    fired = [s for s in signals if (s["book"], s["type"]) in newly]

    if notify and fired:
        _notify(fired)

    return {
        "fired": fired,
        "cleared": [{"book": b, "type": t} for b, t in cleared],
        "active_count": len(current_keys),
    }


def _notify(fired: list[dict]) -> None:
    from services.pil import config as pil_config
    if not pil_config.telegram_enabled():
        return
    from services.pil.notify import send_telegram
    lines = ["🚨 <b>PIL Alerts</b>"]
    for s in fired[:10]:
        icon = "🔴" if s.get("severity") == "CRITICAL" else "🟠"
        lines.append(f"{icon} {s['message']}")
    send_telegram("\n".join(lines))


def active() -> list[dict]:
    from dashboard.backend.db import pil as pildb
    return pildb.get_alerts(active_only=True)
