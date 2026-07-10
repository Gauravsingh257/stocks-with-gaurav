"""
tests/test_pil_analytics.py
===========================
Tests for combined analytics (services/pil/analytics.py) and capital allocation
(services/pil/allocation.py) on hand-built ledger maps.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pil_an_test_")

import pytest  # noqa: E402

from services.pil import analytics as an  # noqa: E402
from services.pil import allocation as al  # noqa: E402


def _curve(vals):
    return [{"date": f"2026-01-{i + 1:02d}", "value": v} for i, v in enumerate(vals)]


def _books():
    return {
        "SWING": {"equity_curve": _curve([100.0, 101.0, 102.0, 103.0]),
                  "total_pnl": 3000.0, "portfolio_value": 1_030_000.0, "initial_capital": 1_000_000.0},
        "LONGTERM": {"equity_curve": _curve([100.0, 100.0, 100.0, 100.0]),
                     "total_pnl": 0.0, "portfolio_value": 1_000_000.0, "initial_capital": 1_000_000.0},
        "MOMENTUM": {"equity_curve": _curve([100.0, 99.0, 98.0, 97.0]),
                     "total_pnl": -1000.0, "portfolio_value": 490_000.0, "initial_capital": 500_000.0},
        "COMBINED": {"equity_curve": _curve([300.0, 300.0, 300.0, 300.0]),
                     "total_pnl": 2000.0, "portfolio_value": 2_520_000.0, "initial_capital": 2_500_000.0},
    }


def test_contribution_ranks_swing_top():
    c = an.contribution(_books())
    assert c["top_contributor"] == "SWING"
    swing = next(r for r in c["rows"] if r["book"] == "SWING")
    assert swing["pnl"] == pytest.approx(3000.0)


def test_what_if_all_swing_matches_swing():
    b = _books()
    res = an.what_if(b, {"SWING": 1.0, "LONGTERM": 0.0, "MOMENTUM": 0.0}, rf=0.0)
    assert res["weights"]["SWING"] == pytest.approx(1.0)
    assert res["ann_return_pct"] > 0            # swing rises
    flat = an.what_if(b, {"LONGTERM": 1.0}, rf=0.0)
    assert flat["ann_return_pct"] == pytest.approx(0.0)
    assert flat["ann_vol_pct"] == pytest.approx(0.0)


def test_what_if_normalises_weights():
    res = an.what_if(_books(), {"SWING": 2, "LONGTERM": 1, "MOMENTUM": 1}, rf=0.0)
    assert sum(res["weights"].values()) == pytest.approx(1.0, abs=1e-6)


def test_optimal_allocation_returns_valid_weights():
    o = an.optimal_allocation(_books(), rf=0.0, step=0.25)
    assert o["max_sharpe"] is not None
    w = o["max_sharpe"]["weights"]
    assert sum(w.values()) == pytest.approx(1.0, abs=0.01)


def test_diversification_benefit_present(monkeypatch):
    monkeypatch.setattr(an.pil_config, "all_book_capital",
                        lambda: {"SWING": 1_000_000.0, "LONGTERM": 1_000_000.0, "MOMENTUM": 500_000.0})
    d = an.diversification_benefit(_books(), rf=0.0)
    assert "combined_vol_pct" in d and "weighted_avg_vol_pct" in d
    assert set(d["weights"]) == {"SWING", "LONGTERM", "MOMENTUM"}


# ── allocation ───────────────────────────────────────────────────────────────

def test_allocation_current_vs_target(monkeypatch):
    monkeypatch.setattr(al.pil_config, "allocation_targets",
                        lambda: {"SWING": 0.60, "LONGTERM": 0.25, "MOMENTUM": 0.15})
    a = al.compute(_books())
    total = a["total_value"]
    assert total == pytest.approx(1_030_000 + 1_000_000 + 490_000)
    swing = next(r for r in a["rows"] if r["book"] == "SWING")
    # current ~0.41 vs target 0.60 -> under-allocated, action ADD
    assert swing["action"] == "ADD"
    assert a["rebalance_needed"] is True


def test_set_targets_normalises_and_persists(monkeypatch):
    out = al.set_targets({"SWING": 50, "LONGTERM": 30, "MOMENTUM": 20})
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)
    assert out["SWING"] == pytest.approx(0.5, abs=1e-6)


def test_set_targets_rejects_zero():
    with pytest.raises(ValueError):
        al.set_targets({"SWING": 0, "LONGTERM": 0, "MOMENTUM": 0})
