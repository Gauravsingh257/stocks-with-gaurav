"""Tests for the exceptionalism final-selection gate (validation_engine)."""

from __future__ import annotations

from services.validation_engine import LayerValidationRecord, apply_exceptionalism_final_gate


def _rec(sym, exc_score, qualifies, entry=100.0):
    r = LayerValidationRecord(scan_id="S", horizon="SWING", symbol=sym, date="2026-07-27")
    r.entry = entry
    r.stop_loss = 95.0
    r.targets = [115.0]
    r.exceptionalism = {"exceptionalism": exc_score, "qualifies": qualifies, "threshold": 78}
    r.final_selected = True   # start SMC-final to prove the gate flips it
    return r


def test_gate_sets_final_to_qualified_top_n():
    recs = [
        _rec("A", 92, True), _rec("B", 85, True), _rec("C", 80, True),
        _rec("LOW", 60, False), _rec("MID", 70, False),
    ]
    selected, n_qual = apply_exceptionalism_final_gate(recs, soft_ceiling=2)
    assert n_qual == 3
    assert [r.symbol for r in selected] == ["A", "B"]              # top-2 by EXC
    assert {r.symbol: r.final_selected for r in recs} == {
        "A": True, "B": True, "C": False, "LOW": False, "MID": False,
    }


def test_gate_no_qualifiers_empties_final():
    recs = [_rec("X", 50, False), _rec("Y", 55, False)]
    selected, n_qual = apply_exceptionalism_final_gate(recs, soft_ceiling=5)
    assert selected == [] and n_qual == 0
    assert all(not r.final_selected for r in recs)   # cash-mode: nothing final


def test_gate_skips_records_without_entry():
    r = _rec("NOENTRY", 95, True)
    r.entry = None
    recs = [r, _rec("OK", 90, True)]
    selected, _ = apply_exceptionalism_final_gate(recs, soft_ceiling=5)
    assert [x.symbol for x in selected] == ["OK"]
    assert r.final_selected is False


def test_gate_clears_rejection_reason_on_selected():
    r = _rec("A", 90, True)
    r.rejection_reason = ["weak_trend"]
    apply_exceptionalism_final_gate([r], soft_ceiling=5)
    assert r.final_selected is True and r.rejection_reason == []
