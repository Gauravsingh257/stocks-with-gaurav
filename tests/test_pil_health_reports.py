"""
tests/test_pil_health_reports.py
================================
Tests for portfolio health scoring (services/pil/health.py) and the report
builders (services/pil/reports.py).
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pil_hr_test_")

import pytest  # noqa: E402

from services.pil import health as h  # noqa: E402
from services.pil import reports as rep  # noqa: E402


def _good():
    ledger = {"positions": [{"weight_pct": 8.0}], "open_positions": 8}
    m = {"hit_rate_pct": 70, "profit_factor": 3.0, "expectancy_pct": 5, "risk_score": 20,
         "max_drawdown_pct": -5, "mtd_pct": 5, "closed_trades": 30}
    return ledger, m


def _bad():
    ledger = {"positions": [{"weight_pct": 40.0}], "open_positions": 1}
    m = {"hit_rate_pct": 20, "profit_factor": 0.5, "expectancy_pct": -5, "risk_score": 90,
         "max_drawdown_pct": -40, "mtd_pct": -10, "closed_trades": 0}
    return ledger, m


def test_status_thresholds():
    assert h._status(80) == "GREEN"
    assert h._status(55) == "YELLOW"
    assert h._status(30) == "RED"


def test_healthy_book_is_green():
    led, m = _good()
    r = h.health_for_book("SWING", led, m)
    assert r["status"] == "GREEN"
    assert r["overall"] >= 70
    assert set(r["sub_scores"]) >= {"quality", "risk", "drawdown", "momentum", "concentration"}


def test_unhealthy_book_is_red():
    led, m = _bad()
    r = h.health_for_book("SWING", led, m)
    assert r["status"] == "RED"
    assert r["overall"] < 45


def test_combined_extra_adds_liquidity_and_replacement():
    led, m = _good()
    r = h.health_for_book("COMBINED", led, m, {"liquidity": 80.0, "replacement_pressure": 75.0})
    assert "liquidity" in r["sub_scores"]
    assert "replacement_pressure" in r["sub_scores"]


# ── reports ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _seeded(monkeypatch):
    from dashboard.backend.db import portfolio as pdb
    from dashboard.backend.db import momentum_portfolio as mdb
    pdb.init_portfolio_db(); mdb.init_momentum_db()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        for t in ("portfolio_journal", "portfolio_positions", "momentum_journal", "momentum_positions"):
            c.execute(f"DELETE FROM {t}")
        c.commit()
    finally:
        c.close()
    pid = pdb.add_position({"symbol": "HDFCBANK", "horizon": "SWING", "entry_price": 100.0,
                            "stop_loss": 92.0, "status": "ACTIVE"})
    pdb.close_position(pid, exit_price=115.0, exit_reason="TARGET_HIT")
    monkeypatch.setenv("PIL_CAPITAL_SWING", "1000000")


def test_build_daily_structure(_seeded):
    r = rep.build_daily("2026-07-10")
    assert r["kind"] == "daily"
    for key in ("portfolio_summary", "engine_summary", "sector_exposure",
                "risk_warnings", "portfolio_health", "cash_position"):
        assert key in r
    # summary text renders
    txt = rep.summary_text(r)
    assert "PIL Daily" in txt


def test_build_monthly_has_html(_seeded):
    r = rep.build_monthly("2026-07")
    assert r["kind"] == "monthly"
    assert "html" in r and "<html" in r["html"].lower()
    assert "Performance" in r["html"]
    assert r["period"] in r["html"]
    # win/loss distribution present
    assert "buckets" in r["win_distribution"]


def test_monthly_svg_equity_renders():
    curve = [{"date": f"2026-07-{i+1:02d}", "value": 100 + i} for i in range(10)]
    svg = rep._svg_equity(curve)
    assert svg.startswith("<svg") and "polyline" in svg
