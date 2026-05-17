"""
PHASE G2-3 — Canonical lifecycle ledger (SHADOW, append-only).

Records the planned-execution state machine transitions to the
`lifecycle_events` table — the future single source of truth for
FILTERED→ARMED→ENTRY_ACTIVE→LIVE_TRACKING→CLOSED/EXPIRED
(see docs/CANONICAL_ARCHITECTURE.md Task 7).

Distinct from the existing `dashboard/backend/lifecycle.py` (a read-side
terminal-event classifier). This module is WRITE-ONLY persistence.

G2-3 scope: nothing reads this for behaviour. Every call is best-effort
and MUST NOT raise — a ledger failure can never affect a recommendation,
a position, or a scan. Zero behaviour change.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("dashboard.lifecycle_ledger")

VALID_STATES = {
    "FILTERED", "ARMED", "ENTRY_ACTIVE", "LIVE_TRACKING",
    "CLOSED_WIN", "CLOSED_LOSS", "CLOSED_EXIT", "EXPIRED",
}


def record_lifecycle_event(
    symbol: str,
    state: str,
    *,
    horizon: str = "SWING",
    prev_state: str | None = None,
    source: str = "system",
    recommendation_id: int | None = None,
    position_id: int | None = None,
    user_id: int | None = None,
    planned_entry: float | None = None,
    zone_low: float | None = None,
    zone_high: float | None = None,
    stop_loss: float | None = None,
    target_1: float | None = None,
    target_2: float | None = None,
    cmp_at_event: float | None = None,
    rr_planned: float | None = None,
    regime: str | None = None,
    sector: str | None = None,
    sector_band: str | None = None,
    setup: str | None = None,
    confidence: float | None = None,
    manual_override: bool = False,
    details: dict | None = None,
) -> None:
    """Append one canonical lifecycle event. Best-effort; never raises."""
    try:
        if not symbol or state not in VALID_STATES:
            return
        from dashboard.backend.db import get_connection

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO lifecycle_events (
                    symbol, horizon, state, prev_state, source,
                    recommendation_id, position_id, user_id,
                    planned_entry, zone_low, zone_high, stop_loss,
                    target_1, target_2, cmp_at_event, rr_planned,
                    regime, sector, sector_band, setup, confidence,
                    manual_override, details
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(symbol), str(horizon), str(state),
                    prev_state, str(source),
                    recommendation_id, position_id, user_id,
                    planned_entry, zone_low, zone_high, stop_loss,
                    target_1, target_2, cmp_at_event, rr_planned,
                    regime, sector, sector_band, setup, confidence,
                    1 if manual_override else 0,
                    json.dumps(details, default=str) if details else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # never propagate — shadow only
        log.debug("lifecycle event skipped (%s %s): %s", symbol, state, exc)


def recent_events(limit: int = 100, symbol: str | None = None, state: str | None = None) -> list[dict]:
    """Read-only inspection. Best-effort; returns [] on any failure."""
    try:
        from dashboard.backend.db import get_connection

        conn = get_connection()
        try:
            q = "SELECT * FROM lifecycle_events"
            clauses, args = [], []
            if symbol:
                clauses.append("symbol = ?")
                args.append(symbol)
            if state:
                clauses.append("state = ?")
                args.append(state)
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            q += " ORDER BY id DESC LIMIT ?"
            args.append(max(1, min(limit, 1000)))
            return [dict(r) for r in conn.execute(q, args).fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        log.debug("recent_events failed: %s", exc)
        return []


def summary() -> dict:
    """Read-only state counts."""
    try:
        from dashboard.backend.db import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT state, COUNT(*) c FROM lifecycle_events GROUP BY state"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0]
            return {"total": total, "by_state": {r["state"]: r["c"] for r in rows}}
        finally:
            conn.close()
    except Exception as exc:
        log.debug("summary failed: %s", exc)
        return {"total": 0, "by_state": {}}
