"""
Phase 2 — SMC as a ranking factor rather than a hard gate.

The property that matters most is REVERSIBILITY: with PHASE2_SMC_AS_SCORE unset,
every touched path must behave exactly as it does on main.
"""

from __future__ import annotations

import pytest

from services.phase2_ranking import (
    DEFAULT_WEIGHTS,
    rank_candidates,
    select_top,
    smc_as_score_enabled,
    weights,
)

# ── flag ──────────────────────────────────────────────────────────────────────

def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("PHASE2_SMC_AS_SCORE", raising=False)
    assert smc_as_score_enabled() is False


def test_flag_accepts_the_usual_truthy_spellings(monkeypatch):
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("PHASE2_SMC_AS_SCORE", v)
        assert smc_as_score_enabled() is True
    monkeypatch.setenv("PHASE2_SMC_AS_SCORE", "0")
    assert smc_as_score_enabled() is False


# ── weights ───────────────────────────────────────────────────────────────────

def test_default_weights_are_the_preregistered_set(monkeypatch):
    for env in ("PHASE2_W_MOM20", "PHASE2_W_SMC", "PHASE2_W_QUALITY", "PHASE2_W_MOM50"):
        monkeypatch.delenv(env, raising=False)
    w = weights()
    assert w == pytest.approx({"momentum20": .35, "smc": .25, "quality": .20, "momentum50": .20})
    assert sum(w.values()) == pytest.approx(1.0)


def test_weights_are_env_tunable_and_renormalised(monkeypatch):
    """The sweep could not separate the variants at 43 days, so the weights must
    be recalibratable on evidence later without a redeploy — and a partial
    override must not silently rescale the whole score."""
    monkeypatch.setenv("PHASE2_W_SMC", "0.0")
    w = weights()
    assert w["smc"] == 0.0
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["momentum20"] > DEFAULT_WEIGHTS["momentum20"]   # renormalised upward


def test_nonsense_weights_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("PHASE2_W_MOM20", "not-a-number")
    assert weights()["momentum20"] == pytest.approx(DEFAULT_WEIGHTS["momentum20"])
    for env in ("PHASE2_W_MOM20", "PHASE2_W_SMC", "PHASE2_W_QUALITY", "PHASE2_W_MOM50"):
        monkeypatch.setenv(env, "0")
    assert weights() == DEFAULT_WEIGHTS   # an all-zero set is not a ranking


# ── ranking ───────────────────────────────────────────────────────────────────

def _c(key, m20, smc, q, m50):
    return {"key": key, "momentum20": m20, "smc": smc, "quality": q, "momentum50": m50}


def test_stronger_candidate_ranks_first():
    out = rank_candidates([
        _c("weak", 1.0, 30.0, 0.5, 1.0),
        _c("strong", 20.0, 90.0, 0.9, 15.0),
        _c("mid", 8.0, 60.0, 0.7, 6.0),
    ])
    assert [r["key"] for r in out] == ["strong", "mid", "weak"]
    assert out[0]["score"] > out[-1]["score"]


def test_smc_orders_but_no_longer_rejects():
    """A stock with a weak SMC band must still be RANKED, not dropped — that is
    the whole change. It should sit below an otherwise-identical stock with a
    strong band, and above nothing at all."""
    out = rank_candidates([
        _c("weak_smc", 15.0, 0.0, 0.9, 12.0),
        _c("strong_smc", 15.0, 100.0, 0.9, 12.0),
    ])
    assert {r["key"] for r in out} == {"weak_smc", "strong_smc"}
    assert out[0]["key"] == "strong_smc"


def test_missing_factor_scores_at_the_cohort_mean():
    """A data gap must neither reward nor punish — otherwise the ranking quietly
    becomes a coverage gate."""
    out = rank_candidates([
        _c("a", 10.0, 50.0, 0.7, 5.0),
        _c("b", 20.0, 50.0, 0.7, 5.0),
        {"key": "gap", "smc": 50.0, "quality": 0.7, "momentum50": 5.0},  # no momentum20
    ])
    gap = next(r for r in out if r["key"] == "gap")
    assert gap["components"]["momentum20"] == pytest.approx(0.0)


def test_ranking_is_cross_sectional_not_absolute():
    """Scored within the scan. Shifting every candidate by a constant must not
    change the order — an absolute threshold would drift with the market and
    become a gate again."""
    base = [_c("a", 5.0, 40.0, 0.5, 4.0), _c("b", 9.0, 70.0, 0.8, 9.0)]
    lifted = [_c("a", 105.0, 40.0, 0.5, 104.0), _c("b", 109.0, 70.0, 0.8, 109.0)]
    assert [r["key"] for r in rank_candidates(base)] == [r["key"] for r in rank_candidates(lifted)]


def test_identical_candidates_do_not_explode():
    out = rank_candidates([_c("a", 5.0, 50.0, 0.5, 5.0), _c("b", 5.0, 50.0, 0.5, 5.0)])
    assert len(out) == 2
    assert all(r["score"] == pytest.approx(0.0) for r in out)


def test_empty_and_single_inputs():
    assert rank_candidates([]) == []
    one = rank_candidates([_c("solo", 5.0, 50.0, 0.5, 5.0)])
    assert len(one) == 1 and one[0]["score"] == pytest.approx(0.0)


# ── budget ────────────────────────────────────────────────────────────────────

def test_budget_is_respected_exactly():
    """Switching the flag must change WHICH stocks are chosen, never HOW MANY —
    everything downstream depends on the daily opportunity count."""
    cands = [_c(f"s{i}", float(i), float(i * 10), i / 10, float(i)) for i in range(20)]
    assert len(select_top(cands, 6)) == 6
    assert len(select_top(cands, 0)) == 0
    assert len(select_top(cands, 999)) == 20
    assert [r["key"] for r in select_top(cands, 3)] == ["s19", "s18", "s17"]


def test_ranking_does_not_mutate_its_input():
    cands = [_c("a", 5.0, 50.0, 0.5, 5.0), _c("b", 9.0, 70.0, 0.8, 9.0)]
    before = [dict(c) for c in cands]
    rank_candidates(cands)
    assert cands == before


# ── the flag must not become a no-op downstream ───────────────────────────────

class _Rec:
    def __init__(self, sym, final, exc=95.0, entry=100.0, layers=(True, True, True)):
        self.symbol = sym
        self.final_selected = final
        self.entry = entry
        self.layer1_pass, self.layer2_pass, self.layer3_pass = layers
        self.exceptionalism = {"qualifies": True, "exceptionalism": exc}
        self.rejection_reason = []


def test_exceptionalism_cannot_readmit_what_the_ranking_dropped(monkeypatch):
    """apply_exceptionalism_final_gate runs last and rewrites final_selected. If
    it ignored the ranking it would silently void the whole flag — the exact bug
    that made PHASE1_STRICT_FUNNEL a no-op in production."""
    from services.validation_engine import apply_exceptionalism_final_gate

    monkeypatch.setenv("PHASE2_SMC_AS_SCORE", "1")
    recs = [
        _Rec("KEPT", True, exc=80.0),
        _Rec("DROPPED_BY_RANK", False, exc=99.0),   # scores higher, but was trimmed
    ]
    selected, n = apply_exceptionalism_final_gate(recs, soft_ceiling=20)
    assert [r.symbol for r in selected] == ["KEPT"]
    assert n == 1


def test_exceptionalism_unchanged_when_flag_is_off(monkeypatch):
    from services.validation_engine import apply_exceptionalism_final_gate

    monkeypatch.delenv("PHASE2_SMC_AS_SCORE", raising=False)
    monkeypatch.delenv("PHASE1_STRICT_FUNNEL", raising=False)
    recs = [_Rec("A", False, exc=80.0), _Rec("B", False, exc=99.0)]
    selected, n = apply_exceptionalism_final_gate(recs, soft_ceiling=20)
    assert {r.symbol for r in selected} == {"A", "B"}   # gate decides, not final_selected
    assert n == 2


# ── sector overrides: precedence, and "unclassified never means excluded" ─────

def test_manual_override_outranks_every_automatic_tier(monkeypatch, tmp_path):
    import services.sector_classification as sc

    csv = tmp_path / "ov.csv"
    csv.write_text("symbol,sector\nRATNAVEER,Capital Goods\n", encoding="utf-8")
    monkeypatch.setattr(sc, "_OVERRIDE_PATH", csv)
    monkeypatch.setattr(sc, "_overrides", None)
    monkeypatch.setattr(sc, "_memo", {"RATNAVEER": "SHOULD_NOT_WIN"})
    monkeypatch.setattr(sc, "_yfinance_sector", lambda s: "ALSO_SHOULD_NOT_WIN")
    assert sc.resolve_sector("NSE:RATNAVEER") == "Capital Goods"


def test_unclassifiable_becomes_unassigned_not_dropped(monkeypatch, tmp_path):
    import services.sector_classification as sc

    monkeypatch.setattr(sc, "_OVERRIDE_PATH", tmp_path / "none.csv")
    monkeypatch.setattr(sc, "_overrides", None)
    monkeypatch.setattr(sc, "_memo", {})
    monkeypatch.setattr(sc, "_yfinance_sector", lambda s: None)
    monkeypatch.setattr(sc, "_legacy_sector", lambda s: None)
    assert sc.resolve_sector("NOTAREALTICKER") == sc.UNKNOWN


def test_unassigned_stays_eligible_for_selection(monkeypatch):
    """A stock we cannot classify must still be allowed to be found. Coverage is
    a limit of our reference data, not a property of the company."""
    from services.regime_governor import SECTOR_NOT_LAGGING, _sector_allows

    monkeypatch.delenv("SECTOR_UNASSIGNED_BLOCKS", raising=False)
    monkeypatch.setenv("PHASE1_SECTOR_UNKNOWN_STRICT", "1")
    assert _sector_allows("unknown", SECTOR_NOT_LAGGING) is True
    assert _sector_allows("lagging", SECTOR_NOT_LAGGING) is False


def test_unassigned_still_obeys_concentration_control(monkeypatch):
    """Eligible is not the same as unlimited — the diversification cap must
    still bind on the Unassigned bucket."""
    import services.regime_governor as rg

    monkeypatch.setenv("PHASE1_SECTOR_UNKNOWN_STRICT", "1")
    monkeypatch.setenv("MAX_PER_SECTOR", "2")
    monkeypatch.setattr(rg, "_sector_of", lambda sym, st: "Unknown")

    class _I:
        def __init__(self, s): self.symbol = s

    kept, diag = rg.enforce_sector_diversification(
        [_I(f"S{i}") for i in range(6)], symbol_of=lambda i: i.symbol, strength={"sectors": {}})
    assert len(kept) == 2 and diag["dropped"] == 4
    assert diag["per_sector"] == {"Unassigned": 2}


def test_shipped_override_file_is_loadable_and_has_priority_entries():
    """The owner's worksheet is committed; guard against it being emptied or
    reshaped by a future regeneration."""
    import csv
    import os

    path = "data/sector_overrides.csv"
    assert os.path.exists(path), "sector override worksheet is missing"
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) > 400, f"override file looks truncated: {len(rows)} rows"
    assert {"symbol", "sector"} <= set(rows[0].keys())
    assert all(r["sector"].strip() for r in rows), "an override row has no sector"
    assert not any(r["sector"].strip().lower() in ("unknown", "unassigned") for r in rows), \
        "Unassigned must not be written as an override — it is the fallback, not an assignment"


# ── horizon scope: validated on SWING, must not silently change Long-Term ────

def test_flag_applies_only_to_swing_by_default(monkeypatch):
    """The research measured SWING only and the phase brief said Long-Term must
    not change — but Long-Term auto-promotes into the live book, so an
    unscoped flag would move real positions on unvalidated evidence."""
    monkeypatch.setenv("PHASE2_SMC_AS_SCORE", "1")
    monkeypatch.delenv("PHASE2_HORIZONS", raising=False)
    assert smc_as_score_enabled("SWING") is True
    assert smc_as_score_enabled("LONGTERM") is False
    assert smc_as_score_enabled(None) is True          # "is it configured at all"


def test_horizon_scope_is_widenable_when_evidence_arrives(monkeypatch):
    monkeypatch.setenv("PHASE2_SMC_AS_SCORE", "1")
    monkeypatch.setenv("PHASE2_HORIZONS", "SWING,LONGTERM")
    assert smc_as_score_enabled("SWING") is True
    assert smc_as_score_enabled("LONGTERM") is True


def test_scope_is_irrelevant_while_the_master_flag_is_off(monkeypatch):
    monkeypatch.delenv("PHASE2_SMC_AS_SCORE", raising=False)
    monkeypatch.setenv("PHASE2_HORIZONS", "SWING,LONGTERM")
    for h in ("SWING", "LONGTERM", None):
        assert smc_as_score_enabled(h) is False


def test_longterm_exceptionalism_not_gutted_by_swing_only_scope(monkeypatch):
    """With the ranking skipped on LONGTERM, the exceptionalism gate must NOT
    intersect with final_selected — doing so would drop every long-term pick."""
    from services.validation_engine import apply_exceptionalism_final_gate

    monkeypatch.setenv("PHASE2_SMC_AS_SCORE", "1")
    monkeypatch.delenv("PHASE2_HORIZONS", raising=False)
    monkeypatch.delenv("PHASE1_STRICT_FUNNEL", raising=False)

    recs = [_Rec("A", False, exc=80.0), _Rec("B", False, exc=99.0)]
    for r in recs:
        r.horizon = "LONGTERM"
    selected, n = apply_exceptionalism_final_gate(recs, soft_ceiling=20)
    assert {r.symbol for r in selected} == {"A", "B"}
    assert n == 2
