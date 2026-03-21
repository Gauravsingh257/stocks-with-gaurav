"""
dashboard/backend/db/schema.py
SQLite schema for dashboard.db — 4 tables as per architecture.
Real-time sync: trade_ledger_2026.csv → trades table via mtime watcher.
"""

import sqlite3
import csv
import os
import logging
import threading
import time
import json
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Use DATA_DIR for persistent storage (Railway volume); else project root
_root = Path(__file__).resolve().parents[3]
_data_dir = Path(os.getenv("DATA_DIR", _root))
DB_PATH = _data_dir / "dashboard.db"
TRADE_LEDGER_PATH = _root / "trade_ledger_2026.csv"

DDL = """
-- ─────────────────────────────────────────
-- TABLE 1: trades  (source of truth)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    setup           TEXT NOT NULL,
    entry           REAL,
    exit_price      REAL,
    result          TEXT CHECK(result IN ('WIN','LOSS','RUNNING','CANCELLED')),
    pnl_r           REAL,
    score           REAL,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_date   ON trades(date);
CREATE INDEX IF NOT EXISTS idx_trades_setup  ON trades(setup);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);

-- ─────────────────────────────────────────
-- TABLE 2: agent_logs  (every agent run)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name      TEXT NOT NULL,
    run_time        TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT NOT NULL DEFAULT 'OK',
    summary         TEXT,
    findings_json   TEXT,
    actions_json    TEXT,
    metrics_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_ts    ON agent_logs(run_time);
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent ON agent_logs(agent_name);

-- ─────────────────────────────────────────
-- TABLE 3: parameter_versions  (config tracking)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parameter_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    parameter       TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    changed_by      TEXT DEFAULT 'manual',
    note            TEXT
);

-- ─────────────────────────────────────────
-- TABLE 4: regime_history  (market regime log)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS regime_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    regime          TEXT NOT NULL CHECK(regime IN ('BULLISH','BEARISH','NEUTRAL')),
    bull_score      INTEGER DEFAULT 0,
    bear_score      INTEGER DEFAULT 0,
    oi_pcr          REAL,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_regime_ts ON regime_history(timestamp);

-- ─────────────────────────────────────────
-- TABLE 5: agent_action_queue  (approval gate)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_action_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    agent           TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    symbol          TEXT,
    payload         TEXT NOT NULL,
    status          TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED','APPLIED')),
    processed_at    TEXT,
    engine_note     TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON agent_action_queue(status);

-- ─────────────────────────────────────────
-- TABLE 6: stock_recommendations (AI research ideas)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_recommendations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                TEXT NOT NULL,
    agent_type            TEXT NOT NULL CHECK(agent_type IN ('SWING','LONGTERM')),
    entry_price           REAL NOT NULL,
    stop_loss             REAL,
    targets               TEXT NOT NULL DEFAULT '[]',
    confidence_score      REAL NOT NULL DEFAULT 0,
    setup                 TEXT,
    expected_holding_period TEXT,
    technical_signals     TEXT,
    fundamental_signals   TEXT,
    sentiment_signals     TEXT,
    technical_factors     TEXT,
    fundamental_factors   TEXT,
    sentiment_factors     TEXT,
    fair_value_estimate   REAL,
    entry_zone            TEXT,
    long_term_target      REAL,
    risk_factors          TEXT,
    reasoning             TEXT NOT NULL DEFAULT '',
    signals_updated_at    TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stock_reco_type_created ON stock_recommendations(agent_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_reco_symbol ON stock_recommendations(symbol);

-- ─────────────────────────────────────────
-- TABLE 7: running_trades (live monitored recommendations)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS running_trades (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                TEXT NOT NULL,
    recommendation_id     INTEGER,
    entry_price           REAL NOT NULL,
    stop_loss             REAL NOT NULL,
    targets               TEXT NOT NULL DEFAULT '[]',
    current_price         REAL NOT NULL,
    profit_loss           REAL NOT NULL DEFAULT 0,
    profit_loss_pct       REAL NOT NULL DEFAULT 0,
    drawdown              REAL NOT NULL DEFAULT 0,
    drawdown_pct          REAL NOT NULL DEFAULT 0,
    high_since_entry      REAL,
    low_since_entry       REAL,
    days_held             INTEGER NOT NULL DEFAULT 0,
    distance_to_target    REAL,
    distance_to_stop_loss REAL,
    status                TEXT NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','TARGET_HIT','STOP_HIT','CLOSED')),
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(recommendation_id) REFERENCES stock_recommendations(id)
);
CREATE INDEX IF NOT EXISTS idx_running_trades_status ON running_trades(status);
CREATE INDEX IF NOT EXISTS idx_running_trades_symbol ON running_trades(symbol);

-- ─────────────────────────────────────────
-- TABLE 8: ranking_runs (weekly ranking audit trail)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ranking_runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time              TEXT NOT NULL DEFAULT (datetime('now')),
    horizon               TEXT NOT NULL CHECK(horizon IN ('SWING','LONGTERM')),
    universe_requested    INTEGER NOT NULL,
    universe_scanned      INTEGER NOT NULL,
    quality_passed        INTEGER NOT NULL,
    ranked_candidates     INTEGER NOT NULL,
    selected_count        INTEGER NOT NULL,
    notes                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_ranking_runs_horizon_time ON ranking_runs(horizon, run_time DESC);
"""


def get_connection() -> sqlite3.Connection:
    """Return a thread-safe WAL-mode connection to dashboard.db."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create all tables (idempotent — safe to call on every startup)."""
    conn = get_connection()
    try:
        conn.executescript(DDL)
        conn.commit()
        logger.info(f"[DB] dashboard.db initialized at {DB_PATH}")
    finally:
        conn.close()

    # Migrate agent_logs from old schema if needed
    migrate_agent_logs()
    migrate_stock_recommendations()
    migrate_running_trades()


# ── CSV Watcher state ─────────────────────────────────────────────────────────
_last_mtime: float = 0.0
_watcher_thread: threading.Thread | None = None
_sync_lock = threading.Lock()
_warned_no_csv: bool = False


def full_sync_from_csv(force: bool = False) -> int:
    """
    Full reload: clears trades table and re-imports every row from
    trade_ledger_2026.csv.  Called on startup and whenever the file changes.
    Returns number of rows inserted, or -1 if skipped (file unchanged).
    """
    global _last_mtime, _warned_no_csv

    if not TRADE_LEDGER_PATH.exists():
        if not _warned_no_csv:
            logger.info("[DB] trade_ledger_2026.csv not found (normal in production — trades sync via API)")
            _warned_no_csv = True
        return 0

    current_mtime = TRADE_LEDGER_PATH.stat().st_mtime
    if not force and current_mtime == _last_mtime:
        return -1  # unchanged — skip

    with _sync_lock:
        # Re-check under lock to avoid double-reload from concurrent calls
        current_mtime = TRADE_LEDGER_PATH.stat().st_mtime
        if not force and current_mtime == _last_mtime:
            return -1

        conn = get_connection()
        try:
            rows_to_insert = []
            with open(TRADE_LEDGER_PATH, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows_to_insert.append((
                        row.get("date", "").strip(),
                        row.get("symbol", "").strip(),
                        row.get("direction", "").strip().upper(),
                        row.get("setup", "").strip(),
                        float(row["entry"]) if row.get("entry") else None,
                        float(row["exit_price"]) if row.get("exit_price") else None,
                        row.get("result", "").strip().upper(),
                        float(row["pnl_r"]) if row.get("pnl_r") else None,
                    ))

            if not rows_to_insert:
                logger.warning("[DB Sync] CSV parsed 0 rows — aborting sync to protect existing data")
                return 0

            conn.execute("DELETE FROM trades")
            conn.executemany(
                "INSERT INTO trades (date, symbol, direction, setup, entry, exit_price, result, pnl_r) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )
            conn.commit()
            _last_mtime = current_mtime
            logger.info(f"[DB Sync] Reloaded {len(rows_to_insert)} trades from CSV (mtime changed)")
            return len(rows_to_insert)

        except Exception as exc:
            conn.rollback()
            logger.error(f"[DB Sync] Failed: {exc}")
            return 0
        finally:
            conn.close()


# Keep old name as alias so existing imports don't break
def migrate_trade_ledger() -> int:
    """Alias for full_sync_from_csv (force=True). Kept for backward compat."""
    return full_sync_from_csv(force=True)


def get_sync_info() -> dict:
    """Return current sync state for the /sync-status endpoint."""
    csv_exists = TRADE_LEDGER_PATH.exists()
    csv_mtime = TRADE_LEDGER_PATH.stat().st_mtime if csv_exists else None
    conn = get_connection()
    try:
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()
    return {
        "csv_path":      str(TRADE_LEDGER_PATH),
        "csv_exists":    csv_exists,
        "csv_mtime":     datetime.fromtimestamp(csv_mtime).isoformat() if csv_mtime else None,
        "last_sync":     datetime.fromtimestamp(_last_mtime).isoformat() if _last_mtime else None,
        "in_sync":       csv_mtime == _last_mtime if csv_mtime else False,
        "db_trade_count": trade_count,
    }


def start_csv_watcher(interval_seconds: int = 30) -> None:
    """
    Start a background daemon thread that polls trade_ledger_2026.csv every
    `interval_seconds` and calls full_sync_from_csv() when the file changes.
    Safe to call multiple times — only one watcher thread will run.
    In production (no CSV): skip polling to avoid log noise.
    """
    global _watcher_thread

    if _watcher_thread is not None and _watcher_thread.is_alive():
        logger.debug("[DB Watcher] Already running — skipping duplicate start")
        return

    # In production (e.g. Railway /app), CSV is never present — trades sync via API.
    # Skip the watcher to avoid repeated "not found" logs every 30s.
    if not TRADE_LEDGER_PATH.exists() and "/app" in str(TRADE_LEDGER_PATH):
        logger.info("[DB Watcher] Skipped — no CSV in production (trades sync via /api/journal/sync)")
        return

    def _watch_loop():
        logger.info(f"[DB Watcher] Started — polling every {interval_seconds}s")
        while True:
            try:
                result = full_sync_from_csv()
                if result > 0:
                    logger.info(f"[DB Watcher] Auto-synced {result} trades from CSV")
            except Exception as exc:
                logger.error(f"[DB Watcher] Error: {exc}")
            time.sleep(interval_seconds)

    _watcher_thread = threading.Thread(target=_watch_loop, daemon=True, name="csv-watcher")
    _watcher_thread.start()


def log_agent_action(
    agent: str,
    action_type: str,
    symbol: str = None,
    payload: str = "{}",
    status: str = "PENDING",
) -> int:
    """Insert a row into agent_action_queue. Returns new row id."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO agent_action_queue
              (agent, action_type, symbol, payload, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent, action_type, symbol, payload, status),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def migrate_agent_logs() -> None:
    """
    One-time migration: drop old agent_logs schema and recreate with new columns.
    Safe because the old schema never successfully received any rows (all INSERTs
    used wrong column names and silently failed).
    """
    conn = get_connection()
    try:
        # Check if table has old schema by looking for 'agent' column (new has 'agent_name')
        cursor = conn.execute("PRAGMA table_info(agent_logs)")
        columns = {row[1] for row in cursor.fetchall()}

        if "agent_name" in columns:
            # Already migrated
            return

        if "agent" in columns:
            logger.info("[DB] Migrating agent_logs to new schema…")
            conn.execute("DROP TABLE IF EXISTS agent_logs")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_logs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name      TEXT NOT NULL,
                    run_time        TEXT NOT NULL DEFAULT (datetime('now')),
                    status          TEXT NOT NULL DEFAULT 'OK',
                    summary         TEXT,
                    findings_json   TEXT,
                    actions_json    TEXT,
                    metrics_json    TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_logs_ts    ON agent_logs(run_time);
                CREATE INDEX IF NOT EXISTS idx_agent_logs_agent ON agent_logs(agent_name);
            """)
            conn.commit()
            logger.info("[DB] agent_logs migration complete")
    finally:
        conn.close()


def migrate_stock_recommendations() -> None:
    """Ensure latest recommendation evidence columns exist."""
    conn = get_connection()
    try:
        cursor = conn.execute("PRAGMA table_info(stock_recommendations)")
        cols = {row[1] for row in cursor.fetchall()}
        for col_name in ("technical_signals", "fundamental_signals", "sentiment_signals", "signals_updated_at"):
            if col_name not in cols:
                conn.execute(f"ALTER TABLE stock_recommendations ADD COLUMN {col_name} TEXT")
        conn.commit()
    finally:
        conn.close()


def migrate_running_trades() -> None:
    """Ensure new tracking columns exist in running_trades."""
    conn = get_connection()
    try:
        cursor = conn.execute("PRAGMA table_info(running_trades)")
        cols = {row[1] for row in cursor.fetchall()}
        new_cols = [
            ("profit_loss_pct", "REAL NOT NULL DEFAULT 0"),
            ("drawdown_pct", "REAL NOT NULL DEFAULT 0"),
            ("high_since_entry", "REAL"),
            ("low_since_entry", "REAL"),
            ("days_held", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for col_name, col_def in new_cols:
            if col_name not in cols:
                conn.execute(f"ALTER TABLE running_trades ADD COLUMN {col_name} {col_def}")
        conn.commit()
    finally:
        conn.close()


def log_regime_change(regime: str, bull_score: int = 0, bear_score: int = 0, oi_pcr: float = None) -> None:
    """Insert a row into regime_history."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO regime_history (regime, bull_score, bear_score, oi_pcr) VALUES (?, ?, ?, ?)",
            (regime, bull_score, bear_score, oi_pcr),
        )
        conn.commit()
    finally:
        conn.close()


def create_stock_recommendation(payload: dict) -> int:
    """Insert a recommendation row and return id."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO stock_recommendations (
                symbol, agent_type, entry_price, stop_loss, targets, confidence_score,
                setup, expected_holding_period, technical_signals, fundamental_signals,
                sentiment_signals, technical_factors, fundamental_factors, sentiment_factors,
                fair_value_estimate, entry_zone, long_term_target, risk_factors, reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["symbol"],
                payload["agent_type"],
                float(payload["entry_price"]),
                float(payload["stop_loss"]) if payload.get("stop_loss") is not None else None,
                json.dumps(payload.get("targets", [])),
                float(payload.get("confidence_score", 0)),
                payload.get("setup"),
                payload.get("expected_holding_period"),
                json.dumps(payload.get("technical_signals", {})),
                json.dumps(payload.get("fundamental_signals", {})),
                json.dumps(payload.get("sentiment_signals", {})),
                json.dumps(payload.get("technical_factors", {})),
                json.dumps(payload.get("fundamental_factors", {})),
                json.dumps(payload.get("sentiment_factors", {})),
                float(payload["fair_value_estimate"]) if payload.get("fair_value_estimate") is not None else None,
                json.dumps(payload.get("entry_zone")) if payload.get("entry_zone") is not None else None,
                float(payload["long_term_target"]) if payload.get("long_term_target") is not None else None,
                json.dumps(payload.get("risk_factors", [])),
                payload.get("reasoning", ""),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_stock_recommendations(agent_type: str, limit: int = 20) -> list[dict]:
    """Return latest recommendations by type."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM stock_recommendations
            WHERE agent_type = ?
              AND COALESCE(technical_signals, '') != ''
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (agent_type, limit),
        ).fetchall()
        results: list[dict] = []
        for row in rows:
            item = dict(row)
            for key, fallback in (
                ("targets", []),
                ("technical_factors", {}),
                ("fundamental_factors", {}),
                ("sentiment_factors", {}),
                ("technical_signals", {}),
                ("fundamental_signals", {}),
                ("sentiment_signals", {}),
                ("risk_factors", []),
                ("entry_zone", []),
            ):
                raw = item.get(key)
                if raw:
                    try:
                        item[key] = json.loads(raw)
                    except json.JSONDecodeError:
                        item[key] = fallback
                else:
                    item[key] = fallback
            results.append(item)
        return results
    finally:
        conn.close()


def get_latest_recommendation_by_symbol(symbol: str) -> dict | None:
    """Get latest recommendation for a symbol."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM stock_recommendations
            WHERE symbol = ?
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_running_trade(payload: dict) -> int:
    """Create running trade row and return id."""
    conn = get_connection()
    entry = float(payload["entry_price"])
    current = float(payload.get("current_price", entry))
    try:
        cur = conn.execute(
            """
            INSERT INTO running_trades (
                symbol, recommendation_id, entry_price, stop_loss, targets,
                current_price, profit_loss, profit_loss_pct, drawdown, drawdown_pct,
                high_since_entry, low_since_entry, days_held,
                distance_to_target, distance_to_stop_loss, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["symbol"],
                payload.get("recommendation_id"),
                entry,
                float(payload["stop_loss"]),
                json.dumps(payload.get("targets", [])),
                current,
                float(payload.get("profit_loss", 0)),
                float(payload.get("profit_loss_pct", 0)),
                float(payload.get("drawdown", 0)),
                float(payload.get("drawdown_pct", 0)),
                float(payload.get("high_since_entry", current)),
                float(payload.get("low_since_entry", current)),
                int(payload.get("days_held", 0)),
                float(payload["distance_to_target"]) if payload.get("distance_to_target") is not None else None,
                float(payload["distance_to_stop_loss"]) if payload.get("distance_to_stop_loss") is not None else None,
                payload.get("status", "RUNNING"),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_active_running_trade_by_symbol(symbol: str) -> dict | None:
    """Return active running trade for symbol."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM running_trades
            WHERE symbol = ? AND status = 'RUNNING'
            ORDER BY datetime(created_at) DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        raw_targets = item.get("targets")
        item["targets"] = json.loads(raw_targets) if raw_targets else []
        return item
    finally:
        conn.close()


def update_running_trade(
    trade_id: int,
    *,
    current_price: float,
    profit_loss: float,
    profit_loss_pct: float = 0.0,
    drawdown: float,
    drawdown_pct: float = 0.0,
    high_since_entry: float | None = None,
    low_since_entry: float | None = None,
    days_held: int = 0,
    distance_to_target: float | None,
    distance_to_stop_loss: float | None,
    status: str,
) -> None:
    """Update live metrics for a running trade."""
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE running_trades
            SET current_price = ?, profit_loss = ?, profit_loss_pct = ?,
                drawdown = ?, drawdown_pct = ?,
                high_since_entry = CASE WHEN ? IS NULL THEN high_since_entry
                                        WHEN high_since_entry IS NULL THEN ?
                                        WHEN ? > high_since_entry THEN ?
                                        ELSE high_since_entry END,
                low_since_entry  = CASE WHEN ? IS NULL THEN low_since_entry
                                        WHEN low_since_entry IS NULL THEN ?
                                        WHEN ? < low_since_entry THEN ?
                                        ELSE low_since_entry END,
                days_held = ?,
                distance_to_target = ?, distance_to_stop_loss = ?,
                status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                float(current_price),
                float(profit_loss),
                float(profit_loss_pct),
                float(drawdown),
                float(drawdown_pct),
                # high_since_entry CASE (4 params: sentinel, init, compare, replace)
                high_since_entry, high_since_entry, high_since_entry, high_since_entry,
                # low_since_entry CASE
                low_since_entry, low_since_entry, low_since_entry, low_since_entry,
                int(days_held),
                float(distance_to_target) if distance_to_target is not None else None,
                float(distance_to_stop_loss) if distance_to_stop_loss is not None else None,
                status,
                trade_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_running_trades(limit: int = 50, active_only: bool = True) -> list[dict]:
    """Return running trades for monitor."""
    conn = get_connection()
    try:
        if active_only:
            rows = conn.execute(
                """
                SELECT * FROM running_trades
                WHERE status = 'RUNNING'
                ORDER BY datetime(updated_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM running_trades
                ORDER BY datetime(updated_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        results: list[dict] = []
        for row in rows:
            item = dict(row)
            raw_targets = item.get("targets")
            item["targets"] = json.loads(raw_targets) if raw_targets else []
            results.append(item)
        return results
    finally:
        conn.close()


def log_ranking_run(
    *,
    horizon: str,
    universe_requested: int,
    universe_scanned: int,
    quality_passed: int,
    ranked_candidates: int,
    selected_count: int,
    notes: str | None = None,
) -> int:
    """Persist ranking run metrics for audit."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO ranking_runs (
                horizon, universe_requested, universe_scanned, quality_passed,
                ranked_candidates, selected_count, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                horizon,
                int(universe_requested),
                int(universe_scanned),
                int(quality_passed),
                int(ranked_candidates),
                int(selected_count),
                notes,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_ranking_runs(horizon: str | None = None, limit: int = 20) -> list[dict]:
    """Fetch recent ranking audit rows."""
    conn = get_connection()
    try:
        if horizon:
            rows = conn.execute(
                """
                SELECT * FROM ranking_runs
                WHERE horizon = ?
                ORDER BY datetime(run_time) DESC
                LIMIT ?
                """,
                (horizon, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM ranking_runs
                ORDER BY datetime(run_time) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
