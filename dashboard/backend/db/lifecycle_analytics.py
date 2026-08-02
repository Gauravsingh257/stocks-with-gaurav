"""
dashboard/backend/db/lifecycle_analytics.py
===========================================
Derived analytics over the lifecycle ledger, plus rolled-up period snapshots.

Everything here is computed from CLOSED, genuinely-executed positions. Ideas
that never filled are counted as signals elsewhere but contribute no return, so
they cannot flatter expectancy, profit factor or any risk-adjusted ratio.

Snapshots exist so trend views (win-rate over time, monthly return, rolling RR)
read one row per period instead of recomputing the whole history on every page
load.
"""

from __future__ import annotations

import json
import logging
import math
import statistics as st
from datetime import datetime, timezone, timedelta

from .schema import get_connection
from .trade_lifecycle import init_lifecycle_db, CLOSED_STATUSES

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

# Indian risk-free proxy, annual. Used only for Sharpe/Sortino; configurable
# because the number is a convention, not a fact about the strategy.
_RF_ANNUAL = 0.065


def _closed_rows(conn, portfolio: str | None = None, since: str | None = None):
    where = ["record_state = 'ACTIVE'", "is_duplicate = 0",
             f"status IN ({','.join('?' * len(CLOSED_STATUSES))})"]
    params: list = list(CLOSED_STATUSES)
    if portfolio and portfolio.upper() != "ALL":
        where.append("(portfolio = ? OR source = ?)")
        params += [portfolio.upper(), portfolio.upper()]
    if since:
        where.append("exit_at >= ?")
        params.append(since)
    return conn.execute(
        f"SELECT symbol, pnl_pct, rr_realized, holding_days, mae_pct, mfe_pct, status, "
        f"exit_at, entry_price, stop_loss, time_to_target_days, time_to_stop_days "
        f"FROM trade_lifecycle WHERE {' AND '.join(where)} ORDER BY datetime(exit_at) ASC",
        params,
    ).fetchall()


def _slots_for(portfolio: str | None) -> int:
    """Capacity of the book being measured — the divisor that turns a sum of
    trade percentages into a portfolio number."""
    import os as _os
    sw = int(_os.getenv("PORTFOLIO_MAX_SWING", "20"))
    lt = int(_os.getenv("PORTFOLIO_MAX_LONGTERM", "20"))
    mo = int(_os.getenv("MOMENTUM_MAX_POSITIONS", "20"))
    p = (portfolio or "ALL").upper()
    if p == "SWING":
        return sw
    if p == "LONGTERM":
        return lt
    if p == "MOMENTUM":
        return mo
    return sw + lt + mo


def analytics(portfolio: str | None = None, since: str | None = None) -> dict:
    """Risk/return metrics over closed positions.

    Return and drawdown are reported in BOOK terms — the sum of trade
    percentages divided by the book's slot count — because each position is only
    1/slots of capital. Summing raw trade percentages answers "how did the
    average trade do", not "what did the book make", and publishing the sum as a
    return overstates it by roughly the slot count. Both numbers are returned,
    named for exactly what they are."""
    init_lifecycle_db()
    conn = get_connection()
    try:
        rows = [dict(r) for r in _closed_rows(conn, portfolio, since)]
    finally:
        conn.close()

    pnls = [r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]
    n = len(pnls)
    if n == 0:
        return {"closed_trades": 0, "note": "no closed positions in this selection"}

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    win_rate = len(wins) / n

    avg_win = st.mean(wins) if wins else 0.0
    avg_loss = st.mean(losses) if losses else 0.0
    # Expectancy in % per trade — what one trade is worth on average.
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    profit_factor = (gross_win / gross_loss) if gross_loss else (float("inf") if gross_win else 0.0)

    # Equity curve in BOOK terms. Building it from raw trade percentages made
    # the swing book report a -70.55% max drawdown, which is not a drawdown any
    # portfolio could survive — it was 20 slots' worth of per-trade moves added
    # together. Weighted, the real figure is -3.53%.
    slots = _slots_for(portfolio)
    eq, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p / slots
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    book_return = eq
    sum_trade_return = sum(pnls)
    recovery = (book_return / abs(max_dd)) if max_dd else (float("inf") if book_return > 0 else 0.0)

    # Sharpe / Sortino on the per-trade return series. Annualised using the
    # observed trade frequency rather than a fixed 252 — these are trades, not
    # daily marks, so pretending otherwise would overstate both.
    sharpe = sortino = None
    if n > 1:
        sd = st.pstdev(pnls)
        holds = [r["holding_days"] for r in rows if r.get("holding_days")]
        avg_hold = st.mean(holds) if holds else 20.0
        trades_per_year = 252.0 / max(avg_hold, 1.0)
        rf_per_trade = _RF_ANNUAL * 100 / max(trades_per_year, 1.0)
        excess = st.mean(pnls) - rf_per_trade
        if sd:
            sharpe = round(excess / sd * math.sqrt(trades_per_year), 2)
        downside = [p for p in pnls if p < rf_per_trade]
        dsd = st.pstdev(downside) if len(downside) > 1 else None
        if dsd:
            sortino = round(excess / dsd * math.sqrt(trades_per_year), 2)

    def _avg_giveback(rs):
        """How much of the best price was handed back, on average — the single
        most actionable number in this set."""
        vals = [r["mfe_pct"] - r["pnl_pct"] for r in rs
                if r.get("mfe_pct") is not None and r.get("pnl_pct") is not None]
        return sum(vals) / len(vals) if vals else None

    def _avg(key, pred=None):
        vals = [r[key] for r in rows
                if r.get(key) is not None and (pred is None or pred(r))]
        return round(st.mean(vals), 2) if vals else None

    return {
        "closed_trades": n,
        "win_rate_pct": round(win_rate * 100, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(expectancy, 3),
        "avg_giveback_pct": (lambda g: round(g, 2) if g is not None else None)(_avg_giveback(rows)),
        "expectancy_r": _avg("rr_realized"),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "payoff_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss else None,
        # Book figures — what the portfolio actually did.
        "book_return_pct": round(book_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "book_slots": slots,
        # Sum of per-trade percentages. Useful for comparing trades to each
        # other; NEVER a portfolio return.
        "sum_trade_return_pct": round(sum_trade_return, 2),
        "total_return_pct": round(book_return, 2),   # back-compat alias
        "recovery_factor": round(recovery, 2) if recovery != float("inf") else None,
        "sharpe": sharpe,
        "sortino": sortino,
        "avg_mae_pct": _avg("mae_pct"),
        "avg_mfe_pct": _avg("mfe_pct"),
        "avg_holding_days": _avg("holding_days"),
        "avg_time_to_target_days": _avg("holding_days", lambda r: r["status"] == "TARGET_HIT"),
        "avg_time_to_stop_days": _avg("holding_days", lambda r: r["status"] == "STOP_HIT"),
        "basis": (f"closed, genuinely-executed positions only; unfilled ideas contribute "
                  f"no return. Return and drawdown are book-weighted over {slots} slots — "
                  f"each position is 1/{slots} of capital, so a sum of trade percentages "
                  f"is not a portfolio return."),
    }


def _period_key(period: str, dt: datetime) -> str:
    if period == "DAILY":
        return dt.date().isoformat()
    if period == "WEEKLY":
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"
    return f"{dt.year}-{dt.month:02d}"


def snapshot_stats(periods=("DAILY", "WEEKLY", "MONTHLY"),
                   portfolios=("ALL", "SWING", "LONGTERM", "MOMENTUM")) -> dict:
    """Persist the current stats for each period/book. Idempotent per key —
    re-running the same day overwrites that day's row rather than appending."""
    from .trade_lifecycle_query import stats as live_stats

    init_lifecycle_db()
    now = datetime.now(_IST)
    written = 0
    conn = get_connection()
    try:
        for period in periods:
            key = _period_key(period, now)
            for book in portfolios:
                try:
                    payload = {**live_stats(portfolio=book), **analytics(portfolio=book)}
                except Exception:
                    logger.debug("[LifecycleAnalytics] snapshot failed for %s/%s", period, book)
                    continue
                conn.execute(
                    "INSERT INTO lifecycle_stats_snapshots (period, period_key, portfolio, payload, created_at) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(period, period_key, portfolio) DO UPDATE SET "
                    "payload = excluded.payload, created_at = excluded.created_at",
                    (period, key, book, json.dumps(payload, default=str), now.isoformat()),
                )
                written += 1
        conn.commit()
        return {"ok": True, "written": written, "as_of": now.isoformat()}
    except Exception as exc:
        logger.error("[LifecycleAnalytics] snapshot_stats failed: %s", exc)
        return {"ok": False, "reason": str(exc), "written": written}
    finally:
        conn.close()


def stats_history(period: str = "DAILY", portfolio: str = "ALL", limit: int = 180) -> dict:
    """Stored snapshots, oldest→newest, for trend charts."""
    init_lifecycle_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT period_key, payload, created_at FROM lifecycle_stats_snapshots "
            "WHERE period = ? AND portfolio = ? ORDER BY period_key DESC LIMIT ?",
            (period.upper(), portfolio.upper(), int(limit)),
        ).fetchall()
        out = []
        for r in reversed(rows):
            try:
                out.append({"period_key": r["period_key"], **json.loads(r["payload"])})
            except Exception:
                continue
        return {"period": period.upper(), "portfolio": portfolio.upper(), "points": out}
    finally:
        conn.close()
