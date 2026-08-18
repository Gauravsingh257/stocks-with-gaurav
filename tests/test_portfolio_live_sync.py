"""Real-time engine-trade → portfolio sync + instant entry alert (glue logic).

DB-free: the seed and the Telegram alert are both monkeypatched, so these tests
verify only the tracker's fan-out contract — flag gating, one alert per NEW
position, and best-effort isolation — without touching sqlite or the network.
"""

from __future__ import annotations

import services.portfolio_tracker as pt
import dashboard.backend.db.portfolio as dbp
import services.portfolio_manager as pm


def _no_gate(monkeypatch):
    """These tests are DB-free and monkeypatch the seed, so no real Portfolio
    row exists. They verify the tracker's FAN-OUT contract (one alert per new
    row, isolation on failure), not admission — admission has its own suite in
    tests/test_entry_gate.py. Disable the gate so each test checks one thing."""
    monkeypatch.setenv("ENTRY_GATE_ENFORCE", "0")


def _patch(monkeypatch, new_rows, sink):
    _no_gate(monkeypatch)
    monkeypatch.setattr(dbp, "seed_portfolio_from_recommendations", lambda: new_rows)
    monkeypatch.setattr(pm, "send_portfolio_triggered_alert",
                        lambda *a, **k: sink.append(a))


def test_alerts_once_per_new_entry(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_LIVE_SYNC_ENABLED", "1")
    sink: list = []
    rows = [
        {"symbol": "NSE:IIFL", "horizon": "SWING", "entry_price": 544.7,
         "current_price": 552.7, "stop_loss": 525.24, "target_1": 576.81},
        {"symbol": "NSE:TCS", "horizon": "SWING", "entry_price": 3900.0,
         "current_price": 3900.0, "stop_loss": 3750.0, "target_1": None},
    ]
    _patch(monkeypatch, rows, sink)
    n = pt._sync_engine_trades()
    assert n == 2
    assert len(sink) == 2
    # alert carries (symbol, horizon, entry, trigger, stop, target_1)
    assert sink[0][0] == "NSE:IIFL" and sink[0][2] == 544.7


def test_no_new_rows_no_alert(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_LIVE_SYNC_ENABLED", "1")
    sink: list = []
    _patch(monkeypatch, [], sink)
    assert pt._sync_engine_trades() == 0
    assert sink == []


def test_flag_off_is_inert(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_LIVE_SYNC_ENABLED", "0")
    called = {"seed": False}

    def _seed():
        called["seed"] = True
        return [{"symbol": "X", "horizon": "SWING", "entry_price": 1.0,
                 "current_price": 1.0, "stop_loss": 0.9, "target_1": None}]

    monkeypatch.setattr(dbp, "seed_portfolio_from_recommendations", _seed)
    assert pt._sync_engine_trades() == 0
    assert called["seed"] is False  # gated BEFORE any DB work


def test_one_bad_alert_does_not_abort_rest(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_LIVE_SYNC_ENABLED", "1")
    sink: list = []
    rows = [
        {"symbol": "NSE:AAA", "horizon": "SWING", "entry_price": 10.0,
         "current_price": 10.0, "stop_loss": 9.0, "target_1": None},
        {"symbol": "NSE:BBB", "horizon": "SWING", "entry_price": 20.0,
         "current_price": 20.0, "stop_loss": 18.0, "target_1": None},
    ]
    _no_gate(monkeypatch)
    monkeypatch.setattr(dbp, "seed_portfolio_from_recommendations", lambda: rows)

    def _alert(symbol, *a, **k):
        if symbol == "NSE:AAA":
            raise RuntimeError("telegram down")
        sink.append(symbol)

    monkeypatch.setattr(pm, "send_portfolio_triggered_alert", _alert)
    n = pt._sync_engine_trades()
    assert n == 2               # both counted as seeded
    assert sink == ["NSE:BBB"]  # AAA's failure didn't block BBB
