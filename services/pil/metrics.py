"""
services/pil/metrics.py
=======================
The PIL metrics engine (Part 1). Computes the full PMS metric set for a single
book ledger (from services/pil/accounting.reconstruct) and for the COMBINED book:

  Returns:      total, today, MTD, QTD, YTD, CAGR
  Risk:         volatility, max drawdown, Sharpe, Sortino, Calmar, risk score
  Trade stats:  hit rate, expectancy, profit factor, avg winner/loser,
                win/loss ratio, average hold time, portfolio turnover

Portfolio-level ratios come from the daily ₹ equity curve (annualised with 252
trading days); trade stats come from the reconstructed closed-trade ₹ P&L. This
mirrors the honest, self-contained style of services/momentum_analytics.py but
works at the portfolio (₹) level rather than per-trade R.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone, timedelta
from statistics import mean, pstdev
from typing import Any

_IST = timezone(timedelta(hours=5, minutes=30))
_TRADING_DAYS = 252


def _today() -> date:
    return datetime.now(_IST).date()


def _daily_returns(curve: list[dict]) -> list[float]:
    vals = [p["value"] for p in curve if p.get("value")]
    out = []
    for i in range(1, len(vals)):
        prev = vals[i - 1]
        if prev:
            out.append(vals[i] / prev - 1.0)
    return out


def _value_before(curve: list[dict], boundary: str) -> float | None:
    """NAV on the last day strictly before `boundary` (YYYY-MM-DD)."""
    base = None
    for p in curve:
        if p["date"] < boundary:
            base = p["value"]
        else:
            break
    return base


def _period_return(curve: list[dict], boundary: str) -> float:
    if not curve:
        return 0.0
    base = _value_before(curve, boundary)
    if base is None:
        base = curve[0]["value"]
    cur = curve[-1]["value"]
    return round((cur - base) / base * 100, 2) if base else 0.0


def _max_drawdown_pct(curve: list[dict]) -> float:
    peak = None
    maxdd = 0.0
    for p in curve:
        v = p["value"]
        peak = v if peak is None else max(peak, v)
        if peak:
            maxdd = min(maxdd, v / peak - 1.0)
    return round(maxdd * 100, 2)


def _cagr_pct(curve: list[dict]) -> float:
    if len(curve) < 2:
        return 0.0
    start_v = curve[0]["value"]
    end_v = curve[-1]["value"]
    if start_v <= 0 or end_v <= 0:
        return 0.0
    d0 = datetime.fromisoformat(curve[0]["date"]).date()
    d1 = datetime.fromisoformat(curve[-1]["date"]).date()
    years = max((d1 - d0).days, 1) / 365.25
    if years < (1 / 365.25):
        return 0.0
    return round(((end_v / start_v) ** (1 / years) - 1) * 100, 2)


def _risk_ratios(curve: list[dict], rf: float) -> dict[str, float]:
    rets = _daily_returns(curve)
    n = len(rets)
    if n < 2:
        return {"volatility_pct": 0.0, "sharpe": 0.0, "sortino": 0.0}
    mu = mean(rets)
    sd = pstdev(rets)
    downside = [min(0.0, r) for r in rets]
    dd_sd = math.sqrt(sum(d * d for d in downside) / n)
    ann_ret = mu * _TRADING_DAYS
    ann_vol = sd * math.sqrt(_TRADING_DAYS)
    ann_dd = dd_sd * math.sqrt(_TRADING_DAYS)
    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else 0.0
    sortino = (ann_ret - rf) / ann_dd if ann_dd > 0 else 0.0
    return {
        "volatility_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
    }


def _trade_stats(closed: list[dict], initial_capital: float, curve: list[dict]) -> dict[str, Any]:
    n = len(closed)
    if n == 0:
        return {
            "closed_trades": 0, "hit_rate_pct": 0.0, "expectancy": 0.0,
            "expectancy_pct": 0.0, "profit_factor": 0.0, "avg_winner": 0.0,
            "avg_loser": 0.0, "avg_winner_pct": 0.0, "avg_loser_pct": 0.0,
            "win_loss_ratio": 0.0, "avg_hold_days": 0.0, "turnover_pct": 0.0,
        }
    pnls = [t["pnl"] for t in closed]
    pcts = [t["pnl_pct"] for t in closed]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    avg_win = mean([t["pnl"] for t in wins]) if wins else 0.0
    avg_loss = mean([t["pnl"] for t in losses]) if losses else 0.0

    # portfolio turnover ≈ total invested notional / avg capital, annualised
    invested_total = sum(t.get("cost_basis", 0.0) for t in closed)
    avg_capital = mean([p["value"] for p in curve]) if curve else (initial_capital or 1.0)
    span_days = 1
    if curve and len(curve) >= 2:
        d0 = datetime.fromisoformat(curve[0]["date"]).date()
        d1 = datetime.fromisoformat(curve[-1]["date"]).date()
        span_days = max((d1 - d0).days, 1)
    turnover = (invested_total / avg_capital) * (365.0 / span_days) if avg_capital else 0.0

    return {
        "closed_trades": n,
        "hit_rate_pct": round(len(wins) / n * 100, 1),
        "expectancy": round(mean(pnls), 2),
        "expectancy_pct": round(mean(pcts), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0
        else (999.0 if gross_win else 0.0),
        "avg_winner": round(avg_win, 2),
        "avg_loser": round(avg_loss, 2),
        "avg_winner_pct": round(mean([t["pnl_pct"] for t in wins]), 2) if wins else 0.0,
        "avg_loser_pct": round(mean([t["pnl_pct"] for t in losses]), 2) if losses else 0.0,
        "win_loss_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss else 0.0,
        "avg_hold_days": round(mean([t.get("days_held") or 0 for t in closed]), 1),
        "turnover_pct": round(turnover * 100, 1),
    }


def _risk_score(vol_pct: float, maxdd_pct: float, top_weight_pct: float) -> float:
    """0 (calm) .. 100 (hot) composite. Blends annualised vol, drawdown depth and
    single-name concentration. Purely descriptive — never gates a trade."""
    vol_c = min(vol_pct / 40.0, 1.0)          # 40% vol -> full
    dd_c = min(abs(maxdd_pct) / 30.0, 1.0)     # 30% DD -> full
    conc_c = min(top_weight_pct / 25.0, 1.0)   # 25% single name -> full
    score = 100 * (0.4 * vol_c + 0.4 * dd_c + 0.2 * conc_c)
    return round(score, 1)


def metrics_for_book(ledger: dict, *, rf: float | None = None) -> dict[str, Any]:
    """Full Part-1 metric block for one book (or COMBINED) ledger."""
    from services.pil import config as pil_config
    rf = pil_config.risk_free_rate() if rf is None else rf

    curve = ledger.get("equity_curve") or []
    closed = ledger.get("closed_trades") or []
    initial = ledger.get("initial_capital", 0.0)
    today = _today()
    month_start = today.replace(day=1).isoformat()
    q_month = 3 * ((today.month - 1) // 3) + 1
    quarter_start = today.replace(month=q_month, day=1).isoformat()
    year_start = today.replace(month=1, day=1).isoformat()

    ratios = _risk_ratios(curve, rf)
    maxdd = _max_drawdown_pct(curve)
    cagr = _cagr_pct(curve)
    trade = _trade_stats(closed, initial, curve)
    top_weight = ledger["positions"][0]["weight_pct"] if ledger.get("positions") else 0.0

    calmar = round(cagr / abs(maxdd), 2) if maxdd else 0.0

    # today's return: last point vs the prior day
    today_ret = 0.0
    if len(curve) >= 2 and curve[-2]["value"]:
        today_ret = round((curve[-1]["value"] - curve[-2]["value"]) / curve[-2]["value"] * 100, 2)

    return {
        "book": ledger.get("book"),
        "label": ledger.get("label"),
        # balances (₹)
        "portfolio_value": ledger.get("portfolio_value"),
        "invested_capital": ledger.get("invested"),
        "available_cash": ledger.get("cash"),
        "initial_capital": initial,
        "realized_pnl": ledger.get("realized_pnl"),
        "unrealized_pnl": ledger.get("unrealized_pnl"),
        "open_positions": ledger.get("open_positions"),
        "pending_positions": ledger.get("pending_positions", 0),
        # returns
        "total_return_pct": ledger.get("total_return_pct"),
        "today_return_pct": today_ret,
        "mtd_pct": _period_return(curve, month_start),
        "qtd_pct": _period_return(curve, quarter_start),
        "ytd_pct": _period_return(curve, year_start),
        "cagr_pct": cagr,
        # risk
        "volatility_pct": ratios["volatility_pct"],
        "max_drawdown_pct": maxdd,
        "sharpe": ratios["sharpe"],
        "sortino": ratios["sortino"],
        "calmar": calmar,
        "risk_score": _risk_score(ratios["volatility_pct"], maxdd, top_weight),
        # trade stats
        **trade,
    }


def metrics_all(books: dict[str, dict]) -> dict[str, dict]:
    """Metric block for every book present in the ledger map (incl. COMBINED)."""
    return {b: metrics_for_book(ledger) for b, ledger in books.items()}
