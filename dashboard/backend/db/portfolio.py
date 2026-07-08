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
import os
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
    -- PENDING = armed, awaiting the planned entry to be genuinely traded through
    -- (no P&L, no days-held, excluded from analytics). EXPIRED = an armed idea
    -- that never triggered and was retired (never a trade → never journaled).
    status                TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('PENDING','ACTIVE','TARGET_HIT','STOP_HIT','CLOSED','PARTIAL_EXIT','EXPIRED')),
    exit_price            REAL,
    exit_reason           TEXT,
    -- arm_ref_price = CMP at arm time (decides pullback vs breakout trigger side).
    -- entered_at    = when the entry ACTUALLY triggered (NULL while PENDING);
    --                 days_held + P&L are measured from this, not from created_at.
    arm_ref_price         REAL,
    entered_at            TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_portfolio_status ON portfolio_positions(status);
CREATE INDEX IF NOT EXISTS idx_portfolio_horizon ON portfolio_positions(horizon, status);
CREATE INDEX IF NOT EXISTS idx_portfolio_symbol ON portfolio_positions(symbol);
-- Enforce the REAL invariant: only one ACTIVE position per symbol+horizon.
-- (Terminal rows are historical trades — a symbol may be traded/stopped out more
-- than once over time, so the old table-level UNIQUE(symbol,horizon,status) was
-- wrong: a second STOP_HIT collided and crashed the price tracker mid-tick.)
CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_active_unique
    ON portfolio_positions(symbol, horizon) WHERE status IN ('ACTIVE','PENDING');

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


def migrate_portfolio_positions() -> None:
    """Replace the over-broad table-level UNIQUE(symbol,horizon,status) with a
    partial unique index on ACTIVE-only.

    The old constraint blocked a symbol from having two terminal rows (e.g. a
    second STOP_HIT after being re-traded), which made the shared position
    tracker raise IntegrityError mid-tick and silently FREEZE every position
    after it. SQLite can't drop a table constraint in place, so rebuild the
    table preserving all rows + ids (portfolio_journal.position_id refs stay
    valid). Idempotent: only rebuilds while the old constraint is still present.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='portfolio_positions'"
        ).fetchone()
        old_sql = (row[0] if row else "") or ""
        if "UNIQUE(symbol, horizon, status)" in old_sql or "UNIQUE (symbol, horizon, status)" in old_sql:
            cnt_before = conn.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0]
            colnames = [r[1] for r in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()]
            collist = ", ".join(colnames)
            import re as _re
            new_sql = old_sql.replace("portfolio_positions", "portfolio_positions_new", 1)
            # Drop the table-level UNIQUE constraint (with its leading comma).
            new_sql = _re.sub(r",\s*UNIQUE\s*\(\s*symbol\s*,\s*horizon\s*,\s*status\s*\)", "", new_sql)
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(new_sql)
            conn.execute(f"INSERT INTO portfolio_positions_new ({collist}) SELECT {collist} FROM portfolio_positions")
            cnt_after = conn.execute("SELECT COUNT(*) FROM portfolio_positions_new").fetchone()[0]
            if cnt_after != cnt_before:
                conn.execute("DROP TABLE portfolio_positions_new")
                conn.rollback()
                logger.error("[Portfolio] migration aborted: row count %d != %d", cnt_after, cnt_before)
                return
            conn.execute("DROP TABLE portfolio_positions")
            conn.execute("ALTER TABLE portfolio_positions_new RENAME TO portfolio_positions")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_status ON portfolio_positions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_horizon ON portfolio_positions(horizon, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_symbol ON portfolio_positions(symbol)")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            logger.warning("[Portfolio] migrated UNIQUE(symbol,horizon,status) -> ACTIVE-only partial index (%d rows preserved)", cnt_after)
        # Always ensure the partial unique index exists (new + migrated schemas).
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_active_unique "
            "ON portfolio_positions(symbol, horizon) WHERE status = 'ACTIVE'"
        )
        conn.commit()
    except Exception as exc:
        logger.error("[Portfolio] migrate_portfolio_positions failed (non-fatal): %s", exc)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
    finally:
        conn.close()


def migrate_portfolio_pending() -> None:
    """Add the arm-on-tap columns + expand the status CHECK to allow
    PENDING / EXPIRED.

    Two parts, both idempotent:
      1. ADD COLUMN arm_ref_price / entered_at when missing (cheap, in place).
      2. Rebuild the table when the status CHECK still forbids 'PENDING'
         (SQLite can't ALTER a CHECK in place). Preserves every row + id so the
         portfolio_journal FK references stay valid.

    Runs AFTER migrate_portfolio_positions so it operates on the post-rebuild
    table shape.
    """
    conn = get_connection()
    try:
        # Give the schema rebuild room to acquire the write lock behind the live
        # price-tracker connection (WAL still needs exclusivity for DROP/RENAME).
        try:
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
        if "arm_ref_price" not in cols:
            conn.execute("ALTER TABLE portfolio_positions ADD COLUMN arm_ref_price REAL")
        if "entered_at" not in cols:
            conn.execute("ALTER TABLE portfolio_positions ADD COLUMN entered_at TEXT")
        conn.commit()

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='portfolio_positions'"
        ).fetchone()
        old_sql = (row[0] if row else "") or ""
        # Only rebuild if the CHECK still lacks PENDING (i.e. pre-migration schema).
        if "'PENDING'" in old_sql:
            # Ensure the pending-aware unique index exists, then done.
            conn.execute("DROP INDEX IF EXISTS idx_portfolio_active_unique")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_active_unique "
                "ON portfolio_positions(symbol, horizon) WHERE status IN ('ACTIVE','PENDING')"
            )
            conn.commit()
            return

        import re
        cnt_before = conn.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0]
        colnames = [r[1] for r in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()]
        collist = ", ".join(colnames)

        # Build the new table SQL by MUTATING the current definition, not a
        # hardcoded copy — so any runtime-added columns (e.g. watchlist's
        # `source`) are preserved. Swap only the status CHECK to allow
        # PENDING/EXPIRED. (A hardcoded DDL that omitted `source` was why the
        # INSERT…SELECT failed on prod.)
        new_sql = old_sql.replace("portfolio_positions", "portfolio_positions_pend", 1)
        new_check = ("CHECK(status IN ('PENDING','ACTIVE','TARGET_HIT','STOP_HIT',"
                     "'CLOSED','PARTIAL_EXIT','EXPIRED'))")
        new_sql = re.sub(r"CHECK\s*\(\s*status\s+IN\s*\([^)]*\)\s*\)", new_check, new_sql, count=1)
        if "'PENDING'" not in new_sql:
            logger.error("[Portfolio] pending migration: could not rewrite status CHECK; aborting")
            return

        # Run the schema surgery in AUTOCOMMIT so PRAGMA foreign_keys=OFF actually
        # takes effect (it is a no-op inside a transaction).
        conn.commit()
        prev_iso = conn.isolation_level
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE IF EXISTS portfolio_positions_pend")
            conn.execute(new_sql)
            conn.execute(f"INSERT INTO portfolio_positions_pend ({collist}) SELECT {collist} FROM portfolio_positions")
            cnt_after = conn.execute("SELECT COUNT(*) FROM portfolio_positions_pend").fetchone()[0]
            if cnt_after != cnt_before:
                conn.execute("DROP TABLE portfolio_positions_pend")
                logger.error("[Portfolio] pending migration aborted: row count %d != %d", cnt_after, cnt_before)
                return
            conn.execute("DROP TABLE portfolio_positions")
            conn.execute("ALTER TABLE portfolio_positions_pend RENAME TO portfolio_positions")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_status ON portfolio_positions(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_horizon ON portfolio_positions(horizon, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_symbol ON portfolio_positions(symbol)")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_active_unique "
                "ON portfolio_positions(symbol, horizon) WHERE status IN ('ACTIVE','PENDING')"
            )
            conn.execute("PRAGMA foreign_keys=ON")
        finally:
            conn.isolation_level = prev_iso
        logger.warning("[Portfolio] migrated status CHECK -> PENDING/EXPIRED + arm-on-tap columns (%d rows preserved)", cnt_after)
    except Exception as exc:
        logger.error("[Portfolio] migrate_portfolio_pending failed (non-fatal): %s", exc)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
    finally:
        conn.close()


def migrate_portfolio_risk_columns() -> None:
    """Add the risk-engine sizing columns (idempotent, in-place ADD COLUMN — no
    rebuild). position_size = ₹ notional allocated by risk-normalized sizing;
    risk_weight_pct = that as a % of notional capital; atr_pct / turnover_cr =
    the liquidity metrics used to down-size. All nullable → legacy rows are
    unaffected and the columns are harmless when the engine is disabled."""
    cols_to_add = [
        ("position_size", "REAL"),
        ("risk_weight_pct", "REAL"),
        ("atr_pct", "REAL"),
        ("turnover_cr", "REAL"),
    ]
    conn = get_connection()
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
        for name, decl in cols_to_add:
            if name not in have:
                conn.execute(f"ALTER TABLE portfolio_positions ADD COLUMN {name} {decl}")
        conn.commit()
    except Exception as exc:
        logger.error("[Portfolio] migrate_portfolio_risk_columns failed (non-fatal): %s", exc)
    finally:
        conn.close()


def portfolio_schema_diag() -> dict:
    """Read-only diagnostic: does the live table allow PENDING, and which
    arm-on-tap columns exist? Used to confirm the migration landed on prod."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='portfolio_positions'"
        ).fetchone()
        sql = (row[0] if row else "") or ""
        cols = [r[1] for r in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()]
        return {
            "allows_pending": "'PENDING'" in sql,
            "has_arm_ref_price": "arm_ref_price" in cols,
            "has_entered_at": "entered_at" in cols,
            "columns": cols,
        }
    finally:
        conn.close()


def init_portfolio_db() -> None:
    """Create portfolio tables (idempotent)."""
    conn = get_connection()
    try:
        conn.executescript(PORTFOLIO_DDL)
        conn.commit()
        logger.info("[Portfolio] Tables initialized")
    finally:
        conn.close()
    migrate_portfolio_positions()
    migrate_portfolio_pending()
    migrate_portfolio_risk_columns()


# ──────────────────────────────────────────────────────────────────────────────
# Portfolio CRUD
# ──────────────────────────────────────────────────────────────────────────────

# Portfolio capacity — env-configurable (was a hardcoded 10, which left the
# book "FULL" and blocked new promotions while the research inventory had
# already been expanded to 50). These are a separate layer from
# RESEARCH_MAX_INVENTORY: that caps how many IDEAS are surfaced; this caps how
# many live POSITIONS the portfolio holds. Default raised 10 → 20.
MAX_SWING_POSITIONS = int(os.getenv("PORTFOLIO_MAX_SWING", "20"))
MAX_LONGTERM_POSITIONS = int(os.getenv("PORTFOLIO_MAX_LONGTERM", "20"))


# ──────────────────────────────────────────────────────────────────────────────
# Setup-aware re-entry guard
# ──────────────────────────────────────────────────────────────────────────────
# Stops the "churn" pathology where the same failed setup is re-promoted over and
# over (observed: APTUS ×9, KALYANKJIL ×4 — same entry, structure-break, never
# reaching target). Blocks ONLY a re-promotion that is the *same failed setup*;
# a genuinely new setup (different entry level, reclaimed 200-DMA, or elapsed
# cooldown) is always allowed. See LAUNCH_AUDIT_REPORT / shadow-log analysis.
#
# Mode: "on" enforces, "shadow" logs what it *would* block but allows, "off"
# disables entirely. Default "on" (validated clean against full journal history).
REENTRY_GUARD_MODE = os.getenv("PORTFOLIO_REENTRY_GUARD", "on").strip().lower()
REENTRY_COOLDOWN_DAYS = int(os.getenv("PORTFOLIO_REENTRY_COOLDOWN_DAYS", "10"))
REENTRY_SAME_ENTRY_PCT = float(os.getenv("PORTFOLIO_REENTRY_SAME_ENTRY_PCT", "3.0"))
_REENTRY_FAIL_REASONS = {"STRUCTURE_BREAK", "STOP_HIT", "STALE_EXIT", "TREND_BREAK"}


def _reentry_would_block(symbol: str, horizon: str, entry: float,
                         cmp: float | None = None) -> tuple[bool, str]:
    """Evaluate the four setup-aware gates. Returns (would_block, reason).

    Blocks only when ALL hold:
      G1  a prior exit for symbol+horizon within REENTRY_COOLDOWN_DAYS
      G2  that exit was a FAILURE (STRUCTURE_BREAK / STOP_HIT), not a target hit
      G3  the new entry is within REENTRY_SAME_ENTRY_PCT of the failed entry
          (same price level — not a fresh breakout at a new level)
      G4  setup-aware: the structural disqualifier still holds — price is still
          below the 200-DMA. If the DMA can't be fetched or cmp is unknown, we
          FAIL OPEN (allow) so uncertainty never suppresses a real opportunity.

    Pure evaluation — never enforces on its own; the caller applies the mode.
    """
    sym = symbol.strip().upper()
    hz = horizon.upper()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT entry_price, exit_reason, closed_at FROM portfolio_journal "
            "WHERE symbol = ? AND horizon = ? ORDER BY datetime(closed_at) DESC LIMIT 1",
            (sym, hz),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return False, "no-prior-exit"

    last_entry = row["entry_price"]
    last_reason = row["exit_reason"]

    # G2 — only failures qualify (a prior TARGET_HIT is not a failed setup).
    if last_reason not in _REENTRY_FAIL_REASONS:
        return False, f"last-exit-not-failure({last_reason})"

    # G1 — recency (uses reliable closed_at vs real-time IST).
    try:
        ca = str(row["closed_at"]).replace(" ", "T")
        cdt = datetime.fromisoformat(ca)
        if cdt.tzinfo is None:
            cdt = cdt.replace(tzinfo=_IST)
        days = (datetime.now(_IST) - cdt).days
    except Exception:
        return False, "cooldown-unparseable(fail-open)"
    if days > REENTRY_COOLDOWN_DAYS:
        return False, f"cooldown-elapsed({days}d>{REENTRY_COOLDOWN_DAYS}d)"

    # G3 — same price level.
    if not last_entry or last_entry <= 0:
        return False, "no-prior-entry"
    delta_pct = abs(entry - last_entry) / last_entry * 100.0
    if delta_pct > REENTRY_SAME_ENTRY_PCT:
        return False, f"new-entry-level(Δ{delta_pct:.1f}%>{REENTRY_SAME_ENTRY_PCT}%)"

    # For a STALE_EXIT (dead-money time-stop), the "same setup" test is simply a
    # re-buy at the same entry within cooldown — there is no 200-DMA structural
    # condition to re-check, so G1+G2+G3 are sufficient to block the churn.
    if last_reason == "STALE_EXIT":
        return True, f"repeat-stalled-setup(cooldown={days}d,Δentry={delta_pct:.1f}%)"

    # G4 — setup-aware structural gate: still below the 200-DMA?
    if cmp is None:
        return False, "cmp-unknown(fail-open)"
    dma = None
    try:
        from services.position_tracking_service import _get_200dma
        dma = _get_200dma(sym if sym.startswith("NSE:") else f"NSE:{sym}")
    except Exception:
        dma = None
    if dma is None:
        return False, "200dma-unavailable(fail-open)"
    if cmp >= dma:
        return False, f"reclaimed-200dma(cmp {cmp:.2f}>=dma {dma:.2f}) — new setup"

    return True, (f"repeat-failed-setup(last={last_reason},cooldown={days}d,"
                  f"Δentry={delta_pct:.1f}%,cmp {cmp:.2f}<dma {dma:.2f})")


def reentry_guard_blocks(symbol: str, horizon: str, entry: float,
                         cmp: float | None = None) -> bool:
    """Enforcement wrapper. Always logs a would-block decision; only returns
    True (block) when the guard is in 'on' mode. In 'shadow' mode it logs the
    would-block and returns False so promotion proceeds (observation only)."""
    if REENTRY_GUARD_MODE == "off":
        return False
    would, reason = _reentry_would_block(symbol, horizon, entry, cmp)
    if not would:
        return False
    enforce = REENTRY_GUARD_MODE == "on"
    logger.warning("[ReentryGuard] %s %s/%s @%.2f — %s",
                   "BLOCK" if enforce else "SHADOW-BLOCK", symbol, horizon, entry, reason)
    return enforce


def add_position(payload: dict) -> int:
    """
    Add a stock to the portfolio. Returns position ID.

    status:
      - 'PENDING' (default for auto-promotions) = ARMED, awaiting the planned
        entry to be genuinely traded through. No P&L / days-held until it
        triggers; excluded from analytics. arm_ref_price records the CMP at arm
        time (decides pullback vs breakout trigger side).
      - 'ACTIVE' = live position (entered_at set now). Used for a genuine manual
        fill or the reconciliation of an already-triggered position.

    A "slot" is consumed by an ACTIVE *or* PENDING position, so capacity counts
    both — the book never commits more than MAX per horizon. Rejects if the
    symbol is already ACTIVE or PENDING in that horizon.
    """
    symbol = payload["symbol"].strip().upper()
    horizon = payload["horizon"].upper()
    if horizon not in ("SWING", "LONGTERM"):
        raise ValueError(f"Invalid horizon: {horizon}")

    status = (payload.get("status") or "ACTIVE").upper()
    if status not in ("PENDING", "ACTIVE"):
        raise ValueError(f"add_position status must be PENDING or ACTIVE, got {status}")

    conn = get_connection()
    try:
        # A symbol already committed (armed or live) can't be committed again.
        existing = conn.execute(
            "SELECT id, status FROM portfolio_positions WHERE symbol = ? AND horizon = ? "
            "AND status IN ('ACTIVE','PENDING')",
            (symbol, horizon),
        ).fetchone()
        if existing:
            raise ValueError(f"{symbol} already {existing['status']} in {horizon} portfolio")

        # Capacity counts BOTH active and armed (each holds a slot).
        max_pos = MAX_SWING_POSITIONS if horizon == "SWING" else MAX_LONGTERM_POSITIONS
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM portfolio_positions WHERE horizon = ? "
            "AND status IN ('ACTIVE','PENDING')",
            (horizon,),
        ).fetchone()["cnt"]
        if count >= max_pos:
            raise ValueError(f"{horizon} portfolio full ({count}/{max_pos})")

        entry = float(payload["entry_price"])
        sl = float(payload["stop_loss"])
        t1 = float(payload.get("target_1") or 0) or None
        t2 = float(payload.get("target_2") or 0) or None
        cmp = float(payload.get("current_price") or entry)
        arm_ref = float(payload.get("arm_ref_price") or cmp)
        # A PENDING row has NOT entered yet → no entered_at, no live price shown
        # as a fill. An ACTIVE row enters now.
        entered_at = None if status == "PENDING" else datetime.now(_IST).isoformat()

        # Risk-engine sizing fields (nullable — present only when the engine sized
        # this position). Columns exist via migrate_portfolio_risk_columns.
        _has_risk_cols = "position_size" in {
            r[1] for r in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()
        }
        if _has_risk_cols:
            cursor = conn.execute(
                """
                INSERT INTO portfolio_positions
                    (symbol, horizon, direction, entry_price, stop_loss, target_1, target_2,
                     current_price, confidence_score, reasoning, recommendation_id, status,
                     arm_ref_price, entered_at, position_size, risk_weight_pct, atr_pct, turnover_cr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, horizon, payload.get("direction", "LONG"),
                    entry, sl, t1, t2, cmp,
                    float(payload.get("confidence_score", 0)),
                    payload.get("reasoning", ""), payload.get("recommendation_id"),
                    status, arm_ref, entered_at,
                    payload.get("position_size"), payload.get("risk_weight_pct"),
                    payload.get("atr_pct"), payload.get("turnover_cr"),
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO portfolio_positions
                    (symbol, horizon, direction, entry_price, stop_loss, target_1, target_2,
                     current_price, confidence_score, reasoning, recommendation_id, status,
                     arm_ref_price, entered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, horizon, payload.get("direction", "LONG"),
                    entry, sl, t1, t2, cmp,
                    float(payload.get("confidence_score", 0)),
                    payload.get("reasoning", ""), payload.get("recommendation_id"),
                    status, arm_ref, entered_at,
                ),
            )
        conn.commit()
        pos_id = cursor.lastrowid
        logger.info("[Portfolio] Added %s to %s as %s (id=%d)", symbol, horizon, status, pos_id)
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

        # Status must be a valid enum value; exit_reason is the descriptive cause
        # (e.g. STRUCTURE_BREAK / MANUAL are reasons, not statuses). Map anything
        # that isn't a terminal status onto CLOSED so the CHECK never rejects it.
        final_status = exit_reason if exit_reason in ("TARGET_HIT", "STOP_HIT", "CLOSED", "PARTIAL_EXIT") else "CLOSED"

        # Update position
        conn.execute(
            """
            UPDATE portfolio_positions SET
                status = ?, exit_price = ?, exit_reason = ?,
                profit_loss = ?, profit_loss_pct = ?,
                current_price = ?, closed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (final_status, exit_price, exit_reason, pl, pl_pct, exit_price, now_str, now_str, position_id),
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


def get_portfolio(horizon: str | None = None, include_closed: bool = False,
                  include_pending: bool = False) -> list[dict]:
    """Get portfolio positions.

    Default: ACTIVE only (this is what the price tracker consumes — a PENDING row
    has not entered, so it must NEVER be tracked/priced/exited as a live trade).
    include_pending=True additionally returns armed (PENDING) rows for the API/UI
    so they can be shown as "Awaiting Entry" (still excluded from P&L/analytics).
    include_closed=True returns everything (history views).
    """
    conn = get_connection()
    try:
        conditions = []
        params: list = []
        if horizon:
            conditions.append("horizon = ?")
            params.append(horizon.upper())
        if not include_closed:
            if include_pending:
                conditions.append("status IN ('ACTIVE','PENDING')")
            else:
                conditions.append("status = 'ACTIVE'")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""
            SELECT * FROM portfolio_positions
            {where}
            ORDER BY
                CASE status WHEN 'ACTIVE' THEN 0 WHEN 'PENDING' THEN 1 ELSE 2 END,
                datetime(created_at) DESC
            LIMIT 100
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_positions(horizon: str | None = None) -> list[dict]:
    """Armed (PENDING) positions awaiting an entry tap."""
    conn = get_connection()
    try:
        if horizon:
            rows = conn.execute(
                "SELECT * FROM portfolio_positions WHERE status = 'PENDING' AND horizon = ? "
                "ORDER BY datetime(created_at) DESC",
                (horizon.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM portfolio_positions WHERE status = 'PENDING' "
                "ORDER BY datetime(created_at) DESC",
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def activate_pending_position(position_id: int, trigger_price: float,
                              entered_at: str | None = None) -> bool:
    """Flip an armed PENDING position to ACTIVE — the entry genuinely triggered.

    entry_price is UNCHANGED (the strategy's planned entry, which was just traded
    through). days-held + P&L now measure from entered_at (= trigger time). Resets
    the high/low-since-entry window to the trigger price so drawdown is measured
    from the real entry, not the arm period. Returns True if a row flipped.
    """
    now = entered_at or datetime.now(_IST).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT entry_price FROM portfolio_positions WHERE id = ? AND status = 'PENDING'",
            (position_id,),
        ).fetchone()
        if not row:
            return False
        entry = float(row["entry_price"])
        pl = round(trigger_price - entry, 2)
        pl_pct = round((trigger_price - entry) / entry * 100, 2) if entry else 0.0
        conn.execute(
            """
            UPDATE portfolio_positions SET
                status = 'ACTIVE', entered_at = ?, current_price = ?,
                high_since_entry = ?, low_since_entry = ?,
                profit_loss = ?, profit_loss_pct = ?, drawdown = 0, drawdown_pct = 0,
                days_held = 0, updated_at = ?
            WHERE id = ? AND status = 'PENDING'
            """,
            (now, trigger_price, trigger_price, trigger_price, pl, pl_pct, now, position_id),
        )
        conn.commit()
        logger.info("[Portfolio] ARM→ACTIVE id=%d triggered @%.2f (entry %.2f)",
                    position_id, trigger_price, entry)
        return True
    finally:
        conn.close()


def reclassify_active_to_pending(position_id: int, arm_ref_price: float | None = None) -> bool:
    """One-time reconciliation: demote an ACTIVE position back to PENDING because
    its entry was NEVER actually traded through (a phantom entry). Wipes the
    fabricated P&L / days-held so it accrues nothing until a genuine tap. Returns
    True if a row changed."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE portfolio_positions SET
                status = 'PENDING', entered_at = NULL,
                profit_loss = 0, profit_loss_pct = 0, drawdown = 0, drawdown_pct = 0,
                days_held = 0, high_since_entry = NULL, low_since_entry = NULL,
                arm_ref_price = COALESCE(?, arm_ref_price, current_price, entry_price),
                updated_at = ?
            WHERE id = ? AND status = 'ACTIVE'
            """,
            (arm_ref_price, datetime.now(_IST).isoformat(), position_id),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def backfill_entered_at(position_id: int, entered_at: str) -> bool:
    """Reconciliation: for a legitimately-triggered ACTIVE position, stamp the
    real entry date so days-held is measured from the tap (not from arming).
    Only fills when currently NULL. Returns True if a row changed."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE portfolio_positions SET entered_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'ACTIVE' AND entered_at IS NULL",
            (entered_at, datetime.now(_IST).isoformat(), position_id),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def expire_pending_position(position_id: int, reason: str = "EXPIRED") -> bool:
    """Retire an armed position that never triggered (ran away or timed out).

    This is NOT a trade — it never entered — so it is NOT journaled. It simply
    frees the slot. Returns True if a row was expired.
    """
    now = datetime.now(_IST).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE portfolio_positions SET status = 'EXPIRED', exit_reason = ?, "
            "closed_at = ?, updated_at = ? WHERE id = ? AND status = 'PENDING'",
            (reason, now, now, position_id),
        )
        conn.commit()
        if cur.rowcount:
            logger.info("[Portfolio] PENDING expired id=%d (%s)", position_id, reason)
        return bool(cur.rowcount)
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
    """Return the symbol's COMMITTED position (ACTIVE or armed PENDING) if any.

    Used as the "already in the book?" guard by the selectors/promotion path, so
    a symbol that is merely armed (awaiting entry) is not re-armed or duplicated.
    """
    conn = get_connection()
    try:
        if horizon:
            row = conn.execute(
                "SELECT * FROM portfolio_positions WHERE symbol = ? AND horizon = ? "
                "AND status IN ('ACTIVE','PENDING')",
                (symbol.strip().upper(), horizon.upper()),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM portfolio_positions WHERE symbol = ? AND status IN ('ACTIVE','PENDING')",
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
    """Position counts per horizon.

    `swing`/`longterm` = ACTIVE (live) counts — what analytics/capacity-of-live
    care about. `*_pending` = armed (awaiting entry). `*_used` = ACTIVE+PENDING,
    the number of committed slots (this is what capacity is enforced against, so
    an armed slot can't be double-filled). Cap = `*_max`.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT horizon, status, COUNT(*) as cnt FROM portfolio_positions "
            "WHERE status IN ('ACTIVE','PENDING') GROUP BY horizon, status"
        ).fetchall()
        active = {"SWING": 0, "LONGTERM": 0}
        pending = {"SWING": 0, "LONGTERM": 0}
        for r in rows:
            if r["status"] == "ACTIVE":
                active[r["horizon"]] = r["cnt"]
            elif r["status"] == "PENDING":
                pending[r["horizon"]] = r["cnt"]
        return {
            "swing": active["SWING"],
            "swing_pending": pending["SWING"],
            "swing_used": active["SWING"] + pending["SWING"],
            "swing_max": MAX_SWING_POSITIONS,
            "longterm": active["LONGTERM"],
            "longterm_pending": pending["LONGTERM"],
            "longterm_used": active["LONGTERM"] + pending["LONGTERM"],
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
                SUM(CASE WHEN exit_reason = 'TARGET_HIT' THEN 1 ELSE 0 END) as target_hits,
                SUM(CASE WHEN exit_reason = 'STOP_HIT' THEN 1 ELSE 0 END) as stop_hits,
                SUM(CASE WHEN exit_reason = 'STRUCTURE_BREAK' THEN 1 ELSE 0 END) as structure_exits,
                SUM(CASE WHEN exit_reason NOT IN ('TARGET_HIT','STOP_HIT','STRUCTURE_BREAK') THEN 1 ELSE 0 END) as other_exits,
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
        target_hits = row["target_hits"] or 0

        # ── Unique-setup view ────────────────────────────────────────────────
        # Collapse repeat re-entries of the SAME setup (same symbol + same entry
        # price) into ONE representative trade (earliest by closed_at). This is
        # what the churn bug (e.g. APTUS re-promoted 9x at the same entry) and the
        # now-live re-entry guard concern. Shown alongside the realized numbers so
        # a single failed setup recorded N times doesn't N-count the loss.
        # NOTE: this is an ADJUSTED metric — the realized hit_rate_pct above
        # remains the ground truth across all closed trades.
        urows = conn.execute(
            f"SELECT symbol, entry_price, profit_loss_pct, closed_at "
            f"FROM portfolio_journal {where} ORDER BY datetime(closed_at) ASC",
            params,
        ).fetchall()
        _seen: dict[tuple, float] = {}
        for ur in urows:
            key = (str(ur["symbol"]).upper(), round(float(ur["entry_price"] or 0), 2))
            if key not in _seen:  # keep earliest occurrence as representative
                _seen[key] = float(ur["profit_loss_pct"] or 0)
        unique_total = len(_seen)
        unique_wins = sum(1 for p in _seen.values() if p > 0)

        return {
            "total_trades": total,
            "wins": wins,
            "losses": row["losses"] or 0,
            # hit_rate_pct = % of ALL closed trades that were net positive (realized truth).
            "hit_rate_pct": round(wins / total * 100, 1) if total > 0 else 0.0,
            # Unique-setup view: repeat re-entries of the same setup collapsed to one.
            "unique_trades": unique_total,
            "unique_wins": unique_wins,
            "unique_hit_rate_pct": round(unique_wins / unique_total * 100, 1) if unique_total > 0 else 0.0,
            "repeat_reentries_collapsed": total - unique_total,
            # Exit-reason breakdown so the UI can distinguish "hit target" from
            # "cut early by structure-break" from "stopped out" — otherwise a
            # low win rate hides a positive-expectancy system.
            "target_hits": target_hits,
            "stop_hits": row["stop_hits"] or 0,
            "structure_exits": row["structure_exits"] or 0,
            "other_exits": row["other_exits"] or 0,
            "target_hit_rate_pct": round(target_hits / total * 100, 1) if total > 0 else 0.0,
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

        # Live active counts per horizon — the seed path must respect the same
        # capacity cap as add_position/promote_to_portfolio, otherwise it can
        # bulk-insert past MAX and the book shows an over-cap "25/20" state.
        active_counts = {
            r["horizon"]: r["cnt"]
            for r in conn.execute(
                "SELECT horizon, COUNT(*) AS cnt FROM portfolio_positions "
                "WHERE status = 'ACTIVE' GROUP BY horizon"
            ).fetchall()
        }

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

            # Respect capacity — never seed past MAX for the horizon.
            cap = MAX_SWING_POSITIONS if horizon == "SWING" else MAX_LONGTERM_POSITIONS
            if active_counts.get(horizon, 0) >= cap:
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
            active_counts[horizon] = active_counts.get(horizon, 0) + 1
            seeded += 1

        conn.commit()
        if seeded:
            logger.info("[Portfolio] Seeded %d positions from existing running_trades", seeded)
        return seeded
    finally:
        conn.close()
