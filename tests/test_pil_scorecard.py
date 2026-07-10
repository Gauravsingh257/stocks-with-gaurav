"""
tests/test_pil_scorecard.py
===========================
Tests for the engine scorecard generator (services/pil/scorecard.py): the pure
attribution / ranking-quality / quality-score helpers on synthetic journals, plus
an integration pass that seeds the real Swing DB and asserts the funnel + realised
performance block.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pil_sc_test_")

import pytest  # noqa: E402

from services.pil import scorecard as sc  # noqa: E402


def _j(symbol, pnl_pct, *, sector=None, entry_model=None, regime=None, conviction=None,
       closed="2026-07-05"):
    d = {"symbol": symbol, "profit_loss_pct": pnl_pct, "closed_at": closed,
         "exit_reason": "TARGET_HIT" if pnl_pct > 0 else "STOP_HIT"}
    if sector: d["sector"] = sector
    if entry_model: d["entry_model"] = entry_model
    if regime: d["regime"] = regime
    if conviction is not None: d["quality_score"] = conviction
    return d


def test_attr_by_entry_model_and_best_worst():
    journal = [
        _j("A", 10, entry_model="vcp"), _j("B", 6, entry_model="vcp"),
        _j("C", -4, entry_model="breakout"), _j("D", -8, entry_model="breakout"),
    ]
    attr = sc._attr(journal, "entry_model")
    assert attr["vcp"]["n"] == 2
    assert attr["vcp"]["avg_pnl_pct"] == pytest.approx(8.0)
    best, worst = sc._best_worst(attr)
    assert best["name"] == "vcp"
    assert worst["name"] == "breakout"


def test_attr_by_sector_uses_reference_map():
    journal = [_j("HDFCBANK", 5), _j("ICICIBANK", 3), _j("TCS", -2)]
    attr = sc._attr(journal, "", symbol_sector=True)
    assert "Banking" in attr and attr["Banking"]["n"] == 2
    assert "IT" in attr


def test_ranking_quality_positive_when_conviction_predicts():
    journal = [_j(f"S{i}", pnl, conviction=conv)
               for i, (conv, pnl) in enumerate([(90, 12), (80, 8), (70, 4), (60, -2), (50, -6)])]
    rq = sc._ranking_quality(journal)
    assert rq is not None and rq > 0.8


def test_engine_quality_score_bounds():
    assert 0 <= sc._engine_quality_score(0, 0, -10, -1) <= 100
    assert sc._engine_quality_score(70, 3.0, 8, 0.8) > sc._engine_quality_score(30, 1.0, 1, 0.0)


def test_replacement_efficiency_none_without_replace_exits():
    assert sc._replacement_efficiency([_j("A", 5)]) is None
    j = [{"symbol": "X", "profit_loss_pct": -3, "exit_reason": "REPLACED_BY_BETTER"}]
    assert sc._replacement_efficiency(j) == pytest.approx(-3.0)


# ── integration ──────────────────────────────────────────────────────────────

def test_generate_swing_scorecard_integration(monkeypatch):
    monkeypatch.setenv("PIL_CAPITAL_SWING", "1000000")
    from dashboard.backend.db import portfolio as pdb
    pdb.init_portfolio_db()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_journal"); c.execute("DELETE FROM portfolio_positions"); c.commit()
    finally:
        c.close()

    for sym, exitp in [("HDFCBANK", 115.0), ("TCS", 90.0), ("INFY", 120.0)]:
        pid = pdb.add_position({"symbol": sym, "horizon": "SWING", "entry_price": 100.0,
                                "stop_loss": 92.0, "status": "ACTIVE", "confidence_score": 70.0})
        pdb.close_position(pid, exit_price=exitp, exit_reason="TARGET_HIT" if exitp > 100 else "STOP_HIT")

    card = sc.generate("SWING", scope="monthly", period="2026-07")
    assert card["book"] == "SWING"
    assert card["funnel"]["closed"] == 3
    assert card["performance"]["closed_trades"] == 3
    assert card["performance"]["hit_rate_pct"] == pytest.approx(66.7, abs=0.5)  # 2 of 3 win
    # sector attribution present (HDFCBANK/ICICI Banking, TCS/INFY IT)
    assert card["attribution"]["best_sector"] is not None
    assert 0 <= card["quality"]["engine_quality_score"] <= 100
