"""
services/position_stores.py
===========================
Store adapters for the shared PositionTrackingService. Each adapter exposes a
position store (a table of live trades) behind ONE small interface, so the
single tracking engine can run identically against multiple stores:

  - PortfolioPositionStore  → `portfolio_positions` (system-wide book)
  - UserPositionStore       → `user_positions`      (per-user book)   [PR-B]

PR-A introduces only PortfolioPositionStore and is behavior-preserving: the
system portfolio is tracked exactly as before, just routed through this
adapter instead of inline SQL.

Normalized shape (TrackedPosition) lets the engine stay store-agnostic; each
adapter maps its own column names / status vocabulary to this shape and back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class TrackedPosition:
    id: int
    symbol: str
    entry: float
    stop_loss: float
    target_1: float | None
    target_2: float | None
    status: str
    created_at: str | None
    high_since_entry: float | None
    low_since_entry: float | None
    days_held: int
    raw: dict


@runtime_checkable
class PositionStore(Protocol):
    """Interface every position store implements for the shared tracker."""

    name: str

    def enabled(self) -> bool: ...
    def list_active(self) -> list[TrackedPosition]: ...
    def update_metrics(self, position_id: int, **metrics) -> None: ...
    def close(self, position_id: int, exit_price: float, exit_reason: str) -> None: ...
    def map_status(self, canonical: str) -> str: ...


class PortfolioPositionStore:
    """System-wide `portfolio_positions` book. Wraps the existing db helpers so
    behavior is byte-identical to the pre-refactor inline tracker."""

    name = "system_portfolio"

    def enabled(self) -> bool:
        return True

    def list_active(self) -> list[TrackedPosition]:
        from dashboard.backend.db.portfolio import get_portfolio

        out: list[TrackedPosition] = []
        for r in get_portfolio(include_closed=False) or []:
            try:
                out.append(TrackedPosition(
                    id=int(r["id"]),
                    symbol=r["symbol"],
                    entry=float(r["entry_price"]),
                    stop_loss=float(r["stop_loss"]),
                    target_1=float(r["target_1"]) if r.get("target_1") else None,
                    target_2=float(r["target_2"]) if r.get("target_2") else None,
                    status=r.get("status", "ACTIVE"),
                    created_at=r.get("created_at"),
                    high_since_entry=(float(r["high_since_entry"]) if r.get("high_since_entry") else None),
                    low_since_entry=(float(r["low_since_entry"]) if r.get("low_since_entry") else None),
                    days_held=int(r.get("days_held") or 0),
                    raw=r,
                ))
            except Exception:
                continue
        return out

    def update_metrics(self, position_id: int, **metrics) -> None:
        from dashboard.backend.db.portfolio import update_position_price

        update_position_price(position_id, **metrics)

    def close(self, position_id: int, exit_price: float, exit_reason: str) -> None:
        from services.portfolio_manager import close_portfolio_position

        close_portfolio_position(position_id, exit_price, exit_reason)

    def map_status(self, canonical: str) -> str:
        # portfolio_positions already uses the canonical vocabulary
        # (ACTIVE / TARGET_HIT / STOP_HIT / CLOSED) — identity map.
        return canonical
