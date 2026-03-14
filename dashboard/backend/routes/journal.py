"""
dashboard/backend/routes/journal.py
Trade journal — filterable, sortable trade history from dashboard.db.
"""

import os
from fastapi import APIRouter, Query, Header, HTTPException
from typing import Optional
from pydantic import BaseModel
from dashboard.backend.db import get_connection

router = APIRouter(prefix="/api/journal", tags=["journal"])


class TradeRow(BaseModel):
    date: str
    symbol: str
    direction: str
    setup: str
    entry: Optional[float] = None
    exit_price: Optional[float] = None
    result: str
    pnl_r: Optional[float] = None


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


@router.post("/sync")
def sync_trades(
    trades: list[TradeRow],
    x_sync_key: Optional[str] = Header(default=None, alias="X-Sync-Key"),
):
    """
    Upsert trades from local trade_ledger. Run sync_trades_to_cloud.ps1 to push.
    Optional: set TRADES_SYNC_KEY env and pass X-Sync-Key header.
    """
    sync_key = os.getenv("TRADES_SYNC_KEY", "").strip()
    if sync_key and x_sync_key != sync_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Sync-Key")

    conn = get_connection()
    try:
        conn.execute("DELETE FROM trades")
        for t in trades:
            if not (t.date and t.symbol):
                continue
            conn.execute(
                """
                INSERT INTO trades (date, symbol, direction, setup, entry, exit_price, result, pnl_r)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    t.date.strip(),
                    t.symbol.strip().upper(),
                    (t.direction or "LONG")[:5].upper(),
                    (t.setup or "").strip(),
                    t.entry,
                    t.exit_price,
                    (t.result or "RUNNING")[:10].upper(),
                    t.pnl_r,
                ),
            )
        conn.commit()
        inserted = len(trades)
    finally:
        conn.close()
    return {"status": "ok", "synced": inserted}
