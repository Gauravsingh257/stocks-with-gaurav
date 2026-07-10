"""
services/pil/health.py
======================
Portfolio health scoring (Part 6). Each book (and combined) receives a set of
0..100 sub-scores that roll up into an overall health score and a GREEN /
YELLOW / RED status:

  quality        realised edge (hit-rate, profit-factor, expectancy)
  risk           inverse of the composite risk score (calm = healthy)
  drawdown       shallow drawdown = healthy
  momentum       recent trajectory (MTD)
  concentration  well-spread book = healthy (inverse of top-name weight)
  diversification breadth of open holdings
  maturity       length of the realised track record (proven vs unproven)
  liquidity      (combined) share of holdings above the liquidity floor
  replacement    (combined) allocation drift pressure

Descriptive only — health never gates a trading decision.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

GREEN, YELLOW, RED = "GREEN", "YELLOW", "RED"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _status(score: float) -> str:
    if score >= 70:
        return GREEN
    if score >= 45:
        return YELLOW
    return RED


def _score_quality(m: dict) -> float:
    hr = min(m.get("hit_rate_pct", 0) / 60.0, 1.0)
    pf = min(max(m.get("profit_factor", 0), 0) / 2.5, 1.0)
    exp = (min(max(m.get("expectancy_pct", 0), -5), 5) + 5) / 10
    return _clamp(100 * (0.4 * hr + 0.35 * pf + 0.25 * exp))


def _score_risk(m: dict) -> float:
    return _clamp(100 - m.get("risk_score", 0))


def _score_drawdown(m: dict) -> float:
    dd = abs(m.get("max_drawdown_pct", 0))
    return _clamp(100 * (1 - min(dd / 30.0, 1.0)))


def _score_momentum(m: dict) -> float:
    mtd = m.get("mtd_pct", 0)
    return _clamp(50 + mtd * 5)  # +10% MTD -> 100, -10% -> 0


def _score_concentration(ledger: dict) -> float:
    positions = ledger.get("positions", [])
    if not positions:
        return 70.0  # all cash = no concentration risk
    top = positions[0].get("weight_pct", 0)
    # weight_pct is share of portfolio value; scale so 25%+ single name = 0
    return _clamp(100 * (1 - min(top / 25.0, 1.0)))


def _score_diversification(ledger: dict) -> float:
    n = ledger.get("open_positions", 0)
    if n == 0:
        return 60.0
    return _clamp(100 * min(n / 10.0, 1.0))  # 10+ names = fully diversified


def _score_maturity(m: dict) -> float:
    n = m.get("closed_trades", 0)
    return _clamp(100 * min(n / 30.0, 1.0))


def health_for_book(book: str, ledger: dict, m: dict,
                    combined_extra: dict | None = None) -> dict[str, Any]:
    sub = {
        "quality": round(_score_quality(m), 1),
        "risk": round(_score_risk(m), 1),
        "drawdown": round(_score_drawdown(m), 1),
        "momentum": round(_score_momentum(m), 1),
        "concentration": round(_score_concentration(ledger), 1),
        "diversification": round(_score_diversification(ledger), 1),
        "maturity": round(_score_maturity(m), 1),
    }
    weights = {
        "quality": 0.22, "risk": 0.15, "drawdown": 0.18, "momentum": 0.12,
        "concentration": 0.12, "diversification": 0.11, "maturity": 0.10,
    }
    if combined_extra:
        sub["liquidity"] = round(combined_extra.get("liquidity", 70.0), 1)
        sub["replacement_pressure"] = round(combined_extra.get("replacement_pressure", 70.0), 1)
        weights = {
            "quality": 0.18, "risk": 0.13, "drawdown": 0.15, "momentum": 0.10,
            "concentration": 0.10, "diversification": 0.09, "maturity": 0.08,
            "liquidity": 0.09, "replacement_pressure": 0.08,
        }
    overall = round(sum(sub[k] * w for k, w in weights.items()), 1)
    return {
        "book": book,
        "sub_scores": sub,
        "overall": overall,
        "status": _status(overall),
        "worst_factor": min(sub, key=lambda k: sub[k]),
        "best_factor": max(sub, key=lambda k: sub[k]),
    }


def compute(books: dict[str, dict]) -> dict[str, Any]:
    """Health for every book + combined."""
    from services.pil import metrics, exposure, allocation
    met = metrics.metrics_all(books)

    out: dict[str, Any] = {}
    for b in ("SWING", "LONGTERM", "MOMENTUM"):
        out[b] = health_for_book(b, books.get(b, {}), met[b])

    # combined pulls in liquidity + allocation-drift (replacement pressure)
    exp = exposure.compute(books)
    alloc = allocation.compute(books)
    drift = max((abs(r["deviation"]) for r in alloc["rows"]), default=0.0)
    combined_extra = {
        "liquidity": exp.get("liquidity_coverage_pct", 70.0) or 70.0,
        "replacement_pressure": _clamp(100 * (1 - min(drift / 0.30, 1.0))),
    }
    out["COMBINED"] = health_for_book("COMBINED", books.get("COMBINED", {}),
                                      met["COMBINED"], combined_extra)
    out["overall_status"] = out["COMBINED"]["status"]
    out["overall_score"] = out["COMBINED"]["overall"]
    return out
