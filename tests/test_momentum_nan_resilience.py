"""Momentum cycle survives malformed provider data (2026-08-21 regression).

Yahoo returned NaN OHLC for 18 of 19 holdings. `process_active` only checked
`cmp is None`, so NaN reached SQLite — which stores NaN as NULL — and the whole
cycle died on its FIRST holding: no exits, no trailing stops, no re-scoring, no
new admissions, for every cycle thereafter.
"""

from __future__ import annotations

import math
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="mom_nan_test_"))

import pytest  # noqa: E402

import services.momentum_portfolio_manager as mgr  # noqa: E402
from dashboard.backend.db import momentum_portfolio as db  # noqa: E402

NAN, INF = float("nan"), float("inf")
REQUIRED_NUMERIC = ("entry_price", "stop_loss", "current_price", "profit_loss",
                    "profit_loss_pct", "drawdown_pct", "days_held")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    db.init_momentum_db()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM momentum_journal")
        c.execute("DELETE FROM momentum_positions")
        c.commit()
    finally:
        c.close()
    monkeypatch.setenv("MOMENTUM_PORTFOLIO_ENABLED", "1")
    monkeypatch.setenv("MOMENTUM_ALLOW_DUP_EXPOSURE", "1")


def _active(symbol: str, entry=100.0, stop=92.0) -> int:
    pid = db.add_position({"symbol": symbol, "entry_price": entry, "stop_loss": stop,
                           "status": "PENDING", "quality_score": 70.0, "initial_stop": stop,
                           "target_1": 130.0, "target_2": 130.0})
    assert db.activate_pending(pid, entry)
    return pid


def _row(pid: int) -> dict:
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.row_factory = __import__("sqlite3").Row
        return dict(c.execute("SELECT * FROM momentum_positions WHERE id=?", (pid,)).fetchone())
    finally:
        c.close()


def _assert_no_non_finite(pid: int):
    row = _row(pid)
    for field in REQUIRED_NUMERIC:
        value = row[field]
        assert value is not None, f"{field} became NULL for position {pid}"
        assert math.isfinite(float(value)), f"{field} is non-finite ({value!r})"


# ── the original crash, reproduced ───────────────────────────────────────────
def test_nan_cmp_no_longer_crashes_process_active():
    """THE regression: a NaN close used to raise IntegrityError and kill the cycle."""
    pid = _active("BLSE")
    res = mgr.process_active(lambda s: (NAN, []))   # exactly what Yahoo served
    assert res["exited"] == 0 and res["updated"] == 0
    assert res["failed"] == 0                        # skipped cleanly, not "failed"
    _assert_no_non_finite(pid)


def test_inf_cmp_is_also_rejected():
    pid = _active("BLSE")
    mgr.process_active(lambda s: (INF, []))
    _assert_no_non_finite(pid)


def test_position_is_left_untouched_rather_than_written_with_garbage():
    pid = _active("BLSE", entry=100.0)
    before = _row(pid)
    mgr.process_active(lambda s: (NAN, []))
    after = _row(pid)
    assert after["current_price"] == before["current_price"]
    assert after["profit_loss_pct"] == before["profit_loss_pct"]


# ── one bad symbol must not stop the book ────────────────────────────────────
def test_one_bad_symbol_does_not_stop_the_remaining_positions():
    """The production failure mode: the FIRST bad holding aborted all the rest."""
    bad = _active("BADSYM", entry=100.0, stop=92.0)
    good_a = _active("GOODA", entry=100.0, stop=92.0)
    good_b = _active("GOODB", entry=100.0, stop=92.0)

    def provider(symbol):
        return (NAN, []) if symbol == "BADSYM" else (110.0, [])

    res = mgr.process_active(provider)
    assert res["updated"] == 2, "healthy holdings must still be processed"
    for pid in (good_a, good_b):
        assert _row(pid)["current_price"] == 110.0
        _assert_no_non_finite(pid)
    _assert_no_non_finite(bad)


def test_a_raising_symbol_also_does_not_stop_the_book():
    good = _active("GOODA")

    def provider(symbol):
        if symbol == "BOOM":
            raise RuntimeError("provider exploded")
        return (110.0, [])

    _active("BOOM")
    res = mgr.process_active(provider)
    assert res["updated"] == 1
    assert _row(good)["current_price"] == 110.0


# ── DB write boundary ────────────────────────────────────────────────────────
def test_update_position_drops_non_finite_instead_of_raising():
    pid = _active("BLSE")
    db.update_position(pid, current_price=NAN, profit_loss_pct=NAN, days_held=4)
    row = _row(pid)
    assert row["days_held"] == 4          # the finite field still applied
    _assert_no_non_finite(pid)


def test_close_position_rejects_non_finite_exit_price():
    pid = _active("BLSE")
    with pytest.raises(ValueError, match="non-finite exit_price"):
        db.close_position(pid, NAN, "TARGET_HIT")
    assert _row(pid)["status"] == "ACTIVE"   # not half-closed


def test_activate_pending_rejects_non_finite_trigger():
    pid = db.add_position({"symbol": "CCC", "entry_price": 100.0, "stop_loss": 92.0,
                           "status": "PENDING", "initial_stop": 92.0})
    with pytest.raises(ValueError, match="non-finite trigger_price"):
        db.activate_pending(pid, NAN)


# ── normal behaviour is unchanged ────────────────────────────────────────────
def test_finite_prices_still_exit_and_update_normally():
    pid = _active("AAA", entry=100.0, stop=92.0)
    res = mgr.process_active(lambda s: (135.0, []))   # above target 130
    assert res["exited"] == 1 and res["failed"] == 0
    assert _row(pid)["status"] in ("TARGET_HIT", "CLOSED")
