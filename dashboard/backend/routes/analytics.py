"""
dashboard/backend/routes/analytics.py
Performance analytics computed from the trades table in dashboard.db.
Includes equity curve, per-setup stats, win rate rolling 20,
drawdown velocity, time-of-day heatmap, regime-based split.
"""

from fastapi import APIRouter, Query
from typing import Optional
from collections import defaultdict
from datetime import datetime
from dashboard.backend.db import get_connection
from dashboard.backend.db.schema import full_sync_from_csv, get_sync_info

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


@router.get("/summary")
def get_summary():
    """Top-level performance metrics (total trades, WR, PF, expectancy, etc.)."""
    conn = get_connection()
    try:
        rows = _rows_to_dicts(conn.execute("SELECT * FROM trades ORDER BY date ASC").fetchall())
    finally:
        conn.close()

    if not rows:
        return {"error": "No trade data found"}

    completed = [r for r in rows if r["result"] in ("WIN", "LOSS")]
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
    pf           = round(gross_profit / gross_loss, 4) if gross_loss else float("inf")
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
        "win_rate":            round(win_count / total, 4) if total else 0,  # 0-1 decimal for frontend
        "win_rate_pct":        win_rate,  # kept for backwards compat
        "total_r":             total_r,
        "profit_factor":       pf,
        "expectancy_r":        expectancy,
        "avg_win_r":           avg_win,
        "avg_loss_r":          avg_loss,
        "max_drawdown_r":      round(max_dd, 4),
        "max_consec_losses":   max_streak,
    }


@router.get("/equity-curve")
def get_equity_curve():
    """Cumulative R-multiple over time — for area chart on frontend."""
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                "SELECT date, pnl_r, result FROM trades WHERE result IN ('WIN','LOSS') ORDER BY date ASC"
            ).fetchall()
        )
    finally:
        conn.close()

    cumulative = 0.0
    curve = []
    for r in rows:
        cumulative += r["pnl_r"] or 0
        curve.append({"date": r["date"], "cumulative_r": round(cumulative, 4), "trade_r": r["pnl_r"]})

    return {"equity_curve": curve}


# Setups disabled in engine — hidden from dashboard by default
# Phase 1 (8-phase upgrade): SETUP-D re-enabled for index instruments → removed from this set
_DISABLED_SETUPS = {"B", "SETUP-D-V2"}

@router.get("/by-setup")
def get_by_setup(include_disabled: bool = Query(default=False, description="Include historically disabled setups")):
    """Win rate, expectancy, total R — broken down by setup type."""
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                "SELECT setup, result, pnl_r FROM trades WHERE result IN ('WIN','LOSS')"
            ).fetchall()
        )
    finally:
        conn.close()

    # Filter out disabled setups unless explicitly requested
    if not include_disabled:
        rows = [r for r in rows if r["setup"] not in _DISABLED_SETUPS]

    buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "total_r": 0.0})
    for r in rows:
        b = buckets[r["setup"]]
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
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                "SELECT date, result FROM trades WHERE result IN ('WIN','LOSS') ORDER BY date ASC"
            ).fetchall()
        )
    finally:
        conn.close()

    if len(rows) < window:
        return {"data": [], "rolling_winrate": [], "window": window}

    points = []
    for i in range(window - 1, len(rows)):
        chunk = rows[i - window + 1: i + 1]
        wins  = sum(1 for r in chunk if r["result"] == "WIN")
        points.append({
            "date":         chunk[-1]["date"],
            "win_rate":     round(wins / window, 4),  # 0-1 decimal for frontend
            "win_rate_pct": round(wins / window * 100, 1),  # kept for backwards compat
            "idx":          i,    # frontend uses 'idx'
            "index":        i,    # kept for backwards compat
        })

    return {"data": points, "rolling_winrate": points, "window": window}  # 'data' for frontend


@router.get("/time-of-day")
def get_time_of_day():
    """PnL aggregated by hour of day — for heatmap / bar chart."""
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                "SELECT date, pnl_r, result FROM trades WHERE result IN ('WIN','LOSS')"
            ).fetchall()
        )
    finally:
        conn.close()

    buckets = defaultdict(lambda: {"trades": 0, "total_r": 0.0, "wins": 0})
    for r in rows:
        try:
            hour = int(r["date"][11:13])   # "2026-02-04 13:20:07" → 13
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
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                "SELECT date, pnl_r, result FROM trades WHERE result IN ('WIN','LOSS')"
            ).fetchall()
        )
    finally:
        conn.close()

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    buckets = defaultdict(lambda: {"trades": 0, "total_r": 0.0, "wins": 0})
    for r in rows:
        try:
            d = dt.fromisoformat(r["date"])
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
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                "SELECT date, pnl_r, result FROM trades WHERE result IN ('WIN','LOSS') ORDER BY date ASC"
            ).fetchall()
        )
    finally:
        conn.close()

    if not rows:
        return {"drawdown_velocity": []}

    cumulative = 0.0
    peak = 0.0
    in_dd = False
    dd_start_date = None
    dd_start_val = 0.0
    dd_events = []

    for r in rows:
        cumulative += r["pnl_r"] or 0
        date = r["date"][:10]

        if cumulative > peak:
            if in_dd and dd_start_date:
                dd_events.append({
                    "start": dd_start_date,
                    "end": date,
                    "depth_r": round(dd_start_val - cumulative + (cumulative - cumulative), 4),
                })
            peak = cumulative
            in_dd = False
        else:
            if not in_dd:
                in_dd = True
                dd_start_date = date
                dd_start_val = peak

    return {"drawdown_velocity": dd_events, "total_dd_events": len(dd_events)}


@router.get("/calendar-heatmap")
def get_calendar_heatmap():
    """Daily PnL for calendar heatmap on the analytics page."""
    conn = get_connection()
    try:
        rows = _rows_to_dicts(
            conn.execute(
                "SELECT date, pnl_r, result FROM trades WHERE result IN ('WIN','LOSS') ORDER BY date ASC"
            ).fetchall()
        )
    finally:
        conn.close()

    daily = defaultdict(lambda: {"trades": 0, "total_r": 0.0, "wins": 0})
    for r in rows:
        day = r["date"][:10]
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