"""
dashboard/backend/db/trade_lifecycle.py
=======================================
Canonical trade-lifecycle ledger — the single history of everything that
happens on the platform.

WHY THIS EXISTS
---------------
The Track Record page used to read `stock_recommendations`, which records
research IDEAS, not trades. The consequences were visible on a public page:

  * ideas that were never taken into any book (TIL, STALLION, PNGJL, SENCO…)
    were displayed as successful "Target Hit" trades;
  * genuine portfolio trades were absent entirely — SCANSTL closed at +51.18%
    in the swing book and appeared nowhere;
  * the page's win rate could never agree with the books' win rate, because it
    was measuring a different population.

This ledger fixes the cause rather than the symptom. Every module writes its
lifecycle events here, and Track Record reads ONLY from here.

MODEL
-----
`trade_lifecycle`         one row per trade/idea = its CURRENT state
`trade_lifecycle_events`  append-only history; nothing is ever overwritten

A lifecycle row is identified by a deterministic UUID derived from
(source, source_table, source_id), so backfills and repeated writes converge on
the same row instead of duplicating it.

STATUS VOCABULARY
-----------------
An idea that never became a trade is NEVER_EXECUTED or EXPIRED — never
TARGET_HIT. That distinction is the whole point: "the level we published was
reached" is not the same claim as "we held this and made money".
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

from .schema import get_connection

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

# Namespace for deterministic ids — stable across runs and machines.
_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# ── Vocabulary ───────────────────────────────────────────────────────────────
SOURCES = ("RESEARCH", "SWING", "LONGTERM", "MOMENTUM", "MANUAL", "PAPER")

STATUSES = (
    # ── pre-entry ──
    "IDEA_GENERATED",   # research published it; nothing committed yet
    "AWAITING_ENTRY",   # armed, planned entry not yet traded through
    "ENTRY_TRIGGERED",  # entry tapped, fill being recorded
    # ── live ──
    "ACTIVE",           # live position
    "BREAKEVEN",        # stop moved to entry — risk removed
    "TRAILING_SL",      # stop trailed above entry
    "PARTIAL_EXIT",     # part booked, remainder running
    "TARGET1_HIT",      # first target booked, position may continue
    "TARGET2_HIT",
    "TARGET3_HIT",
    # ── terminal ──
    "TARGET_HIT",       # final target — position fully closed in profit
    "STOP_HIT",
    "TIME_EXIT",        # closed by a time/stale rule, not by price
    "FORCED_EXIT",      # closed by risk override / structural break
    "MANUAL_CLOSED",
    "EXPIRED",          # armed idea that timed out without triggering
    "CANCELLED",
    "INVALIDATED",      # setup broke before entry
    "NEVER_EXECUTED",   # published idea that was never taken into a book
)

# Statuses that represent a REAL position that was actually held. Execution
# rate, win rate and return are computed over these only — an idea that never
# filled has no P&L to contribute.
EXECUTED_STATUSES = (
    "ENTRY_TRIGGERED", "ACTIVE", "BREAKEVEN", "TRAILING_SL", "PARTIAL_EXIT",
    "TARGET1_HIT", "TARGET2_HIT", "TARGET3_HIT",
    "TARGET_HIT", "STOP_HIT", "TIME_EXIT", "FORCED_EXIT", "MANUAL_CLOSED",
)
CLOSED_STATUSES = ("TARGET_HIT", "STOP_HIT", "TIME_EXIT", "FORCED_EXIT", "MANUAL_CLOSED")
OPEN_STATUSES = ("ENTRY_TRIGGERED", "ACTIVE", "BREAKEVEN", "TRAILING_SL",
                 "PARTIAL_EXIT", "TARGET1_HIT", "TARGET2_HIT", "TARGET3_HIT")
PENDING_STATUSES = ("AWAITING_ENTRY", "IDEA_GENERATED")
DEAD_STATUSES = ("EXPIRED", "CANCELLED", "INVALIDATED", "NEVER_EXECUTED")

# Soft delete — a row is never removed, only reclassified.
RECORD_STATES = ("ACTIVE", "ARCHIVED", "HIDDEN", "DUPLICATE")

_DDL = """
CREATE TABLE IF NOT EXISTS trade_lifecycle (
    uuid                TEXT PRIMARY KEY,
    source              TEXT NOT NULL,      -- RESEARCH | SWING | LONGTERM | MOMENTUM | MANUAL | PAPER
    portfolio           TEXT,               -- book it belongs to (NULL for research-only)
    engine              TEXT,               -- SMC | MOMENTUM | MANUAL | AI
    setup               TEXT,
    strategy            TEXT,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL DEFAULT 'LONG',
    confidence          REAL,
    entry_price         REAL,
    stop_loss           REAL,
    target_1            REAL,
    target_2            REAL,
    -- lifecycle timestamps
    idea_at             TEXT,               -- when the idea was generated
    entry_trigger_at    TEXT,               -- when price traded through the entry
    entry_fill_at       TEXT,               -- when the position actually opened
    exit_at             TEXT,
    -- outcome
    exit_price          REAL,
    exit_reason         TEXT,
    status              TEXT NOT NULL,
    executed            INTEGER NOT NULL DEFAULT 0,   -- 1 = a real position existed
    pnl_pct             REAL,
    pnl_rs              REAL,
    rr_realized         REAL,
    holding_days        INTEGER,
    partial_exits       INTEGER NOT NULL DEFAULT 0,
    trail_updates       INTEGER NOT NULL DEFAULT 0,
    -- provenance (lets a backfill converge instead of duplicating)
    source_table        TEXT,
    source_id           TEXT,
    is_duplicate        INTEGER NOT NULL DEFAULT 0,
    is_legacy           INTEGER NOT NULL DEFAULT 0,   -- reconstructed, not natively written
    -- derived, indexed for fast filtering
    year                INTEGER,
    month               INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tl_symbol     ON trade_lifecycle(symbol);
CREATE INDEX IF NOT EXISTS idx_tl_status     ON trade_lifecycle(status);
CREATE INDEX IF NOT EXISTS idx_tl_source     ON trade_lifecycle(source);
CREATE INDEX IF NOT EXISTS idx_tl_portfolio  ON trade_lifecycle(portfolio);
CREATE INDEX IF NOT EXISTS idx_tl_created    ON trade_lifecycle(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tl_updated    ON trade_lifecycle(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tl_exit       ON trade_lifecycle(exit_at DESC);
CREATE INDEX IF NOT EXISTS idx_tl_ym         ON trade_lifecycle(year, month);
CREATE INDEX IF NOT EXISTS idx_tl_executed   ON trade_lifecycle(executed, status);
CREATE INDEX IF NOT EXISTS idx_tl_engine     ON trade_lifecycle(engine);
CREATE INDEX IF NOT EXISTS idx_tl_filter     ON trade_lifecycle(is_duplicate, source, status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tl_provenance ON trade_lifecycle(source_table, source_id);

-- Append-only audit trail. A lifecycle event NEVER overwrites history.
CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lifecycle_id  TEXT NOT NULL,
    event         TEXT NOT NULL,
    from_status   TEXT,
    to_status     TEXT,
    price         REAL,
    note          TEXT,
    payload       TEXT,
    occurred_at   TEXT NOT NULL,
    recorded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tle_lifecycle ON trade_lifecycle_events(lifecycle_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_tle_occurred  ON trade_lifecycle_events(occurred_at DESC);
"""


# Columns added after the first release. ADD COLUMN is cheap and in-place, so
# the ledger extends without a rebuild and old rows stay valid.
_EXTRA_COLS = [
    # ── lifecycle chaining ───────────────────────────────────────────────────
    # A research idea is the FIRST event of a trade's life, not something the
    # portfolio row replaces. `chain_id` groups every stage of one idea so
    # "200 ideas -> 55 entries -> 30 targets" stays answerable; `parent_id`
    # points a portfolio row back at the idea that produced it.
    ("chain_id", "TEXT"),
    ("parent_id", "TEXT"),
    ("stage", "TEXT"),                  # IDEA | POSITION
    # ── engine attribution ───────────────────────────────────────────────────
    ("engine_version", "TEXT"),
    ("strategy_version", "TEXT"),
    ("algorithm_hash", "TEXT"),
    # ── immutable snapshots ──────────────────────────────────────────────────
    # The setup may not exist six months from now; keep enough to rebuild the
    # recommendation exactly as it looked at the time.
    ("recommendation_json", "TEXT"),
    ("context_json", "TEXT"),           # ATR, sector, regime, confidence breakdown, AI note
    ("chart_entry_json", "TEXT"),
    ("chart_exit_json", "TEXT"),
    ("entry_reason", "TEXT"),
    ("exit_note", "TEXT"),
    # ── excursion + multi-target ─────────────────────────────────────────────
    ("mae_pct", "REAL"),                # worst adverse excursion
    ("mfe_pct", "REAL"),                # best favourable excursion
    ("high_since_entry", "REAL"),
    ("low_since_entry", "REAL"),
    ("target_3", "REAL"),
    ("target_1_hit_at", "TEXT"),
    ("target_2_hit_at", "TEXT"),
    ("target_3_hit_at", "TEXT"),
    ("breakeven_at", "TEXT"),
    ("trail_stop", "REAL"),
    ("time_to_target_days", "REAL"),
    ("time_to_stop_days", "REAL"),
    # ── soft delete ──────────────────────────────────────────────────────────
    ("record_state", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
]

_EXTRA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tl_chain  ON trade_lifecycle(chain_id)",
    "CREATE INDEX IF NOT EXISTS idx_tl_parent ON trade_lifecycle(parent_id)",
    "CREATE INDEX IF NOT EXISTS idx_tl_stage  ON trade_lifecycle(stage, status)",
    "CREATE INDEX IF NOT EXISTS idx_tl_state  ON trade_lifecycle(record_state)",
    "CREATE INDEX IF NOT EXISTS idx_tl_engver ON trade_lifecycle(engine, engine_version)",
]

_STATS_DDL = """
-- Rolled-up analytics so trend views never recompute years of history.
CREATE TABLE IF NOT EXISTS lifecycle_stats_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    period        TEXT NOT NULL,      -- DAILY | WEEKLY | MONTHLY
    period_key    TEXT NOT NULL,      -- 2026-08-02 | 2026-W31 | 2026-08
    portfolio     TEXT NOT NULL DEFAULT 'ALL',
    payload       TEXT NOT NULL,      -- full stats JSON
    created_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lss_key
    ON lifecycle_stats_snapshots(period, period_key, portfolio);
CREATE INDEX IF NOT EXISTS idx_lss_period ON lifecycle_stats_snapshots(period, period_key DESC);
"""


def init_lifecycle_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(_DDL)
        conn.executescript(_STATS_DDL)
        have = {r[1] for r in conn.execute("PRAGMA table_info(trade_lifecycle)").fetchall()}
        for name, decl in _EXTRA_COLS:
            if name not in have:
                conn.execute(f"ALTER TABLE trade_lifecycle ADD COLUMN {name} {decl}")
        for idx in _EXTRA_INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


def make_uuid(source: str, source_table: str, source_id) -> str:
    return str(uuid.uuid5(_NS, f"{source}|{source_table}|{source_id}"))


def _now() -> str:
    return datetime.now(_IST).isoformat()


def _ym(ts: str | None) -> tuple[int | None, int | None]:
    if not ts:
        return None, None
    try:
        d = datetime.fromisoformat(str(ts).replace(" ", "T").replace("Z", "+00:00"))
        return d.year, d.month
    except (TypeError, ValueError):
        s = str(ts)
        try:
            return int(s[0:4]), int(s[5:7])
        except (ValueError, IndexError):
            return None, None


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ── Writing ──────────────────────────────────────────────────────────────────

def record_event(lifecycle_id: str, event: str, *, from_status: str | None = None,
                 to_status: str | None = None, price: float | None = None,
                 note: str | None = None, payload: str | None = None,
                 occurred_at: str | None = None, conn=None) -> None:
    """Append one immutable lifecycle event. Never updates an existing row."""
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.execute(
            "INSERT INTO trade_lifecycle_events "
            "(lifecycle_id, event, from_status, to_status, price, note, payload, occurred_at, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (lifecycle_id, event, from_status, to_status, price, note, payload,
             occurred_at or _now(), _now()),
        )
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def upsert(rec: dict, *, conn=None, event: str | None = None) -> str:
    """Insert or update one lifecycle row, keyed on its deterministic UUID.

    The current-state row is updated in place; the transition is additionally
    appended to trade_lifecycle_events so the audit history stays complete.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        lid = rec.get("uuid") or make_uuid(rec["source"], rec.get("source_table") or "",
                                           rec.get("source_id") or "")
        rec["uuid"] = lid
        anchor = rec.get("exit_at") or rec.get("entry_fill_at") or rec.get("idea_at")
        y, m = _ym(anchor)
        rec["created_at"] = rec.get("created_at") or rec.get("idea_at") or _now()
        rec["updated_at"] = _now()
        rec["year"], rec["month"] = y, m
        rec["executed"] = 1 if rec.get("status") in EXECUTED_STATUSES else 0
        # NOT NULL columns need a concrete value even when a caller omits them —
        # a partial record must never abort the whole backfill.
        for col, default in (("direction", "LONG"), ("partial_exits", 0),
                             ("trail_updates", 0), ("is_duplicate", 0),
                             ("is_legacy", 0), ("status", "NEVER_EXECUTED"),
                             ("record_state", "ACTIVE"),
                             ("stage", "POSITION" if rec.get("portfolio") else "IDEA")):
            if rec.get(col) is None:
                rec[col] = default
        # A trade's life is one chain: the research idea is its FIRST stage, not
        # something a later portfolio row replaces. Default the chain to the row
        # itself so a standalone record is still a (single-stage) chain.
        rec.setdefault("chain_id", rec.get("parent_id") or lid)
        if rec.get("chain_id") is None:
            rec["chain_id"] = lid
        # A duplicate is soft-deleted, never removed.
        if rec.get("is_duplicate") and rec.get("record_state") == "ACTIVE":
            rec["record_state"] = "DUPLICATE"

        prev = conn.execute("SELECT status FROM trade_lifecycle WHERE uuid = ?", (lid,)).fetchone()
        cols = ["uuid", "source", "portfolio", "engine", "setup", "strategy", "symbol",
                "direction", "confidence", "entry_price", "stop_loss", "target_1", "target_2",
                "idea_at", "entry_trigger_at", "entry_fill_at", "exit_at", "exit_price",
                "exit_reason", "status", "executed", "pnl_pct", "pnl_rs", "rr_realized",
                "holding_days", "partial_exits", "trail_updates", "source_table", "source_id",
                "is_duplicate", "is_legacy", "year", "month", "created_at", "updated_at",
                "chain_id", "parent_id", "stage", "engine_version", "strategy_version",
                "algorithm_hash", "recommendation_json", "context_json", "chart_entry_json",
                "chart_exit_json", "entry_reason", "exit_note", "mae_pct", "mfe_pct",
                "high_since_entry", "low_since_entry", "target_3", "target_1_hit_at",
                "target_2_hit_at", "target_3_hit_at", "breakeven_at", "trail_stop",
                "time_to_target_days", "time_to_stop_days", "record_state"]
        vals = [rec.get(c) for c in cols]
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("uuid", "created_at"))
        conn.execute(
            f"INSERT INTO trade_lifecycle ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(uuid) DO UPDATE SET {updates}",
            vals,
        )
        prev_status = prev["status"] if prev else None
        changed = prev_status != rec.get("status")
        if event or changed:
            record_event(lid, event or "STATUS_CHANGE", from_status=prev_status,
                         to_status=rec.get("status"), price=rec.get("exit_price"),
                         occurred_at=anchor, conn=conn)
        if own:
            conn.commit()
        # Push, don't poll. Only real transitions are announced — a no-op resync
        # must not wake every connected browser.
        if changed and event != "BACKFILL":
            try:
                from dashboard.backend.lifecycle_bus import publish
                publish("LIFECYCLE_UPDATED", lifecycle_id=lid, symbol=rec.get("symbol"),
                        portfolio=rec.get("portfolio") or rec.get("source"),
                        status=rec.get("status"), from_status=prev_status,
                        pnl_pct=rec.get("pnl_pct"))
            except Exception:
                logger.debug("[Lifecycle] bus publish failed (non-fatal)", exc_info=True)
        return lid
    finally:
        if own:
            conn.close()
