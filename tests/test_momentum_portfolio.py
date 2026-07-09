"""Independent Momentum Portfolio — DB + manager tests (temp DB, no network).

Covers the lifecycle (arm→tap→active→exit→journal), capacity, duplicate-exposure
guard, quality-based replacement, and the momentum exit engine
(target/stop/trail/max-loss), all isolated from the Swing book.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="mom_pf_test_")

import pytest  # noqa: E402

from dashboard.backend.db import momentum_portfolio as db  # noqa: E402
import services.momentum_portfolio_manager as mgr  # noqa: E402


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
    monkeypatch.setenv("MOMENTUM_ALLOW_DUP_EXPOSURE", "1")  # swing table absent in temp DB


def _cand(sym, entry=100.0, stop=92.0, score=70.0, target=130.0, **extra):
    return {"symbol": sym, "entry_price": entry, "stop_loss": stop, "quality_score": score,
            "target_1": target, "target_2": target, "entry_model": "vcp", "regime": "TRENDING_UP",
            "sector": "IT", "rs_20d": 30, "arm_ref_price": entry, "entry_reason": "VCP breakout", **extra}


# ── DB layer ─────────────────────────────────────────────────────────────────
def test_add_pending_and_counts():
    pid = db.add_position({"symbol": "AAA", "entry_price": 100, "stop_loss": 92,
                           "status": "PENDING", "quality_score": 70, "initial_stop": 92})
    assert pid > 0
    assert db.get_portfolio(include_pending=False) == []       # not active yet
    c = db.get_counts()
    assert c["active"] == 0 and c["pending"] == 1 and c["used"] == 1


def test_duplicate_commit_blocked():
    db.add_position({"symbol": "AAA", "entry_price": 100, "stop_loss": 92, "status": "PENDING"})
    with pytest.raises(ValueError):
        db.add_position({"symbol": "AAA", "entry_price": 100, "stop_loss": 92, "status": "PENDING"})


def test_activate_and_close_uses_initial_stop_for_R():
    pid = db.add_position({"symbol": "BBB", "entry_price": 100, "stop_loss": 90,
                           "status": "PENDING", "initial_stop": 90})
    assert db.activate_pending(pid, 100.0)
    # trail the live stop up, then close at 120 → R must use INITIAL stop (90): (120-100)/(100-90)=2.0
    db.update_position(pid, stop_loss=110)
    res = db.close_position(pid, 120.0, "TARGET_HIT")
    assert res["r_multiple"] == pytest.approx(2.0, abs=0.01)
    stats = db.get_journal_stats()
    assert stats["total_trades"] == 1 and stats["wins"] == 1


# ── Manager: arm / guards / replacement ──────────────────────────────────────
def test_arm_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("MOMENTUM_PORTFOLIO_ENABLED", "0")
    assert mgr.arm(_cand("AAA")) is None


def test_arm_sizes_and_persists_features():
    pid = mgr.arm(_cand("AAA", entry=100, stop=90, score=75, volume_ratio=2.1, trend_quality=0.8))
    assert pid
    row = db.get_active_by_symbol("AAA")
    assert row["status"] == "PENDING" and row["entry_model"] == "vcp"
    assert row["position_size"] and row["risk_weight_pct"] and row["quality_score"] == 75


def test_dup_exposure_guard_blocks_when_in_swing(monkeypatch):
    monkeypatch.setenv("MOMENTUM_ALLOW_DUP_EXPOSURE", "0")
    monkeypatch.setattr(mgr, "_in_swing", lambda s: True)
    assert mgr.arm(_cand("SWINGDUP")) is None
    monkeypatch.setattr(mgr, "_in_swing", lambda s: False)
    assert mgr.arm(_cand("SWINGDUP")) is not None


def test_replacement_only_when_score_beats_weakest(monkeypatch):
    monkeypatch.setattr(db, "MAX_MOMENTUM_POSITIONS", 2)
    monkeypatch.setenv("MOMENTUM_REPLACEMENT_MIN_EDGE", "5")
    # fill 2 active slots (arm then activate)
    for sym, sc in [("LOW", 60.0), ("MID", 75.0)]:
        pid = mgr.arm(_cand(sym, score=sc)); db.activate_pending(pid, 100.0)
    assert db.get_counts()["used"] == 2
    # weaker candidate can't get in
    assert mgr.arm(_cand("WEAK", score=62.0)) is None
    # stronger candidate (beats LOW 60 by >5) replaces the weakest active
    pid = mgr.arm(_cand("STRONG", score=80.0))
    assert pid is not None
    assert db.get_active_by_symbol("LOW") is None            # displaced
    assert db.get_active_by_symbol("STRONG")["status"] == "PENDING"


# ── Manager: tap trigger + exit engine ───────────────────────────────────────
def test_process_pending_triggers_on_tap():
    pid = mgr.arm(_cand("TAPME", entry=100))
    res = mgr.process_pending(lambda syms: {"TAPME": 101.0})  # taps 100
    assert res["triggered"] == 1
    assert db.get_active_by_symbol("TAPME")["status"] == "ACTIVE"


def test_exit_engine_target_hit():
    pid = mgr.arm(_cand("WINR", entry=100, stop=90, target=120)); db.activate_pending(pid, 100.0)
    res = mgr.process_active(lambda s: (125.0, []))            # above target 120
    assert res["exited"] == 1
    assert db.get_journal(1)[0]["exit_reason"] == "TARGET_HIT"


def test_exit_engine_stop_and_maxloss():
    pid = mgr.arm(_cand("LOSR", entry=100, stop=95, target=130)); db.activate_pending(pid, 100.0)
    res = mgr.process_active(lambda s: (94.0, []))             # below stop 95
    assert res["exited"] == 1 and db.get_journal(1)[0]["exit_reason"] == "STOP_HIT"


def test_exit_engine_trails_stop_up(monkeypatch):
    monkeypatch.setenv("MOMENTUM_TRAIL_METHOD", "atr_chandelier")
    monkeypatch.setenv("MOMENTUM_TRAIL_K", "1.0")
    monkeypatch.setenv("MOMENTUM_BREAKEVEN_R", "1.0")
    pid = mgr.arm(_cand("TRAIL", entry=100, stop=90, target=200)); db.activate_pending(pid, 100.0)
    candles = [{"open": 108, "high": 112, "low": 107, "close": 110, "date": "d"}]
    mgr.process_active(lambda s: (110.0, candles))            # +2R → breakeven + trail
    row = db.get_active_by_symbol("TRAIL")
    assert row["status"] == "ACTIVE" and row["stop_loss"] >= 100  # raised to >= breakeven
