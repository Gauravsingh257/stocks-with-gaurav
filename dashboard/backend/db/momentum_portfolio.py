"""
dashboard/backend/db/momentum_portfolio.py
============================================
Persistence for the INDEPENDENT Momentum Portfolio. Completely separate tables
(`momentum_positions`, `momentum_journal`) so it never touches the Swing /
Long-Term `portfolio_positions` book. Same durable SQLite DB (Railway volume),
additive schema only.

Every row captures the full research feature set (entry model, risk model,
regime, sector, RS, volume/trend scores, ATR, MFE/MAE, quality score, entry/exit
reasons) so the live book doubles as an ongoing experiment dataset.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta

from .schema import get_connection

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

MAX_MOMENTUM_POSITIONS = int(os.getenv("MOMENTUM_MAX_POSITIONS", "20"))

_DDL = """
CREATE TABLE IF NOT EXISTS momentum_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'LONG' CHECK(direction IN ('LONG','SHORT')),
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK(status IN ('PENDING','ACTIVE','TARGET_HIT','STOP_HIT','CLOSED','EXPIRED')),
    entry_price     REAL NOT NULL,          -- planned entry (trigger)
    stop_loss       REAL NOT NULL,          -- LIVE stop (trailed up over time)
    initial_stop    REAL,                   -- stop at entry (fixed R denominator)
    target_1        REAL, target_2 REAL,
    current_price   REAL, arm_ref_price REAL,
    -- lifecycle
    entered_at      TEXT, days_held INTEGER NOT NULL DEFAULT 0,
    -- ranking + research features
    quality_score   REAL, rank INTEGER,
    entry_model     TEXT, risk_model TEXT, regime TEXT, sector TEXT,
    rs_20d REAL, volume_ratio REAL, trend_quality REAL, atr_pct REAL,
    base_atr_pct REAL, breakout_score REAL, extension_atr REAL,
    -- live metrics
    profit_loss REAL NOT NULL DEFAULT 0, profit_loss_pct REAL NOT NULL DEFAULT 0,
    drawdown_pct REAL NOT NULL DEFAULT 0, high_since_entry REAL, low_since_entry REAL,
    mfe_r REAL, mae_r REAL,
    -- explanations + sizing
    entry_reason TEXT, exit_reason TEXT, exit_price REAL,
    position_size REAL, risk_weight_pct REAL, reasoning TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_mom_pos_status ON momentum_positions(status);
CREATE INDEX IF NOT EXISTS idx_mom_pos_symbol ON momentum_positions(symbol);
-- one committed (active or armed) row per symbol
CREATE UNIQUE INDEX IF NOT EXISTS idx_mom_pos_committed_unique
    ON momentum_positions(symbol) WHERE status IN ('ACTIVE','PENDING');

CREATE TABLE IF NOT EXISTS momentum_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    entry_price REAL NOT NULL, exit_price REAL, stop_loss REAL, target_1 REAL,
    profit_loss REAL DEFAULT 0, profit_loss_pct REAL DEFAULT 0, r_multiple REAL,
    days_held INTEGER DEFAULT 0, high_since_entry REAL, low_since_entry REAL,
    mfe_r REAL, mae_r REAL,
    quality_score REAL, entry_model TEXT, risk_model TEXT, regime TEXT, sector TEXT,
    rs_20d REAL, volume_ratio REAL, trend_quality REAL, atr_pct REAL, breakout_score REAL,
    entry_reason TEXT, exit_reason TEXT NOT NULL, reasoning TEXT,
    created_at TEXT NOT NULL, closed_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(position_id) REFERENCES momentum_positions(id)
);
CREATE INDEX IF NOT EXISTS idx_mom_journal_symbol ON momentum_journal(symbol);
CREATE INDEX IF NOT EXISTS idx_mom_journal_closed ON momentum_journal(closed_at DESC);
"""


# Phase-B attribution columns (additive; nullable; safe on old rows).
_ATTR_COLS = [
    ("discovery_rank", "INTEGER"),
    ("momentum_rank", "INTEGER"),
    ("selection_reason", "TEXT"),
    ("replacement_reason", "TEXT"),
    ("classification", "TEXT"),   # ELITE | GOOD | WEAK | REPLACE
]


def _migrate_attr(conn) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(momentum_positions)").fetchall()}
    for name, decl in _ATTR_COLS:
        if name not in have:
            conn.execute(f"ALTER TABLE momentum_positions ADD COLUMN {name} {decl}")
    have_j = {r[1] for r in conn.execute("PRAGMA table_info(momentum_journal)").fetchall()}
    for name, decl in (("discovery_rank", "INTEGER"), ("momentum_rank", "INTEGER"),
                       ("selection_reason", "TEXT"), ("replacement_reason", "TEXT")):
        if name not in have_j:
            conn.execute(f"ALTER TABLE momentum_journal ADD COLUMN {name} {decl}")


def init_momentum_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(_DDL)
        _migrate_attr(conn)
        conn.commit()
        logger.info("[MomentumPortfolio] tables initialised")
    finally:
        conn.close()


def set_classification(position_id: int, classification: str, quality_score: float | None = None) -> None:
    conn = get_connection()
    try:
        if quality_score is not None:
            conn.execute("UPDATE momentum_positions SET classification=?, quality_score=?, updated_at=? WHERE id=?",
                         (classification, quality_score, datetime.now(_IST).isoformat(), position_id))
        else:
            conn.execute("UPDATE momentum_positions SET classification=?, updated_at=? WHERE id=?",
                         (classification, datetime.now(_IST).isoformat(), position_id))
        conn.commit()
    finally:
        conn.close()


_INSERT_COLS = [
    "symbol", "direction", "status", "entry_price", "stop_loss", "initial_stop", "target_1", "target_2",
    "current_price", "arm_ref_price", "entered_at", "quality_score", "rank", "entry_model",
    "risk_model", "regime", "sector", "rs_20d", "volume_ratio", "trend_quality", "atr_pct",
    "base_atr_pct", "breakout_score", "extension_atr", "entry_reason", "position_size",
    "risk_weight_pct", "reasoning",
    "discovery_rank", "momentum_rank", "selection_reason", "replacement_reason",
]


def add_position(payload: dict) -> int:
    """Insert a momentum position (PENDING by default). Enforces capacity + the
    one-committed-per-symbol rule. Returns the new id, or raises ValueError."""
    init_momentum_db()
    symbol = payload["symbol"].strip().upper()
    status = (payload.get("status") or "PENDING").upper()
    if status not in ("PENDING", "ACTIVE"):
        raise ValueError(f"add_position status must be PENDING/ACTIVE, got {status}")

    conn = get_connection()
    try:
        if conn.execute(
            "SELECT 1 FROM momentum_positions WHERE symbol=? AND status IN ('ACTIVE','PENDING')",
            (symbol,),
        ).fetchone():
            raise ValueError(f"{symbol} already committed in Momentum portfolio")
        used = conn.execute(
            "SELECT COUNT(*) FROM momentum_positions WHERE status IN ('ACTIVE','PENDING')"
        ).fetchone()[0]
        if used >= MAX_MOMENTUM_POSITIONS:
            raise ValueError(f"Momentum portfolio full ({used}/{MAX_MOMENTUM_POSITIONS})")

        entered_at = None if status == "PENDING" else datetime.now(_IST).isoformat()
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, (dict, list)):
            reasoning = json.dumps(reasoning, default=str)
        vals = {**payload, "symbol": symbol, "status": status, "entered_at": entered_at,
                "reasoning": reasoning}
        vals.setdefault("direction", "LONG")
        vals.setdefault("initial_stop", payload.get("stop_loss"))
        vals.setdefault("current_price", payload.get("arm_ref_price") or payload["entry_price"])
        cur = conn.execute(
            f"INSERT INTO momentum_positions ({', '.join(_INSERT_COLS)}) "
            f"VALUES ({', '.join(['?'] * len(_INSERT_COLS))})",
            tuple(vals.get(c) for c in _INSERT_COLS),
        )
        conn.commit()
        logger.info("[MomentumPortfolio] added %s as %s (id=%d)", symbol, status, cur.lastrowid)
        return cur.lastrowid
    finally:
        conn.close()


def get_portfolio(include_pending: bool = True, include_closed: bool = False) -> list[dict]:
    conn = get_connection()
    try:
        if include_closed:
            where = ""
        elif include_pending:
            where = "WHERE status IN ('ACTIVE','PENDING')"
        else:
            where = "WHERE status = 'ACTIVE'"
        rows = conn.execute(
            f"SELECT * FROM momentum_positions {where} "
            "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 WHEN 'PENDING' THEN 1 ELSE 2 END, "
            "quality_score DESC, datetime(created_at) DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_counts() -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) c FROM momentum_positions "
            "WHERE status IN ('ACTIVE','PENDING') GROUP BY status"
        ).fetchall()
        d = {r["status"]: r["c"] for r in rows}
        active = d.get("ACTIVE", 0); pending = d.get("PENDING", 0)
        return {"active": active, "pending": pending, "used": active + pending,
                "max": MAX_MOMENTUM_POSITIONS}
    finally:
        conn.close()


def get_active_by_symbol(symbol: str) -> dict | None:
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT * FROM momentum_positions WHERE symbol=? AND status IN ('ACTIVE','PENDING')",
            (symbol.strip().upper(),),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def lowest_active_score() -> dict | None:
    """The weakest ACTIVE holding — the replacement target when the book is full."""
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT * FROM momentum_positions WHERE status='ACTIVE' "
            "ORDER BY quality_score ASC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def update_position(position_id: int, **fields) -> None:
    allowed = {"current_price", "profit_loss", "profit_loss_pct", "drawdown_pct",
               "high_since_entry", "low_since_entry", "mfe_r", "mae_r", "days_held",
               "stop_loss", "status", "quality_score", "rank"}
    upd = {k: v for k, v in fields.items() if k in allowed}
    if not upd:
        return
    upd["updated_at"] = datetime.now(_IST).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE momentum_positions SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?",
            list(upd.values()) + [position_id],
        )
        conn.commit()
    finally:
        conn.close()


def activate_pending(position_id: int, trigger_price: float) -> bool:
    now = datetime.now(_IST).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT entry_price FROM momentum_positions WHERE id=? AND status='PENDING'",
            (position_id,),
        ).fetchone()
        if not row:
            return False
        entry = float(row["entry_price"]); pl = round(trigger_price - entry, 2)
        pl_pct = round((trigger_price - entry) / entry * 100, 2) if entry else 0.0
        conn.execute(
            "UPDATE momentum_positions SET status='ACTIVE', entered_at=?, current_price=?, "
            "high_since_entry=?, low_since_entry=?, profit_loss=?, profit_loss_pct=?, "
            "drawdown_pct=0, days_held=0, updated_at=? WHERE id=? AND status='PENDING'",
            (now, trigger_price, trigger_price, trigger_price, pl, pl_pct, now, position_id),
        )
        conn.commit()
        logger.info("[MomentumPortfolio] ARM→ACTIVE id=%d @%.2f", position_id, trigger_price)
        return True
    finally:
        conn.close()


def expire_pending(position_id: int, reason: str = "EXPIRED") -> bool:
    now = datetime.now(_IST).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE momentum_positions SET status='EXPIRED', exit_reason=?, closed_at=?, "
            "updated_at=? WHERE id=? AND status='PENDING'",
            (reason, now, now, position_id),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def close_position(position_id: int, exit_price: float, exit_reason: str) -> dict:
    """Close an ACTIVE position → journal it (immutable). Returns the journal row."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM momentum_positions WHERE id=? AND status='ACTIVE'", (position_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Momentum position {position_id} not found/active")
        pos = dict(row)
        entry = float(pos["entry_price"]); sl = float(pos["stop_loss"])
        sl_init = float(pos.get("initial_stop") or sl)   # R measured from the ENTRY stop
        pl = round(exit_price - entry, 2)
        pl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
        r_mult = round((exit_price - entry) / (entry - sl_init), 3) if (entry - sl_init) > 0 else None
        now = datetime.now(_IST).isoformat()
        final = exit_reason if exit_reason in ("TARGET_HIT", "STOP_HIT", "CLOSED") else "CLOSED"
        conn.execute(
            "UPDATE momentum_positions SET status=?, exit_price=?, exit_reason=?, profit_loss=?, "
            "profit_loss_pct=?, current_price=?, closed_at=?, updated_at=? WHERE id=?",
            (final, exit_price, exit_reason, pl, pl_pct, exit_price, now, now, position_id),
        )
        conn.execute(
            "INSERT INTO momentum_journal (position_id, symbol, entry_price, exit_price, stop_loss, "
            "target_1, profit_loss, profit_loss_pct, r_multiple, days_held, high_since_entry, "
            "low_since_entry, mfe_r, mae_r, quality_score, entry_model, risk_model, regime, sector, "
            "rs_20d, volume_ratio, trend_quality, atr_pct, breakout_score, entry_reason, exit_reason, "
            "reasoning, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (position_id, pos["symbol"], entry, exit_price, sl, pos.get("target_1"), pl, pl_pct,
             r_mult, pos.get("days_held", 0), pos.get("high_since_entry"), pos.get("low_since_entry"),
             pos.get("mfe_r"), pos.get("mae_r"), pos.get("quality_score"), pos.get("entry_model"),
             pos.get("risk_model"), pos.get("regime"), pos.get("sector"), pos.get("rs_20d"),
             pos.get("volume_ratio"), pos.get("trend_quality"), pos.get("atr_pct"),
             pos.get("breakout_score"), pos.get("entry_reason"), exit_reason, pos.get("reasoning"),
             pos["created_at"]),
        )
        conn.commit()
        logger.info("[MomentumPortfolio] closed %s (id=%d) %s R=%s", pos["symbol"], position_id,
                    exit_reason, r_mult)
        return {"position_id": position_id, "symbol": pos["symbol"], "entry": entry,
                "exit": exit_price, "pnl_pct": pl_pct, "r_multiple": r_mult, "exit_reason": exit_reason}
    finally:
        conn.close()


def get_journal(limit: int = 100) -> list[dict]:
    init_momentum_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM momentum_journal ORDER BY datetime(closed_at) DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_journal_stats() -> dict:
    init_momentum_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) n, "
            "SUM(CASE WHEN r_multiple>0 THEN 1 ELSE 0 END) wins, "
            "ROUND(AVG(profit_loss_pct),2) avg_pnl, ROUND(SUM(profit_loss_pct),2) tot_pnl, "
            "ROUND(AVG(r_multiple),3) exp_r, ROUND(AVG(days_held),1) avg_hold, "
            "ROUND(AVG(CASE WHEN r_multiple>0 THEN r_multiple END),2) avg_win_r, "
            "ROUND(AVG(CASE WHEN r_multiple<=0 THEN r_multiple END),2) avg_loss_r "
            "FROM momentum_journal"
        ).fetchone()
        n = row["n"] or 0; wins = row["wins"] or 0
        gross = conn.execute("SELECT "
            "COALESCE(SUM(CASE WHEN r_multiple>0 THEN r_multiple END),0), "
            "COALESCE(ABS(SUM(CASE WHEN r_multiple<=0 THEN r_multiple END)),0) "
            "FROM momentum_journal").fetchone()
        pf = round(gross[0] / gross[1], 2) if gross[1] else (float("inf") if gross[0] else 0.0)
        return {
            "total_trades": n, "wins": wins, "hit_rate_pct": round(wins / n * 100, 1) if n else 0.0,
            "avg_pnl_pct": row["avg_pnl"] or 0.0, "total_pnl_pct": row["tot_pnl"] or 0.0,
            "expectancy_r": row["exp_r"] or 0.0, "profit_factor": pf,
            "avg_win_r": row["avg_win_r"] or 0.0, "avg_loss_r": row["avg_loss_r"] or 0.0,
            "avg_days_held": row["avg_hold"] or 0.0,
        }
    finally:
        conn.close()
