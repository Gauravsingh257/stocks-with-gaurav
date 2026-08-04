"""
dashboard/backend/routes/lifecycle.py

Track Record API — served entirely from the canonical lifecycle ledger.

None of these endpoints touch `stock_recommendations`. That table records
research IDEAS, and serving statistics from it is what put never-traded names
(TIL, STALLION, PNGJL, SENCO…) on a public page as successful "Target Hit"
trades while real portfolio trades such as SCANSTL were missing entirely.
"""

import logging
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
    stage: str = Query("ALL"),
    engine_version: str | None = Query(None),
    record_state: str = Query("ACTIVE"),
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
            stage=stage, engine_version=engine_version, record_state=record_state,
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
    stage: str = Query("ALL"),
    engine_version: str | None = Query(None),
):
    """Summary cards over the FULL filtered set — never one page, never a
    status-filtered subset unless the caller explicitly asked for one."""
    from dashboard.backend.db.trade_lifecycle_query import stats
    try:
        return stats(portfolio=portfolio, status=status, execution=execution,
                     engine=engine, month=month, year=year,
                     min_confidence=min_confidence, outcome=outcome, symbol=symbol,
                     stage=stage, engine_version=engine_version)
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


@router.get("/orphans")
def orphan_positions():
    """Positions in a TERMINAL status that have no journal row.

    close_position() writes both, so a terminal position without a journal entry
    means something closed it outside that path. Such rows are invisible
    everywhere: the summary endpoint returns ACTIVE/PENDING only, and the ledger
    backfill reads ACTIVE/PENDING/EXPIRED plus the journal — so the position is
    real, was alerted on, and appears nowhere.
    """
    from dashboard.backend.db.schema import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT p.id, p.symbol, p.horizon, p.status, p.entry_price, p.exit_price, "
            "p.exit_reason, p.profit_loss_pct, p.created_at, p.entered_at, p.closed_at "
            "FROM portfolio_positions p "
            "LEFT JOIN portfolio_journal j ON j.position_id = p.id "
            "WHERE p.status NOT IN ('ACTIVE','PENDING','EXPIRED') AND j.id IS NULL "
            "ORDER BY datetime(COALESCE(p.closed_at, p.created_at)) DESC"
        ).fetchall()
        by_status: dict = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        return {"orphan_count": len(rows), "by_status": by_status,
                "items": [dict(r) for r in rows[:100]]}
    finally:
        conn.close()


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

@router.get("/stream")
async def lifecycle_stream(request: Request):
    """Server-Sent Events — the push channel that replaces polling.

    Every lifecycle transition is announced here the moment it is written, so
    Track Record and any other screen stay in step with the books without
    re-asking on a timer.
    """
    from dashboard.backend.lifecycle_bus import event_stream
    return StreamingResponse(
        event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # stop nginx buffering the stream
        },
    )


@router.get("/analytics")
def lifecycle_analytics(portfolio: str = Query("ALL"), since: str | None = Query(None)):
    """MAE/MFE, expectancy, profit factor, Sharpe, Sortino, recovery factor,
    time-to-target and time-to-stop — over closed, executed positions only."""
    from dashboard.backend.db.lifecycle_analytics import analytics
    try:
        return analytics(portfolio=portfolio, since=since)
    except Exception as exc:
        log.exception("lifecycle analytics failed")
        return {"error": str(exc), "closed_trades": 0}


@router.get("/history")
def lifecycle_history(period: str = Query("DAILY", pattern="^(DAILY|WEEKLY|MONTHLY)$"),
                      portfolio: str = Query("ALL"),
                      limit: int = Query(180, ge=1, le=1000)):
    """Stored period snapshots for trend charts — no recomputation of history."""
    from dashboard.backend.db.lifecycle_analytics import stats_history
    try:
        return stats_history(period=period, portfolio=portfolio, limit=limit)
    except Exception as exc:
        log.exception("lifecycle history failed")
        return {"error": str(exc), "points": []}


@router.post("/snapshot")
def lifecycle_snapshot():
    """Persist current stats into the daily/weekly/monthly rollups."""
    from dashboard.backend.db.lifecycle_analytics import snapshot_stats
    return snapshot_stats()


@router.post("/trade/{lifecycle_id}/state")
def set_record_state(lifecycle_id: str, state: str = Query(..., pattern="^(ACTIVE|ARCHIVED|HIDDEN|DUPLICATE)$")):
    """Soft delete. A trade is never removed — only reclassified, with the
    change appended to its event history."""
    from dashboard.backend.db.schema import get_connection
    from dashboard.backend.db.trade_lifecycle import record_event
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE trade_lifecycle SET record_state = ?, updated_at = datetime('now') WHERE uuid = ?",
            (state.upper(), lifecycle_id),
        )
        conn.commit()
        if cur.rowcount:
            record_event(lifecycle_id, "RECORD_STATE_CHANGED", note=state.upper())
        return {"ok": bool(cur.rowcount), "uuid": lifecycle_id, "record_state": state.upper()}
    finally:
        conn.close()


# ── Visual analytics dashboards ──────────────────────────────────────────────

@router.get("/dashboard/monthly")
def dashboard_monthly(portfolio: str = Query("ALL"), months: int = Query(24, ge=1, le=120)):
    """Return, win rate and cumulative curve per calendar month."""
    from dashboard.backend.db.lifecycle_dashboards import monthly_performance
    try:
        return monthly_performance(portfolio=portfolio, months=months)
    except Exception as exc:
        log.exception("monthly dashboard failed")
        return {"points": [], "error": str(exc)}


@router.get("/dashboard/engines")
def dashboard_engines(by_version: bool = Query(False)):
    """Compare books, or engine VERSIONS, on the same closed population."""
    from dashboard.backend.db.lifecycle_dashboards import engine_comparison
    try:
        return engine_comparison(by_version=by_version)
    except Exception as exc:
        log.exception("engine comparison failed")
        return {"rows": [], "error": str(exc)}


@router.get("/dashboard/funnel")
def dashboard_funnel(portfolio: str = Query("ALL")):
    """Idea -> entry -> closed -> target, with the drop at each stage."""
    from dashboard.backend.db.lifecycle_dashboards import conversion_funnel
    try:
        return conversion_funnel(portfolio=portfolio)
    except Exception as exc:
        log.exception("funnel failed")
        return {"stages": [], "error": str(exc)}


@router.get("/dashboard/exits")
def dashboard_exits(portfolio: str = Query("ALL")):
    """Exit-reason attribution — where the money is made and lost."""
    from dashboard.backend.db.lifecycle_dashboards import exit_attribution
    try:
        return exit_attribution(portfolio=portfolio)
    except Exception as exc:
        log.exception("exit attribution failed")
        return {"rows": [], "error": str(exc)}


@router.get("/detail/{lifecycle_id}")
def full_trade_detail(lifecycle_id: str):
    """Everything the trade-detail page needs in one call: ledger row, event
    history, chain, alerts, price path and derived post-trade analysis."""
    from dashboard.backend.db.lifecycle_dashboards import trade_detail
    try:
        return trade_detail(lifecycle_id)
    except Exception as exc:
        log.exception("trade detail failed")
        return {"found": False, "error": str(exc)}


# ── Cross-engine chain attribution ───────────────────────────────────────────

@router.post("/chain/link")
def chain_link(dry_run: bool = Query(False)):
    """Attach POSITION rows to the IDEA that plausibly produced them."""
    from dashboard.backend.db.lifecycle_chain import link_chains
    return link_chains(dry_run=dry_run)


@router.get("/chain/{chain_id}")
def chain_view(chain_id: str):
    """Every stage of one idea's life, across whichever engines picked it up."""
    from dashboard.backend.db.lifecycle_chain import chain
    try:
        return chain(chain_id)
    except Exception as exc:
        return {"chain_id": chain_id, "stages": [], "error": str(exc)}


@router.get("/chain-attribution")
def chain_attribution():
    """How each engine converts published ideas into outcomes."""
    from dashboard.backend.db.lifecycle_chain import cross_engine_attribution
    try:
        return cross_engine_attribution()
    except Exception as exc:
        log.exception("chain attribution failed")
        return {"per_engine": [], "error": str(exc)}


# ── Capture: charts, context, algorithm hash, manual/paper writers ───────────

@router.post("/capture/charts")
def capture_charts(limit: int = Query(25, ge=1, le=200)):
    """Attach entry/exit OHLC windows to executed rows that lack them.

    Rate-limited because each row costs a broker call.
    """
    from dashboard.backend.db.lifecycle_capture import capture_missing_charts
    return capture_missing_charts(limit=limit)


@router.post("/capture/algorithm-hash")
def capture_algorithm_hash():
    """Stamp rows that predate the algorithm_hash producer."""
    from dashboard.backend.db.lifecycle_capture import backfill_algorithm_hash
    return backfill_algorithm_hash()


@router.get("/algorithm-hash")
def current_algorithm_hash(engine: str = Query("SMC"), version: str | None = Query(None)):
    """Fingerprint of the parameters currently deciding behaviour."""
    from dashboard.backend.db.lifecycle_capture import algorithm_hash, _HASH_ENV_KEYS
    return {"engine": engine, "version": version,
            "algorithm_hash": algorithm_hash(engine, version),
            "inputs": list(_HASH_ENV_KEYS)}


class ManualTradeRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    target_3: float | None = None
    exit_price: float | None = None
    direction: str = "LONG"
    status: str | None = None
    setup: str | None = None
    strategy: str | None = None
    confidence: float | None = None
    entry_at: str | None = None
    exit_at: str | None = None
    exit_reason: str | None = None
    entry_reason: str | None = None
    exit_note: str | None = None
    holding_days: int | None = None
    external_id: str | None = None


@router.post("/manual")
def add_manual_trade(req: ManualTradeRequest, source: str = Query("MANUAL", pattern="^(MANUAL|PAPER)$")):
    """Record a manual or paper trade.

    MANUAL and PAPER were wired into the schema and filters but had no producer,
    so those filter options always returned nothing. This is that producer.
    """
    from dashboard.backend.db.lifecycle_capture import record_manual_trade
    try:
        uid = record_manual_trade(req.model_dump(), source=source)
        return {"ok": True, "uuid": uid, "source": source.upper()}
    except Exception as exc:
        log.exception("manual trade write failed")
        return {"ok": False, "error": str(exc)}
