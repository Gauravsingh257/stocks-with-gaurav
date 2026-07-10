"""
tests/test_pil_metrics.py
=========================
Known-value tests for the PIL metrics engine (services/pil/metrics.py). Ledgers
are hand-built so every expected number is computed by hand; `_today` is pinned
so MTD/QTD/YTD boundaries are deterministic.
"""

from __future__ import annotations

from datetime import date

import pytest

from services.pil import metrics


def _ledger(curve, closed, **over):
    base = {
        "book": "SWING", "label": "Swing", "initial_capital": 100_000.0,
        "portfolio_value": curve[-1]["value"] if curve else 100_000.0,
        "invested": 0.0, "cash": 0.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0,
        "open_positions": 0, "total_return_pct": 0.0, "positions": [],
        "closed_trades": closed, "equity_curve": curve,
    }
    base.update(over)
    return base


CLOSED = [
    {"pnl": 3000.0, "pnl_pct": 15.0, "cost_basis": 20000.0, "days_held": 10},
    {"pnl": -1000.0, "pnl_pct": -5.0, "cost_basis": 20000.0, "days_held": 5},
    {"pnl": 2000.0, "pnl_pct": 10.0, "cost_basis": 20000.0, "days_held": 8},
    {"pnl": -500.0, "pnl_pct": -2.5, "cost_basis": 20000.0, "days_held": 3},
]


def test_trade_stats_known_values():
    led = _ledger([{"date": "2026-07-10", "value": 100000.0}], CLOSED)
    m = metrics.metrics_for_book(led, rf=0.0)
    assert m["closed_trades"] == 4
    assert m["hit_rate_pct"] == 50.0
    assert m["profit_factor"] == pytest.approx(3.33, abs=0.01)   # 5000 / 1500
    assert m["expectancy"] == pytest.approx(875.0)               # mean pnl
    assert m["expectancy_pct"] == pytest.approx(4.38, abs=0.01)
    assert m["avg_winner"] == pytest.approx(2500.0)
    assert m["avg_loser"] == pytest.approx(-750.0)
    assert m["win_loss_ratio"] == pytest.approx(3.33, abs=0.01)
    assert m["avg_hold_days"] == pytest.approx(6.5)


def test_max_drawdown_known():
    curve = [{"date": f"2026-01-0{i+1}", "value": v}
             for i, v in enumerate([100.0, 110.0, 90.0, 120.0])]
    led = _ledger(curve, [])
    m = metrics.metrics_for_book(led, rf=0.0)
    # trough 90 vs peak 110 -> -18.18%
    assert m["max_drawdown_pct"] == pytest.approx(-18.18, abs=0.05)


def test_cagr_known(monkeypatch):
    curve = [{"date": "2025-07-10", "value": 100_000.0},
             {"date": "2026-07-10", "value": 110_000.0}]
    led = _ledger(curve, [])
    m = metrics.metrics_for_book(led, rf=0.0)
    assert m["cagr_pct"] == pytest.approx(10.0, abs=0.3)   # ~1yr, +10%


def test_period_returns_deterministic(monkeypatch):
    monkeypatch.setattr(metrics, "_today", lambda: date(2026, 7, 10))
    curve = [
        {"date": "2025-12-31", "value": 100_000.0},
        {"date": "2026-06-30", "value": 110_000.0},
        {"date": "2026-07-01", "value": 111_000.0},
        {"date": "2026-07-09", "value": 112_000.0},
        {"date": "2026-07-10", "value": 115_000.0},
    ]
    led = _ledger(curve, [], total_return_pct=15.0)
    m = metrics.metrics_for_book(led, rf=0.0)
    assert m["ytd_pct"] == pytest.approx(15.0, abs=0.01)   # vs 2025-12-31 base
    assert m["mtd_pct"] == pytest.approx(4.55, abs=0.02)   # vs 2026-06-30 base
    assert m["qtd_pct"] == pytest.approx(4.55, abs=0.02)
    assert m["today_return_pct"] == pytest.approx(2.68, abs=0.02)  # vs 2026-07-09


def test_volatility_and_sharpe_present():
    # alternating returns => nonzero vol, finite sharpe
    vals = [100.0]
    for i in range(30):
        vals.append(vals[-1] * (1.01 if i % 2 == 0 else 0.995))
    curve = [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "value": v}
             for i, v in enumerate(vals)]
    m = metrics.metrics_for_book(_ledger(curve, []), rf=0.0)
    assert m["volatility_pct"] > 0
    assert isinstance(m["sharpe"], float)
    assert 0 <= m["risk_score"] <= 100


def test_empty_book_metrics_safe():
    m = metrics.metrics_for_book(_ledger([{"date": "2026-07-10", "value": 100000.0}], []), rf=0.0)
    assert m["closed_trades"] == 0
    assert m["hit_rate_pct"] == 0.0
    assert m["sharpe"] == 0.0
    assert m["max_drawdown_pct"] == 0.0
