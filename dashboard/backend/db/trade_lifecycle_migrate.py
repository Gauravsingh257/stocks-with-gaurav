"""
dashboard/backend/db/trade_lifecycle_migrate.py
===============================================
Reconstruct the canonical lifecycle ledger from every pre-existing source.

Idempotent: lifecycle UUIDs are derived from (source, source_table, source_id),
so re-running converges on the same rows instead of duplicating them. Safe to
call on every startup.

Precedence — a portfolio row always beats the research idea that spawned it,
because the portfolio row is what actually happened:

    portfolio_journal      closed SWING / LONGTERM trades  (SCANSTL lives here)
    portfolio_positions    live + armed + expired SWING / LONGTERM
    momentum_journal       closed MOMENTUM trades
    momentum_positions     live + armed MOMENTUM
    stock_recommendations  research IDEAS — recorded as NEVER_EXECUTED unless a
                           real position for that symbol exists

That last rule is the correction the whole redesign exists for. Ideas such as
TIL / STALLION / PNGJL / SENCO were being published as successful "Target Hit"
trades on a public page despite never being taken into any book. An idea that
never filled carries no P&L here — "the level we published was reached" is not
the same claim as "we held this and made money".
"""

from __future__ import annotations

import json
import logging
import os

from .schema import get_connection
from .trade_lifecycle import init_lifecycle_db, upsert, _f

logger = logging.getLogger(__name__)


# Engine attribution. Bumping these makes "which engine version performs best?"
# answerable from the ledger alone, without re-deriving it from git history.
ENGINE_VERSIONS = {
    "SMC": os.getenv("SMC_ENGINE_VERSION", "SMC v4.2.1"),
    "MOMENTUM": os.getenv("MOMENTUM_ENGINE_VERSION", "Momentum v2.1"),
}


def _pct(price, entry):
    """Excursion as % from entry — the raw high/low is meaningless on its own."""
    p, e = _f(price), _f(entry)
    if p is None or not e:
        return None
    return round((p - e) / e * 100, 2)


def _exit_reason_to_status(reason: str | None) -> str:
    r = (reason or "").strip().upper()
    if r == "TARGET_HIT":
        return "TARGET_HIT"
    if r == "STOP_HIT":
        return "STOP_HIT"
    if r.startswith("EXPIRED"):
        return "EXPIRED"
    if r in ("CANCELLED", "CANCELED"):
        return "CANCELLED"
    if r in ("STALE_EXIT", "EXPIRED_TIMEOUT", "TIME_EXIT"):
        return "TIME_EXIT"          # closed by a time rule, not by price
    if r in ("STRUCTURE_BREAK", "TREND_BREAK", "FORCED_EXIT") or r.startswith("MANUAL:"):
        return "FORCED_EXIT"        # risk/structure override closed it
    return "MANUAL_CLOSED"


def _rr(entry, sl, pnl_pct):
    e, s = _f(entry), _f(sl)
    if e and s and e > s and pnl_pct is not None:
        risk_pct = (e - s) / e * 100
        if risk_pct:
            return round(pnl_pct / risk_pct, 3)
    return None


def backfill(dry_run: bool = False) -> dict:
    init_lifecycle_db()
    conn = get_connection()
    c = {"portfolio_journal": 0, "portfolio_positions": 0, "momentum_journal": 0,
         "momentum_positions": 0, "research": 0, "research_never_executed": 0}
    try:
        def _has(t):
            return bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())

        def _cols(t):
            return {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}

        traded: set[str] = set()

        # ── 1. Closed SWING / LONGTERM ───────────────────────────────────────
        if _has("portfolio_journal"):
            dupe = "is_duplicate" if "is_duplicate" in _cols("portfolio_journal") else "0 AS is_duplicate"
            for r in conn.execute(
                f"SELECT id, symbol, horizon, direction, entry_price, exit_price, stop_loss, "
                f"target_1, target_2, profit_loss, profit_loss_pct, days_held, confidence_score, "
                f"reasoning, exit_reason, created_at, closed_at, high_since_entry, "
                f"low_since_entry, {dupe} FROM portfolio_journal"
            ).fetchall():
                d = dict(r)
                sym = str(d["symbol"]).upper()
                traded.add(sym.replace("NSE:", ""))
                pnl = _f(d["profit_loss_pct"])
                if not dry_run:
                    upsert({
                        "source": d["horizon"], "portfolio": d["horizon"], "engine": "SMC",
                        "strategy": d.get("reasoning") or None, "symbol": sym,
                        "direction": d.get("direction") or "LONG",
                        "confidence": _f(d.get("confidence_score")),
                        "entry_price": _f(d["entry_price"]), "stop_loss": _f(d["stop_loss"]),
                        "target_1": _f(d.get("target_1")), "target_2": _f(d.get("target_2")),
                        "idea_at": d.get("created_at"), "entry_fill_at": d.get("created_at"),
                        "entry_trigger_at": d.get("created_at"), "exit_at": d.get("closed_at"),
                        "exit_price": _f(d.get("exit_price")), "exit_reason": d.get("exit_reason"),
                        "status": _exit_reason_to_status(d.get("exit_reason")),
                        "pnl_pct": pnl, "pnl_rs": _f(d.get("profit_loss")),
                        "rr_realized": _rr(d["entry_price"], d["stop_loss"], pnl),
                        "holding_days": d.get("days_held"),
                        "stage": "POSITION",
                        "engine_version": ENGINE_VERSIONS.get("SMC"),
                        "mfe_pct": _pct(d.get("high_since_entry"), d.get("entry_price")),
                        "mae_pct": _pct(d.get("low_since_entry"), d.get("entry_price")),
                        "high_since_entry": _f(d.get("high_since_entry")),
                        "low_since_entry": _f(d.get("low_since_entry")),
                        "context_json": json.dumps({
                            "reasoning": d.get("reasoning"), "horizon": d.get("horizon"),
                            "confidence": _f(d.get("confidence_score")),
                        }, default=str),
                        "source_table": "portfolio_journal", "source_id": str(d["id"]),
                        "is_duplicate": int(d.get("is_duplicate") or 0), "is_legacy": 1,
                        "created_at": d.get("created_at"),
                    }, conn=conn, event="BACKFILL")
                c["portfolio_journal"] += 1

        # ── 2. Live / armed / expired SWING / LONGTERM ───────────────────────
        if _has("portfolio_positions"):
            for r in conn.execute(
                "SELECT id, symbol, horizon, direction, entry_price, stop_loss, target_1, "
                "target_2, profit_loss, profit_loss_pct, days_held, confidence_score, status, "
                "exit_reason, entered_at, created_at FROM portfolio_positions "
                "WHERE status IN ('ACTIVE','PENDING','EXPIRED')"
            ).fetchall():
                d = dict(r)
                sym = str(d["symbol"]).upper()
                st = str(d["status"]).upper()
                status = {"ACTIVE": "ACTIVE", "PENDING": "AWAITING_ENTRY"}.get(st, "EXPIRED")
                if status == "ACTIVE":
                    traded.add(sym.replace("NSE:", ""))
                if not dry_run:
                    upsert({
                        "source": d["horizon"], "portfolio": d["horizon"], "engine": "SMC",
                        "symbol": sym, "direction": d.get("direction") or "LONG",
                        "confidence": _f(d.get("confidence_score")),
                        "entry_price": _f(d["entry_price"]), "stop_loss": _f(d["stop_loss"]),
                        "target_1": _f(d.get("target_1")), "target_2": _f(d.get("target_2")),
                        "idea_at": d.get("created_at"), "entry_trigger_at": d.get("entered_at"),
                        "entry_fill_at": d.get("entered_at"), "status": status,
                        "exit_reason": d.get("exit_reason") if status == "EXPIRED" else None,
                        "pnl_pct": _f(d.get("profit_loss_pct")) if status == "ACTIVE" else None,
                        "pnl_rs": _f(d.get("profit_loss")) if status == "ACTIVE" else None,
                        "holding_days": d.get("days_held") if status == "ACTIVE" else None,
                        "stage": "POSITION", "engine_version": ENGINE_VERSIONS.get("SMC"),
                        "source_table": "portfolio_positions", "source_id": str(d["id"]),
                        "is_legacy": 1, "created_at": d.get("created_at"),
                    }, conn=conn, event="BACKFILL")
                c["portfolio_positions"] += 1

        # ── 3. MOMENTUM ──────────────────────────────────────────────────────
        if _has("momentum_journal"):
            for r in conn.execute(
                "SELECT id, symbol, entry_price, exit_price, stop_loss, target_1, profit_loss, "
                "profit_loss_pct, r_multiple, days_held, quality_score, entry_model, regime, "
                "sector, exit_reason, created_at, closed_at FROM momentum_journal"
            ).fetchall():
                d = dict(r)
                sym = str(d["symbol"]).upper()
                traded.add(sym.replace("NSE:", ""))
                if not dry_run:
                    upsert({
                        "source": "MOMENTUM", "portfolio": "MOMENTUM", "engine": "MOMENTUM",
                        "setup": d.get("entry_model"), "strategy": d.get("regime"),
                        "symbol": sym, "direction": "LONG",
                        "confidence": _f(d.get("quality_score")),
                        "entry_price": _f(d["entry_price"]), "stop_loss": _f(d.get("stop_loss")),
                        "target_1": _f(d.get("target_1")), "idea_at": d.get("created_at"),
                        "entry_fill_at": d.get("created_at"), "entry_trigger_at": d.get("created_at"),
                        "exit_at": d.get("closed_at"), "exit_price": _f(d.get("exit_price")),
                        "exit_reason": d.get("exit_reason"),
                        "status": _exit_reason_to_status(d.get("exit_reason")),
                        "pnl_pct": _f(d.get("profit_loss_pct")), "pnl_rs": _f(d.get("profit_loss")),
                        "rr_realized": _f(d.get("r_multiple")), "holding_days": d.get("days_held"),
                        "stage": "POSITION",
                        "engine_version": ENGINE_VERSIONS.get("MOMENTUM"),
                        "context_json": json.dumps({
                            "regime": d.get("regime"), "sector": d.get("sector"),
                            "entry_model": d.get("entry_model"),
                            "quality_score": _f(d.get("quality_score")),
                        }, default=str),
                        "source_table": "momentum_journal", "source_id": str(d["id"]),
                        "is_legacy": 1, "created_at": d.get("created_at"),
                    }, conn=conn, event="BACKFILL")
                c["momentum_journal"] += 1

        if _has("momentum_positions"):
            for r in conn.execute(
                "SELECT id, symbol, entry_price, stop_loss, target_1, profit_loss, "
                "profit_loss_pct, days_held, quality_score, entry_model, regime, status, "
                "exit_reason, entered_at, created_at FROM momentum_positions "
                "WHERE status IN ('ACTIVE','PENDING','EXPIRED')"
            ).fetchall():
                d = dict(r)
                sym = str(d["symbol"]).upper()
                st = str(d["status"]).upper()
                status = {"ACTIVE": "ACTIVE", "PENDING": "AWAITING_ENTRY"}.get(st, "EXPIRED")
                if status == "ACTIVE":
                    traded.add(sym.replace("NSE:", ""))
                if not dry_run:
                    upsert({
                        "source": "MOMENTUM", "portfolio": "MOMENTUM", "engine": "MOMENTUM",
                        "setup": d.get("entry_model"), "strategy": d.get("regime"),
                        "symbol": sym, "direction": "LONG",
                        "confidence": _f(d.get("quality_score")),
                        "entry_price": _f(d["entry_price"]), "stop_loss": _f(d.get("stop_loss")),
                        "target_1": _f(d.get("target_1")), "idea_at": d.get("created_at"),
                        "entry_trigger_at": d.get("entered_at"), "entry_fill_at": d.get("entered_at"),
                        "status": status,
                        "pnl_pct": _f(d.get("profit_loss_pct")) if status == "ACTIVE" else None,
                        "holding_days": d.get("days_held") if status == "ACTIVE" else None,
                        "stage": "POSITION", "engine_version": ENGINE_VERSIONS.get("MOMENTUM"),
                        "source_table": "momentum_positions", "source_id": str(d["id"]),
                        "is_legacy": 1, "created_at": d.get("created_at"),
                    }, conn=conn, event="BACKFILL")
                c["momentum_positions"] += 1

        # ── 4. Research ideas ────────────────────────────────────────────────
        if _has("stock_recommendations"):
            for r in conn.execute(
                "SELECT sr.id, sr.symbol, sr.agent_type, sr.setup, sr.status, sr.entry_price, "
                "sr.stop_loss, sr.targets, sr.confidence_score, sr.exit_reason, sr.created_at, "
                "rt.status AS trade_status, rt.entry_triggered_at "
                "FROM stock_recommendations sr LEFT JOIN running_trades rt "
                "ON rt.recommendation_id = sr.id AND rt.id = "
                "(SELECT MAX(rt2.id) FROM running_trades rt2 WHERE rt2.recommendation_id = sr.id)"
            ).fetchall():
                d = dict(r)
                sym = str(d["symbol"]).upper()
                raw = str(d.get("trade_status") or d.get("status") or "").upper()
                executed_downstream = sym.replace("NSE:", "") in traded
                if executed_downstream:
                    # The idea DID become a trade. It stays as the first stage of
                    # that chain — not discarded — so "200 ideas -> 55 entries"
                    # remains answerable. The outcome lives on the position row.
                    status = "ENTRY_TRIGGERED"
                elif raw in ("RUNNING", "ACTIVE"):
                    status = "AWAITING_ENTRY"
                elif raw in ("CANCELLED", "CANCELED"):
                    status = "CANCELLED"
                elif raw == "EXPIRED":
                    status = "EXPIRED"
                else:
                    status = "NEVER_EXECUTED"
                if status == "NEVER_EXECUTED":
                    c["research_never_executed"] += 1
                try:
                    t = json.loads(d.get("targets") or "[]")
                    tgt = _f(t[0]) if t else None
                except Exception:
                    tgt = None
                if not dry_run:
                    upsert({
                        "source": "RESEARCH", "portfolio": None, "engine": "SMC",
                        "setup": d.get("setup"), "symbol": sym, "direction": "LONG",
                        "confidence": _f(d.get("confidence_score")),
                        "entry_price": _f(d.get("entry_price")), "stop_loss": _f(d.get("stop_loss")),
                        "target_1": tgt, "idea_at": d.get("created_at"),
                        "entry_trigger_at": d.get("entry_triggered_at"), "status": status,
                        "stage": "IDEA",
                        "engine_version": ENGINE_VERSIONS.get("SMC"),
                        "recommendation_json": json.dumps({
                            "symbol": sym, "agent_type": d.get("agent_type"),
                            "setup": d.get("setup"), "entry": _f(d.get("entry_price")),
                            "stop_loss": _f(d.get("stop_loss")), "targets": d.get("targets"),
                            "confidence": _f(d.get("confidence_score")),
                            "published_at": d.get("created_at"),
                        }, default=str),
                        # An unexecuted idea has NO P&L — recording one would put
                        # a hypothetical number beside real trades.
                        "pnl_pct": None, "holding_days": None,
                        "exit_reason": d.get("exit_reason"),
                        "source_table": "stock_recommendations", "source_id": str(d["id"]),
                        "is_legacy": 1, "created_at": d.get("created_at"),
                    }, conn=conn, event="BACKFILL")
                c["research"] += 1

        if not dry_run:
            conn.commit()
            # Stamp the config fingerprint so "which rules produced this trade?"
            # is answerable even when a version label was never bumped.
            try:
                from .lifecycle_capture import backfill_algorithm_hash
                c["algorithm_hash_stamped"] = backfill_algorithm_hash().get("stamped", 0)
            except Exception:
                pass
        c["ledger_rows"] = conn.execute("SELECT COUNT(*) n FROM trade_lifecycle").fetchone()["n"]
        logger.info("[Lifecycle] backfill: %s", c)
        return {"ok": True, "dry_run": dry_run, **c}
    except Exception as exc:
        logger.error("[Lifecycle] backfill failed: %s", exc, exc_info=True)
        return {"ok": False, "reason": str(exc), **c}
    finally:
        conn.close()
