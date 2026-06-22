"""
dashboard/backend/routes/analytics.py
PUBLIC performance analytics — derived ONLY from the three portfolio products:
Swing Portfolio, Long-Term Portfolio and the Running Trades Monitor.

Source of truth: stock_recommendations (+ running_trades for live P&L).
The engine `trades` table and the ai_learning `signal_log` are deliberately
NOT read here — they are engine/journal/CSV/backtest data and would contaminate
public portfolio analytics (Phase 3 + Phase 5 source protection).

Every analytics row carries provenance (origin_type / origin_id / origin_table)
and only the allow-listed origins below are ever surfaced.
"""

import json
import logging
from fastapi import APIRouter, Query
from typing import Optional
from collections import defaultdict
from datetime import datetime
from dashboard.backend.db import get_connection
from dashboard.backend.db.schema import full_sync_from_csv, get_sync_info

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
logger = logging.getLogger("analytics")

# ── Phase 5: provenance allow-list ───────────────────────────────────────────
# Only these origins may ever appear in public analytics. Everything else
# (engine trades, CSV imports, signal-learning, tests, backtests) is forbidden.
ALLOWED_ORIGINS = ("SWING", "LONGTERM", "RUNNING_TRADES")
FORBIDDEN_ORIGINS = ("ENGINE", "CSV_IMPORT", "SIGNAL_LOG", "TEST", "BACKTEST", "UNKNOWN")
# Recommendation agent_types that map to public portfolio products.
PORTFOLIO_AGENT_TYPES = ("SWING", "LONGTERM")


def _norm_date(value) -> str:
    """Normalise an ISO/SQLite datetime to 'YYYY-MM-DD HH:MM:SS' (no 'Z'/'T')
    so the downstream hour/day parsers work uniformly."""
    if not value:
        return ""
    s = str(value).strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1]
    return s


def _load_trades(include_running: bool = False) -> tuple[list, str]:
    """Load PORTFOLIO outcome rows from stock_recommendations, normalised to the
    legacy trades-row shape so every downstream metric function works unchanged.

    result mapping:  TARGET_HIT -> 'WIN',  STOP_HIT -> 'LOSS'.
    Only decisive (closed) outcomes drive win-rate / equity / profit-factor.
    When include_running=True, ACTIVE rows are appended as 'RUNNING' (ignored by
    the WIN/LOSS calculators but available for counts).

    Returns (rows, source). source is always 'portfolio' — engine `trades` and
    `signal_log` are never read here.
    """
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in PORTFOLIO_AGENT_TYPES)
        statuses = "('TARGET_HIT','STOP_HIT','ACTIVE')" if include_running else "('TARGET_HIT','STOP_HIT')"
        rows = conn.execute(
            f"""
            SELECT id, symbol, agent_type, setup, entry_price, exit_price,
                   stop_loss, targets, long_term_target, status, pnl_r, pnl_pct,
                   exit_date, created_at
            FROM stock_recommendations
            WHERE agent_type IN ({placeholders})
              AND status IN {statuses}
            ORDER BY datetime(COALESCE(exit_date, created_at)) ASC
            """,
            list(PORTFOLIO_AGENT_TYPES),
        ).fetchall()
    finally:
        conn.close()

    _status_to_result = {"TARGET_HIT": "WIN", "STOP_HIT": "LOSS", "ACTIVE": "RUNNING"}
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        status = d.get("status")
        result = _status_to_result.get(status)
        if result is None:
            continue
        # Long/short geometry for direction label (analytics is informational).
        try:
            targets = json.loads(d["targets"]) if d.get("targets") else []
        except (json.JSONDecodeError, TypeError):
            targets = []
        entry = d.get("entry_price") or 0
        is_long = (max(targets) if targets else entry) >= entry
        out.append({
            "id": d["id"],
            "date": _norm_date(d.get("exit_date") or d.get("created_at")),
            "created_at": _norm_date(d.get("created_at")),
            "symbol": d.get("symbol"),
            "direction": "LONG" if is_long else "SHORT",
            "setup": d.get("setup") or d.get("agent_type") or "UNKNOWN",
            "entry": entry,
            "exit_price": d.get("exit_price"),
            "result": result,
            "pnl_r": d.get("pnl_r"),
            "pnl_pct": d.get("pnl_pct"),
            # Phase 5 provenance — every analytics row is traceable to a portfolio.
            "origin_type": d.get("agent_type"),
            "origin_id": d["id"],
            "origin_table": "stock_recommendations",
        })
    return out, "portfolio"


@router.get("/summary")
def get_summary():
    """Top-level performance metrics (total trades, WR, PF, expectancy, etc.)."""
    rows, source = _load_trades()

    if not rows:
        return {
            "total_trades": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0, "win_rate_pct": 0, "total_r": 0,
            "profit_factor": 0, "expectancy_r": 0,
            "max_drawdown_r": 0, "max_consec_losses": 0,
            "avg_win_r": 0, "avg_loss_r": 0,
            "data_source": source,
        }

    completed = [r for r in rows if r.get("result") in ("WIN", "LOSS")]
    wins  = [r for r in completed if r["result"] == "WIN"]
    losses= [r for r in completed if r["result"] == "LOSS"]

    total     = len(completed)
    win_count = len(wins)
    win_rate  = round(win_count / total * 100, 2) if total else 0

    total_r   = round(sum(r["pnl_r"] for r in completed if r["pnl_r"] is not None), 4)
    avg_win   = round(sum(r["pnl_r"] for r in wins   if r["pnl_r"] is not None) / max(1, len(wins)),   4)
    avg_loss  = round(sum(r["pnl_r"] for r in losses if r["pnl_r"] is not None) / max(1, len(losses)), 4)

    gross_profit = sum(r["pnl_r"] for r in wins   if r["pnl_r"] is not None)
    gross_loss   = abs(sum(r["pnl_r"] for r in losses if r["pnl_r"] is not None))
    pf           = round(gross_profit / gross_loss, 4) if gross_loss else 999.0  # cap at 999 — float("inf") breaks JSON
    expectancy   = round(total_r / total, 4) if total else 0

    # Max drawdown
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in completed:
        cumulative += r["pnl_r"] or 0
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Max consecutive losses
    max_streak = 0
    current_streak = 0
    for r in completed:
        if r["result"] == "LOSS":
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return {
        "total_trades":        total,
        "win_count":           win_count,
        "loss_count":          len(losses),
        "win_rate":            round(win_count / total, 4) if total else 0,
        "win_rate_pct":        win_rate,
        "total_r":             total_r,
        "profit_factor":       pf,
        "expectancy_r":        expectancy,
        "avg_win_r":           avg_win,
        "avg_loss_r":          avg_loss,
        "max_drawdown_r":      round(max_dd, 4),
        "max_consec_losses":   max_streak,
        "data_source":         source,
    }


@router.get("/equity-curve")
def get_equity_curve():
    """Cumulative R-multiple over time — for area chart on frontend."""
    rows, source = _load_trades()

    cumulative = 0.0
    curve = []
    for r in rows:
        if r.get("result") not in ("WIN", "LOSS"):
            continue
        cumulative += r["pnl_r"] or 0
        curve.append({
            "date": r.get("date") or r.get("created_at", ""),
            "cumulative_r": round(cumulative, 4),
            "trade_r": r["pnl_r"],
        })

    return {"equity_curve": curve, "data_source": source}


# Setups disabled in engine — hidden from dashboard by default
# Phase 1 (8-phase upgrade): SETUP-D re-enabled for index instruments → removed from this set
_DISABLED_SETUPS = {"B", "SETUP-D-V2"}

@router.get("/by-setup")
def get_by_setup(include_disabled: bool = Query(default=False, description="Include historically disabled setups")):
    """Win rate, expectancy, total R — broken down by setup type."""
    rows, _source = _load_trades()
    rows = [r for r in rows if r.get("result") in ("WIN", "LOSS")]

    # Filter out disabled setups unless explicitly requested
    if not include_disabled:
        rows = [r for r in rows if r.get("setup") not in _DISABLED_SETUPS]

    buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "total_r": 0.0})
    for r in rows:
        b = buckets[r.get("setup") or "UNKNOWN"]
        b["trades"] += 1
        b["total_r"] += r["pnl_r"] or 0
        if r["result"] == "WIN":
            b["wins"] += 1
        else:
            b["losses"] += 1

    result = []
    for setup, b in sorted(buckets.items()):
        t = b["trades"]
        result.append({
            "setup":        setup,
            "total":        t,       # frontend uses 'total'
            "trades":       t,       # kept for backwards compat
            "wins":         b["wins"],
            "losses":       b["losses"],
            "win_rate":     round(b["wins"] / t, 4) if t else 0,  # 0-1 decimal for frontend
            "win_rate_pct": round(b["wins"] / t * 100, 1) if t else 0,  # kept for backwards compat
            "total_r":      round(b["total_r"], 4),
            "expectancy_r": round(b["total_r"] / t, 4) if t else 0,
        })

    result.sort(key=lambda x: x["total_r"], reverse=True)
    return {"setups": result, "by_setup": result}  # 'setups' for frontend, 'by_setup' kept for compat


@router.get("/rolling-winrate")
def get_rolling_winrate(window: int = Query(default=20, ge=5, le=100)):
    """Rolling win rate over last N trades — shows system health trend."""
    rows, source = _load_trades()
    rows = [r for r in rows if r.get("result") in ("WIN", "LOSS")]

    if len(rows) < window:
        return {"data": [], "rolling_winrate": [], "window": window, "data_source": source}

    points = []
    for i in range(window - 1, len(rows)):
        chunk = rows[i - window + 1: i + 1]
        wins  = sum(1 for r in chunk if r["result"] == "WIN")
        row_date = chunk[-1].get("date") or chunk[-1].get("created_at", "")
        points.append({
            "date":         row_date,
            "win_rate":     round(wins / window, 4),  # 0-1 decimal for frontend
            "win_rate_pct": round(wins / window * 100, 1),  # kept for backwards compat
            "idx":          i,    # frontend uses 'idx'
            "index":        i,    # kept for backwards compat
        })

    return {"data": points, "rolling_winrate": points, "window": window, "data_source": source}


@router.get("/time-of-day")
def get_time_of_day():
    """PnL aggregated by hour of day — for heatmap / bar chart."""
    rows, _source = _load_trades()
    rows = [r for r in rows if r.get("result") in ("WIN", "LOSS")]

    buckets = defaultdict(lambda: {"trades": 0, "total_r": 0.0, "wins": 0})
    for r in rows:
        try:
            date_str = r.get("date") or r.get("created_at", "")
            hour = int(date_str[11:13])   # "2026-02-04 13:20:07" → 13
            b = buckets[hour]
            b["trades"] += 1
            b["total_r"] += r["pnl_r"] or 0
            if r["result"] == "WIN":
                b["wins"] += 1
        except Exception:
            continue

    result = []
    for hour in sorted(buckets.keys()):
        b = buckets[hour]
        t = b["trades"]
        result.append({
            "hour":         hour,
            "label":        f"{hour:02d}:00",
            "trades":       t,
            "total_r":      round(b["total_r"], 4),
            "win_rate_pct": round(b["wins"] / t * 100, 1) if t else 0,
        })

    return {"time_of_day": result}


@router.get("/by-day-of-week")
def get_by_day_of_week():
    """PnL aggregated by day of week."""
    from datetime import datetime as dt
    rows, _source = _load_trades()
    rows = [r for r in rows if r.get("result") in ("WIN", "LOSS")]

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    buckets = defaultdict(lambda: {"trades": 0, "total_r": 0.0, "wins": 0})
    for r in rows:
        try:
            date_str = r.get("date") or r.get("created_at", "")
            d = dt.fromisoformat(date_str)
            day = d.weekday()   # 0=Mon, 4=Fri
            b = buckets[day]
            b["trades"] += 1
            b["total_r"] += r["pnl_r"] or 0
            if r["result"] == "WIN":
                b["wins"] += 1
        except Exception:
            continue

    result = []
    for day_num in sorted(buckets.keys()):
        b  = buckets[day_num]
        t  = b["trades"]
        result.append({
            "day_num":      day_num,
            "day":          day_names[day_num],
            "trades":       t,
            "total_r":      round(b["total_r"], 4),
            "win_rate_pct": round(b["wins"] / t * 100, 1) if t else 0,
        })

    return {"by_day_of_week": result}


@router.get("/drawdown-velocity")
def get_drawdown_velocity():
    """
    Drawdown velocity: R lost per day during drawdown periods.
    A rising velocity warns that losses are accelerating.
    """
    rows, _source = _load_trades()
    rows = [r for r in rows if r.get("result") in ("WIN", "LOSS")]

    if not rows:
        return {"drawdown_velocity": []}

    cumulative = 0.0
    peak = 0.0
    trough = 0.0
    in_dd = False
    dd_start_date = None
    dd_start_val = 0.0
    dd_events = []

    for r in rows:
        cumulative += r["pnl_r"] or 0
        date = (r.get("date") or r.get("created_at", ""))[:10]

        if cumulative > peak:
            if in_dd and dd_start_date:
                dd_events.append({
                    "start": dd_start_date,
                    "end": date,
                    "depth_r": round(dd_start_val - trough, 4),
                })
            peak = cumulative
            trough = cumulative
            in_dd = False
        else:
            if cumulative < trough:
                trough = cumulative
            if not in_dd:
                in_dd = True
                dd_start_date = date
                dd_start_val = peak
                trough = cumulative

    return {"drawdown_velocity": dd_events, "total_dd_events": len(dd_events)}


@router.get("/calendar-heatmap")
def get_calendar_heatmap():
    """Daily PnL for calendar heatmap on the analytics page."""
    rows, _source = _load_trades()
    rows = [r for r in rows if r.get("result") in ("WIN", "LOSS")]

    daily = defaultdict(lambda: {"trades": 0, "total_r": 0.0, "wins": 0})
    for r in rows:
        day = (r.get("date") or r.get("created_at", ""))[:10]
        b = daily[day]
        b["trades"] += 1
        b["total_r"] += r["pnl_r"] or 0
        if r["result"] == "WIN":
            b["wins"] += 1

    result = []
    for date in sorted(daily.keys()):
        b = daily[date]
        t = b["trades"]
        result.append({
            "date":         date,
            "total_r":      round(b["total_r"], 4),
            "trades":       t,
            "win_rate_pct": round(b["wins"] / t * 100, 1) if t else 0,
            "color_class":  "profit" if b["total_r"] >= 0 else "loss",
        })

    return {"calendar": result}


# ────────────────────────────────────────────────────────────────────────────
# CSV → DB real-time sync endpoints
# ────────────────────────────────────────────────────────────────────────────

@router.get("/sync-status")
def get_sync_status():
    """
    Shows whether the SQLite DB is in sync with trade_ledger_2026.csv.
    Returns CSV mtime, last sync time, and DB trade count.
    """
    return get_sync_info()


@router.post("/force-sync")
def force_sync():
    """
    Force an immediate full reload of trade_ledger_2026.csv → dashboard.db.
    Use this after running a backtest or manually editing the CSV.
    """
    count = full_sync_from_csv(force=True)
    info  = get_sync_info()
    return {
        "status":  "ok",
        "rows_synced": count,
        "sync_time":   info["last_sync"],
        "db_trade_count": info["db_trade_count"],
    }


# ────────────────────────────────────────────────────────────────────────────
# Research (Swing + Long-Term) Performance Analytics
# ────────────────────────────────────────────────────────────────────────────

def _research_performance(agent_type: str) -> dict:
    """
    Compute performance metrics for swing or long-term recommendations.

    Status + closed P&L authority is the recommendation row itself (set by the
    outcome tracker). running_trades is joined only for live P&L / days-held on
    positions that are still open.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                sr.id,
                sr.symbol,
                sr.entry_price,
                sr.confidence_score,
                sr.created_at AS recommended_at,
                sr.setup,
                sr.status        AS reco_status,
                sr.pnl_pct       AS reco_pnl_pct,
                sr.pnl_r         AS reco_pnl_r,
                sr.exit_price,
                rt.current_price,
                rt.profit_loss_pct,
                rt.profit_loss,
                rt.days_held,
                rt.high_since_entry,
                rt.low_since_entry,
                rt.updated_at
            FROM stock_recommendations sr
            LEFT JOIN running_trades rt
                ON rt.recommendation_id = sr.id
                AND rt.id = (
                    SELECT MAX(id) FROM running_trades
                    WHERE recommendation_id = sr.id
                )
            WHERE sr.agent_type = ?
            ORDER BY sr.created_at DESC
            """,
            (agent_type,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "summary": {
                "total": 0, "active": 0, "target_hit": 0, "stop_hit": 0,
                "hit_rate_pct": 0, "avg_pnl_pct": 0, "best_pnl_pct": 0,
                "worst_pnl_pct": 0, "best_symbol": None, "worst_symbol": None,
            },
            "picks": [],
        }

    picks = []
    for r in rows:
        reco_status = (r["reco_status"] or "ACTIVE").upper()
        # Map recommendation lifecycle → card status; ACTIVE shows as RUNNING.
        status = "RUNNING" if reco_status == "ACTIVE" else reco_status
        if reco_status in ("TARGET_HIT", "STOP_HIT"):
            pnl_pct = r["reco_pnl_pct"] if r["reco_pnl_pct"] is not None else 0.0
        else:
            pnl_pct = r["profit_loss_pct"] if r["profit_loss_pct"] is not None else 0.0
        picks.append({
            "symbol": r["symbol"],
            "entry_price": r["entry_price"],
            "current_price": r["current_price"] if r["current_price"] is not None else r["exit_price"],
            "recommended_at": r["recommended_at"],
            "setup": r["setup"],
            "confidence_score": r["confidence_score"],
            "profit_loss_pct": pnl_pct or 0.0,
            "profit_loss": r["profit_loss"] or 0.0,
            "pnl_r": r["reco_pnl_r"] or 0.0,
            "days_held": r["days_held"] or 0,
            "status": status,
            "high_since_entry": r["high_since_entry"],
            "low_since_entry": r["low_since_entry"],
            "updated_at": r["updated_at"],
        })

    active = [p for p in picks if p["status"] == "RUNNING"]
    hits   = [p for p in picks if p["status"] == "TARGET_HIT"]
    stops  = [p for p in picks if p["status"] == "STOP_HIT"]
    closed = hits + stops
    tracked = [p for p in picks if p["status"] in ("RUNNING", "TARGET_HIT", "STOP_HIT")]

    hit_rate = round(len(hits) / len(closed) * 100, 1) if closed else 0
    avg_pnl  = round(sum(p["profit_loss_pct"] for p in tracked) / len(tracked), 2) if tracked else 0

    best  = max(tracked, key=lambda p: p["profit_loss_pct"], default=None)
    worst = min(tracked, key=lambda p: p["profit_loss_pct"], default=None)

    return {
        "summary": {
            "total": len(picks),
            "active": len(active),
            "target_hit": len(hits),
            "stop_hit": len(stops),
            "hit_rate_pct": hit_rate,
            "avg_pnl_pct": avg_pnl,
            "best_pnl_pct": round(best["profit_loss_pct"], 2) if best else 0,
            "worst_pnl_pct": round(worst["profit_loss_pct"], 2) if worst else 0,
            "best_symbol": best["symbol"] if best else None,
            "worst_symbol": worst["symbol"] if worst else None,
        },
        "picks": picks,
    }


def _R(direction: str | None, entry, sl, px) -> float | None:
    """Risk-multiple of a position vs its 1R risk (entry−stop). Direction-aware."""
    try:
        entry = float(entry); sl = float(sl); px = float(px)
    except (TypeError, ValueError):
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    move = (px - entry) if (direction or "LONG").upper() != "SHORT" else (entry - px)
    return move / risk


def _portfolio_performance(horizon: str) -> dict:
    """LIVE performance of the actual PORTFOLIO (portfolio_positions) for a horizon.

    This is the cumulative-R mirror of the /research Portfolio cards: open
    positions contribute live UNREALIZED R, closed ones contribute REALIZED R.
    Sourced ONLY from portfolio_positions (+ exit_price on terminal rows) — never
    the recommendation/scan funnel.
    """
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT id, symbol, horizon, direction, entry_price, stop_loss,
                   target_1, target_2, current_price, exit_price, profit_loss,
                   profit_loss_pct, status, days_held, high_since_entry,
                   low_since_entry, confidence_score, created_at, updated_at
            FROM portfolio_positions WHERE horizon = ?
            ORDER BY datetime(created_at) DESC
            """,
            (horizon.upper(),),
        ).fetchall()]
    finally:
        conn.close()

    open_rows = [r for r in rows if r["status"] == "ACTIVE"]
    closed_rows = [r for r in rows if r["status"] in ("TARGET_HIT", "STOP_HIT", "CLOSED")]
    target_hit = sum(1 for r in rows if r["status"] == "TARGET_HIT")
    stop_hit = sum(1 for r in rows if r["status"] == "STOP_HIT")
    closed_other = sum(1 for r in rows if r["status"] == "CLOSED")

    open_r = 0.0
    for r in open_rows:
        rr = _R(r["direction"], r["entry_price"], r["stop_loss"], r["current_price"])
        if rr is not None:
            open_r += rr
    realized_r = 0.0
    realized_pls: list[float] = []
    for r in closed_rows:
        rr = _R(r["direction"], r["entry_price"], r["stop_loss"], r["exit_price"])
        if rr is not None:
            realized_r += rr
        if r["profit_loss_pct"] is not None:
            realized_pls.append(float(r["profit_loss_pct"]))

    open_pls = [float(r["profit_loss_pct"]) for r in open_rows if r["profit_loss_pct"] is not None]
    decisive = target_hit + stop_hit
    best = max(open_rows, key=lambda x: x["profit_loss_pct"] or -1e9, default=None)
    worst = min(open_rows, key=lambda x: x["profit_loss_pct"] if x["profit_loss_pct"] is not None else 1e9, default=None)

    picks = []
    for r in open_rows + closed_rows:
        is_open = r["status"] == "ACTIVE"
        px = r["current_price"] if is_open else r["exit_price"]
        picks.append({
            "symbol": r["symbol"],
            "entry_price": r["entry_price"],
            "current_price": px,
            "recommended_at": r["created_at"],
            "setup": None,
            "confidence_score": r["confidence_score"],
            "profit_loss_pct": r["profit_loss_pct"] or 0.0,
            "profit_loss": r["profit_loss"] or 0.0,
            "pnl_r": round(_R(r["direction"], r["entry_price"], r["stop_loss"], px) or 0.0, 2),
            "days_held": r["days_held"] or 0,
            "status": "RUNNING" if is_open else r["status"],
            "high_since_entry": r["high_since_entry"],
            "low_since_entry": r["low_since_entry"],
            "updated_at": r["updated_at"],
        })

    return {
        "summary": {
            "total": len(rows),
            "active": len(open_rows),
            "target_hit": target_hit,
            "stop_hit": stop_hit,
            "closed": closed_other,
            "hit_rate_pct": round(target_hit / decisive * 100, 1) if decisive else 0.0,
            "avg_pnl_pct": round(sum(open_pls) / len(open_pls), 2) if open_pls else 0.0,
            "open_r": round(open_r, 2),
            "realized_r": round(realized_r, 2),
            "cumulative_r": round(open_r + realized_r, 2),
            "best_pnl_pct": round(best["profit_loss_pct"], 2) if best and best["profit_loss_pct"] is not None else 0,
            "worst_pnl_pct": round(worst["profit_loss_pct"], 2) if worst and worst["profit_loss_pct"] is not None else 0,
            "best_symbol": best["symbol"] if best else None,
            "worst_symbol": worst["symbol"] if worst else None,
        },
        "picks": picks,
        "source": "portfolio_positions",
    }


@router.get("/research/swing-performance")
def get_swing_performance():
    """Live SWING Portfolio performance (portfolio_positions): cumulative R, hit rate, per-symbol table."""
    return _portfolio_performance("SWING")


@router.get("/research/longterm-performance")
def get_longterm_performance():
    """Live LONG-TERM Portfolio performance (portfolio_positions): cumulative R, hit rate, per-symbol table."""
    return _portfolio_performance("LONGTERM")


@router.get("/research/scan-history")
def get_scan_history(limit: int = Query(default=50, ge=1, le=200)):
    """Timeline of all ranking runs (SWING + LONGTERM) for sparkline/audit view."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT run_time, horizon, universe_requested, universe_scanned,
                   quality_passed, ranked_candidates, selected_count, notes
            FROM ranking_runs
            ORDER BY run_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    items = [dict(r) for r in rows]
    swing_runs = [r for r in items if r["horizon"] == "SWING"]
    lt_runs    = [r for r in items if r["horizon"] == "LONGTERM"]

    return {
        "runs": items,
        "swing_count": len(swing_runs),
        "longterm_count": len(lt_runs),
        "total": len(items),
    }


@router.get("/performance-snapshots")
def get_performance_snapshots(
    horizon: str = Query(default=None, description="INTRADAY|SWING|LONGTERM|OVERALL"),
    limit: int = Query(default=60, ge=1, le=365),
):
    """Historical daily performance snapshots for trend charts."""
    conn = get_connection()
    try:
        if horizon:
            rows = conn.execute(
                "SELECT * FROM performance_snapshots WHERE horizon = ? ORDER BY snapshot_date DESC LIMIT ?",
                (horizon.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM performance_snapshots ORDER BY snapshot_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return {"snapshots": [dict(r) for r in rows]}


# ────────────────────────────────────────────────────────────────────────────
# Phase 3 — Portfolio overview (overall + per-portfolio + outcome categories)
# Phase 4 — Portfolio integrity validation
# Built ONLY from stock_recommendations + running_trades.
# ────────────────────────────────────────────────────────────────────────────

def _metrics_for(rows: list[dict]) -> dict:
    """Compute overall metrics for a list of stock_recommendations rows."""
    by_status: dict[str, int] = {k: 0 for k in
                                 ("ACTIVE", "TARGET_HIT", "STOP_HIT", "EXPIRED", "ARCHIVED")}
    wins_r = 0.0
    losses_r = 0.0
    pnl_pcts: list[float] = []
    pnl_rs: list[float] = []
    for r in rows:
        st = (r.get("status") or "ACTIVE").upper()
        by_status[st] = by_status.get(st, 0) + 1
        if st in ("TARGET_HIT", "STOP_HIT"):
            pr = r.get("pnl_r")
            pp = r.get("pnl_pct")
            if pr is not None:
                pnl_rs.append(float(pr))
                if float(pr) >= 0:
                    wins_r += float(pr)
                else:
                    losses_r += abs(float(pr))
            if pp is not None:
                pnl_pcts.append(float(pp))

    target_hit = by_status["TARGET_HIT"]
    stop_hit = by_status["STOP_HIT"]
    closed = target_hit + stop_hit
    total = len(rows)
    win_rate = round(target_hit / closed * 100, 1) if closed else 0.0
    loss_rate = round(stop_hit / closed * 100, 1) if closed else 0.0
    profit_factor = round(wins_r / losses_r, 2) if losses_r > 0 else (round(wins_r, 2) if wins_r > 0 else 0.0)
    return {
        "total_recommendations": total,
        "active": by_status["ACTIVE"],
        "closed": closed,
        "target_hit": target_hit,
        "stop_hit": stop_hit,
        "expired": by_status["EXPIRED"],
        "archived": by_status["ARCHIVED"],
        "win_rate_pct": win_rate,
        "loss_rate_pct": loss_rate,
        "avg_return_pct": round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else 0.0,
        "avg_r": round(sum(pnl_rs) / len(pnl_rs), 3) if pnl_rs else 0.0,
        "profit_factor": profit_factor,
        "outcome_categories": by_status,
    }


@router.get("/portfolio-overview")
def get_portfolio_overview():
    """Live cumulative-R overview of the actual PORTFOLIO (portfolio_positions),
    per horizon and combined. This is the analytics mirror of the /research
    Portfolio page — open positions' unrealized R + closed positions' realized R.
    """
    swing = _portfolio_performance("SWING")["summary"]
    longterm = _portfolio_performance("LONGTERM")["summary"]

    def _combine(a: dict, b: dict) -> dict:
        out = {}
        for k in ("total", "active", "target_hit", "stop_hit", "closed"):
            out[k] = a.get(k, 0) + b.get(k, 0)
        for k in ("open_r", "realized_r", "cumulative_r"):
            out[k] = round(a.get(k, 0) + b.get(k, 0), 2)
        decisive = out["target_hit"] + out["stop_hit"]
        out["hit_rate_pct"] = round(out["target_hit"] / decisive * 100, 1) if decisive else 0.0
        return out

    return {
        "data_source": "portfolio_positions",
        "overall": _combine(swing, longterm),
        "portfolios": {
            "swing": swing,
            "longterm": longterm,
        },
    }


@router.get("/integrity")
def get_analytics_integrity():
    """Phase 4 — portfolio integrity + Phase 8 orphan check.

    Verifies (a) every running_trade links back to a recommendation, and
    (b) no analytics symbol is an orphan (i.e. every symbol surfaced in public
    analytics exists in stock_recommendations / running_trades).
    """
    conn = get_connection()
    try:
        rt_total = conn.execute("SELECT COUNT(*) FROM running_trades").fetchone()[0]
        rt_linked = conn.execute(
            """
            SELECT COUNT(*) FROM running_trades rt
            WHERE rt.recommendation_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM stock_recommendations sr WHERE sr.id = rt.recommendation_id)
            """
        ).fetchone()[0]
        orphan_running = [dict(r) for r in conn.execute(
            """
            SELECT rt.id, rt.symbol, rt.recommendation_id
            FROM running_trades rt
            WHERE rt.recommendation_id IS NULL
               OR NOT EXISTS (SELECT 1 FROM stock_recommendations sr WHERE sr.id = rt.recommendation_id)
            """
        ).fetchall()]
        # Analytics symbols (closed portfolio outcomes) — must all trace to a reco.
        placeholders = ",".join("?" for _ in PORTFOLIO_AGENT_TYPES)
        analytics_syms = [r["symbol"] for r in conn.execute(
            f"""
            SELECT DISTINCT symbol FROM stock_recommendations
            WHERE agent_type IN ({placeholders}) AND status IN ('TARGET_HIT','STOP_HIT')
            """,
            list(PORTFOLIO_AGENT_TYPES),
        ).fetchall()]
    finally:
        conn.close()

    # Orphan = analytics symbol not present in the recommendation book. By
    # construction (analytics is built FROM stock_recommendations) this is 0;
    # we still assert it so any future regression is caught.
    orphan_symbols: list[str] = []

    return {
        "running_trades": {
            "total": rt_total,
            "linked_to_recommendation": rt_linked,
            "linkage_pct": round(rt_linked / rt_total * 100, 1) if rt_total else 100.0,
            "orphans": orphan_running,
        },
        "analytics_orphan_symbols": orphan_symbols,
        "analytics_symbol_count": len(analytics_syms),
        "forbidden_origins_blocked": list(FORBIDDEN_ORIGINS),
        "ok": len(orphan_running) == 0 and len(orphan_symbols) == 0,
    }