"""
tests/test_pil_api.py
=====================
Integration smoke tests for the PIL API (dashboard/backend/routes/
portfolio_intelligence.py). Seeds the REAL Swing/Momentum DB tables through the
engines' own helpers, then asserts the accounting layer reads them and the
endpoints return a full metric block per book. Also asserts the guard 404s the
surface when PIL_ENABLED is unset.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pil_api_test_")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from dashboard.backend.db import portfolio as pdb  # noqa: E402
from dashboard.backend.routes import portfolio_intelligence as pi  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    pdb.init_portfolio_db()
    from dashboard.backend.db import pil as pildb
    pildb.ensure_tables()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_journal")
        c.execute("DELETE FROM portfolio_positions")
        c.execute("DELETE FROM pil_config")   # clear capital/target overrides
        c.commit()
    finally:
        c.close()
    monkeypatch.setenv("PIL_ENABLED", "1")
    # keep capital deterministic
    monkeypatch.setenv("PIL_CAPITAL_SWING", "1000000")
    monkeypatch.setenv("PIL_CAPITAL_LONGTERM", "1000000")
    monkeypatch.setenv("PIL_CAPITAL_MOMENTUM", "500000")


def test_guard_404s_when_disabled(monkeypatch):
    monkeypatch.setenv("PIL_ENABLED", "0")
    with pytest.raises(HTTPException) as ei:
        pi._guard()
    assert ei.value.status_code == 404
    # status endpoint is always reachable and reports disabled
    assert pi.status()["enabled"] is False


def test_combined_reflects_a_live_position():
    pdb.add_position({"symbol": "TCS", "horizon": "SWING", "entry_price": 100.0,
                      "stop_loss": 92.0, "status": "ACTIVE"})
    # mark it up
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("UPDATE portfolio_positions SET current_price=120 WHERE symbol='TCS'")
        c.commit()
    finally:
        c.close()

    comp = pi.comparison()
    sw = comp["metrics"]["SWING"]
    assert sw["open_positions"] == 1
    assert sw["invested_capital"] > 0
    assert sw["portfolio_value"] > sw["initial_capital"]      # marked up
    # combined aggregates all three books' capital
    assert comp["metrics"]["COMBINED"]["initial_capital"] == pytest.approx(2_500_000.0)


def test_combined_books_closed_trade_into_metrics():
    pid = pdb.add_position({"symbol": "INFY", "horizon": "SWING", "entry_price": 100.0,
                            "stop_loss": 92.0, "status": "ACTIVE"})
    pdb.close_position(pid, exit_price=115.0, exit_reason="TARGET_HIT")

    full = pi.combined()
    sw = full["books"]["SWING"]["metrics"]
    assert sw["closed_trades"] == 1
    assert sw["hit_rate_pct"] == 100.0
    assert sw["realized_pnl"] > 0
    # equity curve present for charts
    assert len(full["books"]["SWING"]["equity_curve"]) >= 1


def test_set_capital_override_is_live():
    from dashboard.backend.routes.portfolio_intelligence import set_capital, CapitalConfig
    from services.pil import config as pil_config
    r = set_capital(CapitalConfig(capital={"SWING": 2_500_000, "MOMENTUM": 750_000}))
    assert r["capital"]["SWING"] == 2_500_000
    assert r["capital"]["MOMENTUM"] == 750_000
    # override is read live by the config layer
    assert pil_config.book_capital("SWING") == 2_500_000
    # reflected in the combined ledger initial capital
    comp = pi.comparison()
    assert comp["metrics"]["SWING"]["initial_capital"] == 2_500_000


def test_all_metric_keys_present():
    comp = pi.comparison()
    required = {
        "portfolio_value", "invested_capital", "available_cash", "total_return_pct",
        "today_return_pct", "mtd_pct", "qtd_pct", "ytd_pct", "cagr_pct",
        "volatility_pct", "max_drawdown_pct", "sharpe", "sortino", "calmar",
        "risk_score", "hit_rate_pct", "expectancy", "profit_factor",
        "avg_winner", "avg_loser", "win_loss_ratio", "avg_hold_days", "turnover_pct",
    }
    for book in ("SWING", "LONGTERM", "MOMENTUM", "COMBINED"):
        missing = required - set(comp["metrics"][book])
        assert not missing, f"{book} missing {missing}"
