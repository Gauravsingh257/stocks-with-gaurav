"""Arm-on-tap portfolio: PENDING lifecycle + reconciliation entry-detection.

Covers the two bug fixes:
  A. a promoted idea is armed (PENDING) and only becomes ACTIVE when its planned
     entry is genuinely traded through — no phantom P&L before that.
  B. reconciliation reclassifies only never-triggered positions.
"""

from __future__ import annotations

import os
import tempfile

# Point the SQLite DB at a throwaway dir BEFORE importing the db modules, so the
# real dashboard.db is never touched.
_TMP = tempfile.mkdtemp(prefix="pf_arm_test_")
os.environ["DATA_DIR"] = _TMP

import pytest  # noqa: E402

from dashboard.backend.db.portfolio import (  # noqa: E402
    init_portfolio_db, add_position, get_portfolio, get_pending_positions,
    get_portfolio_counts, activate_pending_position, expire_pending_position,
    get_active_position_by_symbol, reclassify_active_to_pending,
)
from services.portfolio_reconcile import entry_traded_through, reconcile_portfolio_entries  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    init_portfolio_db()
    # Clean any leftover rows between tests.
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_positions")
        c.execute("DELETE FROM portfolio_journal")
        c.commit()
    finally:
        c.close()


# ── Pure entry-detection ─────────────────────────────────────────────────────

def test_entry_traded_through_pullback_tapped():
    ohlc = [{"date": "2026-07-07", "low": 560, "high": 615}]  # range straddles 593
    ok, d = entry_traded_through(593.42, ohlc)
    assert ok and d == "2026-07-07"


def test_entry_never_reached_when_price_stays_above():
    ohlc = [{"date": "2026-07-07", "low": 1615, "high": 1660}]  # entry 1570 below the low
    ok, d = entry_traded_through(1570.65, ohlc)
    assert ok is False and d is None


def test_entry_traded_through_gap():
    # Gapped from a 95-high bar to a 102-low bar → 100 was jumped over → counts.
    ohlc = [{"date": "2026-07-06", "low": 90, "high": 95},
            {"date": "2026-07-07", "low": 102, "high": 110}]
    ok, d = entry_traded_through(100.0, ohlc)
    assert ok and d == "2026-07-07"


# ── PENDING lifecycle (Bug A) ────────────────────────────────────────────────

def test_armed_position_is_pending_and_excluded_from_active():
    pid = add_position({"symbol": "RAYMOND", "horizon": "SWING", "entry_price": 593.42,
                        "stop_loss": 508.51, "target_1": 848.15, "status": "PENDING",
                        "arm_ref_price": 610.35, "current_price": 610.35})
    assert get_portfolio("SWING") == []              # ACTIVE-only view is empty
    pend = get_pending_positions("SWING")
    assert len(pend) == 1 and pend[0]["id"] == pid
    assert pend[0]["entered_at"] is None
    counts = get_portfolio_counts()
    assert counts["swing"] == 0 and counts["swing_pending"] == 1 and counts["swing_used"] == 1


def test_symbol_guard_blocks_double_commit_while_pending():
    add_position({"symbol": "SPARC", "horizon": "SWING", "entry_price": 261.71,
                  "stop_loss": 248.62, "status": "PENDING", "arm_ref_price": 266.34})
    assert get_active_position_by_symbol("SPARC") is not None   # armed counts as committed
    with pytest.raises(ValueError):
        add_position({"symbol": "SPARC", "horizon": "SWING", "entry_price": 261.71,
                      "stop_loss": 248.62, "status": "PENDING"})


def test_activate_on_tap_starts_pnl_from_zero():
    pid = add_position({"symbol": "ADANIENSOL", "horizon": "SWING", "entry_price": 1570.65,
                        "stop_loss": 1492.12, "target_1": 1806.24, "status": "PENDING",
                        "arm_ref_price": 1620.0})
    assert activate_pending_position(pid, trigger_price=1570.65) is True
    live = get_portfolio("SWING")
    assert len(live) == 1
    row = live[0]
    assert row["status"] == "ACTIVE"
    assert row["entered_at"] is not None
    assert row["profit_loss_pct"] == 0.0     # entered at the planned entry — no fabricated gain
    assert row["days_held"] == 0


def test_expire_pending_frees_slot_without_journaling():
    pid = add_position({"symbol": "XYZ", "horizon": "SWING", "entry_price": 100,
                        "stop_loss": 90, "status": "PENDING"})
    assert expire_pending_position(pid, "EXPIRED_TIMEOUT") is True
    assert get_pending_positions("SWING") == []
    assert get_portfolio_counts()["swing_used"] == 0
    from dashboard.backend.db.portfolio import get_journal
    assert get_journal("SWING") == []        # never entered → never a trade


# ── Reconciliation (Bug A migration) ─────────────────────────────────────────

def test_reconcile_reclassifies_only_never_triggered():
    # PHANTOM: added ACTIVE at 1570 but price stayed 1615-1660 → never reached.
    phantom = add_position({"symbol": "ADANIENSOL", "horizon": "SWING", "entry_price": 1570.65,
                            "stop_loss": 1492.12, "status": "ACTIVE", "current_price": 1651})
    # LEGIT: added ACTIVE at 100 and price traded through it.
    legit = add_position({"symbol": "GOODCO", "horizon": "SWING", "entry_price": 100,
                          "stop_loss": 90, "status": "ACTIVE", "current_price": 108})

    def fake_fetch(sym, start):
        return {
            "ADANIENSOL": [{"date": "2026-07-07", "low": 1615, "high": 1660}],
            "GOODCO":     [{"date": "2026-07-07", "low": 96, "high": 110}],
        }.get(sym.replace("NSE:", ""))

    report = reconcile_portfolio_entries(dry_run=False, fetch=fake_fetch)
    assert report["reclassified_count"] == 1
    assert report["reclassified"][0]["symbol"] == "ADANIENSOL"
    assert report["preserved_count"] == 1

    # Phantom is now PENDING (no P&L); legit stays ACTIVE.
    assert get_active_position_by_symbol("GOODCO")["status"] == "ACTIVE"
    pend = get_pending_positions("SWING")
    assert len(pend) == 1 and pend[0]["symbol"] == "ADANIENSOL"
    assert pend[0]["profit_loss_pct"] == 0.0


def test_reconcile_failsafe_keeps_active_on_missing_data():
    add_position({"symbol": "NODATA", "horizon": "SWING", "entry_price": 50,
                  "stop_loss": 45, "status": "ACTIVE", "current_price": 55})
    report = reconcile_portfolio_entries(dry_run=False, fetch=lambda s, d: None)
    assert report["reclassified_count"] == 0
    assert report["skipped_count"] == 1
    assert get_active_position_by_symbol("NODATA")["status"] == "ACTIVE"
