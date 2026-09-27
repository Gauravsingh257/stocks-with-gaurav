"""Debt/equity unit fix (Phase 4A) — correct conversion, flag-gated reach into live scoring."""
from __future__ import annotations

import pytest

from services import fundamental_analysis as fa

INFO = {
    "trailingPE": 28.0, "priceToBook": 7.5, "returnOnEquity": 0.31,
    "revenueGrowth": 0.06, "earningsGrowth": 0.08, "debtToEquity": 9.541,
    "heldPercentInsiders": 0.14, "heldPercentInstitutions": 0.71, "marketCap": 6.4e12,
}


def _legacy_score(info: dict) -> tuple[float, float, float | None]:
    """The pre-fix `_build_snapshot_from_info` arithmetic, copied verbatim."""
    pe = fa._num(info.get("trailingPE")) or fa._num(info.get("forwardPE"))
    pb = fa._num(info.get("priceToBook"))
    roe_raw = fa._num(info.get("returnOnEquity"))
    roe_pct = (roe_raw * 100) if roe_raw is not None else None
    rev_g = fa._num(info.get("revenueGrowth"))
    earn_g = fa._num(info.get("earningsGrowth"))
    de = fa._num(info.get("debtToEquity"))
    if de is not None and de > 10:
        de = de / 100.0
    promoter_pct = (fa._num(info.get("heldPercentInsiders")) or 0.0) * 100
    inst_pct = (fa._num(info.get("heldPercentInstitutions")) or 0.0) * 100
    earnings = fa._norm_earnings_growth(earn_g)
    revenue = fa._norm_revenue_growth(rev_g)
    roe_score = fa._norm_roe(roe_pct)
    debt_score = fa._norm_debt_equity(de)
    inst_score = fa._norm_institutional(inst_pct)
    promoter_score = fa._norm_promoter(promoter_pct)
    sector = fa._clamp01((fa._norm_pb(pb) + fa._norm_pe(pe)) / 2)
    delivery = fa._clamp01((earnings + revenue) / 2)
    mgmt = fa._clamp01((roe_score + debt_score) / 2)
    score = fa._weighted_score([
        (earnings, 0.14), (revenue, 0.14), (sector, 0.10), (inst_score, 0.10),
        (promoter_score, 0.08), (delivery, 0.08), (roe_score, 0.12),
        (roe_score, 0.10), (debt_score, 0.08), (mgmt, 0.06),
    ])
    return score, debt_score, de


@pytest.mark.parametrize("provider,expected", [
    (9.541, 0.0954), (10.211, 0.1021), (36.653, 0.3665), (195.001, 1.95), (3.016, 0.0302), (0.0, 0.0),
])
def test_provider_percent_converts_to_ratio(provider, expected):
    assert round(fa.debt_equity_ratio(provider), 4) == expected


def test_conversion_rejects_non_finite_and_strings():
    assert fa.debt_equity_ratio(None) is None
    assert fa.debt_equity_ratio(float("nan")) is None
    assert fa.debt_equity_ratio("n/a") is None
    assert fa.debt_equity_ratio("45.3") == pytest.approx(0.453)


def test_legacy_rule_is_the_bug_it_replaces():
    # INFY: a debt-free company read as 9.5x leverage under the old rule.
    assert fa.legacy_debt_equity(9.541) == 9.541
    assert fa.legacy_debt_equity(45.3) == pytest.approx(0.453)


def test_flag_off_is_byte_identical_to_pre_fix(monkeypatch):
    monkeypatch.delenv("FUND_DE_UNIT_FIX", raising=False)
    snap = fa._build_snapshot_from_info("NSE:INFY", INFO)
    score, debt, de = _legacy_score(INFO)
    assert snap.fundamental_score == score
    assert snap.debt_quality == debt == 0.0          # the bug, still live by default
    assert snap.raw_debt_equity == round(de, 2) == 9.54


def test_corrected_values_are_always_computed(monkeypatch):
    monkeypatch.delenv("FUND_DE_UNIT_FIX", raising=False)
    snap = fa._build_snapshot_from_info("NSE:INFY", INFO)
    assert snap.raw_debt_equity_provider == 9.541
    assert snap.debt_equity_ratio == 0.0954
    assert snap.debt_quality_de_fixed == pytest.approx(1 - 0.09541 / 4)
    # debt weight 0.08 + half of management's 0.06 = 0.11 per unit of debt_quality
    assert snap.fundamental_score_de_fixed - snap.fundamental_score == pytest.approx(
        0.11 * snap.debt_quality_de_fixed)


def test_flag_on_scores_with_the_corrected_ratio(monkeypatch):
    monkeypatch.setenv("FUND_DE_UNIT_FIX", "1")
    snap = fa._build_snapshot_from_info("NSE:INFY", INFO)
    assert snap.raw_debt_equity == 0.1
    assert snap.fundamental_score == snap.fundamental_score_de_fixed
    assert snap.debt_quality == snap.debt_quality_de_fixed


def test_high_leverage_unchanged_by_the_fix(monkeypatch):
    """Above 10% the old rule already divided — those names must not move."""
    monkeypatch.delenv("FUND_DE_UNIT_FIX", raising=False)
    info = {**INFO, "debtToEquity": 195.0}
    snap = fa._build_snapshot_from_info("NSE:TITAN", info)
    assert snap.fundamental_score == snap.fundamental_score_de_fixed


def test_hash_snapshot_has_no_corrected_fields():
    snap = fa._hash_snapshot("NSE:XYZ")
    assert snap.fundamental_score_de_fixed is None and snap.debt_equity_ratio is None


def test_cache_round_trip_keeps_corrected_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(fa, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(fa, "_fetch_yf_info", lambda s: dict(INFO))
    first = fa._fetch_snapshot("NSE:INFY")
    monkeypatch.setattr(fa, "_fetch_yf_info", lambda s: pytest.fail("cache miss"))
    again = fa._fetch_snapshot("NSE:INFY")
    assert again.fundamental_score_de_fixed == first.fundamental_score_de_fixed
    assert again.debt_equity_ratio == 0.0954


def test_impact_report_counts_only_names_the_fix_moves(monkeypatch):
    from scripts.measure_de_fix_impact import measure

    monkeypatch.delenv("FUND_DE_UNIT_FIX", raising=False)
    snaps = {f"NSE:S{i}": fa._build_snapshot_from_info(f"NSE:S{i}", {**INFO, "debtToEquity": de, "earningsGrowth": g})
             for i, (de, g) in enumerate([(3.0, 0.1), (8.0, 0.3), (45.0, 0.2), (150.0, 0.05), (None, 0.15)])}
    snaps["NSE:HASH"] = fa._hash_snapshot("NSE:HASH")
    rep = measure(snaps, control={"NSE:S0", "NSE:S2"})
    assert rep["real_fundamentals"] == 5 and rep["with_debt_equity"] == 4
    assert rep["touched_by_fix"] == 2 and rep["provider_range_of_touched"] == [3.0, 8.0]
    assert rep["delta_fundamental_score_touched"]["min"] > 0
    ctrl = rep["control_final_selected"]
    assert (ctrl["n"], ctrl["with_real_fundamentals"], ctrl["touched"]) == (2, 2, 1)


def test_impact_report_on_an_all_hash_sample_says_so():
    from scripts.measure_de_fix_impact import measure

    rep = measure({"NSE:A": fa._hash_snapshot("NSE:A")})
    assert rep["real_fundamentals"] == 0 and "error" in rep
