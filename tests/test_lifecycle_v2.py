"""Lifecycle ledger v2 — chaining, expanded states, snapshots, analytics, bus.

The research idea is the FIRST stage of a trade's life, not something a later
portfolio row replaces. Keeping it is what makes "200 ideas -> 55 entries ->
30 targets" answerable; reporting only the 55 trades would silently discard the
funnel that shows how selective the engine actually is.
"""

from __future__ import annotations

import json
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="lifecycle_v2_")

import pytest  # noqa: E402

from dashboard.backend.db.schema import get_connection  # noqa: E402
from dashboard.backend.db.portfolio import init_portfolio_db, add_position, close_position  # noqa: E402
from dashboard.backend.db.trade_lifecycle import (  # noqa: E402
    init_lifecycle_db, upsert, STATUSES, RECORD_STATES, CLOSED_STATUSES, EXECUTED_STATUSES,
)
from dashboard.backend.db.trade_lifecycle_migrate import backfill, ENGINE_VERSIONS  # noqa: E402
from dashboard.backend.db.trade_lifecycle_query import query, stats, timeline  # noqa: E402
from dashboard.backend.db.lifecycle_analytics import analytics, snapshot_stats, stats_history  # noqa: E402


def _wipe():
    c = get_connection()
    try:
        for t in ("trade_lifecycle", "trade_lifecycle_events", "lifecycle_stats_snapshots",
                  "portfolio_journal", "portfolio_positions", "stock_recommendations"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        c.commit()
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _fresh():
    try:
        from dashboard.backend.db.schema import init_db
        init_db()
    except Exception:
        pass
    init_portfolio_db()
    init_lifecycle_db()
    _wipe()
    yield
    _wipe()


def _idea(symbol, status="ACTIVE"):
    c = get_connection()
    try:
        c.execute(
            "INSERT INTO stock_recommendations (symbol, agent_type, status, entry_price, "
            "stop_loss, targets, confidence_score, setup, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
            (symbol, "SWING", status, 100.0, 90.0, "[130.0]", 72.0, "SMC_BOS"),
        )
        c.commit()
    finally:
        c.close()


def _trade(symbol, entry, exit_px, reason="TARGET_HIT"):
    pid = add_position({"symbol": symbol, "horizon": "SWING", "entry_price": entry,
                        "stop_loss": entry * 0.9, "target_1": entry * 1.2, "status": "ACTIVE"})
    close_position(pid, exit_px, reason)
    return pid


# ── 1. Research is preserved as stage one ────────────────────────────────────

def test_research_idea_is_kept_as_first_stage_not_discarded():
    _idea("NSE:CHAINED")
    _trade("NSE:CHAINED", 100.0, 120.0, "TARGET_HIT")
    backfill()

    rows = query(limit=20, symbol="CHAINED")["items"]
    stages = {r["stage"] for r in rows}
    assert "IDEA" in stages, "the research idea must survive as stage one"
    assert "POSITION" in stages, "the portfolio row is a separate stage"
    idea = next(r for r in rows if r["stage"] == "IDEA")
    pos = next(r for r in rows if r["stage"] == "POSITION")
    assert idea["status"] == "ENTRY_TRIGGERED"
    assert pos["status"] == "TARGET_HIT", "the outcome lives on the position row"


def test_funnel_reports_ideas_entries_and_targets_separately():
    for i in range(10):
        _idea(f"NSE:I{i}")
    _trade("NSE:I0", 100.0, 120.0, "TARGET_HIT")
    _trade("NSE:I1", 100.0, 90.0, "STOP_HIT")
    backfill()

    s = stats()
    assert s["ideas_generated"] == 10, "all 10 ideas must remain visible"
    assert s["positions_taken"] == 2, "only 2 became positions"
    assert s["target_hits"] == 1
    assert s["idea_to_entry_pct"] == 20.0
    assert s["closed_trades"] == 2, "win rate still measures real trades only"


# ── 2. Expanded lifecycle states ─────────────────────────────────────────────

def test_expanded_states_exist_and_are_classified():
    for s in ("TARGET1_HIT", "TARGET2_HIT", "TARGET3_HIT", "PARTIAL_EXIT",
              "TRAILING_SL", "BREAKEVEN", "MANUAL_CLOSED", "TIME_EXIT",
              "FORCED_EXIT", "CANCELLED", "IDEA_GENERATED"):
        assert s in STATUSES, f"{s} must be a supported lifecycle state"
    for s in ("TARGET1_HIT", "TARGET2_HIT", "BREAKEVEN", "TRAILING_SL", "PARTIAL_EXIT"):
        assert s in EXECUTED_STATUSES and s not in CLOSED_STATUSES, \
            f"{s} means the position is still live, not finished"
    for s in ("TIME_EXIT", "FORCED_EXIT"):
        assert s in CLOSED_STATUSES


def test_rule_based_exits_map_to_distinct_terminal_states():
    _trade("NSE:STALE", 100.0, 101.0, "STALE_EXIT")
    _trade("NSE:BROKE", 100.0, 97.0, "STRUCTURE_BREAK")
    backfill()
    got = {r["symbol"].replace("NSE:", ""): r["status"] for r in query(limit=50)["items"]}
    assert got["STALE"] == "TIME_EXIT", "a time-stop is not a manual close"
    assert got["BROKE"] == "FORCED_EXIT", "a structure break is a forced exit"


# ── 3. Engine attribution + immutable snapshots ──────────────────────────────

def test_engine_version_and_recommendation_snapshot_are_stored():
    _idea("NSE:SNAP")
    _trade("NSE:SNAP2", 100.0, 120.0, "TARGET_HIT")
    backfill()

    idea = query(limit=10, symbol="SNAP", stage="IDEA")["items"][0]
    assert idea["engine_version"] == ENGINE_VERSIONS["SMC"]
    payload = json.loads(idea["recommendation_json"])
    assert payload["setup"] == "SMC_BOS" and payload["confidence"] == 72.0, \
        "the recommendation must be reproducible exactly as published"

    pos = query(limit=10, symbol="SNAP2", stage="POSITION")["items"][0]
    assert pos["engine_version"] == ENGINE_VERSIONS["SMC"]
    assert json.loads(pos["context_json"]) is not None


def test_can_filter_by_engine_version():
    _trade("NSE:EV", 100.0, 120.0, "TARGET_HIT")
    backfill()
    assert query(limit=10, engine_version=ENGINE_VERSIONS["SMC"])["total"] >= 1
    assert query(limit=10, engine_version="SMC v0.0-nonexistent")["total"] == 0


# ── 4. Soft delete ───────────────────────────────────────────────────────────

def test_soft_delete_hides_without_deleting():
    assert "ARCHIVED" in RECORD_STATES and "HIDDEN" in RECORD_STATES
    _trade("NSE:SOFT", 100.0, 120.0, "TARGET_HIT")
    backfill()
    uid = query(limit=5, symbol="SOFT")["items"][0]["uuid"]

    c = get_connection()
    try:
        c.execute("UPDATE trade_lifecycle SET record_state='ARCHIVED' WHERE uuid=?", (uid,))
        c.commit()
    finally:
        c.close()

    assert query(limit=5, symbol="SOFT")["total"] == 0, "archived rows leave the default view"
    assert query(limit=5, symbol="SOFT", record_state="ARCHIVED")["total"] == 1
    assert query(limit=5, symbol="SOFT", record_state="ALL")["total"] == 1

    c = get_connection()
    try:
        n = c.execute("SELECT COUNT(*) n FROM trade_lifecycle WHERE uuid=?", (uid,)).fetchone()["n"]
    finally:
        c.close()
    assert n == 1, "the row must still exist — nothing is ever deleted"


# ── 5. Derived analytics ─────────────────────────────────────────────────────

def test_analytics_computes_the_full_metric_set():
    for i in range(6):
        _trade(f"NSE:A{i}", 100.0, 115.0 if i < 4 else 94.0,
               "TARGET_HIT" if i < 4 else "STOP_HIT")
    backfill()

    a = analytics()
    assert a["closed_trades"] == 6
    assert a["win_rate_pct"] == pytest.approx(66.7, abs=0.2)
    assert a["profit_factor"] > 1
    assert a["expectancy_pct"] > 0
    assert a["payoff_ratio"] is not None
    assert a["max_drawdown_pct"] <= 0
    for k in ("sharpe", "sortino", "recovery_factor", "avg_mae_pct", "avg_mfe_pct",
              "avg_time_to_target_days", "avg_time_to_stop_days"):
        assert k in a, f"{k} must be reported"


def test_unexecuted_ideas_never_enter_analytics():
    _trade("NSE:REAL", 100.0, 120.0, "TARGET_HIT")
    for i in range(15):
        _idea(f"NSE:GHOST{i}", status="TARGET_HIT")
    backfill()
    assert analytics()["closed_trades"] == 1, \
        "ideas that never filled must not flatter expectancy or profit factor"


# ── 6. Stats snapshots ───────────────────────────────────────────────────────

def test_snapshots_are_written_and_readable():
    _trade("NSE:SNAPX", 100.0, 120.0, "TARGET_HIT")
    backfill()
    res = snapshot_stats(periods=("DAILY",), portfolios=("ALL",))
    assert res["ok"] and res["written"] == 1

    hist = stats_history(period="DAILY", portfolio="ALL")
    assert len(hist["points"]) == 1
    assert hist["points"][0]["closed_trades"] == 1


def test_snapshot_is_idempotent_per_period():
    _trade("NSE:SNAPY", 100.0, 120.0, "TARGET_HIT")
    backfill()
    snapshot_stats(periods=("DAILY",), portfolios=("ALL",))
    snapshot_stats(periods=("DAILY",), portfolios=("ALL",))
    assert len(stats_history(period="DAILY")["points"]) == 1, \
        "the same day must overwrite, not append"


# ── 7. Event bus ─────────────────────────────────────────────────────────────

def test_status_change_publishes_to_the_bus(monkeypatch):
    from dashboard.backend import lifecycle_bus
    seen: list = []
    monkeypatch.setattr(lifecycle_bus, "_deliver", lambda p: seen.append(p))

    uid = upsert({"source": "MANUAL", "symbol": "NSE:BUS", "status": "AWAITING_ENTRY",
                  "source_table": "manual", "source_id": "bus1"})
    upsert({"source": "MANUAL", "symbol": "NSE:BUS", "status": "ACTIVE",
            "source_table": "manual", "source_id": "bus1"})
    upsert({"source": "MANUAL", "symbol": "NSE:BUS", "status": "TARGET_HIT",
            "source_table": "manual", "source_id": "bus1", "pnl_pct": 10.0})

    events = [e for e in seen if e.get("event") == "LIFECYCLE_UPDATED"]
    assert len(events) >= 2, "every real transition must be announced"
    assert events[-1]["status"] == "TARGET_HIT"
    assert events[-1]["lifecycle_id"] == uid
    assert all("version" in e for e in events), "each event carries a ledger version"


def test_backfill_does_not_spam_the_bus(monkeypatch):
    from dashboard.backend import lifecycle_bus
    seen: list = []
    _trade("NSE:QUIET", 100.0, 120.0, "TARGET_HIT")
    monkeypatch.setattr(lifecycle_bus, "_deliver", lambda p: seen.append(p))
    backfill()
    assert not [e for e in seen if e.get("event") == "LIFECYCLE_UPDATED"], \
        "a resync must not wake every connected browser"


# ── 8. History remains append-only ───────────────────────────────────────────

def test_timeline_preserves_every_transition():
    uid = upsert({"source": "MANUAL", "symbol": "NSE:TL", "status": "IDEA_GENERATED",
                  "source_table": "manual", "source_id": "tl1"})
    for s in ("AWAITING_ENTRY", "ENTRY_TRIGGERED", "ACTIVE", "BREAKEVEN",
              "TARGET1_HIT", "TRAILING_SL", "TARGET_HIT"):
        upsert({"source": "MANUAL", "symbol": "NSE:TL", "status": s,
                "source_table": "manual", "source_id": "tl1"})
    t = timeline(uid)
    seq = [e["to_status"] for e in t["events"]]
    assert seq == ["IDEA_GENERATED", "AWAITING_ENTRY", "ENTRY_TRIGGERED", "ACTIVE",
                   "BREAKEVEN", "TARGET1_HIT", "TRAILING_SL", "TARGET_HIT"]
    assert t["trade"]["status"] == "TARGET_HIT"
