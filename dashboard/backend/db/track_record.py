"""
dashboard/backend/db/track_record.py
=====================================
Immutable, survivorship-free **track-record ledger** (PR3).

Why this exists
---------------
The public track record was read from `stock_recommendations` joined to
`running_trades`. Those rows are *mutable* (outcomes overwrite the row) and are
**recycled** by `expire_old_recommendations` after 7 days — so a recommendation
that neither hit its target nor its stop simply *vanishes* from history. That is
textbook survivorship bias: only the resolved (often winning) subset survives.

This ledger fixes that with an append-only table:
  * one row is written when a recommendation is **published** (immutable snapshot
    of entry/stop/target/confidence at publish time),
  * its outcome is written **exactly once** when the recommendation resolves —
    `TARGET_HIT`, `STOP_HIT`, or **`EXPIRED`** (the survivorship fix: timed-out
    ideas are recorded as a neutral outcome, never dropped),
  * a resolved row is **never rewritten** (write-once), so history is honest.

Isolated `research_track_record` table — additive only, never touches the engine
tables. All helpers are import-safe, self-initialise on first use, and are
best-effort (a ledger failure must never break the recommendation pipeline).

Kill switch: `TRACK_RECORD_LEDGER_ENABLED` (default "1"). Set "0" to disable all
writes (reads still work against whatever was already captured).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from .schema import get_connection

logger = logging.getLogger("dashboard.db.track_record")

_DDL = """
CREATE TABLE IF NOT EXISTS research_track_record (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id  INTEGER UNIQUE,          -- one ledger row per recommendation
    symbol             TEXT NOT NULL,
    horizon            TEXT,                     -- SWING | LONGTERM
    setup              TEXT,
    entry_price        REAL,
    stop_loss          REAL,
    final_target       REAL,
    targets            TEXT,                     -- JSON list
    confidence_score   REAL,
    scan_cmp           REAL,
    rr_planned         REAL,
    published_at       TEXT NOT NULL,            -- immutable
    -- ── outcome (write-once) ──
    outcome            TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN|TARGET_HIT|STOP_HIT|EXPIRED
    exit_price         REAL,
    pnl_pct            REAL,
    pnl_r              REAL,
    holding_days       INTEGER,
    mfe_pct            REAL,                     -- max favourable excursion (max gain)
    mae_pct            REAL,                     -- max adverse excursion (max drawdown)
    resolved_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_track_record_outcome ON research_track_record(outcome);
CREATE INDEX IF NOT EXISTS idx_track_record_horizon ON research_track_record(horizon, published_at);
"""

_TERMINAL = ("TARGET_HIT", "STOP_HIT", "EXPIRED")


def ledger_enabled() -> bool:
    return str(os.getenv("TRACK_RECORD_LEDGER_ENABLED", "1")).strip().lower() in ("1", "true", "yes", "on")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init(conn) -> None:
    conn.executescript(_DDL)


def _rr_planned(entry, stop, final_target) -> float | None:
    try:
        entry = float(entry); stop = float(stop); tgt = float(final_target)
        risk = abs(entry - stop)
        return round(abs(tgt - entry) / risk, 2) if risk > 0 else None
    except (TypeError, ValueError):
        return None


def publish(rec_id: int, payload: dict) -> None:
    """Record a recommendation at publish time. Idempotent (INSERT OR IGNORE on
    recommendation_id) so re-publishes / dedup-updates never create duplicates
    and never overwrite the original immutable snapshot. Best-effort."""
    if not ledger_enabled() or not rec_id or rec_id < 0:
        return
    try:
        targets = payload.get("targets") or []
        final_target = None
        if targets:
            try:
                final_target = float(targets[-1])
            except (TypeError, ValueError):
                final_target = None
        entry = payload.get("entry_price")
        stop = payload.get("stop_loss")
        conn = get_connection()
        try:
            _init(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO research_track_record
                    (recommendation_id, symbol, horizon, setup, entry_price, stop_loss,
                     final_target, targets, confidence_score, scan_cmp, rr_planned, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(rec_id),
                    payload.get("symbol"),
                    payload.get("agent_type") or payload.get("horizon"),
                    payload.get("setup"),
                    entry, stop, final_target,
                    json.dumps(targets),
                    payload.get("confidence_score"),
                    payload.get("scan_cmp"),
                    _rr_planned(entry, stop, final_target),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # never break the rec pipeline
        logger.debug("track_record.publish failed rec=%s: %s", rec_id, exc)


def resolve(rec_id: int, outcome: str, *, exit_price: float | None = None,
            pnl_pct: float | None = None, pnl_r: float | None = None,
            mfe_pct: float | None = None, mae_pct: float | None = None) -> None:
    """Write an outcome exactly ONCE. Only transitions rows still `OPEN`, so a
    resolved outcome is never rewritten (immutable history). If the rec was never
    published (ledger added mid-flight), back-fills a minimal row first so the
    outcome is still captured. Best-effort."""
    if not ledger_enabled() or not rec_id or outcome not in _TERMINAL:
        return
    try:
        conn = get_connection()
        try:
            _init(conn)
            row = conn.execute(
                "SELECT id, entry_price, published_at FROM research_track_record WHERE recommendation_id = ?",
                (int(rec_id),),
            ).fetchone()
            if row is None:
                # Back-fill a minimal published row from stock_recommendations so
                # the outcome is not lost (survivorship-safe even for pre-ledger recs).
                sr = conn.execute(
                    "SELECT symbol, agent_type, setup, entry_price, stop_loss, targets, "
                    "confidence_score, scan_cmp, created_at FROM stock_recommendations WHERE id = ?",
                    (int(rec_id),),
                ).fetchone()
                if sr is not None:
                    sr = dict(sr)
                    conn.execute(
                        """INSERT OR IGNORE INTO research_track_record
                           (recommendation_id, symbol, horizon, setup, entry_price, stop_loss,
                            targets, confidence_score, scan_cmp, published_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (int(rec_id), sr.get("symbol"), sr.get("agent_type"), sr.get("setup"),
                         sr.get("entry_price"), sr.get("stop_loss"), sr.get("targets"),
                         sr.get("confidence_score"), sr.get("scan_cmp"),
                         sr.get("created_at") or _now()),
                    )
                    conn.commit()
                    row = conn.execute(
                        "SELECT id, entry_price, published_at FROM research_track_record WHERE recommendation_id = ?",
                        (int(rec_id),),
                    ).fetchone()
            if row is None:
                return
            row = dict(row)
            holding = _holding_days(row.get("published_at"))
            cur = conn.execute(
                """
                UPDATE research_track_record
                SET outcome = ?, exit_price = ?, pnl_pct = ?, pnl_r = ?,
                    holding_days = ?, mfe_pct = ?, mae_pct = ?, resolved_at = ?
                WHERE recommendation_id = ? AND outcome = 'OPEN'
                """,
                (outcome, exit_price, pnl_pct, pnl_r, holding, mfe_pct, mae_pct, _now(), int(rec_id)),
            )
            conn.commit()
            if cur.rowcount:
                logger.info("[ledger] resolved rec=%s → %s pnl=%s%%", rec_id, outcome, pnl_pct)
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("track_record.resolve failed rec=%s: %s", rec_id, exc)


def _holding_days(published_at: str | None) -> int | None:
    if not published_at:
        return None
    try:
        pub = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - pub).days)
    except Exception:
        return None


def stats(horizon: str | None = None) -> dict:
    """Survivorship-free aggregates over the ledger. `resolved` INCLUDES expired
    ideas (counted as non-wins), so the win rate is honest — not just the subset
    that reached a target or stop."""
    try:
        conn = get_connection()
        try:
            _init(conn)
            where, params = "", []
            if horizon and horizon.upper() in ("SWING", "LONGTERM"):
                where = "WHERE horizon = ?"
                params.append(horizon.upper())
            rows = [dict(r) for r in conn.execute(
                f"SELECT outcome, pnl_pct, pnl_r, holding_days FROM research_track_record {where}", params
            ).fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("track_record.stats failed: %s", exc)
        return {"available": False}

    total = len(rows)
    resolved = [r for r in rows if r["outcome"] in _TERMINAL]
    open_n = total - len(resolved)
    wins = [r for r in resolved if r["outcome"] == "TARGET_HIT"]
    losses = [r for r in resolved if r["outcome"] == "STOP_HIT"]
    expired = [r for r in resolved if r["outcome"] == "EXPIRED"]

    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    win_pnls = [r["pnl_pct"] for r in wins]
    loss_pnls = [r["pnl_pct"] for r in losses]
    all_r = [r["pnl_r"] for r in resolved if r.get("pnl_r") is not None]
    # Win rate is over ALL resolved (incl. expired) — survivorship-free.
    win_rate = round(len(wins) / len(resolved) * 100, 1) if resolved else None
    expectancy_r = _avg(all_r)

    return {
        "available": True,
        "horizon": (horizon or "all").upper(),
        "total_published": total,
        "resolved": len(resolved),
        "open": open_n,
        "target_hit": len(wins),
        "stop_hit": len(losses),
        "expired": len(expired),
        "win_rate_pct": win_rate,               # wins / resolved (incl. expired)
        "avg_win_pct": _avg(win_pnls),
        "avg_loss_pct": _avg(loss_pnls),
        "avg_holding_days": _avg([r["holding_days"] for r in resolved]),
        "avg_rr_realized": expectancy_r,        # mean R across all resolved
        "expectancy_r": expectancy_r,
        "note": "Win rate is over ALL resolved recommendations including EXPIRED — survivorship-free.",
    }


def rows(horizon: str | None = None, limit: int = 200) -> list[dict]:
    try:
        conn = get_connection()
        try:
            _init(conn)
            where, params = "", []
            if horizon and horizon.upper() in ("SWING", "LONGTERM"):
                where = "WHERE horizon = ?"
                params.append(horizon.upper())
            params.append(int(limit))
            data = conn.execute(
                f"SELECT * FROM research_track_record {where} ORDER BY datetime(published_at) DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(r) for r in data]
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("track_record.rows failed: %s", exc)
        return []
