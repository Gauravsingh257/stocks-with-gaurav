"""Tests for the historical Exceptionalism backtest (point-in-time + forward)."""

from __future__ import annotations

from datetime import date, timedelta

from scripts.exceptionalism_backtest import (
    ShadowRow, _attach_forward, _pick_as_of_dates, _pit_health, backtest,
)


def _mk_bars(n: int, start_close: float, step: float, start_day="2025-01-01"):
    d0 = date.fromisoformat(start_day)
    bars = []
    for i in range(n):
        c = start_close + i * step
        bars.append({"date": (d0 + timedelta(days=i)).isoformat(),
                     "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 100000 + i})
    return bars


# ── helpers ───────────────────────────────────────────────────────────────────

def test_pick_as_of_cadence():
    bars = _mk_bars(20, 100, 1)
    picks = _pick_as_of_dates(bars, bars[0]["date"], bars[-1]["date"], cadence=5)
    assert picks == [bars[i]["date"] for i in range(0, 20, 5)]


def test_pit_health_trend():
    up = _mk_bars(220, 100, 1)      # steady uptrend → price above both DMAs
    down = _mk_bars(220, 320, -1)   # steady downtrend → price below both DMAs
    assert _pit_health(up, 219, None) == 100.0
    assert _pit_health(down, 219, None) == 0.0
    # breadth blends in
    assert _pit_health(up, 219, 40.0) == round(0.5 * 100 + 0.5 * 40, 1)


def test_attach_forward_and_pending():
    bars = _mk_bars(130, 100, 1)          # close = 100 + i
    row = ShadowRow("BT", "AAA", bars[100]["date"], cmp=200.0, final_selected=True,
                    exceptionalism=80, threshold=70, qualifies=True, market_health=50, sector_band="leading")
    _attach_forward(row, bars, [b["date"] for b in bars], bars[100]["date"], horizons=(1, 3, 5, 10, 20, 40))
    # cmp at idx100 = 200; +5 bars → close 205 → +2.5%
    assert row.forward[5]["ret"] == round((205 - 200) / 200 * 100, 2)
    assert 20 in row.forward           # 130 bars → idx100 + 20 = 120 exists
    assert 40 not in row.forward       # idx100 + 40 = 140 > 129 → pending, not guessed


# ── end-to-end backtest exercises the real engine functions ───────────────────

def test_backtest_end_to_end_synthetic():
    hist = {
        "NSE:UP":   _mk_bars(140, 100, 1.2),    # strong uptrend
        "NSE:FLAT": _mk_bars(140, 200, 0.0),    # flat
        "NSE:DOWN": _mk_bars(140, 300, -0.8),   # downtrend
    }
    nifty = _mk_bars(140, 150, 0.3)
    as_of = hist["NSE:UP"][110]["date"]         # leaves ~29 forward bars
    rows = backtest(hist, nifty, as_of_dates=[as_of], horizons=(1, 5, 10, 20))
    assert rows, "expected verdicts"
    for r in rows:
        assert r.exceptionalism is not None and r.threshold is not None
        assert r.market_health is not None
    # the uptrend name should out-score the downtrend name (relative strength)
    by_sym = {r.symbol: r.exceptionalism for r in rows}
    if "NSE:UP" in by_sym and "NSE:DOWN" in by_sym:
        assert by_sym["NSE:UP"] > by_sym["NSE:DOWN"]
    # forward returns populated for covered horizons
    up = next((r for r in rows if r.symbol == "NSE:UP"), None)
    assert up is not None and up.forward.get(5) and up.forward[5]["ret"] is not None
