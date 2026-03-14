from __future__ import annotations

from typing import Any

from dashboard.backend.db import (
    create_running_trade,
    get_active_running_trade_by_symbol,
    get_stock_recommendations,
    list_running_trades,
    update_running_trade,
)


def _norm_symbol(symbol: str) -> str:
    return symbol.replace("NSE:", "").replace("NFO:", "").strip().upper()


def _extract_symbol_prices(snapshot: dict[str, Any]) -> dict[str, float]:
    """
    Extract latest symbol prices from engine snapshot payload.
    The schema varies by producer, so this intentionally checks multiple keys.
    """
    prices: dict[str, float] = {}
    for trade in snapshot.get("active_trades", []):
        symbol = _norm_symbol(str(trade.get("symbol", "")))
        if not symbol:
            continue
        for key in ("current_price", "ltp", "last_price", "price", "spot", "entry"):
            value = trade.get(key)
            if isinstance(value, (int, float)) and value > 0:
                prices[symbol] = float(value)
                break
    return prices


def _should_activate(entry: float, current: float, entry_zone: list[float] | None = None) -> bool:
    if entry_zone and len(entry_zone) == 2:
        low, high = sorted([float(entry_zone[0]), float(entry_zone[1])])
        return low <= current <= high
    tolerance = entry * 0.003
    return abs(current - entry) <= tolerance


def _distance_to_target(current: float, targets: list[float]) -> float | None:
    if not targets:
        return None
    return float(max(targets) - current)


def process_recommendation_triggers(snapshot: dict[str, Any]) -> dict[str, int]:
    """
    WebSocket-loop hook:
    - activates recommendations when entry/entry-zone is reached
    - updates running-trade metrics on every snapshot tick
    """
    prices = _extract_symbol_prices(snapshot)
    if not prices:
        return {"activated": 0, "updated": 0}

    recommendations = get_stock_recommendations("SWING", limit=100) + get_stock_recommendations("LONGTERM", limit=100)
    activated = 0
    for rec in recommendations:
        symbol = _norm_symbol(str(rec.get("symbol", "")))
        current = prices.get(symbol)
        if current is None:
            continue
        active = get_active_running_trade_by_symbol(symbol)
        if active:
            continue
        entry = float(rec.get("entry_price", 0) or 0)
        if entry <= 0:
            continue
        entry_zone = rec.get("entry_zone") if isinstance(rec.get("entry_zone"), list) else None
        if not _should_activate(entry, current, entry_zone):
            continue

        stop_loss = rec.get("stop_loss")
        if stop_loss is None:
            stop_loss = entry * 0.94
        targets = rec.get("targets") if isinstance(rec.get("targets"), list) else []
        pnl = current - entry
        create_running_trade(
            {
                "symbol": symbol,
                "recommendation_id": rec.get("id"),
                "entry_price": entry,
                "stop_loss": float(stop_loss),
                "targets": targets,
                "current_price": current,
                "profit_loss": pnl,
                "drawdown": min(0.0, pnl),
                "distance_to_target": _distance_to_target(current, targets),
                "distance_to_stop_loss": current - float(stop_loss),
                "status": "RUNNING",
            }
        )
        activated += 1

    running = list_running_trades(limit=200, active_only=True)
    updated = 0
    for trade in running:
        symbol = _norm_symbol(str(trade.get("symbol", "")))
        current = prices.get(symbol)
        if current is None:
            continue
        entry = float(trade["entry_price"])
        stop = float(trade["stop_loss"])
        targets = trade.get("targets") if isinstance(trade.get("targets"), list) else []
        pnl = current - entry
        drawdown = min(float(trade.get("drawdown", 0) or 0), pnl)
        status = "RUNNING"
        if targets and current >= max(float(t) for t in targets):
            status = "TARGET_HIT"
        elif current <= stop:
            status = "STOP_HIT"
        update_running_trade(
            int(trade["id"]),
            current_price=current,
            profit_loss=pnl,
            drawdown=drawdown,
            distance_to_target=_distance_to_target(current, [float(t) for t in targets]),
            distance_to_stop_loss=current - stop,
            status=status,
        )
        updated += 1

    return {"activated": activated, "updated": updated}
