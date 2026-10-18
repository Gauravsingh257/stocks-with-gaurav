"""Guards on the FROZEN pre-registered ranking confirmation.

`scripts/ranking_confirmation.py` is the pre-registration expressed as code
(`docs/validation/PREREG-ranking-confirmation-2026-11.md`). These tests exist so
that it cannot drift from that document, and so that a later edit intended to
make a result pass fails loudly instead.

Everything here runs on **synthetic** data. The real confirmation cohort
(scan dates after 2026-09-17) is deliberately never read: inspecting it before
the gate opens is exactly the peeking the pre-registration forbids.
"""

from __future__ import annotations

import pytest

from scripts import ranking_confirmation as rc

# ── the frozen constants ─────────────────────────────────────────────────────


def test_frozen_parameters_match_the_preregistration():
    """If any of these change, the pre-registration needs a timestamped
    amendment committed BEFORE the run — not a quiet edit here."""
    assert rc.CANDIDATE_FEATURES == ("discovery.momentum_50d_pct",
                                     "discovery.breakout_score",
                                     "discovery.discovery_score")
    assert rc.INCUMBENT == "score_breakdown.composite"
    assert rc.HORIZON == 20
    assert rc.BASKET_N == 20
    assert rc.COST_ROUND_TRIP_PCT == 0.30
    assert rc.BOOTSTRAP_ITERS == 5000
    assert rc.SEED == 7
    assert rc.WINDOW_AFTER == "2026-09-17"
    assert rc.MIN_MATURED_DAYS == 15
    assert rc.MIN_CANDIDATES_PER_DAY == 30
    assert rc.MIN_PRICED_PCT == 75.0
    assert rc.MIN_FEATURE_COVERAGE_PCT == 80.0
    assert rc.MIN_EFFECT_PP == 1.00


# ── ranks, ties, percentiles ─────────────────────────────────────────────────


def test_average_ranks_for_ties():
    # 10 20 20 30 -> ranks 1, 2.5, 2.5, 4
    assert rc.average_ranks([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]


def test_percentiles_span_zero_to_one_and_are_scale_free():
    p = rc.percentiles([1.0, 2.0, 3.0, 4.0])
    assert p == [0.0, 1 / 3, 2 / 3, 1.0]
    # multiplying every value by 1000 cannot change a rank-based percentile
    assert rc.percentiles([1000.0, 2000.0, 3000.0, 4000.0]) == p


def test_all_tied_feature_gives_every_candidate_the_same_percentile():
    assert rc.percentiles([5.0] * 4) == [0.5] * 4


def test_cand_is_the_unweighted_mean_of_three_percentiles():
    rows = [{rc.F1: 1.0, rc.F2: 3.0, rc.F3: 1.0},
            {rc.F1: 2.0, rc.F2: 2.0, rc.F3: 2.0},
            {rc.F1: 3.0, rc.F2: 1.0, rc.F3: 3.0}]
    # F1 pct 0,.5,1 ; F2 pct 1,.5,0 ; F3 pct 0,.5,1  -> mean 1/3, .5, 2/3
    assert rc.cand_scores(rows) == pytest.approx([1 / 3, 0.5, 2 / 3])


def test_cand_weights_are_equal_so_no_feature_dominates_by_scale():
    """F1 on a huge scale must not outvote the others — percentiles prevent it."""
    rows = [{rc.F1: 1e9, rc.F2: 0.0, rc.F3: 0.0},
            {rc.F1: 0.0, rc.F2: 1.0, rc.F3: 1.0}]
    s = rc.cand_scores(rows)
    assert s[1] > s[0]


# ── basket construction ──────────────────────────────────────────────────────


def _rows(n, excess=None):
    return [{"symbol": f"S{i:03d}", "date": "2026-09-30",
             rc.F1: float(i), rc.F2: float(i), rc.F3: float(i),
             rc.INCUMBENT: float(i),
             "excess": (excess[i] if excess else float(i))} for i in range(n)]


def test_basket_size_is_respected_and_picks_the_top():
    rows = _rows(50)
    sel = rc.select(rows, [r[rc.INCUMBENT] for r in rows], rc.BASKET_N)
    assert len(sel) == rc.BASKET_N
    assert {r["symbol"] for r in sel} == {f"S{i:03d}" for i in range(30, 50)}


def test_basket_smaller_than_n_returns_everything_not_an_error():
    rows = _rows(5)
    assert len(rc.select(rows, [r[rc.INCUMBENT] for r in rows], rc.BASKET_N)) == 5


def test_r1_tie_break_is_ascending_symbol_and_deterministic():
    """All scores equal -> the first N symbols alphabetically, every time."""
    rows = _rows(40)
    flat = [1.0] * 40
    first = [r["symbol"] for r in rc.select(rows, flat, rc.BASKET_N)]
    second = [r["symbol"] for r in rc.select(list(reversed(rows)), flat, rc.BASKET_N)]
    assert first == sorted(first)
    assert first == second, "tie-break must not depend on input order"


def test_tie_break_rule_is_identical_for_every_comparator():
    """R1 must not quietly favour B: the same helper selects all four baskets."""
    rows = _rows(40)
    flat = [0.5] * 40
    picks = {k: [r["symbol"] for r in rc.select(rows, flat, rc.BASKET_N)]
             for k in ("A", "B", "C", "D")}
    assert len({tuple(v) for v in picks.values()}) == 1


# ── costs ────────────────────────────────────────────────────────────────────


def test_cost_is_subtracted_from_the_absolute_level():
    rows = [{"excess": 2.0}, {"excess": 4.0}]
    assert rc.basket_return(rows) == pytest.approx(3.0 - rc.COST_ROUND_TRIP_PCT)


def test_cost_cancels_from_any_paired_difference():
    """The pre-registration leans on this: the cost cannot move the primary."""
    a = rc.basket_return([{"excess": 1.0}])
    b = rc.basket_return([{"excess": 4.0}])
    assert (b - a) == pytest.approx(3.0)


def test_basket_return_is_none_when_nothing_is_priceable():
    assert rc.basket_return([{"excess": None}, {"excess": None}]) is None


# ── missing data ─────────────────────────────────────────────────────────────


def test_missing_excess_values_are_excluded_not_treated_as_zero():
    """A zero would be a fabricated outcome and would drag the mean toward 0."""
    assert rc.basket_return([{"excess": 10.0}, {"excess": None}]) == \
        pytest.approx(10.0 - rc.COST_ROUND_TRIP_PCT)


def test_single_candidate_day_cannot_divide_by_zero():
    assert rc.percentiles([1.0]) == [0.0]


# ── the random control ───────────────────────────────────────────────────────


def test_control_seed_is_reproducible_across_processes():
    """SHA-256, not hash(): Python salts string hashing per process, which would
    make comparator C irreproducible between runs."""
    assert rc.day_seed("2026-09-30") == rc.day_seed("2026-09-30")
    assert rc.day_seed("2026-09-30") != rc.day_seed("2026-10-01")
    # pinned value — changing the derivation changes the frozen control
    assert rc.day_seed("2026-09-30") == int(
        __import__("hashlib").sha256(b"7|2026-09-30").hexdigest()[:8], 16)


def test_control_ordering_does_not_depend_on_day_iteration_order():
    import random as _r
    a = [_r.Random(rc.day_seed("2026-10-05")).random() for _ in range(3)]
    b = [_r.Random(rc.day_seed("2026-10-05")).random() for _ in range(3)]
    assert a == b


# ── bootstrap ────────────────────────────────────────────────────────────────


def test_bootstrap_is_deterministic_for_the_same_input():
    diffs = {f"2026-10-{i:02d}": 0.5 * ((-1) ** i) + 1.0 for i in range(1, 21)}
    assert rc.paired_bootstrap(diffs) == rc.paired_bootstrap(diffs)


def test_bootstrap_withholds_below_the_minimum_day_count():
    diffs = {f"2026-10-{i:02d}": 1.0 for i in range(1, rc.MIN_MATURED_DAYS)}
    assert rc.paired_bootstrap(diffs)["withheld"] is True


def test_bootstrap_ci_excludes_zero_only_when_it_should():
    strong = {f"2026-10-{i:02d}": 5.0 for i in range(1, 21)}
    assert rc.paired_bootstrap(strong)["excludes_zero"] is True
    noisy = {f"2026-10-{i:02d}": (5.0 if i % 2 else -5.0) for i in range(1, 21)}
    assert rc.paired_bootstrap(noisy)["excludes_zero"] is False


# ── decision gates ───────────────────────────────────────────────────────────


def _primary(mean, excl=True):
    return {"withheld": False, "mean": mean, "excludes_zero": excl}


def test_pass_requires_effect_size_and_ci_and_coverage_and_control():
    assert rc.decide(True, True, _primary(1.5)) == rc.VERDICT_PASS


def test_effect_below_the_floor_fails_even_with_a_tight_ci():
    """The pre-registered floor exists precisely to stop a real triviality
    passing — this is where the LONGTERM confidence result would have slipped
    through in the earlier study."""
    assert rc.decide(True, True, _primary(0.99)) == rc.VERDICT_FAIL


def test_ci_including_zero_fails_even_with_a_large_mean():
    assert rc.decide(True, True, _primary(5.0, excl=False)) == rc.VERDICT_FAIL


def test_failed_coverage_is_insufficient_not_fail():
    assert rc.decide(False, True, _primary(5.0)) == rc.VERDICT_INSUFFICIENT


def test_failed_control_voids_the_study_whatever_the_primary_says():
    """A random ranking that appears to beat the incumbent means the harness is
    broken; the primary must not be reported as a result."""
    assert rc.decide(True, False, _primary(9.9)) == rc.VOID


def test_coverage_failure_takes_precedence_over_control_failure():
    assert rc.decide(False, False, _primary(9.9)) == rc.VERDICT_INSUFFICIENT


def test_withheld_primary_is_insufficient():
    assert rc.decide(True, True, {"withheld": True}) == rc.VERDICT_INSUFFICIENT


# ── leakage ──────────────────────────────────────────────────────────────────


def test_window_is_strictly_after_the_prior_studies_last_day():
    """Prior work tested up to 2026-09-17. The SQL uses `date > ?`, so 09-17
    itself is excluded and the cohorts cannot overlap."""
    assert rc.WINDOW_AFTER == "2026-09-17"


def test_cand_score_uses_no_outcome_information():
    """Leakage guard: CAND must be computable from features alone. Flipping
    every realised outcome must not move a single score."""
    rows = _rows(40)
    before = rc.cand_scores(rows)
    for r in rows:
        r["excess"] = -r["excess"]
    assert rc.cand_scores(rows) == before


def test_selection_uses_no_outcome_information():
    rows = _rows(40)
    sel_before = [r["symbol"] for r in rc.select(rows, rc.cand_scores(rows), rc.BASKET_N)]
    for r in rows:
        r["excess"] = 999.0 if r["symbol"] < "S020" else -999.0
    sel_after = [r["symbol"] for r in rc.select(rows, rc.cand_scores(rows), rc.BASKET_N)]
    assert sel_before == sel_after


# ── secondaries ──────────────────────────────────────────────────────────────


def test_partial_spearman_is_undefined_when_candidate_perfectly_tracks_incumbent():
    """If CAND were a monotone copy of the incumbent, the partial correlation is
    mathematically UNDEFINED, not zero — the denominator sqrt(1 - r_xz^2)
    vanishes. Returning None is correct and must not be papered over with 0.0,
    which would read as "measured, and no effect".
    """
    y = [1.0, 3.0, 2.0, 5.0, 4.0, 6.0, 8.0, 7.0]
    z = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    x = [v * 2 for v in z]
    assert rc.partial_spearman(y, x, z) is None


def test_partial_spearman_collapses_toward_zero_when_nearly_collinear():
    """One rank swap away from collinear: defined, and close to nothing."""
    y = [1.0, 3.0, 2.0, 5.0, 4.0, 6.0, 8.0, 7.0]
    z = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    x = [2.0, 1.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    r = rc.partial_spearman(y, x, z)
    assert r is not None and abs(r) < 0.35


def test_partial_spearman_survives_when_the_candidate_is_independent():
    y = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    z = [5.0, 1.0, 8.0, 2.0, 7.0, 3.0, 6.0, 4.0]
    r = rc.partial_spearman(y, x, z)
    assert r is not None and r > 0.5


def test_partial_spearman_needs_a_minimum_sample():
    assert rc.partial_spearman([1.0, 2.0], [1.0, 2.0], [1.0, 2.0]) is None


def test_holm_is_more_conservative_than_raw_alpha():
    """Three secondaries, so the smallest p must clear 0.05/3 = 0.01667 — a bar
    that raw alpha=0.05 would wave through."""
    out = rc.holm({"s1": 0.01, "s2": 0.04, "s3": 0.20})
    assert out["s1"] is True          # 0.01 <= 0.05/3 = 0.01667
    assert out["s2"] is False         # 0.04 >  0.05/2 = 0.025
    assert out["s3"] is False         # Holm stops once one fails


def test_holm_rejects_a_p_value_that_raw_alpha_would_accept():
    """p=0.02 passes alpha=0.05 alone but fails Holm across three tests."""
    assert rc.holm({"s1": 0.02, "s2": 0.30, "s3": 0.40})["s1"] is False


# ── R5: duplicate (scan_date, symbol) handling ───────────────────────────────
# The scanner runs several times a day, so 55% of rows in the confirmation
# window are repeats of a symbol already seen that day (57,200 of 103,400 on
# 2026-10-19). Which scan is kept must be decided by the query, not by SQLite's
# physical row order.

def _mk_db(tmp_path, rows):
    """rows = [(id, date, symbol, layer_details_json)]"""
    import json as _j
    import sqlite3 as _s
    p = tmp_path / "d.db"
    con = _s.connect(p)
    con.execute("CREATE TABLE signals_log (id INTEGER PRIMARY KEY, date TEXT, "
                "symbol TEXT, horizon TEXT, layer_details TEXT)")
    for i, d, sym, payload in rows:
        con.execute("INSERT INTO signals_log VALUES (?,?,?,?,?)",
                    (i, d, sym, "SWING", _j.dumps(payload)))
    con.commit()
    con.close()
    return str(p)


def _payload(v):
    return {"discovery": {"momentum_50d_pct": v, "breakout_score": v,
                          "discovery_score": v},
            "score_breakdown": {"composite": v},
            "_pad": "x" * 60}


def test_duplicate_date_symbol_keeps_the_lowest_id(tmp_path):
    """R5: the earliest persisted scan of that date wins — scans publish
    pre-open, so the earliest is closest to when the ranking was published."""
    db = _mk_db(tmp_path, [(5, "2026-09-30", "NSE:AAA", _payload(99.0)),
                           (2, "2026-09-30", "NSE:AAA", _payload(11.0))])
    panel, meta = rc.build_panel(db, {}, "SWING")
    assert meta["counts"]["duplicate_symbol_day"] == 1
    assert len(panel["2026-09-30"]) == 1
    assert panel["2026-09-30"][0][rc.INCUMBENT] == 11.0, "kept the wrong scan"


def test_duplicate_handling_is_independent_of_insertion_order(tmp_path):
    """Inserting the same two rows in the opposite physical order must not
    change which one survives."""
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    rows = [(2, "2026-09-30", "NSE:AAA", _payload(11.0)),
            (5, "2026-09-30", "NSE:AAA", _payload(99.0))]
    pa, _ = rc.build_panel(_mk_db(dir_a, rows), {}, "SWING")
    pb, _ = rc.build_panel(_mk_db(dir_b, list(reversed(rows))), {}, "SWING")
    assert pa["2026-09-30"][0][rc.INCUMBENT] == pb["2026-09-30"][0][rc.INCUMBENT]


def test_rows_before_the_window_are_never_loaded(tmp_path):
    """Leakage guard at the SQL boundary: 2026-09-17 itself is excluded."""
    db = _mk_db(tmp_path, [(1, "2026-09-17", "NSE:OLD", _payload(1.0)),
                           (2, "2026-09-18", "NSE:NEW", _payload(1.0))])
    panel, _ = rc.build_panel(db, {}, "SWING")
    assert "2026-09-17" not in panel
    assert "2026-09-18" in panel


def test_row_missing_the_incumbent_is_excluded_from_all_baskets(tmp_path):
    """R2: basket A is undefined without composite, so the row leaves the pool
    entirely — every comparator must choose from an identical set."""
    bad = {"discovery": {"momentum_50d_pct": 1.0, "breakout_score": 1.0,
                         "discovery_score": 1.0}, "_pad": "x" * 60}
    db = _mk_db(tmp_path, [(1, "2026-09-18", "NSE:AAA", bad),
                           (2, "2026-09-18", "NSE:BBB", _payload(1.0))])
    panel, meta = rc.build_panel(db, {}, "SWING")
    assert meta["counts"]["excluded_missing_feature"] == 1
    assert [r["symbol"] for r in panel["2026-09-18"]] == ["NSE:BBB"]


def test_row_missing_one_candidate_feature_is_excluded(tmp_path):
    part = {"discovery": {"momentum_50d_pct": 1.0, "breakout_score": 1.0},
            "score_breakdown": {"composite": 1.0}, "_pad": "x" * 60}
    db = _mk_db(tmp_path, [(1, "2026-09-18", "NSE:AAA", part)])
    panel, meta = rc.build_panel(db, {}, "SWING")
    assert meta["counts"]["excluded_missing_feature"] == 1
    assert panel.get("2026-09-18", []) == []
