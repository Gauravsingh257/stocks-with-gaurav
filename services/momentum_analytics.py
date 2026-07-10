"""
services/momentum_analytics.py
===============================
Phase-C deep analytics for the independent Momentum Portfolio. Read-only: derives
everything from the immutable `momentum_journal` (realised, R-based) plus the
current ACTIVE holdings (unrealised). Introduces no trading logic.

All performance is in R-multiples (risk-normalised) so trades with different stop
widths compare fairly. Sharpe/Sortino are reported at the TRADE level (per-trade
R distribution) — honest for a trade series; no spurious annualisation.
"""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any, Callable


def _perf(rs: list[float]) -> dict[str, Any]:
    """Core metric block from a list of R-multiples."""
    n = len(rs)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0,
                "avg_win_r": 0.0, "avg_loss_r": 0.0, "total_r": 0.0,
                "sharpe": 0.0, "sortino": 0.0, "max_drawdown_r": 0.0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins); gross_loss = abs(sum(losses))
    sd = pstdev(rs) if n > 1 else 0.0
    downside = [min(0.0, r) for r in rs]
    dd_sd = math.sqrt(sum(d * d for d in downside) / n) if n else 0.0
    # max drawdown of the cumulative-R equity curve
    cum = peak = maxdd = 0.0
    for r in rs:
        cum += r; peak = max(peak, cum); maxdd = min(maxdd, cum - peak)
    m = mean(rs)
    return {
        "n": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "expectancy_r": round(m, 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (float("inf") if gross_win else 0.0),
        "avg_win_r": round(mean(wins), 3) if wins else 0.0,
        "avg_loss_r": round(mean(losses), 3) if losses else 0.0,
        "total_r": round(sum(rs), 2),
        "sharpe": round(m / sd, 2) if sd > 0 else 0.0,
        "sortino": round(m / dd_sd, 2) if dd_sd > 0 else 0.0,
        "max_drawdown_r": round(maxdd, 2),
    }


def _attr(journal: list[dict], key: str) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for j in journal:
        if j.get("r_multiple") is None:
            continue
        groups.setdefault(str(j.get(key) or "unknown"), []).append(float(j["r_multiple"]))
    return {k: _perf(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def _monthly(journal: list[dict]) -> list[dict]:
    buckets: dict[str, list[float]] = {}
    pnl: dict[str, list[float]] = {}
    for j in journal:
        mth = str(j.get("closed_at") or "")[:7]
        if not mth or j.get("r_multiple") is None:
            continue
        buckets.setdefault(mth, []).append(float(j["r_multiple"]))
        pnl.setdefault(mth, []).append(float(j.get("profit_loss_pct") or 0))
    out = []
    for mth in sorted(buckets):
        rs = buckets[mth]; wins = sum(1 for r in rs if r > 0)
        out.append({"month": mth, "trades": len(rs), "win_rate": round(wins / len(rs) * 100, 1),
                    "total_r": round(sum(rs), 2), "total_pnl_pct": round(sum(pnl[mth]), 2)})
    return out


def analytics(journal_provider: Callable[[], list[dict]] | None = None,
              active_provider: Callable[[], list[dict]] | None = None) -> dict[str, Any]:
    """Full analytics payload. Providers injected for testability; default to the
    live Momentum DB."""
    if journal_provider is None or active_provider is None:
        from dashboard.backend.db import momentum_portfolio as db
        journal_provider = journal_provider or (lambda: db.get_journal(1000))
        active_provider = active_provider or (
            lambda: [p for p in db.get_portfolio(include_pending=False) if p.get("status") == "ACTIVE"])

    journal = journal_provider() or []
    active = active_provider() or []

    rs = [float(j["r_multiple"]) for j in journal if j.get("r_multiple") is not None]
    realized = _perf(rs)
    realized["total_return_pct"] = round(sum(float(j.get("profit_loss_pct") or 0) for j in journal), 2)
    realized["avg_hold_days"] = round(mean([j.get("days_held") or 0 for j in journal]), 1) if journal else 0.0

    # Open (unrealised) — current R from entry vs initial stop, and live P&L%.
    open_r = []
    open_pnl = []
    for p in active:
        e = float(p.get("entry_price") or 0); isl = float(p.get("initial_stop") or p.get("stop_loss") or 0)
        cmp = float(p.get("current_price") or e)
        if e > 0 and (e - isl) > 0:
            open_r.append((cmp - e) / (e - isl))
        if p.get("profit_loss_pct") is not None:
            open_pnl.append(float(p["profit_loss_pct"]))

    return {
        "realized": realized,
        "open": {
            "positions": len(active),
            "open_r": round(sum(open_r), 2),
            "unrealized_pnl_pct": round(sum(open_pnl), 2),
            "avg_unrealized_pnl_pct": round(mean(open_pnl), 2) if open_pnl else 0.0,
        },
        "combined_expectancy_r": realized.get("expectancy_r", 0.0),
        "monthly": _monthly(journal),
        "attribution": {
            "sector": _attr(journal, "sector"),
            "regime": _attr(journal, "regime"),
            "entry_model": _attr(journal, "entry_model"),
            "risk_model": _attr(journal, "risk_model"),
        },
        "sample_note": ("Trade-level Sharpe/Sortino; small samples are noisy — "
                        "interpret with the same caution as the research NO-GO."),
    }
