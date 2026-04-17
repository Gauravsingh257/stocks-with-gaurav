"""
dashboard/backend/routes/analytics.py
Performance analytics computed from the trades table in dashboard.db.
Includes equity curve, per-setup stats, win rate rolling 20,
drawdown velocity, time-of-day heatmap, regime-based split.

When the trades table is empty (e.g. trade_ledger_2026.csv not synced),
falls back to the ai_learning signal_log table for live signal data.
"""

import sqlite3
import logging
from pathlib import Path
from fastapi import APIRouter, Query
from typing import Optional
from collections import defaultdict
from datetime import datetime
from dashboard.backend.db import get_connection
from dashboard.backend.db.schema import full_sync_from_csv, get_sync_info

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
logger = logging.getLogger("analytics")

# Path to the ai_learning signals DB
_AI_LEARNING_DB = Path(__file__).resolve().parents[3] / "ai_learning" / "data" / "trade_learning.db"


def _rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


def _get_signal_log_rows() -> list:
    """Read completed signals from ai_learning signal_log and normalise to trades schema."""
    if not _AI_LEARNING_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(_AI_LEARNING_DB))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT created_at AS date, symbol, direction,
                       strategy_name AS setup,
                       entry, stop_loss, target1, target2,
                       result, pnl_r, score, confidence
                FROM signal_log
                WHERE result IN ('WIN','LOSS')
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(f"signal_log fallback error: {exc}")
        return []


def _load_trades(include_running: bool = False) -> tuple[list, str]:
    """
    Load trade rows, falling back to signal_log if trades table is empty.
    Returns (rows, source) where source is 'trades' or 'signal_log'.
    """
    conn = get_connection()
    try:
        result_filter = "('WIN','LOSS','RUNNING')" if include_running else "('WIN','LOSS')"
        rows = _rows_to_dicts(
            conn.execute(f"SELECT * FROM trades WHERE result IN {result_filter} ORDER BY date ASC").fetchall()
        )
    finally:
        conn.close()

    if rows:
        return rows, "trades"

    # Fallback to signal_log
    rows = _get_signal_log_rows()
    return rows, "signal_log"


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
    Joins running_trades ← stock_recommendations to get live P&L, status, days held.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                sr.symbol,
                sr.entry_price,
                sr.confidence_score,
                sr.created_at AS recommended_at,
                sr.setup,
                rt.current_price,
                rt.profit_loss_pct,
                rt.profit_loss,
                rt.days_held,
                rt.status,
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
        picks.append({
            "symbol": r["symbol"],
            "entry_price": r["entry_price"],
            "current_price": r["current_price"],
            "recommended_at": r["recommended_at"],
            "setup": r["setup"],
            "confidence_score": r["confidence_score"],
            "profit_loss_pct": r["profit_loss_pct"] or 0.0,
            "profit_loss": r["profit_loss"] or 0.0,
            "days_held": r["days_held"] or 0,
            "status": r["status"] or "PENDING",
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


@router.get("/research/swing-performance")
def get_swing_performance():
    """Swing scan recommendation performance: hit rate, avg P&L%, per-symbol table."""
    return _research_performance("SWING")


@router.get("/research/longterm-performance")
def get_longterm_performance():
    """Long-term recommendation performance: hit rate, avg P&L%, per-symbol table."""
    return _research_performance("LONGTERM")


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