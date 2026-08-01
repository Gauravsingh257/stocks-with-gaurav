"""Capture layer: charts, context, algorithm_hash producer, MANUAL/PAPER writers.

These were the four columns/sources that existed in the schema with nothing
producing them — filters that always returned nothing and fields that were
always NULL.
"""

from __future__ import annotations

import json
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="lifecycle_cap_")

import pytest  # noqa: E402

from dashboard.backend.db.schema import get_connection  # noqa: E402
from dashboard.backend.db.portfolio import init_portfolio_db, add_position, close_position  # noqa: E402
from dashboard.backend.db.trade_lifecycle import init_lifecycle_db  # noqa: E402
from dashboard.backend.db.trade_lifecycle_migrate import backfill  # noqa: E402
from dashboard.backend.db.trade_lifecycle_query import query, stats  # noqa: E402
from dashboard.backend.db import lifecycle_capture as cap  # noqa: E402


def _wipe():
    c = get_connection()
    try:
        for t in ("trade_lifecycle", "trade_lifecycle_events", "lifecycle_alerts",
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


# ── algorithm_hash producer ──────────────────────────────────────────────────

def test_algorithm_hash_is_stable_and_config_sensitive(monkeypatch):
    a = cap.algorithm_hash("SMC", "v4.2.1")
    assert a == cap.algorithm_hash("SMC", "v4.2.1"), "same config must hash the same"
    assert a != cap.algorithm_hash("MOMENTUM", "v4.2.1"), "engine is part of the identity"

    # Changing a behavioural parameter must change the fingerprint even when the
    # version label is unchanged — that is the whole point of the hash.
    monkeypatch.setenv("PORTFOLIO_REENTRY_COOLDOWN_DAYS", "99")
    assert cap.algorithm_hash("SMC", "v4.2.1") != a


def test_algorithm_hash_is_stamped_on_backfill():
    pid = add_position({"symbol": "NSE:HASH", "horizon": "SWING", "entry_price": 100.0,
                        "stop_loss": 90.0, "target_1": 120.0, "status": "ACTIVE"})
    close_position(pid, 120.0, "TARGET_HIT")
    backfill()

    row = query(limit=5, symbol="HASH")["items"][0]
    assert row["algorithm_hash"], "rows must carry the config fingerprint"
    assert len(row["algorithm_hash"]) == 12


# ── Context enrichment ───────────────────────────────────────────────────────

def test_build_context_includes_known_fields_and_omits_unknown_ones():
    ctx = cap.build_context("NSE:CTX", confidence=71.5, reasoning="BOS retest",
                            extra={"atr_pct": 2.4, "sector": "Auto", "trend": 0.8,
                                   "momentum": 0.6, "liquidity": None})
    assert ctx["confidence"] == 71.5
    assert ctx["reasoning"] == "BOS retest"
    assert ctx["atr_pct"] == 2.4 and ctx["sector"] == "Auto"
    br = ctx.get("confidence_breakdown", {})
    assert br.get("trend") == 0.8 and br.get("momentum") == 0.6
    assert "liquidity" not in br, \
        "an unmeasured factor must be omitted, not zero-filled — 0 reads as 'measured and bad'"


def test_build_context_survives_missing_optional_services():
    ctx = cap.build_context("NSE:BARE")
    assert ctx["symbol"] == "NSE:BARE"
    assert "confidence" not in ctx


# ── Chart capture ────────────────────────────────────────────────────────────

def test_chart_fetch_returns_none_when_broker_unavailable(monkeypatch):
    """No broker must mean 'not captured', never an empty chart."""
    monkeypatch.setattr(cap, "fetch_ohlc_window", cap.fetch_ohlc_window)
    assert cap.fetch_ohlc_window("NSE:NOBROKER", "2026-07-01T09:15:00+05:30") is None


def test_capture_missing_charts_is_safe_with_no_data():
    pid = add_position({"symbol": "NSE:NOCHART", "horizon": "SWING", "entry_price": 100.0,
                        "stop_loss": 90.0, "target_1": 120.0, "status": "ACTIVE"})
    close_position(pid, 120.0, "TARGET_HIT")
    backfill()
    res = cap.capture_missing_charts(limit=5)
    assert res["ok"], "a chart failure must never break the sweep"
    assert res["captured"] == 0 and res["unavailable"] >= 1


# ── MANUAL / PAPER writers ───────────────────────────────────────────────────

def test_manual_trade_is_recorded_and_filterable():
    uid = cap.record_manual_trade({
        "symbol": "TATAMOTORS", "entry_price": 900.0, "stop_loss": 860.0,
        "target_1": 990.0, "exit_price": 985.0, "entry_at": "2026-07-01T10:00:00+05:30",
        "exit_at": "2026-07-20T15:00:00+05:30", "exit_reason": "TARGET_HIT",
        "status": "TARGET_HIT", "setup": "Manual breakout", "confidence": 80.0,
        "external_id": "m-1",
    }, source="MANUAL")
    assert uid

    res = query(limit=10, portfolio="MANUAL")
    assert res["total"] == 1, "the MANUAL filter must finally return something"
    r = res["items"][0]
    assert r["symbol"] == "NSE:TATAMOTORS", "symbols are normalised to NSE:"
    assert r["status"] == "TARGET_HIT" and r["executed"] == 1
    assert r["pnl_pct"] == pytest.approx((985 - 900) / 900 * 100, abs=0.01)
    assert r["rr_realized"] is not None
    assert r["algorithm_hash"] and r["engine"] == "MANUAL"
    assert json.loads(r["recommendation_json"])["setup"] == "Manual breakout"


def test_paper_trade_is_a_separate_book():
    cap.record_manual_trade({"symbol": "INFY", "entry_price": 1500.0, "stop_loss": 1450.0,
                             "external_id": "p-1"}, source="PAPER")
    assert query(limit=10, portfolio="PAPER")["total"] == 1
    assert query(limit=10, portfolio="MANUAL")["total"] == 0, "PAPER must not leak into MANUAL"


def test_manual_writer_is_idempotent_on_external_id():
    payload = {"symbol": "WIPRO", "entry_price": 500.0, "stop_loss": 480.0, "external_id": "dup-1"}
    a = cap.record_manual_trade(payload)
    b = cap.record_manual_trade(payload)
    assert a == b, "the same external id must update, not duplicate"
    assert query(limit=10, portfolio="MANUAL")["total"] == 1


def test_manual_trade_counts_in_stats_like_any_other_book():
    cap.record_manual_trade({"symbol": "HDFCBANK", "entry_price": 100.0, "stop_loss": 90.0,
                             "exit_price": 120.0, "status": "TARGET_HIT", "external_id": "s-1"})
    s = stats(portfolio="MANUAL")
    assert s["closed_trades"] == 1 and s["win_rate_pct"] == 100.0


def test_manual_writer_rejects_an_unknown_source():
    with pytest.raises(ValueError):
        cap.record_manual_trade({"symbol": "X", "entry_price": 1.0}, source="SOMETHING_ELSE")
