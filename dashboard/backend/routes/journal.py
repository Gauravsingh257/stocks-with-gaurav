"""
dashboard/backend/routes/journal.py
Trade journal — filterable, sortable trade history from dashboard.db.
"""

import os
import sqlite3
import logging
import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query, Header, HTTPException
from typing import Optional
from pydantic import BaseModel
from dashboard.backend.db import get_connection

logger = logging.getLogger("journal")

# Path to the ai_learning signals DB (relative to project root)
_AI_LEARNING_DB = Path(__file__).resolve().parents[3] / "ai_learning" / "data" / "trade_learning.db"

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
    screenshot_path: Optional[str] = None
    smc_breakdown: Optional[str] = None
    pine_xval: Optional[str] = None


def _rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


def _migrate_signal_log_columns(conn: sqlite3.Connection) -> None:
    """Match ai_learning TradeStore migrations for read-only journal queries."""
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(signal_log)").fetchall()}
    except sqlite3.OperationalError:
        return
    if "signal_kind" not in existing:
        conn.execute("ALTER TABLE signal_log ADD COLUMN signal_kind TEXT DEFAULT ''")
    if "delivery_channel" not in existing:
        conn.execute("ALTER TABLE signal_log ADD COLUMN delivery_channel TEXT DEFAULT 'telegram'")


def _query_signal_log(
    date_from: Optional[str],
    date_to: Optional[str],
    symbol: Optional[str],
    signal_kind: Optional[str],
    limit: int,
    offset: int,
) -> tuple[list, int]:
    """
    Rows from ai_learning signal_log (Telegram-delivered signals + metadata).
    date_from / date_to: YYYY-MM-DD, inclusive, applied to DATE(created_at).
    """
    if not _AI_LEARNING_DB.exists():
        return [], 0

    clauses: list[str] = []
    params: list = []

    if date_from:
        clauses.append("DATE(created_at) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("DATE(created_at) <= ?")
        params.append(date_to)
    if symbol:
        clauses.append("UPPER(COALESCE(symbol,'')) LIKE ?")
        params.append(f"%{symbol.strip().upper()}%")
    if signal_kind:
        clauses.append("UPPER(COALESCE(signal_kind,'')) = ?")
        params.append(signal_kind.strip().upper())

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    try:
        conn = sqlite3.connect(str(_AI_LEARNING_DB))
        conn.row_factory = sqlite3.Row
        try:
            _migrate_signal_log_columns(conn)
            conn.commit()
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM signal_log{where_sql}",
                params,
            ).fetchone()
            total = int(count_row[0]) if count_row else 0
            rows = conn.execute(
                f"""
                SELECT signal_id, timestamp, symbol, direction, strategy_name,
                       entry, stop_loss, target1, target2, score, confidence,
                       result, pnl_r, created_at, signal_json, signal_kind, delivery_channel
                FROM signal_log
                {where_sql}
                ORDER BY datetime(created_at) DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                sj = d.get("signal_json")
                if sj and isinstance(sj, str):
                    try:
                        parsed = json.loads(sj)
                        d["delivery_format"] = parsed.get("delivery_format")
                        if not d.get("signal_kind") and parsed.get("signal_kind"):
                            d["signal_kind"] = parsed.get("signal_kind")
                    except json.JSONDecodeError:
                        pass
                out.append(d)
            return out, total
        except sqlite3.OperationalError as exc:
            logger.warning("signal_log query error: %s", exc)
            return [], 0
        finally:
            conn.close()
    except Exception as exc:
        logger.error("signal_log query failed: %s", exc)
        return [], 0


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
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")

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
    n = max(1, min(n, 500))
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


@router.get("/signals")
def get_signals(
    date_from: Optional[str] = Query(default=None, description="YYYY-MM-DD inclusive"),
    date_to: Optional[str] = Query(default=None, description="YYYY-MM-DD inclusive"),
    symbol: Optional[str] = Query(default=None),
    signal_kind: Optional[str] = Query(default=None, description="e.g. ENTRY, EXIT_TARGET, EMA_CROSS"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """
    Telegram-delivered signals from signal_log (all kinds: entries, exits, catch-up, etc.).
    If neither date_from nor date_to is set, uses today only (server local date).
    """
    today_str = date.today().isoformat()
    if not date_from and not date_to:
        d_from = d_to = today_str
    elif date_from and not date_to:
        d_from = d_to = date_from
    elif date_to and not date_from:
        d_from = d_to = date_to
    else:
        d_from, d_to = date_from, date_to

    signals, total = _query_signal_log(d_from, d_to, symbol, signal_kind, limit, offset)
    src = "signal_log" if _AI_LEARNING_DB.exists() else "none"
    return {
        "signals": signals,
        "count": len(signals),
        "total": total,
        "date_from": d_from,
        "date_to": d_to,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total,
        "source": src,
    }


def _query_signals_from_redis(date_str: str) -> list:
    """Read today's signals from Redis list (cross-container on Railway)."""
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is None:
            return []
        key = f"signals:today:{date_str}"
        raw_items = r.lrange(key, 0, -1)
        if not raw_items:
            return []
        return [json.loads(item) for item in raw_items]
    except Exception as exc:
        logger.debug("Redis signal query failed: %s", exc)
        return []


@router.get("/signals-today")
def get_signals_today():
    """
    Return all signals generated today.
    Tries Redis first (works cross-container on Railway),
    falls back to local ai_learning signal_log SQLite.
    """
    today_str = date.today().isoformat()

    # Redis first (engine pushes signals here on Railway)
    redis_signals = _query_signals_from_redis(today_str)
    if redis_signals:
        return {
            "signals": redis_signals,
            "count": len(redis_signals),
            "total": len(redis_signals),
            "date": today_str,
            "source": "redis",
        }

    # Fallback: local SQLite (works in local dev)
    signals, total = _query_signal_log(today_str, today_str, None, None, 500, 0)
    if not _AI_LEARNING_DB.exists():
        return {"signals": [], "count": 0, "total": 0, "date": today_str, "source": "none"}

    return {
        "signals": signals,
        "count": len(signals),
        "total": total,
        "date": today_str,
        "source": "signal_log",
    }


@router.get("/sync")
def sync_info():
    """Help text when sync URL is opened in browser (POST only for actual sync)."""
    return {
        "message": "Use POST to sync trades. Run sync_trades_to_cloud.ps1 or sync.bat (answer y to sync).",
        "method": "POST",
        "usage": "Invoke-RestMethod -Uri .../api/journal/sync -Method Post -Body $json -ContentType 'application/json'",
    }


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


@router.post("/trade")
def upsert_single_trade(
    trade: TradeRow,
    x_sync_key: Optional[str] = Header(default=None, alias="X-Sync-Key"),
):
    """
    Upsert a single trade from the engine (fire-and-forget from engine worker).
    Duplicate detection: same (date, symbol, setup) = update; otherwise insert.
    """
    sync_key = os.getenv("TRADES_SYNC_KEY", "").strip()
    if sync_key and x_sync_key != sync_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Sync-Key")

    if not (trade.date and trade.symbol):
        raise HTTPException(status_code=400, detail="date and symbol are required")

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM trades WHERE date = ? AND symbol = ? AND setup = ?",
            (trade.date.strip(), trade.symbol.strip().upper(), (trade.setup or "").strip()),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE trades SET direction=?, entry=?, exit_price=?, result=?, pnl_r=?, screenshot_path=?, smc_breakdown=?, pine_xval=? WHERE id=?",
                (
                    (trade.direction or "LONG")[:5].upper(),
                    trade.entry,
                    trade.exit_price,
                    (trade.result or "RUNNING")[:10].upper(),
                    trade.pnl_r,
                    trade.screenshot_path,
                    trade.smc_breakdown,
                    trade.pine_xval,
                    existing[0],
                ),
            )
            action = "updated"
            trade_id = existing[0]
        else:
            cur = conn.execute(
                "INSERT INTO trades (date, symbol, direction, setup, entry, exit_price, result, pnl_r, screenshot_path, smc_breakdown, pine_xval) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    trade.date.strip(),
                    trade.symbol.strip().upper(),
                    (trade.direction or "LONG")[:5].upper(),
                    (trade.setup or "").strip(),
                    trade.entry,
                    trade.exit_price,
                    (trade.result or "RUNNING")[:10].upper(),
                    trade.pnl_r,
                    trade.screenshot_path,
                    trade.smc_breakdown,
                    trade.pine_xval,
                ),
            )
            action = "inserted"
            trade_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "action": action, "trade_id": trade_id}


# ─────────────────────────────────────────────────────────────────────────────
# Signal Rejections — WHY trades were NOT taken
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/rejections")
def get_rejections(
    symbol: Optional[str] = Query(default=None),
    reason: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Get signal rejection log — shows why potential signals were NOT taken."""
    clauses = []
    params = []

    if symbol:
        clauses.append("symbol LIKE ?")
        params.append(f"%{symbol.upper()}%")
    if reason:
        clauses.append("reason = ?")
        params.append(reason.upper())
    if date_from:
        clauses.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM signal_rejections {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT * FROM signal_rejections {where}
                ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return {
            "rejections": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except sqlite3.OperationalError:
        # Table may not exist yet
        return {"rejections": [], "total": 0, "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.post("/rejections/sync")
def sync_rejections(
    x_sync_key: Optional[str] = Header(default=None, alias="X-Sync-Key"),
):
    """Bulk-sync signal rejections from engine's signal_rejections_today.json."""
    sync_key = os.getenv("TRADES_SYNC_KEY", "").strip()
    if sync_key and x_sync_key != sync_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Sync-Key")

    # Read the rejection file from engine
    rejection_file = Path(__file__).resolve().parents[3] / "signal_rejections_today.json"
    if not rejection_file.exists():
        return {"status": "ok", "synced": 0, "message": "no rejection file found"}

    try:
        records = json.loads(rejection_file.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "error", "message": "failed to parse rejection file"}

    conn = get_connection()
    synced = 0
    try:
        for r in records:
            conn.execute(
                """INSERT OR IGNORE INTO signal_rejections
                   (timestamp, symbol, setup, direction, reason, detail,
                    score, breakdown, entry, sl, target, rr,
                    has_ob, has_fvg, has_choch, has_bos, has_sweep, regime)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.get("timestamp", ""),
                    r.get("symbol", ""),
                    r.get("setup"),
                    r.get("direction"),
                    r.get("reason", "UNKNOWN"),
                    r.get("detail"),
                    r.get("score"),
                    json.dumps(r.get("breakdown")) if r.get("breakdown") else None,
                    r.get("entry"),
                    r.get("sl"),
                    r.get("target"),
                    r.get("rr"),
                    1 if r.get("ob") else 0,
                    1 if r.get("fvg") else 0,
                    1 if r.get("choch") else 0,
                    1 if r.get("bos") else 0,
                    1 if r.get("sweep") else 0,
                    r.get("regime"),
                ),
            )
            synced += 1
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "synced": synced}


# ─────────────────────────────────────────────────────────────────────────────
# Swing & Long-Term ideas journal (from stock_recommendations + running_trades)
# ─────────────────────────────────────────────────────────────────────────────

def _ideas_journal(
    agent_type: str,
    symbol: Optional[str],
    status: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    limit: int,
    offset: int,
) -> dict:
    conn = get_connection()
    try:
        clauses = ["sr.agent_type = ?"]
        params: list = [agent_type]

        if symbol:
            clauses.append("sr.symbol LIKE ?")
            params.append(f"%{symbol.upper()}%")
        if date_from:
            clauses.append("DATE(sr.created_at) >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("DATE(sr.created_at) <= ?")
            params.append(date_to)
        if status:
            clauses.append("rt.status = ?")
            params.append(status.upper())

        where = " AND ".join(clauses)
        count_sql = f"""
            SELECT COUNT(*) FROM stock_recommendations sr
            LEFT JOIN running_trades rt
                ON rt.recommendation_id = sr.id
                AND rt.id = (SELECT MAX(id) FROM running_trades WHERE recommendation_id = sr.id)
            WHERE {where}
        """
        query_sql = f"""
            SELECT
                sr.id, sr.symbol, sr.agent_type, sr.entry_price, sr.stop_loss,
                sr.targets, sr.confidence_score, sr.setup, sr.expected_holding_period,
                sr.reasoning, sr.created_at AS recommended_at,
                rt.current_price, rt.profit_loss, rt.profit_loss_pct,
                rt.days_held, rt.status, rt.high_since_entry, rt.low_since_entry,
                rt.updated_at, rt.drawdown_pct
            FROM stock_recommendations sr
            LEFT JOIN running_trades rt
                ON rt.recommendation_id = sr.id
                AND rt.id = (SELECT MAX(id) FROM running_trades WHERE recommendation_id = sr.id)
            WHERE {where}
            ORDER BY sr.created_at DESC
            LIMIT ? OFFSET ?
        """
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(query_sql, params + [limit, offset]).fetchall()

        items = []
        for r in rows:
            import json as _json
            targets_raw = r["targets"] if isinstance(r["targets"], str) else "[]"
            try:
                targets = _json.loads(targets_raw)
            except Exception:
                targets = []
            items.append({
                "id": r["id"],
                "symbol": r["symbol"],
                "setup": r["setup"],
                "entry_price": r["entry_price"],
                "stop_loss": r["stop_loss"],
                "targets": targets,
                "confidence_score": r["confidence_score"],
                "expected_holding_period": r["expected_holding_period"],
                "reasoning_summary": (r["reasoning"] or "")[:300],
                "recommended_at": r["recommended_at"],
                "current_price": r["current_price"],
                "profit_loss": r["profit_loss"] or 0.0,
                "profit_loss_pct": r["profit_loss_pct"] or 0.0,
                "drawdown_pct": r["drawdown_pct"] or 0.0,
                "days_held": r["days_held"] or 0,
                "status": r["status"] or "PENDING",
                "high_since_entry": r["high_since_entry"],
                "low_since_entry": r["low_since_entry"],
                "updated_at": r["updated_at"],
            })
    finally:
        conn.close()

    return {
        "ideas": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total,
        "agent_type": agent_type,
    }


@router.get("/swing-ideas")
def get_swing_ideas(
    symbol: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="RUNNING|TARGET_HIT|STOP_HIT|PENDING"),
    date_from: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """All swing recommendations with live tracking data. Filterable."""
    return _ideas_journal("SWING", symbol, status, date_from, date_to, limit, offset)


@router.get("/longterm-ideas")
def get_longterm_ideas(
    symbol: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="RUNNING|TARGET_HIT|STOP_HIT|PENDING"),
    date_from: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """All long-term recommendations with live tracking data. Filterable."""
    return _ideas_journal("LONGTERM", symbol, status, date_from, date_to, limit, offset)
