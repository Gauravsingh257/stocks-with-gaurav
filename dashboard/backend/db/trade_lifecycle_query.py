"""
dashboard/backend/db/trade_lifecycle_query.py
=============================================
Read side of the lifecycle ledger: server-side filtering, pagination, stats and
the per-trade timeline.

Every figure Track Record publishes comes from here, and this module touches
`trade_lifecycle` only — never `stock_recommendations`. That single constraint
is what stops the page reporting research ideas as trades.

Filtering is pushed into SQL (indexed on symbol / status / source / portfolio /
engine / year / month / created_at) so the page stays instant as the ledger
grows; the client never receives more than one page of rows.
"""

from __future__ import annotations

import logging

from .schema import get_connection
from .trade_lifecycle import (
    init_lifecycle_db, EXECUTED_STATUSES, CLOSED_STATUSES, OPEN_STATUSES,
    PENDING_STATUSES,
)

logger = logging.getLogger(__name__)

_ALLOWED_SORT = {
    "created_at": "created_at", "updated_at": "updated_at", "exit_at": "exit_at",
    "pnl_pct": "pnl_pct", "symbol": "symbol", "confidence": "confidence",
    "holding_days": "holding_days",
}


def _build_where(
    *, portfolio: str | None = None, status: str | None = None,
    execution: str | None = None, engine: str | None = None,
    month: int | None = None, year: int | None = None,
    min_confidence: float | None = None, outcome: str | None = None,
    symbol: str | None = None, include_duplicates: bool = False,
    stage: str | None = None, engine_version: str | None = None,
    record_state: str = "ACTIVE",
) -> tuple[str, list]:
    parts: list[str] = []
    params: list = []

    if not include_duplicates:
        # Re-close artifacts stay in the ledger for audit but never in stats.
        parts.append("is_duplicate = 0")

    # Soft delete: nothing is ever removed, only reclassified. The default view
    # shows ACTIVE records; ARCHIVED / HIDDEN / DUPLICATE stay queryable.
    if record_state and record_state.upper() != "ALL":
        parts.append("COALESCE(record_state,'ACTIVE') = ?")
        params.append(record_state.upper())

    if stage and stage.upper() != "ALL":
        parts.append("stage = ?")
        params.append(stage.upper())

    if engine_version:
        parts.append("engine_version = ?")
        params.append(engine_version)

    if portfolio and portfolio.upper() != "ALL":
        p = portfolio.upper()
        if p == "RESEARCH":
            parts.append("source = 'RESEARCH'")
        else:
            parts.append("(portfolio = ? OR source = ?)")
            params += [p, p]

    if status and status.upper() != "ALL":
        parts.append("status = ?")
        params.append(status.upper())

    if execution and execution.upper() != "ALL":
        parts.append("executed = ?")
        params.append(1 if execution.upper() == "EXECUTED" else 0)

    if engine and engine.upper() != "ALL":
        parts.append("engine = ?")
        params.append(engine.upper())

    if year:
        parts.append("year = ?")
        params.append(int(year))
    if month:
        parts.append("month = ?")
        params.append(int(month))

    if min_confidence is not None:
        parts.append("confidence >= ?")
        params.append(float(min_confidence))

    if outcome and outcome.upper() != "ALL":
        # Winner / loser is only meaningful for a position that actually existed.
        if outcome.upper() == "WINNER":
            parts.append("executed = 1 AND pnl_pct > 0")
        else:
            parts.append("executed = 1 AND pnl_pct <= 0 AND pnl_pct IS NOT NULL")

    if symbol:
        parts.append("symbol LIKE ?")
        params.append(f"%{symbol.strip().upper()}%")

    return (("WHERE " + " AND ".join(parts)) if parts else ""), params


def query(*, limit: int = 50, offset: int = 0, sort: str = "created_at",
          direction: str = "desc", **filters) -> dict:
    """One page of lifecycle rows plus the total matching count."""
    init_lifecycle_db()
    where, params = _build_where(**filters)
    col = _ALLOWED_SORT.get(sort, "created_at")
    dirn = "ASC" if str(direction).lower() == "asc" else "DESC"
    conn = get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) n FROM trade_lifecycle {where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM trade_lifecycle {where} "
            f"ORDER BY {col} {dirn} NULLS LAST, rowid DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "limit": int(limit),
            "offset": int(offset),
            "has_more": offset + len(rows) < total,
        }
    finally:
        conn.close()


def stats(**filters) -> dict:
    """Summary cards — computed from the ledger, over the FULL filtered set.

    Deliberately independent of pagination: the cards describe every row the
    filter matches, not the page being viewed. They are also independent of the
    status filter unless the caller passes one, so selecting "Target Hit" can
    never turn the headline into a win rate over the winners.
    """
    init_lifecycle_db()
    where, params = _build_where(**filters)
    conn = get_connection()
    try:
        cl = ",".join("?" * len(CLOSED_STATUSES))
        op = ",".join("?" * len(OPEN_STATUSES))
        pe = ",".join("?" * len(PENDING_STATUSES))
        # Bindings must appear in the exact order the placeholders do in the SQL
        # below. Built as an explicit sequence rather than by arithmetic so a
        # future edit to the query can't silently desynchronise the count.
        stat_params = (
            list(CLOSED_STATUSES)      # closed_trades
            + list(CLOSED_STATUSES)    # wins
            + list(OPEN_STATUSES)      # open_trades
            + list(PENDING_STATUSES)   # pending_entries
            + list(CLOSED_STATUSES)    # avg_mae
            + list(CLOSED_STATUSES)    # avg_mfe
            + list(CLOSED_STATUSES)    # avg_return
            + list(CLOSED_STATUSES)    # avg_rr
            + list(CLOSED_STATUSES)    # avg_hold
            + list(CLOSED_STATUSES)    # sum_return
        )
        row = conn.execute(
            f"""
            SELECT
              COUNT(*)                                                        AS signals_generated,
              SUM(CASE WHEN executed = 1 THEN 1 ELSE 0 END)                   AS entries_triggered,
              SUM(CASE WHEN status IN ({cl}) THEN 1 ELSE 0 END)               AS closed_trades,
              SUM(CASE WHEN status = 'TARGET_HIT' THEN 1 ELSE 0 END)          AS target_hits,
              SUM(CASE WHEN status = 'STOP_HIT'  THEN 1 ELSE 0 END)           AS stop_hits,
              SUM(CASE WHEN status IN ({cl}) AND pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN status IN ({op}) THEN 1 ELSE 0 END)               AS open_trades,
              SUM(CASE WHEN status IN ({pe}) THEN 1 ELSE 0 END)               AS pending_entries,
              SUM(CASE WHEN status = 'EXPIRED' THEN 1 ELSE 0 END)             AS expired,
              SUM(CASE WHEN status = 'NEVER_EXECUTED' THEN 1 ELSE 0 END)      AS never_executed,
              SUM(CASE WHEN partial_exits > 0 THEN 1 ELSE 0 END)              AS partial_exits,
              SUM(CASE WHEN stage = 'IDEA' THEN 1 ELSE 0 END)                  AS ideas_generated,
              SUM(CASE WHEN stage = 'POSITION' AND executed = 1 THEN 1 ELSE 0 END) AS positions_taken,
              AVG(CASE WHEN status IN ({cl}) THEN mae_pct END)                 AS avg_mae,
              AVG(CASE WHEN status IN ({cl}) THEN mfe_pct END)                 AS avg_mfe,
              AVG(CASE WHEN status IN ({cl}) THEN pnl_pct END)                AS avg_return,
              AVG(CASE WHEN status IN ({cl}) THEN rr_realized END)            AS avg_rr,
              AVG(CASE WHEN status IN ({cl}) THEN holding_days END)           AS avg_hold,
              SUM(CASE WHEN status IN ({cl}) THEN pnl_pct ELSE 0 END)         AS sum_return
            FROM trade_lifecycle {where}
            """,
            stat_params + params,
        ).fetchone()

        d = {k: (row[k] or 0) for k in row.keys()}
        sig = d["signals_generated"] or 0
        ent = d["entries_triggered"] or 0
        closed = d["closed_trades"] or 0

        def pct(a, b):
            return round(a / b * 100, 1) if b else 0.0

        return {
            "signals_generated": sig,
            "entries_triggered": ent,
            "execution_rate_pct": pct(ent, sig),
            "closed_trades": closed,
            "target_hits": d["target_hits"],
            "stop_hits": d["stop_hits"],
            "target_hit_rate_pct": pct(d["target_hits"], closed),
            "sl_rate_pct": pct(d["stop_hits"], closed),
            "wins": d["wins"],
            "win_rate_pct": pct(d["wins"], closed),
            "avg_return_pct": round(d["avg_return"], 2) if d["avg_return"] else 0.0,
            "sum_return_pct": round(d["sum_return"], 2) if d["sum_return"] else 0.0,
            "avg_rr": round(d["avg_rr"], 2) if d["avg_rr"] else None,
            "avg_holding_days": round(d["avg_hold"], 1) if d["avg_hold"] else 0.0,
            "open_trades": d["open_trades"],
            "pending_entries": d["pending_entries"],
            "expired_signals": d["expired"],
            "never_executed": d["never_executed"],
            "partial_exits": d["partial_exits"],
            # Funnel — the metric that survives keeping research as stage one:
            # ideas generated -> positions actually taken -> targets reached.
            # Reporting only the 55 trades would silently discard the 200 ideas.
            "ideas_generated": d["ideas_generated"],
            "positions_taken": d["positions_taken"],
            "idea_to_entry_pct": pct(d["positions_taken"], d["ideas_generated"]),
            "avg_mae_pct": round(d["avg_mae"], 2) if d["avg_mae"] else None,
            "avg_mfe_pct": round(d["avg_mfe"], 2) if d["avg_mfe"] else None,
            "basis": ("win rate and returns cover CLOSED, genuinely-executed positions only; "
                      "ideas that never filled are counted as signals but carry no P&L"),
        }
    finally:
        conn.close()


def timeline(lifecycle_id: str) -> dict:
    """One trade's full record plus its append-only event history."""
    init_lifecycle_db()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM trade_lifecycle WHERE uuid = ?", (lifecycle_id,)).fetchone()
        if not row:
            return {"found": False}
        events = conn.execute(
            "SELECT event, from_status, to_status, price, note, occurred_at, recorded_at "
            "FROM trade_lifecycle_events WHERE lifecycle_id = ? "
            "ORDER BY datetime(occurred_at) ASC, id ASC",
            (lifecycle_id,),
        ).fetchall()
        return {"found": True, "trade": dict(row), "events": [dict(e) for e in events]}
    finally:
        conn.close()


def facets() -> dict:
    """Distinct filter values actually present, so the UI never offers an empty one."""
    init_lifecycle_db()
    conn = get_connection()
    try:
        def col(name):
            return [r[0] for r in conn.execute(
                f"SELECT DISTINCT {name} FROM trade_lifecycle "
                f"WHERE {name} IS NOT NULL AND is_duplicate = 0 ORDER BY {name}"
            ).fetchall()]
        return {
            "portfolios": col("portfolio"), "sources": col("source"),
            "statuses": col("status"), "engines": col("engine"),
            "years": [int(y) for y in col("year")],
        }
    finally:
        conn.close()
