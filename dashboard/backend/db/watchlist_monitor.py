"""
dashboard/backend/db/watchlist_monitor.py
==========================================
Schema + data helpers for the Watchlist → Entry-Trigger → Live-Tracking
lifecycle (per-user). Tables here STAGE ideas (watchlist_positions) and log
their lifecycle (watchlist_events); when an idea goes ACTIVE it lands in the
existing per-user user_positions book, which the shared PositionTrackingService
now tracks via UserPositionStore.

All DDL is additive + idempotent (CREATE IF NOT EXISTS / ADD COLUMN guarded),
so it is safe to run on every startup and never breaks existing data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .schema import get_connection

log = logging.getLogger("dashboard.db.watchlist_monitor")

_IST = timezone.utc  # timestamps stored as UTC iso; UI localizes

WATCHLIST_DDL = """
CREATE TABLE IF NOT EXISTS watchlist_positions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    symbol               TEXT NOT NULL,
    pattern              TEXT,
    tag                  TEXT,
    entry_low            REAL NOT NULL,
    entry_high           REAL NOT NULL,
    stop_loss            REAL NOT NULL,
    target_1             REAL,
    target_2             REAL,
    capital              REAL,
    risk_percent         REAL,
    calculated_quantity  INTEGER,
    cmp                  REAL,
    status               TEXT NOT NULL DEFAULT 'WAITING'
                           CHECK(status IN ('WAITING','APPROACHING','ACTIONABLE',
                                            'MISSED','ARMED','TRIGGERED','ACTIVE',
                                            'EXPIRED','CLOSED')),
    armed                INTEGER NOT NULL DEFAULT 0,
    triggered            INTEGER NOT NULL DEFAULT 0,
    trigger_time         TEXT,
    auto_entry_override  INTEGER,          -- nullable, null = use user default
    valid_until          TEXT,             -- ISO date, null = no expiry
    source               TEXT NOT NULL DEFAULT 'MANUAL'
                           CHECK(source IN ('RESEARCH_AUTO','MANUAL')),
    linked_position_id   INTEGER,
    notes                TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_wlpos_user  ON watchlist_positions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_wlpos_armed ON watchlist_positions(armed);

CREATE TABLE IF NOT EXISTS watchlist_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id  INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    event_time    TEXT NOT NULL DEFAULT (datetime('now')),
    notes         TEXT,
    payload       TEXT,
    FOREIGN KEY(watchlist_id) REFERENCES watchlist_positions(id)
);
CREATE INDEX IF NOT EXISTS idx_wlevt ON watchlist_events(watchlist_id, event_time);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id              INTEGER PRIMARY KEY,
    auto_entry           INTEGER NOT NULL DEFAULT 0,
    default_capital      REAL,
    default_risk_percent REAL DEFAULT 1.0,
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id)
);
"""

# Columns added to existing tables so the shared engine can track user_positions
# with full parity (live metrics) and the UI can show the Source badge + sizing.
_USER_POSITION_COLS = [
    ("source", "TEXT DEFAULT 'MANUAL'"),
    ("quantity", "INTEGER"),
    ("watchlist_id", "INTEGER"),
    ("current_price", "REAL"),
    ("profit_loss", "REAL"),
    ("profit_loss_pct", "REAL"),
    ("drawdown", "REAL"),
    ("drawdown_pct", "REAL"),
    ("high_since_entry", "REAL"),
    ("low_since_entry", "REAL"),
    ("days_held", "INTEGER"),
    ("updated_at", "TEXT"),
]
_PORTFOLIO_POSITION_COLS = [
    ("source", "TEXT DEFAULT 'RESEARCH_AUTO'"),
]


def _add_columns(conn, table: str, cols: list[tuple[str, str]]) -> None:
    for name, decl in cols:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        except Exception:
            pass  # already exists


def init_watchlist_monitor_tables() -> None:
    """Create watchlist tables + add tracking/source columns. Idempotent."""
    conn = get_connection()
    try:
        # executescript handles multiple statements + SQL comments safely
        # (naive split(';') breaks on semicolons inside comments).
        conn.executescript(WATCHLIST_DDL)
        _add_columns(conn, "user_positions", _USER_POSITION_COLS)
        # portfolio_positions lives in db/portfolio.py; table exists by now
        try:
            _add_columns(conn, "portfolio_positions", _PORTFOLIO_POSITION_COLS)
        except Exception:
            pass
        conn.commit()
        log.info("watchlist_monitor tables initialised")
    finally:
        conn.close()


# ── watchlist_events helper ──────────────────────────────────────────────
def log_watchlist_event(watchlist_id: int, user_id: int, event_type: str,
                        notes: str | None = None, payload: dict | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO watchlist_events (watchlist_id, user_id, event_type, notes, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (watchlist_id, user_id, event_type, notes,
             json.dumps(payload) if payload else None),
        )
        conn.commit()
    except Exception as exc:
        log.debug("watchlist event log failed: %s", exc)
    finally:
        conn.close()


# ── user_positions tracking helpers (used by UserPositionStore) ──────────
def get_active_user_positions() -> list[dict]:
    """All ACTIVE user positions across every user (for the shared tracker)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM user_positions WHERE status = 'ACTIVE'"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


_USER_METRIC_COLS = {
    "current_price", "profit_loss", "profit_loss_pct", "drawdown",
    "drawdown_pct", "high_since_entry", "low_since_entry", "days_held",
    "status",
}


def update_user_position_metrics(position_id: int, *, require_active: bool = False, **metrics) -> None:
    """`require_active=True` guards the write against resurrecting a position
    closed between the tracker reading the active list and writing it back —
    same atomic no-clobber guard as the system portfolio's update_position_price."""
    updates = {k: v for k, v in metrics.items() if k in _USER_METRIC_COLS}
    if not updates:
        return
    updates["updated_at"] = datetime.now(_IST).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    where = "WHERE id = ?"
    if require_active:
        where += " AND status = 'ACTIVE'"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE user_positions SET {set_clause} {where}",
            list(updates.values()) + [position_id],
        )
        conn.commit()
    finally:
        conn.close()


def close_user_position(position_id: int, exit_price: float, exit_reason: str) -> None:
    """Close an ACTIVE user position (shared-engine auto-exit). Mirrors the
    manual close route's status logic + journals back to any linked watchlist
    row. Maps the engine's canonical reasons to the user_positions vocabulary
    (STOP_HIT → SL_HIT)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM user_positions WHERE id = ? AND status = 'ACTIVE'",
            (position_id,),
        ).fetchone()
        if not row:
            return
        d = dict(row)
        entry = float(d["entry_price"])
        sl = float(d["stop_loss"]) if d.get("stop_loss") is not None else None
        pnl_r = round((exit_price - entry) / abs(entry - sl), 2) if (sl and abs(entry - sl) > 0) else None
        status = {"STOP_HIT": "SL_HIT", "TARGET_HIT": "TARGET_HIT"}.get(exit_reason, "CLOSED")
        conn.execute(
            "UPDATE user_positions SET status = ?, exit_price = ?, exit_reason = ?, "
            "pnl_r = ?, exited_at = datetime('now') WHERE id = ?",
            (status, exit_price, exit_reason, pnl_r, position_id),
        )
        conn.commit()
        wl_id = d.get("watchlist_id")
        if wl_id:
            conn.execute(
                "UPDATE watchlist_positions SET status = 'CLOSED', updated_at = datetime('now') WHERE id = ?",
                (wl_id,),
            )
            conn.commit()
            log_watchlist_event(int(wl_id), int(d["user_id"]), status,
                                notes=f"auto-exit @ {exit_price}", payload={"pnl_r": pnl_r})
    except Exception as exc:
        log.warning("close_user_position %s failed: %s", position_id, exc)
    finally:
        conn.close()


# ── watchlist_positions CRUD ─────────────────────────────────────────────
def create_watchlist_idea(user_id: int, payload: dict) -> int:
    """Insert a staged idea (research-promoted or manual). Returns id."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO watchlist_positions
                (user_id, symbol, pattern, tag, entry_low, entry_high, stop_loss,
                 target_1, target_2, capital, risk_percent, calculated_quantity,
                 auto_entry_override, valid_until, source, notes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'WAITING')
            """,
            (
                user_id, payload["symbol"], payload.get("pattern"), payload.get("tag"),
                float(payload["entry_low"]), float(payload["entry_high"]), float(payload["stop_loss"]),
                payload.get("target_1"), payload.get("target_2"),
                payload.get("capital"), payload.get("risk_percent"), payload.get("calculated_quantity"),
                payload.get("auto_entry_override"), payload.get("valid_until"),
                payload.get("source", "MANUAL"), payload.get("notes"),
            ),
        )
        conn.commit()
        wid = int(cur.lastrowid)
    finally:
        conn.close()
    log_watchlist_event(wid, user_id, "CREATED", payload={"symbol": payload["symbol"]})
    return wid


def list_watchlist(user_id: int, include_done: bool = False) -> list[dict]:
    conn = get_connection()
    try:
        if include_done:
            rows = conn.execute(
                "SELECT * FROM watchlist_positions WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM watchlist_positions WHERE user_id = ? "
                "AND status NOT IN ('CLOSED','EXPIRED') ORDER BY created_at DESC",
                (user_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_watchlist_idea(idea_id: int, user_id: int | None = None) -> dict | None:
    conn = get_connection()
    try:
        if user_id is None:
            row = conn.execute("SELECT * FROM watchlist_positions WHERE id = ?", (idea_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM watchlist_positions WHERE id = ? AND user_id = ?",
                               (idea_id, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_watchlist_fields(idea_id: int, **fields) -> None:
    allowed = {"status", "armed", "triggered", "trigger_time", "cmp",
               "linked_position_id", "calculated_quantity", "valid_until",
               "auto_entry_override", "notes", "tag", "capital", "risk_percent"}
    upd = {k: v for k, v in fields.items() if k in allowed}
    if not upd:
        return
    upd["updated_at"] = datetime.now(_IST).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in upd)
    conn = get_connection()
    try:
        conn.execute(f"UPDATE watchlist_positions SET {set_clause} WHERE id = ?",
                     list(upd.values()) + [idea_id])
        conn.commit()
    finally:
        conn.close()


def remove_watchlist_idea(idea_id: int, user_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM watchlist_positions WHERE id = ? AND user_id = ?",
                           (idea_id, user_id))
        conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    if ok:
        log_watchlist_event(idea_id, user_id, "REMOVED")
    return ok


def list_monitorable() -> list[dict]:
    """All watchlist rows the trigger engine should evaluate (not done/active)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM watchlist_positions "
            "WHERE status NOT IN ('ACTIVE','CLOSED','EXPIRED')").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_user_pref(user_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return dict(row)
        return {"user_id": user_id, "auto_entry": 0, "default_capital": None, "default_risk_percent": 1.0}
    finally:
        conn.close()


def set_user_pref(user_id: int, **fields) -> None:
    cur = get_user_pref(user_id)
    auto_entry = int(fields.get("auto_entry", cur.get("auto_entry", 0)) or 0)
    default_capital = fields.get("default_capital", cur.get("default_capital"))
    default_risk = fields.get("default_risk_percent", cur.get("default_risk_percent", 1.0))
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO user_preferences (user_id, auto_entry, default_capital, default_risk_percent, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET auto_entry=excluded.auto_entry, "
            "default_capital=excluded.default_capital, default_risk_percent=excluded.default_risk_percent, "
            "updated_at=datetime('now')",
            (user_id, auto_entry, default_capital, default_risk))
        conn.commit()
    finally:
        conn.close()


def promote_watchlist_to_user_position(idea: dict, entry_price: float, source: str) -> int | None:
    """Create a user_positions row from a watchlist idea (Buy-CMP or trigger).
    Duplicate-guarded: returns None if the symbol is already ACTIVE for the user.
    Links both rows and flips the watchlist idea to ACTIVE."""
    user_id = int(idea["user_id"])
    symbol = idea["symbol"]
    conn = get_connection()
    try:
        dup = conn.execute(
            "SELECT id FROM user_positions WHERE user_id = ? AND symbol = ? AND status = 'ACTIVE'",
            (user_id, symbol)).fetchone()
        if dup:
            return None
        cur = conn.execute(
            """
            INSERT INTO user_positions
                (user_id, symbol, entry_price, stop_loss, target_1, target_2,
                 holding_period, status, source, quantity, watchlist_id)
            VALUES (?, ?, ?, ?, ?, ?, 'Swing', 'ACTIVE', ?, ?, ?)
            """,
            (user_id, symbol, float(entry_price),
             float(idea["stop_loss"]) if idea.get("stop_loss") is not None else None,
             idea.get("target_1"), idea.get("target_2"),
             source, idea.get("calculated_quantity"), int(idea["id"])),
        )
        conn.commit()
        pos_id = int(cur.lastrowid)
    finally:
        conn.close()
    update_watchlist_fields(int(idea["id"]), status="ACTIVE", linked_position_id=pos_id)
    log_watchlist_event(int(idea["id"]), user_id, "ACTIVE",
                        notes=f"{source} @ {entry_price}", payload={"position_id": pos_id})
    return pos_id
