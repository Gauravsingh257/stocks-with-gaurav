"""Risk Engine Dashboard: audit aggregation + config version history (read-only)."""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="risk_dash_")

import pytest  # noqa: E402

from dashboard.backend.services import risk_audit  # noqa: E402
from dashboard.backend.db import risk_config  # noqa: E402


# ── config version history ────────────────────────────────────────────────────
def test_config_history_records_only_on_change():
    base = {"MAX_STOP_PCT": 10.0, "RISK_PER_TRADE_PCT": 1.0}
    r1 = risk_config.record_config_change(base, source="auto")
    assert r1["recorded"] is True                       # first snapshot
    r2 = risk_config.record_config_change(dict(base), source="auto")
    assert r2["recorded"] is False                      # unchanged → no new version
    r3 = risk_config.record_config_change({**base, "MAX_STOP_PCT": 12.0}, source="auto")
    assert r3["recorded"] is True
    assert r3["entry"]["changes"]["MAX_STOP_PCT"] == [10.0, 12.0]   # diff captured


def test_config_history_manual_note_always_records():
    cfg = {"MAX_STOP_PCT": 12.0, "RISK_PER_TRADE_PCT": 1.0}
    risk_config.record_config_change(cfg, source="auto")
    r = risk_config.record_config_change(cfg, reason="Q3 review", source="manual")
    assert r["recorded"] is True and r["entry"]["reason"] == "Q3 review"
    hist = risk_config.get_config_history(10)
    assert hist[0]["reason"] == "Q3 review" and hist[0]["source"] == "manual"


# ── audit aggregation ─────────────────────────────────────────────────────────
def _promos():
    return [
        # accepted, risk-sized + liquidity-adjusted
        {"symbol": "A", "horizon": "SWING", "accepted": True, "reason": "accept_risk_sized",
         "stop_width_pct": 5.0, "old_accepted": True, "old_position_value": 50000,
         "new_position_value": 200000, "risk_weight_pct": 20.0, "liquidity_factor": 0.8, "atr_factor": 1.0},
        # accepted, no adjustment
        {"symbol": "B", "horizon": "SWING", "accepted": True, "reason": "accept_risk_sized",
         "stop_width_pct": 4.0, "old_accepted": True, "old_position_value": 50000,
         "new_position_value": 250000, "risk_weight_pct": 25.0, "liquidity_factor": 1.0, "atr_factor": 1.0},
        # rejected by stop cap; legacy WOULD have taken it
        {"symbol": "ONMOBILE", "horizon": "SWING", "accepted": False, "reason": "stop_too_wide(39.2%>10%)",
         "stop_width_pct": 39.2, "old_accepted": True, "old_position_value": 50000,
         "new_position_value": 0, "risk_weight_pct": 0.0},
    ]


def test_daily_summary_counts(monkeypatch):
    monkeypatch.setattr(risk_audit, "read_decisions",
                        lambda kind, day: _promos() if kind == "promotions" else
                        [{"symbol": "C", "cmp": 90, "dma200": 100, "rs_vs_nifty": -5, "days_held": 12}])
    monkeypatch.setattr(risk_audit, "_portfolio_snapshot", lambda: {"active_positions": 20, "portfolio_heat_pct": 12.3})

    s = risk_audit.daily_summary("2026-07-09")
    p = s["promotions"]
    assert p["total"] == 3 and p["accepted"] == 2 and p["rejected"] == 1
    assert p["stop_cap_rejections"] == 1
    assert p["sizing_adjustments"] == 2         # both accepted changed size vs equal-weight
    assert p["liquidity_adjustments"] == 1      # only A had a factor < 1
    assert s["exits"]["trend_break"] == 1
    cf = s["counterfactual"]
    assert cf["legacy_would_accept"] == 3 and cf["new_accepted"] == 2
    assert cf["rejected_by_new_that_legacy_took"] == 1
    assert s["portfolio"]["portfolio_heat_pct"] == 12.3


def test_daily_summary_empty_is_safe(monkeypatch):
    monkeypatch.setattr(risk_audit, "read_decisions", lambda kind, day: [])
    monkeypatch.setattr(risk_audit, "_portfolio_snapshot", lambda: {})
    s = risk_audit.daily_summary("2026-07-09")
    assert s["promotions"]["total"] == 0 and s["exits"]["trend_break"] == 0
