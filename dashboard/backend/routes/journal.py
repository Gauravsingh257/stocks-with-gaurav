"""
dashboard/backend/routes/journal.py
Trade journal — filterable, sortable trade history from dashboard.db.
"""

from fastapi import APIRouter, Query
from typing import Optional
from dashboard.backend.db import get_connection

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


@router.get("")
def get_journal(
    symbol:    Optional[str] = Query(default=None),
    setup:     Optional[str] = Query(default=None),
    result:    Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    date_to:   Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    limit:     int           = Query(default=100, ge=1, le=1000),
    offset:    int           = Query(default=0,   ge=0),
):
    """
    Paginated, filtered trade journal.
    All filters are optional and combinable.
    """
    clauses = ["result IN ('WIN','LOSS','RUNNING')"]
    params  = []

    if symbol:
        clauses.append("symbol LIKE ?")
        params.append(f"%{symbol.upper()}%")
    if setup:
        clauses.append("setup LIKE ?")
        params.append(f"%{setup.upper()}%")
    if result:
        clauses.append("result = ?")
        params.append(result.upper())
    if direction:
        clauses.append("direction = ?")
        params.append(direction.upper())
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to + " 23:59:59")

    where = " AND ".join(clauses)
    sql = f"""
        SELECT id, date, symbol, direction, setup, entry, exit_price, result, pnl_r, score, notes
        FROM trades
        WHERE {where}
        ORDER BY date DESC
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]

    count_sql = f"SELECT COUNT(*) FROM trades WHERE {where}"

    conn = get_connection()
    try:
        total = conn.execute(count_sql, params[:-2]).fetchone()[0]
        rows  = _rows_to_dicts(conn.execute(sql, params).fetchall())
    finally:
        conn.close()

    return {
        "trades":  rows,
        "total":   total,
        "limit":   limit,
        "offset":  offset,
        "has_more": (offset + limit) < total,
    }


@router.get("/trade/{trade_id}")
def get_trade_detail(trade_id: int):
    """Single trade detail — includes all fields."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return {"error": f"Trade {trade_id} not found"}

    return dict(row)


@router.get("/symbols")
def get_symbols():
    """Distinct symbols traded — for filter dropdown."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM trades ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()
    return {"symbols": [r[0] for r in rows]}


@router.get("/setups")
def get_setups():
    """Distinct setup names — for filter dropdown."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT setup FROM trades ORDER BY setup"
        ).fetchall()
    finally:
        conn.close()
    return {"setups": [r[0] for r in rows]}


@router.get("/recent/{n}")
def get_recent_trades(n: int = 20):
    """Last N completed trades — used by AI chatbot context builder."""
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                """
                SELECT id, date, symbol, direction, setup, entry, exit_price, result, pnl_r
                FROM trades
                WHERE result IN ('WIN','LOSS')
                ORDER BY date DESC
                LIMIT ?
                """,
                (n,),
            ).fetchall()
        )
    finally:
        conn.close()
    return {"recent_trades": rows, "count": len(rows)}


@router.post("/add-note/{trade_id}")
def add_trade_note(trade_id: int, note: str = Query(...)):
    """Add or update analyst note on a trade."""
    conn = get_connection()
    try:
        conn.execute("UPDATE trades SET notes = ? WHERE id = ?", (note, trade_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "trade_id": trade_id, "note": note}
