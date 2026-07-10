"""Momentum analytics — metric correctness + attribution (injected data)."""

from __future__ import annotations

import pytest

from services.momentum_analytics import analytics, _perf, _monthly, _attr


def test_perf_block_math():
    p = _perf([3.0, -1.0, 2.0, -1.0])          # 2 wins, 2 losses
    assert p["n"] == 4 and p["win_rate"] == 50.0
    assert p["expectancy_r"] == pytest.approx((3 - 1 + 2 - 1) / 4, abs=0.001)
    assert p["profit_factor"] == pytest.approx(5.0 / 2.0, abs=0.01)   # (3+2)/(1+1)
    assert p["sharpe"] != 0.0 and p["sortino"] != 0.0
    assert p["max_drawdown_r"] <= 0.0


def test_attribution_groups():
    journal = [
        {"r_multiple": 2.0, "sector": "IT", "regime": "TRENDING_UP", "entry_model": "vcp", "risk_model": "hybrid/atr_chandelier"},
        {"r_multiple": -1.0, "sector": "IT", "regime": "TRENDING_UP", "entry_model": "breakout", "risk_model": "hybrid/atr_chandelier"},
        {"r_multiple": 1.5, "sector": "Pharma", "regime": "SIDEWAYS", "entry_model": "vcp", "risk_model": "structural/ema"},
    ]
    a = _attr(journal, "sector")
    assert a["IT"]["n"] == 2 and a["Pharma"]["n"] == 1
    by_model = _attr(journal, "entry_model")
    assert set(by_model.keys()) == {"vcp", "breakout"}


def test_monthly_grouping():
    j = [{"r_multiple": 1.0, "profit_loss_pct": 5, "closed_at": "2026-05-10T00:00:00"},
         {"r_multiple": -1.0, "profit_loss_pct": -3, "closed_at": "2026-05-20T00:00:00"},
         {"r_multiple": 2.0, "profit_loss_pct": 9, "closed_at": "2026-06-02T00:00:00"}]
    m = _monthly(j)
    assert [x["month"] for x in m] == ["2026-05", "2026-06"]
    assert m[0]["trades"] == 2 and m[1]["total_r"] == 2.0


def test_analytics_full_payload():
    journal = [
        {"r_multiple": 3.0, "profit_loss_pct": 18, "days_held": 12, "sector": "IT",
         "regime": "TRENDING_UP", "entry_model": "vcp", "risk_model": "hybrid/atr_chandelier",
         "closed_at": "2026-06-01T00:00:00"},
        {"r_multiple": -1.0, "profit_loss_pct": -5, "days_held": 4, "sector": "Auto",
         "regime": "SIDEWAYS", "entry_model": "breakout", "risk_model": "structural/ema",
         "closed_at": "2026-06-15T00:00:00"},
    ]
    active = [{"entry_price": 100, "initial_stop": 90, "current_price": 115, "profit_loss_pct": 15, "status": "ACTIVE"}]
    out = analytics(journal_provider=lambda: journal, active_provider=lambda: active)
    assert out["realized"]["n"] == 2
    assert out["open"]["positions"] == 1
    assert out["open"]["open_r"] == pytest.approx(1.5, abs=0.01)     # (115-100)/(100-90)
    assert "sector" in out["attribution"] and "risk_model" in out["attribution"]
    assert len(out["monthly"]) == 1                                  # both in 2026-06
