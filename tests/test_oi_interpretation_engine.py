"""Tests for dashboard OI interpretation engine (Redis-backed deltas)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.backend.services.oi_interpretation_engine import (
    build_interpretation,
    compact_snapshot,
)


def _minimal_snapshot():
    return {
        "last_update": "2026-05-09T10:00:00",
        "pcr": 1.05,
        "pcr_trend": "FLAT",
        "overall_bias": "NEUTRAL",
        "confidence": 60,
        "strike_heatmap": [
            {
                "underlying": "NIFTY",
                "strike": 24000,
                "ce_oi": 1e6,
                "pe_oi": 1e6,
                "ce_change": 0,
                "pe_change": 0,
                "ce_status": "buildup",
                "pe_status": "buildup",
                "spot": 24000,
            },
        ],
        "underlying_summaries": {
            "NIFTY": {
                "spot": 24000,
                "max_call_strike": 24200,
                "max_put_strike": 23800,
                "bias": "BULLISH",
                "pcr": 1.05,
            },
        },
    }


def test_compact_snapshot_roundtrip_keys():
    snap = _minimal_snapshot()
    c = compact_snapshot(snap)
    assert "hm" in c and "ul" in c
    assert "NIFTY:24000" in c["hm"]
    assert c["hm"]["NIFTY:24000"]["ce"] == 1e6


def test_build_interpretation_no_prior():
    out = build_interpretation(_minimal_snapshot(), None, None)
    assert out["delta_vs_prior"]["has_prior"] is False
    assert "NIFTY" in out["guidance"]
    assert out["guidance"]["NIFTY"]["bullish_above"] is not None
    assert out.get("engine_version") == "oi_interp_v2"
    assert out.get("market_regime", {}).get("code")
    assert "confidence" in out and "bullish_pct" in out["confidence"]


def test_build_interpretation_pcr_momentum():
    cur = _minimal_snapshot()
    cur["pcr"] = 1.2
    prior = compact_snapshot(_minimal_snapshot())
    prior["pcr"] = 1.0
    out = build_interpretation(cur, prior, None)
    assert out["delta_vs_prior"]["has_prior"] is True
    assert out["delta_vs_prior"]["pcr_momentum"] == "bullish_shift"
