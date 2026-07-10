"""
tests/test_pil_alerts.py
========================
Tests for the PIL alert rule engine (services/pil/alerts.py): the fire/dedup/
self-clear reconciliation logic (with a stubbed signal source) plus a seeded-DB
integration that fires concentration alerts.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pil_al_test_")

import pytest  # noqa: E402

from services.pil import alerts as al  # noqa: E402
from dashboard.backend.db import pil as pildb  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    pildb.ensure_tables()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM pil_alerts"); c.commit()
    finally:
        c.close()


def _sig(book, type_, sev="WARN"):
    return {"book": book, "type": type_, "severity": sev, "message": f"{book} {type_}",
            "value": 0.5, "threshold": 0.3}


def test_fires_then_dedups(monkeypatch):
    monkeypatch.setattr(al, "_signals", lambda: [_sig("COMBINED", "SECTOR_OVERWEIGHT")])
    r1 = al.evaluate(notify=False)
    assert len(r1["fired"]) == 1
    assert r1["active_count"] == 1
    # second pass: same signal is already active -> not newly fired
    r2 = al.evaluate(notify=False)
    assert r2["fired"] == []
    assert r2["active_count"] == 1
    assert len(pildb.get_alerts(active_only=True)) == 1


def test_self_clears_resolved(monkeypatch):
    monkeypatch.setattr(al, "_signals", lambda: [_sig("COMBINED", "SECTOR_OVERWEIGHT"),
                                                 _sig("SWING", "LARGE_DRAWDOWN")])
    al.evaluate(notify=False)
    assert len(pildb.get_alerts(active_only=True)) == 2
    # drawdown resolves
    monkeypatch.setattr(al, "_signals", lambda: [_sig("COMBINED", "SECTOR_OVERWEIGHT")])
    r = al.evaluate(notify=False)
    assert {"book": "SWING", "type": "LARGE_DRAWDOWN"} in r["cleared"]
    assert len(pildb.get_alerts(active_only=True)) == 1


def test_integration_concentration_fires(monkeypatch):
    monkeypatch.setenv("PIL_CAPITAL_SWING", "1000000")
    from dashboard.backend.db import portfolio as pdb
    pdb.init_portfolio_db()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_positions")
        c.execute("DELETE FROM pil_alerts"); c.commit()
    finally:
        c.close()
    pid = pdb.add_position({"symbol": "HDFCBANK", "horizon": "SWING", "entry_price": 100.0,
                            "stop_loss": 92.0, "status": "ACTIVE"})
    c = get_connection()
    try:
        c.execute("UPDATE portfolio_positions SET current_price=110 WHERE id=?", (pid,)); c.commit()
    finally:
        c.close()
    res = al.evaluate(notify=False)
    types = {f["type"] for f in res["fired"]}
    # single holding => sector + single-stock concentration
    assert "SECTOR_OVERWEIGHT" in types
    assert "SINGLE_STOCK_CONCENTRATION" in types
