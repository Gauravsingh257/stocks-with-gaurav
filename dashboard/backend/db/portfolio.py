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
    -- 1 = this row is a re-seed artifact of a setup already journaled (same
    -- symbol+horizon+entry+origin), NOT an independent trade. Rows are never
    -- deleted (the journal is immutable) but duplicates are excluded from every
    -- published statistic. See mark_journal_duplicates().
    is_duplicate          INTEGER NOT NULL DEFAULT 0,
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


def migrate_journal_duplicate_flag() -> None:
    """Add portfolio_journal.is_duplicate (idempotent ADD COLUMN) and backfill it.

    Why this exists: the seed path used to re-create a position for an engine
    trade that was still open after the portfolio had already exited it, so the
    SAME setup was journaled over and over (observed: CIPLA x11 closed inside 51
    minutes, APTUS x10 — identical entry and identical origin created_at). Those
    rows are a bug artifact, not trades, and they were dominating the published
    win rate and return.
    """
    conn = get_connection()
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_journal)").fetchall()}
        if "is_duplicate" not in have:
            conn.execute("ALTER TABLE portfolio_journal ADD COLUMN is_duplicate INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    except Exception as exc:
        logger.error("[Portfolio] migrate_journal_duplicate_flag failed (non-fatal): %s", exc)
        conn.close()
        return
    finally:
        try:
            conn.close()
        except Exception:
            pass
    mark_journal_duplicates()


def mark_journal_duplicates(dry_run: bool = False) -> dict:
    """Flag re-seed artifacts in portfolio_journal. Idempotent; safe to re-run.

    Duplicate key = (symbol, horizon, entry_price rounded to 2dp, created_at).
    `created_at` is the ORIGIN timestamp copied from the source recommendation,
    so every re-seed of one engine trade carries the identical value while a
    genuine later re-entry of the same name carries a different one. That makes
    the key precise: it collapses the churn loop without ever merging two real
    trades (verified — SAMMAANCAP re-entered at the same price from a different
    origin stays counted twice).

    The row kept as canonical is the LAST by closed_at: it is the terminal
    outcome of that holding (e.g. CIPLA's final STOP_HIT), which is what actually
    happened. Nothing is deleted — the journal stays immutable; duplicates are
    only marked so statistics can exclude them.
    """
    conn = get_connection()
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_journal)").fetchall()}
        if "is_duplicate" not in have:
            return {"ok": False, "reason": "is_duplicate column missing"}

        rows = conn.execute(
            "SELECT id, symbol, horizon, entry_price, created_at, closed_at, profit_loss_pct "
            "FROM portfolio_journal ORDER BY datetime(closed_at) ASC, id ASC"
        ).fetchall()

        groups: dict[tuple, list] = {}
        for r in rows:
            key = (
                str(r["symbol"]).strip().upper(),
                str(r["horizon"]).strip().upper(),
                round(float(r["entry_price"] or 0), 2),
                str(r["created_at"] or ""),
            )
            groups.setdefault(key, []).append(r)

        dupe_ids: list[int] = []
        detail: list[dict] = []
        for key, grp in groups.items():
            if len(grp) < 2:
                continue
            # keep the terminal (last-closed) row; everything before it is churn
            losers = grp[:-1]
            dupe_ids.extend(int(r["id"]) for r in losers)
            detail.append({
                "symbol": key[0], "horizon": key[1], "entry_price": key[2],
                "rows": len(grp), "marked": len(losers),
                "pnl_pct_removed": round(sum(float(r["profit_loss_pct"] or 0) for r in losers), 2),
                "kept_row_pnl_pct": round(float(grp[-1]["profit_loss_pct"] or 0), 2),
            })

        keep_ids = [int(r["id"]) for r in rows if int(r["id"]) not in set(dupe_ids)]
        if not dry_run:
            # Full re-assert (both directions) so the flag is always derivable
            # from the data — never sticky if a group later gains a real row.
            conn.execute("UPDATE portfolio_journal SET is_duplicate = 0")
            if dupe_ids:
                conn.executemany(
                    "UPDATE portfolio_journal SET is_duplicate = 1 WHERE id = ?",
                    [(i,) for i in dupe_ids],
                )
            conn.commit()

        if dupe_ids:
            logger.warning("[Portfolio] journal dedupe: %d duplicate row(s) across %d setup(s) flagged%s",
                           len(dupe_ids), len(detail), " (dry-run)" if dry_run else "")
        return {
            "ok": True, "dry_run": dry_run,
            "total_rows": len(rows), "duplicates": len(dupe_ids), "clean_rows": len(keep_ids),
            "groups": sorted(detail, key=lambda d: -d["marked"]),
        }
    except Exception as exc:
        logger.error("[Portfolio] mark_journal_duplicates failed (non-fatal): %s", exc)
        return {"ok": False, "reason": str(exc)}
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
    migrate_journal_duplicate_flag()


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

        # Self-maintaining duplicate detection. Any write path (seed, promote,
        # manual, future ones) that re-creates a position for an origin already
        # journaled gets flagged here at insert time, so statistics can never
        # again be inflated by re-seed churn even if a new path forgets the
        # guards upstream. The journal row itself is still written — history is
        # immutable; only its weight in published stats changes. The EARLIER row
        # is demoted so the newest close stays canonical (terminal outcome).
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_journal)").fetchall()}
        _has_dupe_col = "is_duplicate" in _cols
        prior_ids: list[int] = []
        if _has_dupe_col:
            prior_ids = [
                int(r["id"]) for r in conn.execute(
                    "SELECT id FROM portfolio_journal WHERE symbol = ? AND horizon = ? "
                    "AND ROUND(entry_price, 2) = ROUND(?, 2) AND created_at = ?",
                    (pos["symbol"], pos["horizon"], entry, pos["created_at"]),
                ).fetchall()
            ]

        # Journal entry (immutable — never deleted)
        cols = ("position_id, symbol, horizon, direction, entry_price, exit_price, "
                "stop_loss, target_1, target_2, profit_loss, profit_loss_pct, "
                "days_held, high_since_entry, low_since_entry, confidence_score, "
                "reasoning, exit_reason, created_at, closed_at")
        vals = [
            position_id, pos["symbol"], pos["horizon"], pos["direction"],
            entry, exit_price, pos["stop_loss"], pos.get("target_1"),
            pos.get("target_2"), pl, pl_pct, pos.get("days_held", 0),
            pos.get("high_since_entry"), pos.get("low_since_entry"),
            pos.get("confidence_score"), pos.get("reasoning"),
            exit_reason, pos["created_at"], now_str,
        ]
        if _has_dupe_col:
            cols += ", is_duplicate"
            vals.append(0)  # newest close is always the canonical one
        conn.execute(
            f"INSERT INTO portfolio_journal ({cols}) VALUES ({', '.join('?' * len(vals))})",
            tuple(vals),
        )
        if prior_ids:
            conn.executemany(
                "UPDATE portfolio_journal SET is_duplicate = 1 WHERE id = ?",
                [(i,) for i in prior_ids],
            )
            logger.warning(
                "[Portfolio] %s re-journaled for an origin already closed (%d prior row(s) "
                "demoted to duplicate) — a write path bypassed the re-seed guard",
                pos["symbol"], len(prior_ids),
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


def update_position_price(position_id: int, *, require_active: bool = False, **kwargs) -> None:
    """Update live price data for a position.

    `require_active=True` makes the write conditional on the row still being
    ACTIVE — an atomic concurrency guard. The price tracker reads the active
    list, then (seconds later) writes each row back stamped ACTIVE; without the
    guard, a position CLOSED in that window (a manual close or an auto-exit) gets
    silently resurrected. With the guard the UPDATE simply matches 0 rows and is
    skipped, so a close can never be clobbered. There is no read-then-write gap,
    so no race remains.
    """
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
    where = "WHERE id = ?"
    values = list(updates.values()) + [position_id]
    if require_active:
        where += " AND status = 'ACTIVE'"

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE portfolio_positions SET {set_clause} {where}",
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


def get_journal(horizon: str | None = None, limit: int = 50,
                include_duplicates: bool = False) -> list[dict]:
    """Get closed trade journal entries (immutable history).

    Re-seed artifacts are hidden by default so the visible history matches the
    population the published stats are computed over — the two must never
    disagree. Pass include_duplicates=True for the raw audit view.
    """
    conn = get_connection()
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_journal)").fetchall()}
        dupe = "" if (include_duplicates or "is_duplicate" not in _cols) else " AND is_duplicate = 0"
        if horizon:
            rows = conn.execute(
                f"SELECT * FROM portfolio_journal WHERE horizon = ?{dupe} "
                f"ORDER BY datetime(closed_at) DESC LIMIT ?",
                (horizon.upper(), limit),
            ).fetchall()
        elif dupe:
            rows = conn.execute(
                "SELECT * FROM portfolio_journal WHERE is_duplicate = 0 "
                "ORDER BY datetime(closed_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM portfolio_journal ORDER BY datetime(closed_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_journal_stats(horizon: str | None = None, include_open: bool = True) -> dict:
    """Book performance: realised (closed) + open (mark-to-market).

    Three invariants this function must never break again:

    1. ONE POPULATION. Every closed-trade number is computed over the same rows —
       real trades only (`is_duplicate = 0`). Previously the headline win rate
       was computed on a de-duplicated population while the return was summed
       over ALL rows including duplicates, so the two figures in the same
       sentence described different trade sets (45.7% vs -41.37%, where the same
       clean population was +24.97%).

    2. NO SUM-OF-PERCENTAGES PRESENTED AS A RETURN. `SUM(profit_loss_pct)` adds
       percentages of different capital bases and is NOT a portfolio return; on
       an N-slot equal-weight book each trade moves capital by pct/N.

    3. THE RETURN MARKS THE OPEN BOOK. `total_book_return_pct` = realised +
       unrealised, so it moves as prices move. Reporting only closed trades made
       a book holding 19 green positions look flat. Win rate stays realised-only
       — an open position has not won until it closes (`blended_hit_rate_pct` is
       exposed for completeness but must not be the headline).

    `include_open=False` gives the realised-only view (used by the consistency
    check, which compares against the visible closed-trade rows).
    """
    from dashboard.backend.db.perf_stats import compute_book_stats

    hz = (horizon or "").upper() or None
    conn = get_connection()
    try:
        # Duplicate exclusion — degrade gracefully if the migration hasn't run.
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_journal)").fetchall()}
        has_dupe = "is_duplicate" in _cols
        dupe_clause = "is_duplicate = 0" if has_dupe else "1=1"

        if hz:
            where, params = f"WHERE horizon = ? AND {dupe_clause}", [hz]
        else:
            where, params = f"WHERE {dupe_clause}", []

        # Fetch the clean rows and let the canonical engine do ALL arithmetic —
        # this function must never compute a metric of its own again.
        trades = [dict(r) for r in conn.execute(
            f"SELECT symbol, profit_loss_pct, exit_reason, days_held, closed_at "
            f"FROM portfolio_journal {where}",
            params,
        ).fetchall()]

        if has_dupe:
            dup_where = "WHERE horizon = ? AND is_duplicate = 1" if hz else "WHERE is_duplicate = 1"
            duplicates_excluded = conn.execute(
                f"SELECT COUNT(*) AS c FROM portfolio_journal {dup_where}",
                [hz] if hz else [],
            ).fetchone()["c"] or 0
        else:
            duplicates_excluded = 0

        # Open book — ACTIVE only. PENDING rows are armed, not entered, so they
        # carry no P&L and must never move the return. Marking these live is what
        # makes the published return respond to the market instead of only
        # changing when a position closes.
        if include_open:
            ow = "WHERE status = 'ACTIVE' AND horizon = ?" if hz else "WHERE status = 'ACTIVE'"
            open_positions = [dict(r) for r in conn.execute(
                f"SELECT symbol, profit_loss_pct FROM portfolio_positions {ow}",
                [hz] if hz else [],
            ).fetchall()]
        else:
            open_positions = []
    finally:
        conn.close()

    # Slots = the book's capacity. Without a horizon this is the combined book.
    if hz == "SWING":
        slots = MAX_SWING_POSITIONS
    elif hz == "LONGTERM":
        slots = MAX_LONGTERM_POSITIONS
    else:
        slots = MAX_SWING_POSITIONS + MAX_LONGTERM_POSITIONS

    return compute_book_stats(
        trades, slots=slots, book=hz or "ALL",
        duplicates_excluded=duplicates_excluded,
        open_positions=open_positions,
    )


def seed_portfolio_from_recommendations() -> list[dict]:
    """
    Mirror the engine's live running_trades into the system portfolio.

    Originally a one-time boot migration; now also called every tracker cycle so
    engine trades taken intraday appear on the site within one cycle instead of
    only at the next web restart. Skips any symbol already committed (ACTIVE or
    armed PENDING). Returns the list of NEWLY-seeded positions so the caller can
    fire a real-time entry alert for each (empty list when nothing new).
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

        new_positions: list[dict] = []
        for row in rows:
            row_d = dict(row)
            symbol = row_d["symbol"]
            horizon = row_d.get("agent_type") or "SWING"

            # Skip if already committed — ACTIVE *or* armed PENDING. (Previously
            # only ACTIVE was checked, so an arm-on-tap idea for the same symbol
            # could be duplicated by the seed.)
            existing = conn.execute(
                "SELECT 1 FROM portfolio_positions WHERE symbol = ? AND horizon = ? "
                "AND status IN ('ACTIVE','PENDING')",
                (symbol, horizon),
            ).fetchone()
            if existing:
                continue

            # ── Already-exited guard (fixes the re-seed churn loop) ───────────
            # The engine holds a trade until ITS own exit; the portfolio applies
            # its own exit rules (stale / structure / trend break) and can close
            # first. When that happened the row above stopped matching, so the
            # next tracker cycle re-seeded the same still-RUNNING engine trade,
            # the portfolio exited it again, and the journal accumulated one
            # phantom "completed trade" per cycle (CIPLA x11 in 51 minutes,
            # APTUS x10). Once the portfolio has exited a given origin, that
            # exposure is done — never resurrect it from the same engine trade.
            origin_created = str(row_d.get("created_at") or "")
            already_exited = conn.execute(
                "SELECT 1 FROM portfolio_journal WHERE symbol = ? AND horizon = ? "
                "AND ROUND(entry_price, 2) = ROUND(?, 2) AND created_at = ? LIMIT 1",
                (symbol, horizon, float(row_d["entry_price"] or 0), origin_created),
            ).fetchone()
            if already_exited:
                logger.debug("[Portfolio] seed skip %s/%s — already exited this origin (%s)",
                             symbol, horizon, origin_created)
                continue

            # Setup-aware re-entry guard — the seed path bypassed it entirely, so
            # a failed setup could re-enter the book here even while the selector
            # path correctly refused it.
            try:
                if reentry_guard_blocks(symbol, horizon, float(row_d["entry_price"] or 0),
                                        cmp=float(row_d.get("current_price") or 0) or None):
                    continue
            except Exception as exc:  # fail open — never suppress a real fill on a guard error
                logger.warning("[Portfolio] seed re-entry guard errored for %s (allowing): %s", symbol, exc)

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

            # A seeded engine trade is a genuine live fill, so record the real
            # entry time (entry_triggered_at) as entered_at and the entry as the
            # arm reference — otherwise the row looks malformed (both null) and
            # days-held can't anchor to the true entry.
            entered_at = (row_d.get("entry_triggered_at") or row_d.get("created_at")
                          or datetime.now(_IST).isoformat())
            arm_ref = row_d.get("current_price") or row_d["entry_price"]
            conn.execute(
                """
                INSERT INTO portfolio_positions
                    (symbol, horizon, direction, entry_price, stop_loss, target_1, target_2,
                     current_price, profit_loss, profit_loss_pct, drawdown, drawdown_pct,
                     high_since_entry, low_since_entry, days_held,
                     confidence_score, reasoning, recommendation_id, status,
                     arm_ref_price, entered_at, created_at)
                VALUES (?, ?, 'LONG', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
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
                    arm_ref, entered_at,
                    row_d.get("created_at", datetime.now(_IST).isoformat()),
                ),
            )
            active_counts[horizon] = active_counts.get(horizon, 0) + 1
            new_positions.append({
                "symbol": symbol, "horizon": horizon,
                "entry_price": float(row_d["entry_price"]),
                "current_price": float(row_d.get("current_price") or row_d["entry_price"]),
                "stop_loss": float(row_d["stop_loss"]),
                "target_1": t1,
            })

        conn.commit()
        if new_positions:
            logger.info("[Portfolio] Seeded %d position(s) from live running_trades", len(new_positions))
        return new_positions
    finally:
        conn.close()
