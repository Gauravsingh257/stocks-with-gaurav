"""Close durability: the price tracker must never resurrect a CLOSED position.

Reproduces the race that made a manual close revert — the tracker reads the
ACTIVE list, the row is closed, then the tracker writes it back stamped ACTIVE —
and proves the `require_active` guard prevents it (atomic no-clobber). Uses a
throwaway sqlite DB so the real dashboard.db is never touched.
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="pf_close_test_")
os.environ["DATA_DIR"] = _TMP

import pytest  # noqa: E402

from dashboard.backend.db.portfolio import (  # noqa: E402
    init_portfolio_db, add_position, close_position, get_position_by_id,
    update_position_price,
)


def _wipe():
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        # Journal FK-references positions → delete the child rows first.
        c.execute("DELETE FROM portfolio_journal")
        c.execute("DELETE FROM portfolio_positions")
        c.commit()
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _fresh_db():
    init_portfolio_db()
    _wipe()
    yield
    # Clean up AFTER too — this suite creates journal rows (close_position); other
    # portfolio test files share the same temp DB and some wipe positions first,
    # so never leave orphan journal rows behind regardless of run order.
    _wipe()


def _add_active() -> int:
    return add_position({
        "symbol": "NSE:TESTCO", "horizon": "SWING",
        "entry_price": 100.0, "stop_loss": 90.0, "target_1": 120.0,
        "status": "ACTIVE",
    })


def test_guarded_write_cannot_resurrect_a_closed_position():
    pid = _add_active()
    close_position(pid, 95.0, "MANUAL")
    assert get_position_by_id(pid)["status"] == "CLOSED"

    # Simulate the tracker's per-tick write landing AFTER the close (the race).
    update_position_price(pid, status="ACTIVE", current_price=96.0, require_active=True)

    assert get_position_by_id(pid)["status"] == "CLOSED"   # stays closed ✅


def test_guarded_write_still_updates_a_live_position():
    pid = _add_active()
    update_position_price(pid, status="ACTIVE", current_price=105.0, require_active=True)
    row = get_position_by_id(pid)
    assert row["status"] == "ACTIVE"
    assert row["current_price"] == 105.0


def test_unguarded_write_would_revert__documents_the_bug():
    # The original behaviour the guard fixes: without require_active, the tracker
    # write DOES clobber the close and resurrect the row.
    pid = _add_active()
    close_position(pid, 95.0, "MANUAL")
    update_position_price(pid, status="ACTIVE", current_price=96.0)  # no guard
    assert get_position_by_id(pid)["status"] == "ACTIVE"


def test_full_store_path_is_guarded():
    # End-to-end through the tracker's store adapter (must pass require_active).
    from services.position_stores import PortfolioPositionStore
    pid = _add_active()
    close_position(pid, 95.0, "MANUAL")
    PortfolioPositionStore().update_metrics(pid, status="ACTIVE", current_price=96.0)
    assert get_position_by_id(pid)["status"] == "CLOSED"
