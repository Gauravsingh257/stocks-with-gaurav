"""
tests/test_pil_accounting.py
============================
Unit tests for the Portfolio Intelligence Layer accounting/ledger reconstruction
(services/pil/accounting.py) and config (services/pil/config.py).

The ledger math is the foundation everything else depends on, so it is tested in
isolation by injecting synthetic lots via monkeypatching `_load_book` — no DB,
no network, fully deterministic.
"""

from __future__ import annotations

import os
import tempfile

# isolate any incidental DB access
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pil_test_"))

import pytest  # noqa: E402

from services.pil import accounting as acc  # noqa: E402
from services.pil import config as pil_config  # noqa: E402


def _open(symbol, entry, current, *, created="2026-01-01", size=None, days=5, sector="IT"):
    return {"symbol": symbol, "entry_price": entry, "current_price": current,
            "created_at": created, "entered_at": created, "position_size": size,
            "days_held": days, "sector": sector, "status": "ACTIVE"}


def _closed(symbol, entry, exit_p, *, created="2026-01-01", closed="2026-01-10",
            size=None, days=9, reason="TARGET_HIT"):
    return {"symbol": symbol, "entry_price": entry, "exit_price": exit_p,
            "created_at": created, "closed_at": closed, "position_size": size,
            "days_held": days, "exit_reason": reason, "sector": "IT"}


def _patch(monkeypatch, opens, closed, max_slots=5, capital=100_000.0):
    monkeypatch.setattr(acc, "_load_book",
                        lambda book: (opens, closed, max_slots, capital))


# ── empty book / cash conservation ───────────────────────────────────────────

def test_empty_book_is_all_cash(monkeypatch):
    _patch(monkeypatch, [], [])
    b = acc.reconstruct("SWING")
    assert b["cash"] == 100_000.0
    assert b["portfolio_value"] == 100_000.0
    assert b["invested"] == 0.0
    assert b["open_positions"] == 0
    assert b["total_return_pct"] == 0.0
    assert b["equity_curve"][-1]["value"] == 100_000.0


def test_closed_winner_books_realized_pnl(monkeypatch):
    # 1 slot of 5 → alloc 20k, qty 200, exit +10% → +2000 realized
    _patch(monkeypatch, [], [_closed("AAA", 100.0, 110.0)])
    b = acc.reconstruct("SWING")
    assert b["realized_pnl"] == pytest.approx(2000.0)
    assert b["cash"] == pytest.approx(102_000.0)
    assert b["portfolio_value"] == pytest.approx(102_000.0)
    assert b["total_return_pct"] == pytest.approx(2.0)
    t = b["closed_trades"][0]
    assert t["pnl"] == pytest.approx(2000.0)
    assert t["pnl_pct"] == pytest.approx(10.0)


def test_open_position_marks_to_market(monkeypatch):
    _patch(monkeypatch, [_open("BBB", 100.0, 120.0)], [])
    b = acc.reconstruct("SWING")
    assert b["invested"] == pytest.approx(20_000.0)
    assert b["market_value"] == pytest.approx(24_000.0)
    assert b["unrealized_pnl"] == pytest.approx(4_000.0)
    assert b["cash"] == pytest.approx(80_000.0)
    assert b["portfolio_value"] == pytest.approx(104_000.0)
    assert b["positions"][0]["weight_pct"] == pytest.approx(24_000.0 / 104_000.0 * 100, abs=0.01)


def test_cash_plus_market_value_equals_portfolio_value(monkeypatch):
    _patch(monkeypatch,
           [_open("BBB", 100.0, 120.0), _open("CCC", 50.0, 45.0)],
           [_closed("AAA", 100.0, 110.0)])
    b = acc.reconstruct("SWING")
    assert b["portfolio_value"] == pytest.approx(b["cash"] + b["market_value"])
    # initial + total_pnl == portfolio_value (accounting identity)
    assert b["initial_capital"] + b["total_pnl"] == pytest.approx(b["portfolio_value"], abs=0.01)


def test_momentum_uses_position_size(monkeypatch):
    # momentum lot with an explicit ₹ notional overrides equal-weight
    _patch(monkeypatch, [_open("MOM", 100.0, 100.0, size=50_000.0)], [],
           max_slots=20, capital=500_000.0)
    b = acc.reconstruct("MOMENTUM")
    assert b["invested"] == pytest.approx(50_000.0)
    assert b["cash"] == pytest.approx(450_000.0)
    assert b["portfolio_value"] == pytest.approx(500_000.0)


def test_allocation_never_exceeds_available_cash(monkeypatch):
    # 10 opens but only 5 slots: allocations must never drive cash negative
    opens = [_open(f"S{i}", 100.0, 100.0) for i in range(10)]
    _patch(monkeypatch, opens, [], max_slots=5)
    b = acc.reconstruct("SWING")
    assert b["cash"] >= -0.01
    assert b["portfolio_value"] == pytest.approx(100_000.0, abs=1.0)


# ── daily curve ──────────────────────────────────────────────────────────────

def test_daily_curve_is_regular_and_forward_filled(monkeypatch):
    _patch(monkeypatch, [], [_closed("AAA", 100.0, 110.0,
                                     created="2026-01-01", closed="2026-01-05")])
    b = acc.reconstruct("SWING")
    curve = b["equity_curve"]
    dates = [p["date"] for p in curve]
    assert dates == sorted(dates)             # chronological
    assert len(set(dates)) == len(dates)      # one point per day
    assert all(p["value"] > 0 for p in curve)


# ── combined ─────────────────────────────────────────────────────────────────

def test_combined_sums_books():
    a = acc._empty_book("SWING")
    a["portfolio_value"] = 102_000.0; a["cash"] = 102_000.0; a["realized_pnl"] = 2_000.0
    c = acc._empty_book("MOMENTUM")
    c["portfolio_value"] = 510_000.0; c["cash"] = 460_000.0
    c["market_value"] = 50_000.0; c["invested"] = 50_000.0; c["unrealized_pnl"] = 0.0
    combined = acc.combine([a, c])
    assert combined["book"] == "COMBINED"
    assert combined["portfolio_value"] == pytest.approx(612_000.0)
    assert combined["cash"] == pytest.approx(562_000.0)


# ── config ───────────────────────────────────────────────────────────────────

def test_default_book_capital(monkeypatch):
    for var in ("PIL_CAPITAL_SWING", "PIL_CAPITAL_LONGTERM", "PIL_CAPITAL_MOMENTUM"):
        monkeypatch.delenv(var, raising=False)
    # ensure no DB overrides interfere
    monkeypatch.setattr(pil_config, "_db_overrides", lambda: {})
    assert pil_config.book_capital("SWING") == 1_000_000.0
    assert pil_config.book_capital("LONGTERM") == 1_000_000.0
    assert pil_config.book_capital("MOMENTUM") == 500_000.0
    assert pil_config.combined_capital() == 2_500_000.0


def test_allocation_targets_normalise_to_one(monkeypatch):
    monkeypatch.setattr(pil_config, "_db_overrides", lambda: {})
    for b in ("SWING", "LONGTERM", "MOMENTUM"):
        monkeypatch.delenv(f"PIL_ALLOC_TARGET_{b}", raising=False)
    tgt = pil_config.allocation_targets()
    assert sum(tgt.values()) == pytest.approx(1.0, abs=1e-6)
    assert tgt["SWING"] > tgt["MOMENTUM"]


def test_enabled_defaults_on(monkeypatch):
    # PIL ships live: ON unless explicitly disabled.
    monkeypatch.delenv("PIL_ENABLED", raising=False)
    assert pil_config.enabled() is True
    monkeypatch.setenv("PIL_ENABLED", "0")
    assert pil_config.enabled() is False
