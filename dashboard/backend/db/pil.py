"""
dashboard/backend/db/pil.py
===========================
Persistence for the Portfolio Intelligence Layer (PIL). Completely separate
`pil_*` tables in the same durable SQLite DB (dashboard.db / Railway volume).
Additive only — PIL never touches the engine tables (portfolio_positions,
momentum_positions, ...); it only reads those through their own getters.

Tables:
  pil_equity_curve      daily portfolio-value snapshots per book (for DD / ratios)
  pil_scorecards        generated daily/monthly engine scorecards (Part 3)
  pil_reports           generated daily/monthly reports (Parts 7/8)
  pil_alerts            fired intelligence alerts (Part 10)
  pil_allocation_targets  capital allocation targets (Part 5)  [via pil_config]
  pil_config            runtime overrides for capital / thresholds / targets

All helpers are import-safe and self-initialise the schema on first use.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from .schema import get_connection

logger = logging.getLogger("dashboard.db.pil")
_IST = timezone(timedelta(hours=5, minutes=30))

_DDL = """
CREATE TABLE IF NOT EXISTS pil_equity_curve (
    book            TEXT NOT NULL,
    date            TEXT NOT NULL,           -- YYYY-MM-DD (IST)
    portfolio_value REAL NOT NULL,
    cash            REAL NOT NULL,
    invested        REAL NOT NULL DEFAULT 0,
    realized_pnl    REAL NOT NULL DEFAULT 0,
    unrealized_pnl  REAL NOT NULL DEFAULT 0,
    open_positions  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (book, date)
);
CREATE INDEX IF NOT EXISTS idx_pil_equity_book ON pil_equity_curve(book, date);

CREATE TABLE IF NOT EXISTS pil_scorecards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,               -- 'daily' | 'monthly'
    book        TEXT NOT NULL,               -- SWING/LONGTERM/MOMENTUM/COMBINED
    period      TEXT NOT NULL,               -- YYYY-MM-DD or YYYY-MM
    payload     TEXT NOT NULL,               -- JSON blob
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(scope, book, period)
);
CREATE INDEX IF NOT EXISTS idx_pil_scorecard ON pil_scorecards(scope, period);

CREATE TABLE IF NOT EXISTS pil_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,               -- 'daily' | 'monthly'
    period      TEXT NOT NULL,               -- YYYY-MM-DD or YYYY-MM
    payload     TEXT NOT NULL,               -- JSON (structured report)
    html        TEXT,                        -- print-ready HTML (monthly)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(kind, period)
);
CREATE INDEX IF NOT EXISTS idx_pil_reports ON pil_reports(kind, period DESC);

CREATE TABLE IF NOT EXISTS pil_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    book        TEXT NOT NULL,               -- book or COMBINED
    type        TEXT NOT NULL,               -- rule id, e.g. SECTOR_OVERWEIGHT
    severity    TEXT NOT NULL DEFAULT 'WARN',-- INFO | WARN | CRITICAL
    message     TEXT NOT NULL,
    value       REAL,
    threshold   REAL,
    active      INTEGER NOT NULL DEFAULT 1,  -- 1 = firing, 0 = cleared
    cleared_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pil_alerts_active ON pil_alerts(active, ts DESC);

CREATE TABLE IF NOT EXISTS pil_config (
    key         TEXT PRIMARY KEY,            -- e.g. capital.SWING, alloc.MOMENTUM
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_initialised = False


def ensure_tables() -> None:
    """Create PIL tables if missing. Cheap + idempotent; safe to call often."""
    global _initialised
    if _initialised:
        return
    conn = get_connection()
    try:
        conn.executescript(_DDL)
        conn.commit()
        _initialised = True
    except Exception as exc:  # never crash a request on schema init
        logger.error("[PIL] ensure_tables failed (non-fatal): %s", exc)
    finally:
        conn.close()


def _today_ist() -> str:
    return datetime.now(_IST).date().isoformat()


# ── Equity curve ─────────────────────────────────────────────────────────────

def upsert_equity_snapshot(book: str, *, date: str | None = None,
                           portfolio_value: float, cash: float, invested: float = 0.0,
                           realized_pnl: float = 0.0, unrealized_pnl: float = 0.0,
                           open_positions: int = 0) -> None:
    ensure_tables()
    date = date or _today_ist()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO pil_equity_curve
                (book, date, portfolio_value, cash, invested, realized_pnl,
                 unrealized_pnl, open_positions, updated_at)
            VALUES (?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(book, date) DO UPDATE SET
                portfolio_value=excluded.portfolio_value, cash=excluded.cash,
                invested=excluded.invested, realized_pnl=excluded.realized_pnl,
                unrealized_pnl=excluded.unrealized_pnl,
                open_positions=excluded.open_positions, updated_at=datetime('now')
            """,
            (book.upper(), date, portfolio_value, cash, invested, realized_pnl,
             unrealized_pnl, open_positions),
        )
        conn.commit()
    finally:
        conn.close()


def get_equity_curve(book: str, *, start: str | None = None,
                     end: str | None = None) -> list[dict]:
    ensure_tables()
    conn = get_connection()
    try:
        clauses = ["book = ?"]
        params: list = [book.upper()]
        if start:
            clauses.append("date >= ?"); params.append(start)
        if end:
            clauses.append("date <= ?"); params.append(end)
        rows = conn.execute(
            f"SELECT * FROM pil_equity_curve WHERE {' AND '.join(clauses)} ORDER BY date ASC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Scorecards ───────────────────────────────────────────────────────────────

def save_scorecard(scope: str, book: str, period: str, payload: dict) -> None:
    ensure_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO pil_scorecards (scope, book, period, payload, created_at)
            VALUES (?,?,?,?,datetime('now'))
            ON CONFLICT(scope, book, period) DO UPDATE SET
                payload=excluded.payload, created_at=datetime('now')
            """,
            (scope, book.upper(), period, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def get_scorecard(scope: str, book: str, period: str) -> dict | None:
    ensure_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT payload FROM pil_scorecards WHERE scope=? AND book=? AND period=?",
            (scope, book.upper(), period),
        ).fetchone()
        return json.loads(row["payload"]) if row else None
    finally:
        conn.close()


# ── Reports ──────────────────────────────────────────────────────────────────

def save_report(kind: str, period: str, payload: dict, html: str | None = None) -> None:
    ensure_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO pil_reports (kind, period, payload, html, created_at)
            VALUES (?,?,?,?,datetime('now'))
            ON CONFLICT(kind, period) DO UPDATE SET
                payload=excluded.payload, html=excluded.html, created_at=datetime('now')
            """,
            (kind, period, json.dumps(payload), html),
        )
        conn.commit()
    finally:
        conn.close()


def get_report(kind: str, period: str | None = None) -> dict | None:
    ensure_tables()
    conn = get_connection()
    try:
        if period:
            row = conn.execute(
                "SELECT * FROM pil_reports WHERE kind=? AND period=?", (kind, period),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM pil_reports WHERE kind=? ORDER BY period DESC LIMIT 1", (kind,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d
    finally:
        conn.close()


def list_reports(kind: str, limit: int = 24) -> list[dict]:
    ensure_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT kind, period, created_at FROM pil_reports WHERE kind=? "
            "ORDER BY period DESC LIMIT ?", (kind, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Alerts ───────────────────────────────────────────────────────────────────

def record_alert(book: str, type_: str, message: str, *, severity: str = "WARN",
                 value: float | None = None, threshold: float | None = None) -> None:
    """Insert a firing alert only if an identical one isn't already active
    (dedup on book+type while active), so re-evaluation doesn't spam."""
    ensure_tables()
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM pil_alerts WHERE book=? AND type=? AND active=1",
            (book.upper(), type_),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE pil_alerts SET value=?, threshold=?, message=?, severity=?, ts=datetime('now') WHERE id=?",
                (value, threshold, message, severity, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO pil_alerts (book, type, severity, message, value, threshold, active) "
                "VALUES (?,?,?,?,?,?,1)",
                (book.upper(), type_, severity, message, value, threshold),
            )
        conn.commit()
    finally:
        conn.close()


def clear_alert(book: str, type_: str) -> None:
    ensure_tables()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE pil_alerts SET active=0, cleared_at=datetime('now') "
            "WHERE book=? AND type=? AND active=1",
            (book.upper(), type_),
        )
        conn.commit()
    finally:
        conn.close()


def get_alerts(active_only: bool = True, limit: int = 100) -> list[dict]:
    ensure_tables()
    conn = get_connection()
    try:
        where = "WHERE active=1" if active_only else ""
        rows = conn.execute(
            f"SELECT * FROM pil_alerts {where} ORDER BY ts DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Config overrides ─────────────────────────────────────────────────────────

def set_config(key: str, value) -> None:
    ensure_tables()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pil_config (key, value, updated_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_config() -> dict[str, str]:
    ensure_tables()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM pil_config").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()
