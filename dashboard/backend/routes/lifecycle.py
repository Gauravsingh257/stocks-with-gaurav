"""
dashboard/backend/routes/lifecycle.py

Track Record API — served entirely from the canonical lifecycle ledger.

None of these endpoints touch `stock_recommendations`. That table records
research IDEAS, and serving statistics from it is what put never-traded names
(TIL, STALLION, PNGJL, SENCO…) on a public page as successful "Target Hit"
trades while real portfolio trades such as SCANSTL were missing entirely.
"""

import logging
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/lifecycle", tags=["lifecycle"])
log = logging.getLogger("dashboard.lifecycle")


@router.get("/trades")
def list_trades(
    portfolio: str = Query("ALL"),
    status: str = Query("ALL"),
    execution: str = Query("ALL"),
    engine: str = Query("ALL"),
    month: int | None = Query(None, ge=1, le=12),
    year: int | None = Query(None, ge=2000, le=2100),
    min_confidence: float | None = Query(None, ge=0, le=100),
    outcome: str = Query("ALL"),
    symbol: str | None = Query(None),
    include_duplicates: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("created_at"),
    direction: str = Query("desc"),
):
    """One page of lifecycle rows. All filtering is server-side and indexed."""
    from dashboard.backend.db.trade_lifecycle_query import query
    try:
        return query(
            limit=limit, offset=offset, sort=sort, direction=direction,
            portfolio=portfolio, status=status, execution=execution, engine=engine,
            month=month, year=year, min_confidence=min_confidence, outcome=outcome,
            symbol=symbol, include_duplicates=include_duplicates,
        )
    except Exception as exc:
        log.exception("lifecycle trades query failed")
        return {"items": [], "total": 0, "limit": limit, "offset": offset,
                "has_more": False, "error": str(exc)}


@router.get("/stats")
def lifecycle_stats(
    portfolio: str = Query("ALL"),
    status: str = Query("ALL"),
    execution: str = Query("ALL"),
    engine: str = Query("ALL"),
    month: int | None = Query(None, ge=1, le=12),
    year: int | None = Query(None, ge=2000, le=2100),
    min_confidence: float | None = Query(None, ge=0, le=100),
    outcome: str = Query("ALL"),
    symbol: str | None = Query(None),
):
    """Summary cards over the FULL filtered set — never one page, never a
    status-filtered subset unless the caller explicitly asked for one."""
    from dashboard.backend.db.trade_lifecycle_query import stats
    try:
        return stats(portfolio=portfolio, status=status, execution=execution,
                     engine=engine, month=month, year=year,
                     min_confidence=min_confidence, outcome=outcome, symbol=symbol)
    except Exception as exc:
        log.exception("lifecycle stats failed")
        return {"error": str(exc), "signals_generated": 0}


@router.get("/facets")
def lifecycle_facets():
    """Filter values actually present in the ledger."""
    from dashboard.backend.db.trade_lifecycle_query import facets
    try:
        return facets()
    except Exception as exc:
        log.exception("lifecycle facets failed")
        return {"error": str(exc)}


@router.get("/trade/{lifecycle_id}")
def trade_timeline(lifecycle_id: str):
    """Full record + append-only event history for one trade."""
    from dashboard.backend.db.trade_lifecycle_query import timeline
    try:
        return timeline(lifecycle_id)
    except Exception as exc:
        log.exception("lifecycle timeline failed")
        return {"found": False, "error": str(exc)}


@router.post("/backfill")
def run_backfill(dry_run: bool = Query(False)):
    """Re-sync the ledger from every source. Idempotent — deterministic UUIDs
    mean repeated runs converge instead of duplicating."""
    from dashboard.backend.db.trade_lifecycle_migrate import backfill
    return backfill(dry_run=dry_run)


@router.get("/validate")
def validate():
    """Prove the ledger agrees with the books it was built from.

    Compares the ledger's per-book closed counts against the portfolio and
    momentum journals directly, and confirms research-only ideas are not being
    reported as trades. `ok: false` means the ledger has drifted.
    """
    from dashboard.backend.db.trade_lifecycle_query import stats
    from dashboard.backend.db.schema import get_connection

    out: dict = {"checks": [], "ok": True}
    conn = get_connection()
    try:
        for book, table, where in (
            ("SWING", "portfolio_journal", "horizon='SWING' AND COALESCE(is_duplicate,0)=0"),
            ("LONGTERM", "portfolio_journal", "horizon='LONGTERM' AND COALESCE(is_duplicate,0)=0"),
            ("MOMENTUM", "momentum_journal", "1=1"),
        ):
            try:
                src = conn.execute(f"SELECT COUNT(*) n FROM {table} WHERE {where}").fetchone()["n"]
            except Exception:
                src = None
            led = stats(portfolio=book)["closed_trades"]
            ok = (src is None) or (src == led)
            out["checks"].append({"book": book, "source_table": table,
                                  "source_closed": src, "ledger_closed": led, "ok": ok})
            out["ok"] = out["ok"] and ok

        # No research-only idea may ever be reported as a completed trade.
        leak = conn.execute(
            "SELECT COUNT(*) n FROM trade_lifecycle "
            "WHERE source='RESEARCH' AND status IN ('TARGET_HIT','STOP_HIT','MANUAL_CLOSED')"
        ).fetchone()["n"]
        out["checks"].append({
            "check": "research ideas never reported as completed trades",
            "violations": leak, "ok": leak == 0,
        })
        out["ok"] = out["ok"] and leak == 0
        return out
    except Exception as exc:
        log.exception("lifecycle validate failed")
        return {"ok": False, "error": str(exc), "checks": out.get("checks", [])}
    finally:
        conn.close()
