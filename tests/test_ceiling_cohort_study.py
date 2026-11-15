"""Cohort semantics, the era guard, and truncation refusal.

These pin the three defects an independent review of PR #229 found: a cohort that
did not mean what it was labelled, a `SELECTED` definition that changed meaning
mid-window, and an export that truncated silently.
"""

from __future__ import annotations

import pytest

from scripts.ceiling_cohort_study import (
    SELECTED_ERA_FROM,
    TruncatedExportError,
    _cohort,
    _export,
)


def _row(**kw):
    base = {"symbol": "NSE:AAA", "date": "2026-09-01", "final_selected": 0,
            "layer1_pass": 1, "layer2_pass": 1, "entry": 100.0, "rejection_reason": []}
    base.update(kw)
    return base


# ── cohort semantics ─────────────────────────────────────────────────────────

def test_selected_wins_regardless_of_other_fields():
    assert _cohort(_row(final_selected=1, rejection_reason=["no_BOS"])) == "SELECTED"


def test_ceiling_cut_requires_an_empty_rejection_reason():
    """The whole point: an un-rejected eligible row is what the ceiling kept out."""
    assert _cohort(_row(rejection_reason=[])) == "CEILING_CUT"


@pytest.mark.parametrize("reasons", [
    ["no_BOS"],
    ["no_BOS", "no_liquidity_sweep"],
    ["weak_trend"],
    ["no_order_block"],
    ["no_BOS", "no_liquidity_sweep", "weak_fundamentals"],
])
def test_structural_rejects_are_excluded_not_pooled_into_ceiling_cut(reasons):
    """~27% of the old CEILING_CUT carried these — cut for structure, not the ceiling."""
    assert _cohort(_row(rejection_reason=reasons)) is None


def test_below_rank_budget_is_phase2_even_with_other_reasons():
    assert _cohort(_row(rejection_reason=["below_rank_budget"])) == "PHASE2_CUT"
    assert _cohort(_row(rejection_reason=["below_rank_budget", "weak_trend"])) == "PHASE2_CUT"


def test_ineligible_rows_are_not_a_cohort():
    assert _cohort(_row(layer1_pass=0)) is None
    assert _cohort(_row(layer2_pass=0)) is None
    assert _cohort(_row(entry=None)) is None


# ── era guard ────────────────────────────────────────────────────────────────

def test_era_boundary_is_the_phase01_date():
    assert SELECTED_ERA_FROM == "2026-08-23"


def test_era_guard_drops_pre_boundary_selected_but_keeps_rejected_cohorts():
    """Replicates the filter main() applies, on the dates that exposed the break.

    2026-07-06 carried 8,088 `final_selected` rows against 20/day after the
    boundary, so pooling the two eras compares different things. The rejected
    cohorts are not era-dependent and must survive the guard.
    """
    rows = [
        _row(date="2026-07-06", final_selected=1),      # pre-era SELECTED -> dropped
        _row(date="2026-08-22", final_selected=1),       # day before -> dropped
        _row(date="2026-08-23", final_selected=1),       # boundary itself -> kept
        _row(date="2026-09-15", final_selected=1),       # post-era -> kept
        _row(date="2026-07-06", rejection_reason=[]),    # pre-era CEILING_CUT -> kept
        _row(date="2026-07-06", rejection_reason=["below_rank_budget"]),
    ]
    kept, dropped = [], 0
    for r in rows:
        coh = _cohort(r)
        if coh is None:
            continue
        if coh == "SELECTED" and str(r["date"])[:10] < SELECTED_ERA_FROM:
            dropped += 1
            continue
        kept.append((coh, r["date"]))

    assert dropped == 2
    assert ("SELECTED", "2026-08-23") in kept        # boundary is inclusive
    assert ("SELECTED", "2026-09-15") in kept
    assert ("CEILING_CUT", "2026-07-06") in kept     # rejected cohorts unaffected
    assert ("PHASE2_CUT", "2026-07-06") in kept
    assert not any(c == "SELECTED" and d < SELECTED_ERA_FROM for c, d in kept)


# ── export truncation ────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, n): self._n = n
    def raise_for_status(self): return None
    def json(self): return {"rows": [{"i": i} for i in range(self._n)]}


def test_export_raises_when_it_hits_the_row_cap(monkeypatch):
    """A capped page is indistinguishable from a complete one, so it must raise."""
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(500))
    with pytest.raises(TruncatedExportError) as e:
        _export("http://x", "selected", 120, "SWING", limit=500)
    assert "cap was hit" in str(e.value)


def test_export_returns_rows_below_the_cap(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(499))
    assert len(_export("http://x", "selected", 52, "SWING", limit=500)) == 499


def test_export_does_not_mistake_an_empty_page_for_truncation(monkeypatch):
    """A genuinely empty window is not truncation and must not raise."""
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(0))
    assert _export("http://x", "selected", 52, "SWING", limit=500) == []


def test_export_propagates_an_over_limit_http_error(monkeypatch):
    """Asking above the endpoint's 50k ceiling is a 422, not a silent empty page.

    An earlier review note of mine claimed over-limit requests came back empty.
    They do not — raise_for_status surfaces the 422 — so that path was never the
    silent one. Pinned here so the claim cannot drift back.
    """
    import requests

    class _Err:
        def raise_for_status(self):
            raise requests.exceptions.HTTPError("422 Client Error: Unprocessable Entity")

        def json(self):                      # pragma: no cover - never reached
            return {"rows": []}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Err())
    with pytest.raises(requests.exceptions.HTTPError):
        _export("http://x", "selected", 52, "SWING", limit=120000)
