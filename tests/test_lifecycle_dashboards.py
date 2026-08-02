"""Trade detail, visual analytics and cross-engine chain attribution."""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="lifecycle_dash_")

import pytest  # noqa: E402

from dashboard.backend.db.schema import get_connection  # noqa: E402
from dashboard.backend.db.portfolio import init_portfolio_db, add_position, close_position  # noqa: E402
from dashboard.backend.db.trade_lifecycle import init_lifecycle_db, record_alert  # noqa: E402
from dashboard.backend.db.trade_lifecycle_migrate import backfill  # noqa: E402
from dashboard.backend.db.trade_lifecycle_query import query  # noqa: E402
from dashboard.backend.db.lifecycle_chain import (  # noqa: E402
    link_chains, chain, cross_engine_attribution,
)
from dashboard.backend.db.lifecycle_dashboards import (  # noqa: E402
    monthly_performance, engine_comparison, conversion_funnel,
    exit_attribution, trade_detail, post_trade_analysis,
)


def _wipe():
    c = get_connection()
    try:
        for t in ("trade_lifecycle", "trade_lifecycle_events", "lifecycle_alerts",
                  "lifecycle_stats_snapshots", "portfolio_journal",
                  "portfolio_positions", "stock_recommendations"):
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


def _idea(symbol, entry=100.0, days_ago=10):
    c = get_connection()
    try:
        c.execute(
            "INSERT INTO stock_recommendations (symbol, agent_type, status, entry_price, "
            "stop_loss, targets, confidence_score, setup, created_at) "
            f"VALUES (?,?,?,?,?,?,?,?,datetime('now','-{days_ago} days'))",
            (symbol, "SWING", "ACTIVE", entry, entry * 0.9, "[130.0]", 75.0, "SMC_BOS"),
        )
        c.commit()
    finally:
        c.close()


def _trade(symbol, entry, exit_px, reason="TARGET_HIT", horizon="SWING"):
    pid = add_position({"symbol": symbol, "horizon": horizon, "entry_price": entry,
                        "stop_loss": entry * 0.9, "target_1": entry * 1.2, "status": "ACTIVE"})
    close_position(pid, exit_px, reason)
    return pid


# ── Cross-engine chain attribution ───────────────────────────────────────────

def test_position_links_back_to_the_idea_that_produced_it():
    _idea("NSE:LINKED", entry=100.0, days_ago=10)
    _trade("NSE:LINKED", 100.0, 120.0, "TARGET_HIT")
    backfill()
    res = link_chains()
    assert res["ok"] and res["linked"] >= 1

    pos = query(limit=10, symbol="LINKED", stage="POSITION")["items"][0]
    assert pos["parent_id"], "the position must point at its originating idea"
    ch = chain(pos["chain_id"])
    assert len(ch["stages"]) == 2, "idea + position are one chain"
    assert ch["converted"] and "SWING" in ch["engines_that_traded_it"]


def test_a_different_price_level_is_not_linked():
    """A conservative linker must not invent causation."""
    _idea("NSE:FAR", entry=100.0, days_ago=5)
    _trade("NSE:FAR", 400.0, 420.0, "TARGET_HIT")   # nowhere near the idea's entry
    backfill()
    link_chains()
    pos = query(limit=10, symbol="FAR", stage="POSITION")["items"][0]
    assert not pos["parent_id"], "an unrelated entry level must stay unlinked"


def test_a_position_opened_before_the_idea_is_not_linked():
    _idea("NSE:BEFORE", entry=100.0, days_ago=0)
    c = get_connection()
    try:
        c.execute("UPDATE stock_recommendations SET created_at = datetime('now','+5 days') "
                  "WHERE symbol='NSE:BEFORE'")
        c.commit()
    finally:
        c.close()
    _trade("NSE:BEFORE", 100.0, 120.0, "TARGET_HIT")
    backfill()
    link_chains()
    pos = query(limit=10, symbol="BEFORE", stage="POSITION")["items"][0]
    assert not pos["parent_id"], "a trade cannot descend from a later idea"


def test_cross_engine_attribution_only_credits_chains_with_an_idea():
    _idea("NSE:CONV", entry=100.0, days_ago=8)
    _trade("NSE:CONV", 100.0, 120.0, "TARGET_HIT")
    _trade("NSE:OWN", 50.0, 60.0, "TARGET_HIT")   # no idea behind it
    backfill()
    link_chains()

    a = cross_engine_attribution()
    assert a["ideas_with_a_chain"] >= 1
    assert a["ideas_converted_to_a_position"] >= 1
    converted = sum(e["converted"] for e in a["per_engine"])
    assert converted == 1, "a trade with no originating idea must not be credited"


def test_link_chains_is_idempotent():
    _idea("NSE:IDEM", entry=100.0, days_ago=6)
    _trade("NSE:IDEM", 100.0, 120.0, "TARGET_HIT")
    backfill()
    a = link_chains()
    b = link_chains()
    assert a["linked"] == b["linked"]


# ── Visual analytics ─────────────────────────────────────────────────────────

def test_monthly_performance_has_a_cumulative_curve():
    for i in range(4):
        _trade(f"NSE:M{i}", 100.0, 110.0 if i % 2 == 0 else 95.0,
               "TARGET_HIT" if i % 2 == 0 else "STOP_HIT")
    backfill()
    pts = monthly_performance()["points"]
    assert pts, "closed trades must produce at least one month"
    assert "cumulative_pnl_pct" in pts[0]
    running = 0.0
    for p in pts:
        running = round(running + p["sum_pnl_pct"], 2)
        assert p["cumulative_pnl_pct"] == pytest.approx(running, abs=0.02)


def test_engine_comparison_by_book_and_by_version():
    _trade("NSE:E1", 100.0, 120.0, "TARGET_HIT", horizon="SWING")
    _trade("NSE:E2", 100.0, 90.0, "STOP_HIT", horizon="LONGTERM")
    backfill()

    by_book = engine_comparison()
    keys = {r["key"] for r in by_book["rows"]}
    assert {"SWING", "LONGTERM"} <= keys

    by_ver = engine_comparison(by_version=True)
    assert by_ver["dimension"] == "engine_version"
    assert by_ver["rows"], "engine versions must be comparable"


def test_conversion_funnel_stages_are_ordered_and_rated():
    for i in range(5):
        _idea(f"NSE:F{i}")
    _trade("NSE:F0", 100.0, 120.0, "TARGET_HIT")
    _trade("NSE:F1", 100.0, 90.0, "STOP_HIT")
    backfill()

    f = conversion_funnel()
    names = [s["stage"] for s in f["stages"]]
    assert names == ["Ideas generated", "Entries taken", "Positions closed", "Targets reached"]
    counts = [s["count"] for s in f["stages"]]
    assert counts[0] >= counts[1] >= counts[3], "the funnel must narrow"
    assert f["leakage"]["ideas_that_never_traded"] == 3


def test_exit_attribution_reports_giveback():
    _trade("NSE:GB", 100.0, 95.0, "STOP_HIT")
    backfill()
    c = get_connection()
    try:
        c.execute("UPDATE trade_lifecycle SET mfe_pct = 9.0 WHERE symbol='NSE:GB'")
        c.commit()
    finally:
        c.close()
    rows = exit_attribution()["rows"]
    stop = next(r for r in rows if r["status"] == "STOP_HIT")
    assert stop["avg_giveback_pct"] == pytest.approx(9.0 - stop["avg_pnl"], abs=0.05)


# ── Trade detail ─────────────────────────────────────────────────────────────

def test_trade_detail_assembles_every_panel():
    _idea("NSE:DET", entry=100.0, days_ago=7)
    _trade("NSE:DET", 100.0, 120.0, "TARGET_HIT")
    record_alert("NSE:DET", "ENTRY", "Entry triggered NSE:DET")
    backfill()
    link_chains()

    uid = query(limit=10, symbol="DET", stage="POSITION")["items"][0]["uuid"]
    d = trade_detail(uid)
    assert d["found"]
    for k in ("trade", "events", "chain", "alerts", "price_path", "analysis"):
        assert k in d, f"detail must include {k}"
    assert d["price_path"]["available"]
    assert any(p["label"] == "Entry" for p in d["price_path"]["points"])
    assert any("DET" in a["message"] for a in d["alerts"])
    assert len(d["chain"]["stages"]) == 2


def test_post_trade_analysis_never_invents_pnl_for_an_unexecuted_idea():
    a = post_trade_analysis({"executed": 0, "status": "NEVER_EXECUTED"})
    assert a["verdict"] == "Never executed"
    assert a["giveback_pct"] is None
    assert "no P&L" in " ".join(a["notes"])


def test_post_trade_analysis_flags_giveback_on_a_round_trip():
    a = post_trade_analysis({
        "executed": 1, "status": "STOP_HIT", "pnl_pct": -5.0, "mfe_pct": 10.0,
        "mae_pct": -5.0, "entry_price": 100.0, "stop_loss": 95.0, "target_1": 108.0,
        "holding_days": 12,
    })
    assert a["verdict"] == "Stopped out"
    assert a["giveback_pct"] == pytest.approx(15.0, abs=0.01)
    joined = " ".join(a["notes"])
    assert "give" in joined.lower() or "Give" in joined


def test_post_trade_analysis_distinguishes_time_and_forced_exits():
    t = post_trade_analysis({"executed": 1, "status": "TIME_EXIT", "pnl_pct": 2.0,
                             "mfe_pct": 9.0, "holding_days": 21})
    assert t["verdict"] == "Closed by time rule"
    f = post_trade_analysis({"executed": 1, "status": "FORCED_EXIT", "pnl_pct": -3.0})
    assert f["verdict"] == "Risk/structure exit"


# ── One book-return definition across every surface ──────────────────────────

@pytest.mark.parametrize("horizon", ["SWING", "LONGTERM"])
def test_every_surface_reports_the_same_book_return(horizon):
    """Header, lifecycle analytics, monthly, engine table and exit table must
    all divide by the same slot count. A panel that publishes a raw sum reads as
    a return roughly `slots` times too large — the original -41.37% bug."""
    from dashboard.backend.db.portfolio import get_journal_stats
    from dashboard.backend.db.lifecycle_analytics import analytics

    for i in range(6):
        pid = add_position({"symbol": f"NSE:X{horizon}{i}", "horizon": horizon,
                            "entry_price": 100.0, "stop_loss": 90.0,
                            "target_1": 120.0, "status": "ACTIVE"})
        close_position(pid, 112.0 if i < 4 else 94.0,
                       "TARGET_HIT" if i < 4 else "STOP_HIT")
    backfill()

    header = get_journal_stats(horizon, include_open=False)["realized_book_return_pct"]
    adv = analytics(horizon)["book_return_pct"]
    monthly = sum(p["book_return_pct"] for p in monthly_performance(horizon)["points"])
    row = next(r for r in engine_comparison()["rows"] if r["key"] == horizon)
    exits = sum(r["book_impact_pct"] for r in exit_attribution(horizon)["rows"])

    for name, val in (("analytics", adv), ("monthly", monthly),
                      ("engine table", row["book_return_pct"]), ("exit table", exits)):
        assert val == pytest.approx(header, abs=0.05), \
            f"{name} book return {val} != header {header}"


def test_raw_sum_is_kept_but_never_equals_the_book_return():
    """Both numbers stay available, and they must be distinguishable — the sum
    is for comparing trades, the book figure is the portfolio."""
    from dashboard.backend.db.lifecycle_analytics import analytics
    for i in range(3):
        pid = add_position({"symbol": f"NSE:RS{i}", "horizon": "SWING", "entry_price": 100.0,
                            "stop_loss": 90.0, "target_1": 120.0, "status": "ACTIVE"})
        close_position(pid, 120.0, "TARGET_HIT")
    backfill()

    a = analytics("SWING")
    assert a["sum_trade_return_pct"] == pytest.approx(60.0, abs=0.05)
    assert a["book_return_pct"] == pytest.approx(60.0 / a["book_slots"], abs=0.05)
    assert a["book_return_pct"] != a["sum_trade_return_pct"]

    row = next(r for r in engine_comparison()["rows"] if r["key"] == "SWING")
    assert row["sum_pnl_pct"] == pytest.approx(60.0, abs=0.05)
    assert row["book_return_pct"] == pytest.approx(60.0 / row["book_slots"], abs=0.05)


def test_momentum_is_book_weighted_too():
    """All three books, not just swing."""
    from dashboard.backend.db.lifecycle_analytics import analytics, _slots_for
    from dashboard.backend.db.trade_lifecycle import upsert

    upsert({"source": "MOMENTUM", "portfolio": "MOMENTUM", "engine": "MOMENTUM",
            "stage": "POSITION", "symbol": "NSE:MOMX", "status": "TARGET_HIT",
            "entry_price": 100.0, "stop_loss": 90.0, "exit_price": 120.0,
            "pnl_pct": 20.0, "exit_at": "2026-07-15T10:00:00+05:30",
            "source_table": "momentum_journal", "source_id": "mx1"})

    a = analytics("MOMENTUM")
    assert a["book_slots"] == _slots_for("MOMENTUM")
    assert a["book_return_pct"] == pytest.approx(20.0 / a["book_slots"], abs=0.05)
