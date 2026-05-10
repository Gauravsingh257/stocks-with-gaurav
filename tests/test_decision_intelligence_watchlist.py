"""Phase 5 decision intelligence — pure unit checks (no Redis)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.backend.services.decision_intelligence_engine import (  # noqa: E402
    _maturity_stage_and_score,
    build_virtual_portfolios,
)
from services.signal_delivery import apply_signal_confidence_prefix  # noqa: E402


def test_maturity_invalidated_zero_score():
    st, sc = _maturity_stage_and_score(None, "INVALIDATED", [], {}, 50.0, False, None)
    assert st == "INVALIDATED"
    assert sc == 0.0


def test_virtual_portfolios_rank_by_priority_and_cap():
    enriched = [
        {
            "symbol": "LOW",
            "horizon": "SWING",
            "decision": {"allocation": {"priority_score": 10.0}, "setup_maturity_score": 70},
        },
        {
            "symbol": "HIGH",
            "horizon": "SWING",
            "decision": {"allocation": {"priority_score": 90.0}, "setup_maturity_score": 60},
        },
    ]
    out = build_virtual_portfolios(enriched)
    assert out["mtf_swing"][0] == "HIGH"
    assert out["short_term_growth"][0] == "HIGH"
    assert out["slot_cap"] == 30


def test_telegram_confidence_prefix_inserts_tier():
    raw = "<b>Signal</b> test"
    out = apply_signal_confidence_prefix(raw, {"confidence_tier": "high conviction"})
    assert out.startswith("[HIGH]")
    assert "Signal" in out


def test_telegram_prefix_skips_without_tier():
    assert apply_signal_confidence_prefix("hello", {}) == "hello"
