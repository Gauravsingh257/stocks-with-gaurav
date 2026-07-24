"""PR3 tests — immutable ledger, entry-state classifier, confidence v2."""

from __future__ import annotations

import importlib

import pytest

from services.entry_state import (
    IN_MOTION, MISSED, READY, WATCH, classify_entry_state, is_actionable,
)
from utils.confidence_v2 import (
    compute_confidence_v2, confidence_v2_enabled, risk_quality_score,
)


# ── immutable, survivorship-free ledger ───────────────────────────────────────

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    from dashboard.backend.db import schema
    monkeypatch.setattr(schema, "DB_PATH", str(tmp_path / "t.db"))
    tr = importlib.import_module("dashboard.backend.db.track_record")
    monkeypatch.setenv("TRACK_RECORD_LEDGER_ENABLED", "1")
    return tr


def _pub(tr, rec_id, sym, entry=100.0, stop=95.0, target=115.0, conf=60.0):
    tr.publish(rec_id, {"symbol": sym, "agent_type": "SWING", "setup": "SMC_SWING",
                        "entry_price": entry, "stop_loss": stop, "targets": [target],
                        "confidence_score": conf, "scan_cmp": entry})


def test_ledger_survivorship_free_winrate(ledger):
    tr = ledger
    for i, s in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        _pub(tr, i + 1, s)
    tr.resolve(1, "TARGET_HIT", exit_price=115.0, pnl_pct=15.0, pnl_r=3.0)
    tr.resolve(2, "STOP_HIT", exit_price=95.0, pnl_pct=-5.0, pnl_r=-1.0)
    tr.resolve(3, "EXPIRED")        # timed out — the survivorship case
    # rec 4 stays OPEN

    st = tr.stats("SWING")
    assert st["total_published"] == 4
    assert st["resolved"] == 3 and st["open"] == 1
    assert st["target_hit"] == 1 and st["stop_hit"] == 1 and st["expired"] == 1
    # Win rate is 1/3 (incl. expired), NOT 1/2 — expired ideas are not dropped.
    assert st["win_rate_pct"] == 33.3


def test_ledger_outcome_is_write_once(ledger):
    tr = ledger
    _pub(tr, 10, "ZZZ")
    tr.resolve(10, "TARGET_HIT", exit_price=115.0, pnl_pct=15.0, pnl_r=3.0)
    tr.resolve(10, "STOP_HIT", exit_price=95.0, pnl_pct=-5.0, pnl_r=-1.0)  # must NOT overwrite
    row = [r for r in tr.rows("SWING") if r["recommendation_id"] == 10][0]
    assert row["outcome"] == "TARGET_HIT" and row["pnl_pct"] == 15.0


def test_ledger_publish_idempotent(ledger):
    tr = ledger
    _pub(tr, 20, "IDEM", conf=60.0)
    _pub(tr, 20, "IDEM", conf=99.0)  # re-publish must not duplicate or overwrite
    rows = [r for r in tr.rows("SWING") if r["recommendation_id"] == 20]
    assert len(rows) == 1 and rows[0]["confidence_score"] == 60.0


def test_ledger_rr_planned_recorded(ledger):
    tr = ledger
    _pub(tr, 30, "RRR", entry=100, stop=95, target=115)  # risk 5, reward 15 → RR 3
    row = [r for r in tr.rows("SWING") if r["recommendation_id"] == 30][0]
    assert row["rr_planned"] == 3.0


# ── entry-state classifier ────────────────────────────────────────────────────

def test_entry_state_ready_at_zone():
    r = classify_entry_state(cmp=100.5, entry=100, stop=95, targets=[130])
    assert r["state"] == READY and r["actionable"] is True


def test_entry_state_ready_slightly_below():
    r = classify_entry_state(cmp=97, entry=100, stop=90, targets=[130])
    assert r["state"] == READY


def test_entry_state_watch_above_but_slow():
    # +4% (past READY band) but fav_R 0.4 and progress 0.13 → still WATCH
    r = classify_entry_state(cmp=104, entry=100, stop=90, targets=[130])
    assert r["state"] == WATCH and r["actionable"] is True


def test_entry_state_in_motion():
    r = classify_entry_state(cmp=106, entry=100, stop=98, targets=[130])
    assert r["state"] == IN_MOTION and r["actionable"] is False


def test_entry_state_missed_by_progress():
    r = classify_entry_state(cmp=115, entry=100, stop=95, targets=[130])  # 50% to target
    assert r["state"] == MISSED and is_actionable(MISSED) is False


def test_entry_state_handles_missing_data():
    r = classify_entry_state(cmp=None, entry=100)
    assert r["state"] == WATCH and r["entry_gap_pct"] is None


# ── confidence v2 ─────────────────────────────────────────────────────────────

def test_confidence_v2_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CONFIDENCE_V2_ENABLED", raising=False)
    assert confidence_v2_enabled() is False


def test_confidence_v2_all_equal_is_that_value():
    dims = {d: 80 for d in ("trend", "momentum", "smc", "volume", "sector", "regime", "risk", "freshness")}
    out = compute_confidence_v2(dims)
    assert out["composite"] == 80.0
    assert abs(sum(v["weight"] for v in out["breakdown"].values()) - 1.0) < 1e-6


def test_confidence_v2_partial_renormalizes():
    out = compute_confidence_v2({"smc": 90, "momentum": 60})
    # only two dims → weights renormalise to sum 1; composite between the two
    assert 60 <= out["composite"] <= 90
    assert abs(sum(v["weight"] for v in out["breakdown"].values()) - 1.0) < 1e-6


def test_confidence_v2_empty():
    assert compute_confidence_v2({})["composite"] == 0.0


def test_risk_quality_monotonic():
    assert risk_quality_score(1.0) < risk_quality_score(2.0) < risk_quality_score(3.0)
    assert risk_quality_score(0) == 0.0
    assert risk_quality_score(10) <= 100.0
