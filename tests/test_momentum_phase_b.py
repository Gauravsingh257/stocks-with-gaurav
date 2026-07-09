"""Momentum Portfolio Phase B — classifier, re-scoring, quality replacement, and
the MomentumPortfolioManager.run() orchestrator (temp DB, injected providers)."""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="mom_b_test_")

import pytest  # noqa: E402

from dashboard.backend.db import momentum_portfolio as db  # noqa: E402
import services.momentum_portfolio_manager as mgr  # noqa: E402
from services import momentum_classifier as clf  # noqa: E402
from services.momentum_portfolio_manager import MomentumPortfolioManager  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    db.init_momentum_db()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM momentum_journal"); c.execute("DELETE FROM momentum_positions"); c.commit()
    finally:
        c.close()
    monkeypatch.setenv("MOMENTUM_PORTFOLIO_ENABLED", "1")
    monkeypatch.setenv("MOMENTUM_ALLOW_DUP_EXPOSURE", "1")
    monkeypatch.setattr(mgr, "_in_swing", lambda s: False)


def _cand(sym, entry=100.0, stop=92.0, score=70.0, sector="IT", **e):
    return {"symbol": sym, "entry_price": entry, "stop_loss": stop, "quality_score": score,
            "target_1": None, "entry_model": "vcp", "regime": "TRENDING_UP", "sector": sector,
            "rs_20d": 30, "arm_ref_price": entry, "entry_reason": "VCP", **e}


# ── Classification ───────────────────────────────────────────────────────────
def test_classify_thresholds():
    assert clf.classify(85) == "ELITE"
    assert clf.classify(70) == "GOOD"
    assert clf.classify(55) == "WEAK"
    assert clf.classify(30) == "REPLACE"


def test_portfolio_quality_penalises_concentration():
    all_it = [{"status": "ACTIVE", "quality_score": 80, "sector": "IT"} for _ in range(3)]
    diverse = [{"status": "ACTIVE", "quality_score": 80, "sector": s} for s in ("IT", "Pharma", "Auto")]
    q_conc = clf.portfolio_quality(all_it)
    q_div = clf.portfolio_quality(diverse)
    assert q_conc["sector_penalty"] > 0 and q_div["sector_penalty"] == 0
    assert q_div["quality"] > q_conc["quality"]


def test_best_replacement_prefers_diversification():
    # book: 3 IT holdings (concentrated). A Pharma candidate of equal score should
    # be able to replace an IT holding because it improves diversification.
    active = [{"id": i, "status": "ACTIVE", "quality_score": 70, "sector": "IT",
               "symbol": f"IT{i}", "entry_price": 100, "current_price": 100} for i in range(3)]
    tgt = clf.best_replacement_target(_cand("PH", score=70, sector="Pharma"), active)
    assert tgt is not None and tgt["sector"] == "IT"          # displaces a concentrated IT name
    # a same-sector, lower-score candidate should NOT displace anything
    assert clf.best_replacement_target(_cand("IT9", score=60, sector="IT"), active) is None


# ── Re-scoring ───────────────────────────────────────────────────────────────
def _leader_candles():
    cs = []
    for i in range(210):
        p = 100 + (180 - 100) * (i / 209)
        cs.append({"open": p * 0.999, "high": p * 1.012, "low": p * 0.988, "close": p,
                   "volume": 1_000_000, "date": f"d{i}"})
    return cs


def test_rescore_active_classifies_and_persists():
    pid = mgr.arm(_cand("LEAD", entry=180, stop=165)); db.activate_pending(pid, 180.0)
    res = mgr.rescore_active(lambda s: (180.0, _leader_candles()))
    assert res["reclassified"] == 1
    row = db.get_active_by_symbol("LEAD")
    assert row["classification"] in ("ELITE", "GOOD", "WEAK", "REPLACE")


# ── Orchestrator ─────────────────────────────────────────────────────────────
def test_run_disabled_returns_status(monkeypatch):
    monkeypatch.setenv("MOMENTUM_PORTFOLIO_ENABLED", "0")
    assert MomentumPortfolioManager().run() == {"status": "disabled"}


def test_run_arms_candidates_and_reports(monkeypatch):
    cands = [_cand("AAA", score=80, sector="IT"), _cand("BBB", score=72, sector="Pharma")]
    m = MomentumPortfolioManager(
        cmp_provider=lambda syms: {},            # nothing taps
        data_provider=lambda s: (None, []),      # no active holdings to process
        candidate_provider=lambda: cands,
    )
    report = m.run()
    assert report["candidates_evaluated"] == 2
    assert len(report["armed"]) == 2
    assert db.get_active_by_symbol("AAA")["status"] == "PENDING"
    assert "portfolio_quality" in report and "counts" in report


def test_run_full_book_quality_replacement(monkeypatch):
    monkeypatch.setattr(db, "MAX_MOMENTUM_POSITIONS", 2)
    monkeypatch.setenv("MOM_REPLACE_MIN_QUALITY_GAIN", "1")
    # fill 2 IT actives (concentrated)
    for s in ("IT1", "IT2"):
        pid = mgr.arm(_cand(s, score=68, sector="IT")); db.activate_pending(pid, 100.0)
    # a strong Pharma candidate should displace one IT (diversification + score)
    m = MomentumPortfolioManager(cmp_provider=lambda x: {}, data_provider=lambda s: (None, []),
                                 candidate_provider=lambda: [_cand("PH", score=78, sector="Pharma")])
    report = m.run()
    assert len(report["armed"]) == 1
    assert db.get_active_by_symbol("PH")["status"] == "PENDING"
    armed = db.get_active_by_symbol("PH")
    assert armed["replacement_reason"] and "quality-swap" in armed["replacement_reason"]
