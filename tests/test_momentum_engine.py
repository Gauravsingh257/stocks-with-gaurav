"""Momentum Continuation Engine — unit tests (isolated; no network).

Covers every stage: config defaults (engine OFF), candidate feed filtering,
metrics, eligibility, entry-model registry, ranking, regime router, and the
end-to-end orchestrator (disabled / regime-blocked / accepted paths).
"""

from __future__ import annotations

import os
import pytest

from services.momentum_engine import cfg, evaluate_candidate
from services.momentum_engine.candidate_feed import from_records
from services.momentum_engine.metrics import compute_metrics
from services.momentum_engine import eligibility, entry_models, ranking, router
from services.momentum_engine.models import MomentumCandidate


# ── Fixtures ─────────────────────────────────────────────────────────────────
def strong_leader():
    """A clean institutional leader: multi-month advance → moderate rise leg →
    tight OSCILLATING base (contracting volatility) near highs → volume-expansion
    press of the top. Passes every eligibility gate and fires a VCP entry.
    Consistent intrabar ranges throughout so ATR-percentile stays realistic."""
    candles = []
    for i in range(200):                      # long base advance 100 -> 172
        p = 100 + (172 - 100) * (i / 199)
        candles.append({"open": p * 0.999, "high": p * 1.012, "low": p * 0.988,
                        "close": p, "volume": 1_000_000, "date": f"d{i}"})
    for j in range(8):                        # moderate rise leg 172 -> 190
        p = 172 + (190 - 172) * (j / 7)
        candles.append({"open": p * 0.998, "high": p * 1.012, "low": p * 0.988,
                        "close": p, "volume": 1_100_000, "date": f"r{j}"})
    base_mid = 191.0
    for j in range(14):                       # tight oscillating base ~2% (VCP)
        cl = base_mid + (0.8 if j % 2 == 0 else -0.8)
        candles.append({"open": base_mid, "high": cl * 1.008, "low": cl * 0.99,
                        "close": cl, "volume": 850_000, "date": f"b{j}"})
    candles[-1] = {"open": 190.6, "high": 193.2, "low": 190.2, "close": 192.6,
                   "volume": 2_400_000, "date": "trig"}   # breakout press on 2.4x volume
    nifty = [{"open": 100, "high": 100.6, "low": 99.4, "close": 100.0,
              "volume": 1, "date": f"n{i}"} for i in range(222)]
    return candles, nifty


def _enable(monkeypatch, **over):
    monkeypatch.setenv("MOMENTUM_ENGINE_ENABLED", "1")
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "0")  # bypass live regime in unit tests
    for k, v in over.items():
        monkeypatch.setenv(k, str(v))


# ── Config ───────────────────────────────────────────────────────────────────
def test_engine_off_by_default():
    c = cfg()
    assert c["MOMENTUM_ENGINE_ENABLED"] is False
    assert c["MOMENTUM_SHADOW_ONLY"] is True
    assert c["MOMENTUM_ALLOCATION_PCT"] == 0.0


# ── Candidate feed (Phase 2) ─────────────────────────────────────────────────
def test_feed_only_discovery_passed_structural_rejects():
    records = [
        {"symbol": "NSE:LODHA", "horizon": "SWING", "date": "2026-07-01", "cmp": 1200,
         "layer1_pass": 1, "final_selected": 0, "rejection_reason": ["no_BOS", "no_liquidity_sweep"]},
        {"symbol": "TAKEN", "layer1_pass": 1, "final_selected": 1,  # SMC took it → excluded
         "cmp": 10, "rejection_reason": []},
        {"symbol": "NODISC", "layer1_pass": 0, "final_selected": 0,  # discovery failed → excluded
         "cmp": 10, "rejection_reason": ["no_BOS"]},
        {"symbol": "FUNDA", "layer1_pass": 1, "final_selected": 0,   # non-structural reject → excluded
         "cmp": 10, "rejection_reason": ["weak_fundamentals"]},
    ]
    cands = from_records(records)
    assert [c.symbol for c in cands] == ["LODHA"]
    assert "no_BOS" in cands[0].smc_rejection_reasons


# ── Metrics + eligibility (Phase 3) ──────────────────────────────────────────
def test_metrics_and_eligibility_pass_for_leader():
    candles, nifty = strong_leader()
    m = compute_metrics(candles, nifty)
    assert m is not None
    assert m["above_200dma"] and m["rs_20d"] is not None and m["rs_20d"] > 5
    e = eligibility.evaluate(m)
    assert e.passed, e.failures


def test_eligibility_blocks_over_extended(monkeypatch):
    candles, nifty = strong_leader()
    m = compute_metrics(candles, nifty)
    # Force an over-extension and confirm it's rejected.
    m2 = dict(m); m2["extension_atr"] = 9.0
    assert eligibility.evaluate(m2).passed is False


def test_eligibility_blocks_below_200dma():
    m = {"rs_20d": 10, "above_200dma": False, "turnover_cr": 50, "last": 100,
         "trend_quality": 0.9, "extension_atr": 1, "extension_pct": 2,
         "consecutive_up_days": 1, "atr_percentile": 0.5, "gap_pct": 0, "volume_ratio": 2, "pos52": 90}
    assert "below_200dma" in eligibility.evaluate(m).failures


# ── Entry models (Phase 4) ───────────────────────────────────────────────────
def test_entry_model_registry_has_only_approved():
    assert set(entry_models.available_models()) == {"vcp", "breakout", "shallow_pullback"}


def test_vcp_entry_triggers_on_tight_base():
    candles, nifty = strong_leader()
    m = compute_metrics(candles, nifty)
    sig = entry_models.detect_entry(m, candles)
    assert sig is not None
    assert sig.model in ("vcp", "breakout")
    assert sig.trigger > m["last"] * 0.98 and sig.stop < sig.trigger


# ── Ranking (Phase 5) ────────────────────────────────────────────────────────
def test_ranking_is_explainable_and_penalises_extension():
    candles, nifty = strong_leader()
    m = compute_metrics(candles, nifty)
    sig = entry_models.detect_entry(m, candles)
    q = ranking.score(m, sig, discovery_breakout_score=90, sector_score=0.9)
    assert 0 <= q.score <= 100 and "rs" in q.components
    over = dict(m); over["extension_atr"] = 5.0
    q2 = ranking.score(over, sig, discovery_breakout_score=90, sector_score=0.9)
    assert q2.penalties["extension"] > 0 and q2.score < q.score


# ── Router (Phase 6) ─────────────────────────────────────────────────────────
def test_router_gate_off_allows(monkeypatch):
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "0")
    ok, _ = router.regime_allows()
    assert ok is True

def test_router_blocks_disallowed_regime(monkeypatch):
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "1")
    ok, reg = router.regime_allows("TRENDING_DOWN")
    assert ok is False and reg == "TRENDING_DOWN"


# ── Orchestrator (end-to-end) ────────────────────────────────────────────────
def test_evaluate_disabled_returns_inert():
    cand = MomentumCandidate("LODHA", "SWING", "2026-07-01", 200.0)
    d = evaluate_candidate(cand, *strong_leader())
    assert d.accepted is False and d.stage == "disabled"


def test_evaluate_accepts_leader_when_enabled(monkeypatch):
    _enable(monkeypatch)
    candles, nifty = strong_leader()
    cand = MomentumCandidate("LODHA", "SWING", "2026-07-01", candles[-1]["close"], breakout_score=90)
    d = evaluate_candidate(cand, candles, nifty, sector_score=0.9)
    assert d.accepted is True and d.stage == "ranked"
    assert d.entry is not None and d.quality_score is not None


def test_evaluate_regime_blocks_when_downtrend(monkeypatch):
    monkeypatch.setenv("MOMENTUM_ENGINE_ENABLED", "1")
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "1")
    cand = MomentumCandidate("LODHA", "SWING", "2026-07-01", 200.0)
    d = evaluate_candidate(cand, *strong_leader(), regime="TRENDING_DOWN")
    assert d.accepted is False and d.stage == "router"
