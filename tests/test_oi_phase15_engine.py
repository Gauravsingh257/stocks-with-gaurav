"""Tests for OI Phase 1.5 engine (digest + probability caps)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.backend.services.oi_phase15_engine import _cap_prob, build_interpretation_digest


def test_cap_prob_bounds():
    assert _cap_prob(200) == 85.0
    assert _cap_prob(-10) == 5.0
    assert _cap_prob(50) == 50.0


def test_build_interpretation_digest_minimal():
    d = build_interpretation_digest(
        {
            "market_regime": {"code": "range_bound", "label": "Range"},
            "market_story": {"headline": "Test headline for digest"},
            "oi_shifts": [{"type": "support_strengthening", "text": "x"}],
            "guidance": {"NIFTY": {"bullish_above": 24000, "bearish_below": 23800}},
            "delta_vs_prior": {"bias_snapshot": "BULLISH"},
            "confidence": {"bullish_pct": 60, "bearish_pct": 40},
            "engine_version": "oi_interp_v2p5",
            "generated_at": "2026-05-09T10:00:00Z",
        }
    )
    assert d["regime"] == "range_bound"
    assert d["top_story"] == "Test headline for digest"
    assert d["strongest_shift"] == "support_strengthening"
    assert d["top_guidance_underlying"] == "NIFTY"
