"""Phantom ACTIVE rows — the ledger must retire what left the source table.

The positions mappers select `WHERE status IN ('ACTIVE','PENDING','EXPIRED')`
and upsert whatever matches, but nothing retired the rows they had already
written. When a position closed, its source row fell out of that WHERE and the
lifecycle row it had created was orphaned: frozen at status='ACTIVE' with the
pnl_pct and holding_days from its last sync. The genuine close then arrived from
the *journal* under a different deterministic UUID, so the same trade appeared
twice — once correctly closed, once permanently "open".

Observed in production on 2026-10-18: 201 ACTIVE ledger rows against 30 real
open positions, and ~70 of them were provably closed in the owner's own export
(RAYMOND listed as both Target Hit +43.41% and Active +41.45%). Every
"% resolved" denominator computed over that ledger was wrong.

The guards these tests hold:
  1. a departed row is ARCHIVED, never deleted and never given an invented
     terminal status — we know it is gone, not how it ended;
  2. a genuinely open position is left alone;
  3. an entirely EMPTY source table archives NOTHING (fresh/ephemeral
     database) while a book that genuinely closed everything still reconciles,
     and
  4. an implausibly large archive run is refused by the cap.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="lifecycle_phantom_"))

import pytest  # noqa: E402

from dashboard.backend.db import trade_lifecycle_migrate as mig  # noqa: E402
from dashboard.backend.db.schema import get_connection  # noqa: E402
from dashboard.backend.db.trade_lifecycle import (  # noqa: E402
    init_lifecycle_db,
    make_uuid,
)

_POSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
    id INTEGER PRIMARY KEY, symbol TEXT, horizon TEXT, direction TEXT,
    entry_price REAL, stop_loss REAL, target_1 REAL, target_2 REAL,
    profit_loss REAL, profit_loss_pct REAL, days_held INTEGER,
    confidence_score REAL, status TEXT, exit_reason TEXT,
    entered_at TEXT, created_at TEXT
)
"""


def _reset():
    init_lifecycle_db()
    conn = get_connection()
    try:
        conn.execute(_POSITIONS_DDL)
        conn.execute("DELETE FROM portfolio_positions")
        conn.execute("DELETE FROM trade_lifecycle")
        conn.commit()
    finally:
        conn.close()


def _add_position(pid, symbol, status="ACTIVE", pnl=5.0, days=3):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO portfolio_positions "
            "(id, symbol, horizon, direction, entry_price, stop_loss, target_1, "
            " profit_loss_pct, days_held, confidence_score, status, entered_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, "NSE:" + symbol, "SWING", "LONG", 100.0, 95.0, 115.0,
             pnl, days, 80.0, status, "2026-09-01T09:20:00+05:30",
             "2026-09-01T09:15:00+05:30"),
        )
        conn.commit()
    finally:
        conn.close()


def _close_position(pid):
    """What production does on a close: the row leaves the mapper's WHERE."""
    conn = get_connection()
    try:
        conn.execute("UPDATE portfolio_positions SET status='CLOSED' WHERE id=?", (pid,))
        conn.commit()
    finally:
        conn.close()


def _ledger(uuid_):
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT status, record_state, pnl_pct FROM trade_lifecycle WHERE uuid=?",
            (uuid_,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _uuid_for(pid):
    return make_uuid("SWING", "portfolio_positions", str(pid))


# -- 1. the defect itself ----------------------------------------------------

def test_departed_position_is_archived_not_left_active():
    _reset()
    _add_position(1, "RAYMOND", pnl=41.45)
    _add_position(2, "SBCL", pnl=3.0)
    mig.backfill()

    assert _ledger(_uuid_for(1))["record_state"] == "ACTIVE"
    assert _ledger(_uuid_for(2))["record_state"] == "ACTIVE"

    _close_position(1)
    out = mig.backfill()

    gone = _ledger(_uuid_for(1))
    assert gone["record_state"] == "ARCHIVED", "closed position still published as ACTIVE"
    assert out["reconcile_portfolio_positions"]["archived"] == 1
    # Still open — must be untouched.
    assert _ledger(_uuid_for(2))["record_state"] == "ACTIVE"


def test_archived_row_keeps_its_data_and_invents_no_outcome():
    """We know the source row is gone; we do NOT know how it ended. Writing a
    terminal status, an exit_reason or an exit P&L here would fabricate a trade
    outcome — the real one arrives from the journal under its own UUID."""
    _reset()
    _add_position(1, "RAYMOND", pnl=41.45)
    _add_position(2, "STILLOPEN")
    mig.backfill()
    _close_position(1)
    mig.backfill()

    row = _ledger(_uuid_for(1))
    assert row["record_state"] == "ARCHIVED"
    # status is deliberately NOT rewritten to STOP_HIT/TARGET_HIT/CLOSED
    assert row["status"] == "ACTIVE"
    assert row["pnl_pct"] == pytest.approx(41.45)

    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT exit_at, exit_price, exit_reason FROM trade_lifecycle WHERE uuid=?",
            (_uuid_for(1),)).fetchone()
    finally:
        conn.close()
    assert r["exit_at"] is None and r["exit_price"] is None and r["exit_reason"] is None


def test_archived_row_disappears_from_the_read_paths():
    """The track-record page and every metric filter on record_state='ACTIVE'."""
    from dashboard.backend.db.trade_lifecycle_query import query, stats

    _reset()
    _add_position(1, "RAYMOND", pnl=41.45)
    _add_position(2, "SBCL", pnl=3.0)
    mig.backfill()
    assert stats()["open_trades"] == 2

    _close_position(1)
    mig.backfill()

    assert stats()["open_trades"] == 1, "phantom still counted in the headline"
    symbols = {t["symbol"] for t in query(limit=100)["items"]}
    assert "NSE:RAYMOND" not in symbols
    assert "NSE:SBCL" in symbols
    # ...but it is retained for audit.
    allsym = {t["symbol"] for t in query(limit=100, record_state="ALL")["items"]}
    assert "NSE:RAYMOND" in allsym


def test_pending_and_expired_rows_are_in_scope_and_not_archived():
    """The mapper upserts ACTIVE, PENDING and EXPIRED alike, so all three must be
    counted as present — otherwise reconciliation would archive armed ideas."""
    _reset()
    _add_position(1, "AAA", status="ACTIVE")
    _add_position(2, "BBB", status="PENDING")
    _add_position(3, "CCC", status="EXPIRED")
    out = mig.backfill()
    assert out["reconcile_portfolio_positions"]["archived"] == 0
    for pid in (1, 2, 3):
        assert _ledger(_uuid_for(pid))["record_state"] == "ACTIVE"


def test_reconciliation_is_idempotent():
    _reset()
    _add_position(1, "RAYMOND")
    _add_position(2, "STILLOPEN")
    mig.backfill()
    _close_position(1)
    first = mig.backfill()
    second = mig.backfill()
    assert first["reconcile_portfolio_positions"]["archived"] == 1
    assert second["reconcile_portfolio_positions"]["archived"] == 0


def test_dry_run_archives_nothing_but_reports_the_candidates():
    _reset()
    _add_position(1, "RAYMOND")
    _add_position(2, "STILLOPEN")
    mig.backfill()
    _close_position(1)
    out = mig.backfill(dry_run=True)
    assert out["reconcile_portfolio_positions"]["candidates"] == 1
    assert out["reconcile_portfolio_positions"]["archived"] == 0
    assert _ledger(_uuid_for(1))["record_state"] == "ACTIVE"


# -- 2. the guards that stop this from erasing the track record --------------

def test_entirely_empty_source_table_archives_nothing():
    """A source table with NO rows at all, while the ledger still references it,
    is the fingerprint of a fresh or ephemeral database — the 2026-09-02
    duplicate-Railway-project failure mode. Trusting it would delete the
    published track record."""
    _reset()
    _add_position(1, "AAA")
    _add_position(2, "BBB")
    mig.backfill()

    conn = get_connection()
    try:
        conn.execute("DELETE FROM portfolio_positions")
        conn.commit()
    finally:
        conn.close()

    out = mig.backfill()
    assert out["reconcile_portfolio_positions"]["skipped"] == "empty_source_table"
    assert out["reconcile_portfolio_positions"]["archived"] == 0
    for pid in (1, 2):
        assert _ledger(_uuid_for(pid))["record_state"] == "ACTIVE"


def test_a_book_that_closed_everything_still_reconciles():
    """Distinct from the case above, and it has really happened: Momentum held an
    empty book for three weeks in July 2026. Rows exist in the table, none are in
    scope — the phantoms must still be retired."""
    _reset()
    _add_position(1, "AAA")
    _add_position(2, "BBB")
    mig.backfill()
    _close_position(1)
    _close_position(2)

    out = mig.backfill()
    assert out["reconcile_portfolio_positions"]["skipped"] is None
    assert out["reconcile_portfolio_positions"]["archived"] == 2
    for pid in (1, 2):
        assert _ledger(_uuid_for(pid))["record_state"] == "ARCHIVED"


def test_cap_refuses_an_implausibly_large_archive_run(monkeypatch):
    _reset()
    for pid in range(1, 6):
        _add_position(pid, "S" + str(pid))
    mig.backfill()
    for pid in range(2, 6):
        _close_position(pid)

    monkeypatch.setenv("LIFECYCLE_RECONCILE_MAX_ARCHIVE", "3")
    out = mig.backfill()
    assert out["reconcile_portfolio_positions"]["skipped"] == "over_cap:3"
    assert out["reconcile_portfolio_positions"]["archived"] == 0
    assert _ledger(_uuid_for(5))["record_state"] == "ACTIVE"

    monkeypatch.setenv("LIFECYCLE_RECONCILE_MAX_ARCHIVE", "100")
    out = mig.backfill()
    assert out["reconcile_portfolio_positions"]["archived"] == 4


# -- 3. the momentum path, whose UUID is derived from a literal ---------------

_MOMENTUM_DDL = """
CREATE TABLE IF NOT EXISTS momentum_positions (
    id INTEGER PRIMARY KEY, symbol TEXT, entry_price REAL, stop_loss REAL,
    target_1 REAL, profit_loss REAL, profit_loss_pct REAL, days_held INTEGER,
    quality_score REAL, entry_model TEXT, regime TEXT, status TEXT,
    exit_reason TEXT, entered_at TEXT, created_at TEXT
)
"""


def test_open_momentum_position_survives_reconciliation():
    """The guard against the worst failure mode here. `seen` is built from
    make_uuid(...) re-derived in the backfill, so if that derivation ever drifts
    from what the mapper actually upserts, EVERY momentum row looks departed and
    the whole book gets archived — caught only by the cap. This test fails the
    moment the two disagree.
    """
    init_lifecycle_db()
    conn = get_connection()
    try:
        conn.execute(_MOMENTUM_DDL)
        conn.execute("DELETE FROM momentum_positions")
        conn.execute("DELETE FROM trade_lifecycle")
        conn.execute(
            "INSERT INTO momentum_positions "
            "(id, symbol, entry_price, stop_loss, target_1, profit_loss_pct, days_held, "
            " quality_score, entry_model, regime, status, entered_at, created_at) "
            "VALUES (1,'NSE:BRIGADE',644.43,660.54,NULL,2.6,0,17,'breakout','RISK_ON',"
            "'ACTIVE','2026-08-24T09:20:00+05:30','2026-08-24T09:15:00+05:30')")
        conn.execute(
            "INSERT INTO momentum_positions "
            "(id, symbol, entry_price, stop_loss, target_1, profit_loss_pct, days_held, "
            " quality_score, entry_model, regime, status, entered_at, created_at) "
            "VALUES (2,'NSE:SIGMAADV',723.66,912.56,NULL,48.81,39,26,'shallow_pullback',"
            "'RISK_ON','ACTIVE','2026-08-23T09:20:00+05:30','2026-08-23T09:15:00+05:30')")
        conn.commit()
    finally:
        conn.close()

    out = mig.backfill()
    assert out["reconcile_momentum_positions"]["archived"] == 0, \
        "an open momentum book was archived — make_uuid drifted from the mapper"
    mu = make_uuid("MOMENTUM", "momentum_positions", "1")
    assert _ledger(mu)["record_state"] == "ACTIVE"

    conn = get_connection()
    try:
        conn.execute("UPDATE momentum_positions SET status='CLOSED' WHERE id=1")
        conn.commit()
    finally:
        conn.close()

    out = mig.backfill()
    assert out["reconcile_momentum_positions"]["archived"] == 1
    assert _ledger(mu)["record_state"] == "ARCHIVED"
    assert _ledger(make_uuid("MOMENTUM", "momentum_positions", "2"))["record_state"] == "ACTIVE"
