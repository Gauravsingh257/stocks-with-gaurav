"""EP3 tests — per-recommendation decision trace + selectivity explainer."""

from __future__ import annotations

from services.decision_trace import build_decision_trace, selectivity_explainer


def _verdict(score=92, threshold=90, reason="qualified", band="leading", health=34):
    return {"exceptionalism": score, "threshold": threshold, "qualifies": score >= threshold,
            "reason": reason, "sector_band": band, "market_health": health}


def test_trace_clears_bar():
    t = build_decision_trace(symbol="NSE:ABC", exceptionalism=_verdict(), entry_state="READY",
                             confidence=72, setup="SMC_SWING")
    assert t["headline"] == "Clears today's bar"
    assert t["why_short"] == "EXC 92 ≥ 90"
    assert "exceptionalism 92" in t["trace"].lower() or "92 clears" in t["trace"].lower()
    labels = {f["label"] for f in t["factors"]}
    assert {"Exceptionalism", "Market Health", "Sector", "Entry"} <= labels


def test_trace_exceptional_override():
    t = build_decision_trace(symbol="ABC", exceptionalism=_verdict(score=93, reason="exceptional_override", band="lagging"),
                             entry_state="READY")
    assert t["headline"] == "Exceptional override"
    assert "override" in t["why_short"]
    assert "override" in t["trace"].lower()


def test_trace_below_bar():
    t = build_decision_trace(symbol="ABC", exceptionalism=_verdict(score=70, threshold=88, reason="below_threshold_88"))
    assert t["headline"] == "Below today's bar"
    assert "70 <" in t["why_short"]


def test_trace_handles_missing_inputs():
    t = build_decision_trace(symbol="ABC", exceptionalism=None)
    assert t["headline"] == "Below today's bar"
    assert isinstance(t["factors"], list)


def test_selectivity_explainer_scarce_zero():
    msg = selectivity_explainer("SCARCE", shown=0, market_health=20)
    assert "cash" in msg.lower()


def test_selectivity_explainer_selective_few():
    msg = selectivity_explainer("SELECTIVE", shown=2, market_health=34)
    assert "2 names" in msg and "bar is raised" in msg.lower()


def test_selectivity_explainer_healthy():
    msg = selectivity_explainer("RICH", shown=12, market_health=78)
    assert "healthy" in msg.lower()
