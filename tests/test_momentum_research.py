"""Momentum research framework — unit tests (isolated; no network).

Covers stop/trail registries, the forward simulator (win/loss/trail/failed-
breakout/not-triggered), performance metrics, the backtest harness with
per-experiment recording + attribution, and the validation splitters.
"""

from __future__ import annotations

import pytest

from services.momentum_engine.research import (
    SimConfig, run_backtest, compare_configs, walk_forward_folds, time_split, stops, trailing,
)
from services.momentum_engine.research.simulator import simulate_trade
from services.momentum_engine.research.metrics import performance
from services.momentum_engine.research.models import SimTrade


def _bars(seq):
    """[(o,h,l,c)] -> candle dicts."""
    return [{"open": o, "high": h, "low": l, "close": c, "volume": 1_000_000, "date": f"f{i}"}
            for i, (o, h, l, c) in enumerate(seq)]


# ── Registries ───────────────────────────────────────────────────────────────
def test_stop_and_trail_registries():
    assert set(stops.available()) == {"structural", "atr_multiple", "pct_cap", "hybrid"}
    assert set(trailing.available()) == {"none", "atr_chandelier", "ema", "structure"}
    # hybrid respects the % cap
    s = stops.initial_stop("hybrid", 100.0, 80.0, 2.0, {"max_stop_pct": 10, "k": 1.5})
    assert 89.9 <= s <= 97.1  # not wider than 10%, not tighter than 1.5xATR


# ── Simulator ────────────────────────────────────────────────────────────────
def test_sim_winner_trails_up():
    cfg = SimConfig(stop_method="structural", trail_method="atr_chandelier",
                    trail_params={"k": 3.0}, max_hold_bars=20)
    # taps trigger 100, base_low 95 (atr 2), then runs to 130 and fades to 120.
    fwd = _bars([(100, 101, 99, 100.5), (101, 110, 100, 109), (109, 122, 108, 121),
                 (121, 131, 120, 130), (130, 131, 118, 120)])
    t = simulate_trade("X", trigger=100.0, base_low=95.0, atr=2.0, forward=fwd, config=cfg)
    assert t.entered and t.r_multiple is not None and t.r_multiple > 0
    assert t.exit_reason in ("TRAIL", "BREAKEVEN", "TIME")
    assert t.mfe_r >= t.r_multiple


def test_sim_loser_stops_out():
    cfg = SimConfig(stop_method="structural", trail_method="none", max_hold_bars=20)
    fwd = _bars([(100, 101, 99, 100), (100, 100, 94, 94.5)])  # taps then breaks base_low 95
    t = simulate_trade("X", trigger=100.0, base_low=95.0, atr=2.0, forward=fwd, config=cfg)
    assert t.entered and t.r_multiple is not None and t.r_multiple < 0


def test_sim_not_triggered():
    cfg = SimConfig(max_arm_bars=3)
    fwd = _bars([(90, 95, 89, 94)] * 5)  # never reaches trigger 100
    t = simulate_trade("X", 100.0, 95.0, 2.0, fwd, cfg)
    assert t.entered is False and t.exit_reason == "NOT_TRIGGERED"


# ── Metrics ──────────────────────────────────────────────────────────────────
def test_performance_block():
    trades = [
        SimTrade("A", True, 100, 130, "TRAIL", 3.0, 5, 3.2, -0.2, 90),
        SimTrade("B", True, 50, 45, "STOP", -1.0, 3, 0.4, -1.0, 40),
        SimTrade("C", True, 20, 26, "TRAIL", 2.0, 6, 2.1, -0.1, 15),
        SimTrade("D", False, None, None, "NOT_TRIGGERED", None, 0, None, None, None),
    ]
    p = performance(trades)
    assert p["n_trades"] == 3
    assert p["win_rate"] == pytest.approx(66.7, abs=0.2)
    assert p["expectancy_r"] == pytest.approx((3 - 1 + 2) / 3, abs=0.01)
    assert p["profit_factor"] == pytest.approx(5.0, abs=0.01)


# ── Backtest harness ─────────────────────────────────────────────────────────
def _leader_sample(scan_date="2026-05-01", regime="TRENDING_UP", sector="IT", rise_to=190.0):
    """A prepared sample: leader history + a forward runner. Passes eligibility
    and fires an entry; the forward path is a clean winner."""
    hist = []
    for i in range(200):
        p = 100 + (172 - 100) * (i / 199)
        hist.append({"open": p * 0.999, "high": p * 1.012, "low": p * 0.988, "close": p,
                     "volume": 1_000_000, "date": f"d{i}"})
    for j in range(8):
        p = 172 + (rise_to - 172) * (j / 7)
        hist.append({"open": p * 0.998, "high": p * 1.012, "low": p * 0.988, "close": p,
                     "volume": 1_100_000, "date": f"r{j}"})
    mid = rise_to + 1.0
    for j in range(14):
        cl = mid + (0.8 if j % 2 == 0 else -0.8)
        hist.append({"open": mid, "high": cl * 1.008, "low": cl * 0.99, "close": cl,
                     "volume": 850_000, "date": f"b{j}"})
    hist[-1] = {"open": mid - 0.5, "high": mid + 2.5, "low": mid - 1.0, "close": mid + 1.5,
                "volume": 2_400_000, "date": "trig"}
    nifty = [{"open": 100, "high": 100.6, "low": 99.4, "close": 100.0, "volume": 1, "date": f"n{i}"}
             for i in range(222)]
    # forward: breaks out and runs up
    top = mid + 2.0
    fwd = _bars([(top, top + 3, top - 1, top + 2), (top + 2, top + 10, top + 1, top + 9),
                 (top + 9, top + 20, top + 8, top + 19), (top + 19, top + 25, top + 15, top + 17),
                 (top + 17, top + 18, top + 5, top + 6)])
    return {"symbol": "LEAD", "scan_date": scan_date, "regime": regime, "sector": sector,
            "history": hist, "nifty": nifty, "forward": fwd, "breakout_score": 90}


def test_run_backtest_produces_result_and_records(monkeypatch):
    monkeypatch.setenv("MOMENTUM_ENGINE_ENABLED", "1")
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "0")
    samples = [_leader_sample(scan_date=f"2026-05-{d:02d}") for d in range(1, 12)]
    cfg = SimConfig(trail_method="atr_chandelier", label="baseline")
    result, records = run_backtest(samples, cfg)
    assert result.n_candidates == len(samples)
    assert result.n_entered >= 1
    assert len(records) >= 1
    r0 = records[0]
    # every experiment carries features + all four "why"s + outcome
    assert r0.rs_20d is not None and r0.quality_score is not None
    assert r0.why_qualified and r0.why_ranked and r0.why_entered and r0.why_exited
    assert r0.outcome in ("WIN", "LOSS", "SCRATCH", "NO_ENTRY")
    assert "TRENDING_UP" in result.by_regime


def test_compare_configs_ranks_and_regime_filter(monkeypatch):
    monkeypatch.setenv("MOMENTUM_ENGINE_ENABLED", "1")
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "0")
    samples = [_leader_sample(scan_date=f"2026-05-{d:02d}") for d in range(1, 9)]
    configs = [
        SimConfig(trail_method="none", label="no_trail"),
        SimConfig(trail_method="atr_chandelier", label="chandelier"),
        SimConfig(regime_filter=("TRENDING_DOWN",), label="wrong_regime"),  # filters everything out
    ]
    results = compare_configs(samples, configs)
    assert len(results) == 3
    wrong = [r for r in results if r.label == "wrong_regime"][0]
    assert wrong.n_entered == 0  # regime filter removed all


# ── Validation splitters ─────────────────────────────────────────────────────
def test_time_split_and_walk_forward_are_chronological():
    samples = [{"scan_date": f"2026-05-{d:02d}"} for d in range(1, 21)]
    tr, te = time_split(samples, 0.7)
    assert len(tr) == 14 and len(te) == 6
    assert max(s["scan_date"] for s in tr) <= min(s["scan_date"] for s in te)
    folds = walk_forward_folds(samples, k=4)
    assert len(folds) == 3
    for train, test in folds:
        assert max(s["scan_date"] for s in train) <= min(s["scan_date"] for s in test)
