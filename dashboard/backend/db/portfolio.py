"""
dashboard/backend/db/portfolio.py

Portfolio persistence layer — two buckets (SWING, LONGTERM) with immutable journal.

Tables:
  - portfolio_positions: Active + closed positions (persist until SL/Target/Manual close)
  - portfolio_journal: Immutable trade history — never deleted

Stocks STAY in the portfolio until explicitly resolved. No auto-expiry.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

from .schema import get_connection

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# ──────────────────────────────────────────────────────────────────────────────
# DDL — portfolio tables
# ──────────────────────────────────────────────────────────────────────────────

PORTFOLIO_DDL = """
-- ─────────────────────────────────────────
-- TABLE: portfolio_positions (persistent portfolio)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio_positions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                TEXT NOT NULL,
    horizon               TEXT NOT NULL CHECK(horizon IN ('SWING','LONGTERM')),
    direction             TEXT NOT NULL DEFAULT 'LONG' CHECK(direction IN ('LONG','SHORT')),
    entry_price           REAL NOT NULL,
    stop_loss             REAL NOT NULL,
    target_1              REAL,
    target_2              REAL,
    current_price         REAL,
    profit_loss           REAL NOT NULL DEFAULT 0,
    profit_loss_pct       REAL NOT NULL DEFAULT 0,
    drawdown              REAL NOT NULL DEFAULT 0,
    drawdown_pct          REAL NOT NULL DEFAULT 0,
    high_since_entry      REAL,
    low_since_entry       REAL,
    days_held             INTEGER NOT NULL DEFAULT 0,
    confidence_score      REAL DEFAULT 0,
    reasoning             TEXT DEFAULT '',
    recommendation_id     INTEGER,
    status                TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','TARGET_HIT','STOP_HIT','CLOSED','PARTIAL_EXIT')),
    exit_price            REAL,
    exit_reason           TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at             TEXT,
    UNIQUE(symbol, horizon, status) -- only one ACTIVE position per symbol+horizon
);
CREATE INDEX IF NOT EXISTS idx_portfolio_status ON portfolio_positions(status);
CREATE INDEX IF NOT EXISTS idx_portfolio_horizon ON portfolio_positions(horizon, status);
CREATE INDEX IF NOT EXISTS idx_portfolio_symbol ON portfolio_positions(symbol);

-- ─────────────────────────────────────────
-- TABLE: portfolio_journal (immutable trade log)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio_journal (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id           INTEGER NOT NULL,
    symbol                TEXT NOT NULL,
    horizon               TEXT NOT NULL CHECK(horizon IN ('SWING','LONGTERM')),
    direction             TEXT NOT NULL DEFAULT 'LONG',
    entry_price           REAL NOT NULL,
    exit_price            REAL,
    stop_loss             REAL,
    target_1              REAL,
    target_2              REAL,
    profit_loss           REAL DEFAULT 0,
    profit_loss_pct       REAL DEFAULT 0,
    days_held             INTEGER DEFAULT 0,
    high_since_entry      REAL,
    low_since_entry       REAL,
    confidence_score      REAL,
    reasoning             TEXT,
    exit_reason           TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    closed_at             TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(position_id) REFERENCES portfolio_positions(id)
);
CREATE INDEX IF NOT EXISTS idx_journal_horizon ON portfolio_journal(horizon, closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_symbol ON portfolio_journal(symbol);
"""


def init_portfolio_db() -> None:
    """Create portfolio tables (idempotent)."""
    conn = get_connection()
    try:
        conn.executescript(PORTFOLIO_DDL)
        conn.commit()
        logger.info("[Portfolio] Tables initialized")
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Portfolio CRUD
# ──────────────────────────────────────────────────────────────────────────────

MAX_SWING_POSITIONS = 10
MAX_LONGTERM_POSITIONS = 10


def add_position(payload: dict) -> int:
    """
    Add a stock to the portfolio. Returns position ID.
    Rejects if horizon is at max capacity or symbol already active in that horizon.
    """
    symbol = payload["symbol"].strip().upper()
    horizon = payload["horizon"].upper()
    if horizon not in ("SWING", "LONGTERM"):
        raise ValueError(f"Invalid horizon: {horizon}")

    conn = get_connection()
    try:
        # Check for existing active position
        existing = conn.execute(
            "SELECT id FROM portfolio_positions WHERE symbol = ? AND horizon = ? AND status = 'ACTIVE'",
            (symbol, horizon),
        ).fetchone()
        if existing:
            raise ValueError(f"{symbol} already active in {horizon} portfolio")

        # Check capacity
        max_pos = MAX_SWING_POSITIONS if horizon == "SWING" else MAX_LONGTERM_POSITIONS
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM portfolio_positions WHERE horizon = ? AND status = 'ACTIVE'",
            (horizon,),
        ).fetchone()["cnt"]
        if count >= max_pos:
            raise ValueError(f"{horizon} portfolio full ({count}/{max_pos})")

        entry = float(payload["entry_price"])
        sl = float(payload["stop_loss"])
        t1 = float(payload.get("target_1") or 0) or None
        t2 = float(payload.get("target_2") or 0) or None
        cmp = float(payload.get("current_price") or entry)

        cursor = conn.execute(
            """
            INSERT INTO portfolio_positions
                (symbol, horizon, direction, entry_price, stop_loss, target_1, target_2,
                 current_price, confidence_score, reasoning, recommendation_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                symbol, horizon,
                payload.get("direction", "LONG"),
                entry, sl, t1, t2, cmp,
                float(payload.get("confidence_score", 0)),
                payload.get("reasoning", ""),
                payload.get("recommendation_id"),
            ),
        )
        conn.commit()
        pos_id = cursor.lastrowid
        logger.info("[Portfolio] Added %s to %s (id=%d)", symbol, horizon, pos_id)
        return pos_id
    finally:
        conn.close()


def close_position(position_id: int, exit_price: float, exit_reason: str) -> dict:
    """
    Close a portfolio position and journal it. Returns journal entry dict.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE id = ? AND status = 'ACTIVE'",
            (position_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Position {position_id} not found or already closed")

        pos = dict(row)
        entry = float(pos["entry_price"])
        pl = round(exit_price - entry, 2)
        pl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
        now_str = datetime.now(_IST).isoformat()

        # Update position
        conn.execute(
            """
            UPDATE portfolio_positions SET
                status = ?, exit_price = ?, exit_reason = ?,
                profit_loss = ?, profit_loss_pct = ?,
                current_price = ?, closed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (exit_reason, exit_price, exit_reason, pl, pl_pct, exit_price, now_str, now_str, position_id),
        )

        # Journal entry (immutable — never deleted)
        conn.execute(
            """
            INSERT INTO portfolio_journal
                (position_id, symbol, horizon, direction, entry_price, exit_price,
                 stop_loss, target_1, target_2, profit_loss, profit_loss_pct,
                 days_held, high_since_entry, low_since_entry, confidence_score,
                 reasoning, exit_reason, created_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id, pos["symbol"], pos["horizon"], pos["direction"],
                entry, exit_price, pos["stop_loss"], pos.get("target_1"),
                pos.get("target_2"), pl, pl_pct, pos.get("days_held", 0),
                pos.get("high_since_entry"), pos.get("low_since_entry"),
                pos.get("confidence_score"), pos.get("reasoning"),
                exit_reason, pos["created_at"], now_str,
            ),
        )
        conn.commit()
        logger.info("[Portfolio] Closed %s (id=%d, reason=%s, PL=%.2f%%)",
                     pos["symbol"], position_id, exit_reason, pl_pct)
        return {
            "position_id": position_id,
            "symbol": pos["symbol"],
            "horizon": pos["horizon"],
            "entry": entry,
            "exit": exit_price,
            "pnl_pct": pl_pct,
            "exit_reason": exit_reason,
        }
    finally:
        conn.close()


def get_portfolio(horizon: str | None = None, include_closed: bool = False) -> list[dict]:
    """Get portfolio positions. Default: ACTIVE only."""
    conn = get_connection()
    try:
        conditions = []
        params: list = []
        if horizon:
            conditions.append("horizon = ?")
            params.append(horizon.upper())
        if not include_closed:
            conditions.append("status = 'ACTIVE'")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""
            SELECT * FROM portfolio_positions
            {where}
            ORDER BY
                CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END,
                datetime(created_at) DESC
            LIMIT 100
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_position_by_id(position_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM portfolio_positions WHERE id = ?", (position_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_active_position_by_symbol(symbol: str, horizon: str | None = None) -> dict | None:
    """Check if a symbol is already active in the portfolio."""
    conn = get_connection()
    try:
        if horizon:
            row = conn.execute(
                "SELECT * FROM portfolio_positions WHERE symbol = ? AND horizon = ? AND status = 'ACTIVE'",
                (symbol.strip().upper(), horizon.upper()),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM portfolio_positions WHERE symbol = ? AND status = 'ACTIVE'",
                (symbol.strip().upper(),),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_position_price(position_id: int, **kwargs) -> None:
    """Update live price data for a position."""
    allowed = {
        "current_price", "profit_loss", "profit_loss_pct",
        "drawdown", "drawdown_pct", "high_since_entry", "low_since_entry",
        "days_held", "status", "exit_price", "exit_reason", "closed_at",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return

    updates["updated_at"] = datetime.now(_IST).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [position_id]

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE portfolio_positions SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def get_portfolio_counts() -> dict:
    """Get active position counts per horizon."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT horizon, COUNT(*) as cnt FROM portfolio_positions WHERE status = 'ACTIVE' GROUP BY horizon"
        ).fetchall()
        counts = {r["horizon"]: r["cnt"] for r in rows}
        return {
            "swing": counts.get("SWING", 0),
            "swing_max": MAX_SWING_POSITIONS,
            "longterm": counts.get("LONGTERM", 0),
            "longterm_max": MAX_LONGTERM_POSITIONS,
        }
    finally:
        conn.close()


def get_journal(horizon: str | None = None, limit: int = 50) -> list[dict]:
    """Get closed trade journal entries (immutable history)."""
    conn = get_connection()
    try:
        if horizon:
            rows = conn.execute(
                "SELECT * FROM portfolio_journal WHERE horizon = ? ORDER BY datetime(closed_at) DESC LIMIT ?",
                (horizon.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM portfolio_journal ORDER BY datetime(closed_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_journal_stats(horizon: str | None = None) -> dict:
    """Aggregate journal performance stats."""
    conn = get_connection()
    try:
        where = "WHERE horizon = ?" if horizon else ""
        params = [horizon.upper()] if horizon else []

        row = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN profit_loss_pct > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN profit_loss_pct <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(profit_loss_pct), 2) as avg_pnl_pct,
                ROUND(SUM(profit_loss_pct), 2) as total_pnl_pct,
                MAX(profit_loss_pct) as best_pnl_pct,
                MIN(profit_loss_pct) as worst_pnl_pct,
                ROUND(AVG(days_held), 1) as avg_days_held
            FROM portfolio_journal
            {where}
            """,
            params,
        ).fetchone()

        total = row["total_trades"] or 0
        wins = row["wins"] or 0
        return {
            "total_trades": total,
            "wins": wins,
            "losses": row["losses"] or 0,
            "hit_rate_pct": round(wins / total * 100, 1) if total > 0 else 0.0,
            "avg_pnl_pct": row["avg_pnl_pct"] or 0.0,
            "total_pnl_pct": row["total_pnl_pct"] or 0.0,
            "best_pnl_pct": row["best_pnl_pct"] or 0.0,
            "worst_pnl_pct": row["worst_pnl_pct"] or 0.0,
            "avg_days_held": row["avg_days_held"] or 0.0,
        }
    finally:
        conn.close()


def seed_portfolio_from_recommendations() -> int:
    """
    One-time migration: seed portfolio from existing active recommendations + running trades.
    Skips any symbol already in portfolio.
    """
    conn = get_connection()
    try:
        # Get all active running trades with their recommendation data
        rows = conn.execute(
            """
            SELECT rt.*, sr.agent_type, sr.confidence_score, sr.reasoning,
                   sr.targets as reco_targets
            FROM running_trades rt
            LEFT JOIN stock_recommendations sr ON rt.recommendation_id = sr.id
            WHERE rt.status = 'RUNNING'
            ORDER BY rt.created_at DESC
            """,
        ).fetchall()

        seeded = 0
        for row in rows:
            row_d = dict(row)
            symbol = row_d["symbol"]
            horizon = row_d.get("agent_type") or "SWING"

            # Skip if already in portfolio
            existing = conn.execute(
                "SELECT 1 FROM portfolio_positions WHERE symbol = ? AND horizon = ? AND status = 'ACTIVE'",
                (symbol, horizon),
            ).fetchone()
            if existing:
                continue

            targets = []
            raw = row_d.get("reco_targets") or row_d.get("targets") or "[]"
            if isinstance(raw, str):
                try:
                    targets = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    targets = []

            t1 = float(targets[0]) if len(targets) > 0 else None
            t2 = float(targets[-1]) if len(targets) > 1 else t1

            conn.execute(
                """
                INSERT INTO portfolio_positions
                    (symbol, horizon, direction, entry_price, stop_loss, target_1, target_2,
                     current_price, profit_loss, profit_loss_pct, drawdown, drawdown_pct,
                     high_since_entry, low_since_entry, days_held,
                     confidence_score, reasoning, recommendation_id, status, created_at)
                VALUES (?, ?, 'LONG', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    symbol, horizon,
                    row_d["entry_price"], row_d["stop_loss"], t1, t2,
                    row_d.get("current_price", row_d["entry_price"]),
                    row_d.get("profit_loss", 0), row_d.get("profit_loss_pct", 0),
                    row_d.get("drawdown", 0), row_d.get("drawdown_pct", 0),
                    row_d.get("high_since_entry"), row_d.get("low_since_entry"),
                    row_d.get("days_held", 0),
                    row_d.get("confidence_score", 0),
                    row_d.get("reasoning", ""),
                    row_d.get("recommendation_id"),
                    row_d.get("created_at", datetime.now(_IST).isoformat()),
                ),
            )
            seeded += 1

        conn.commit()
        if seeded:
            logger.info("[Portfolio] Seeded %d positions from existing running_trades", seeded)
        return seeded
    finally:
        conn.close()
