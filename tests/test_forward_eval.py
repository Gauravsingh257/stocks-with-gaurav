"""The shared forward-return evaluator: bias controls and parity.

Replaces four separate implementations (two of which read the stale
`forward_returns` table). These tests pin the properties that made
`scripts/ceiling_cohort_study.py` trustworthy.
"""

import pytest

from services.forward_eval import (
    Coverage,
    Observation,
    PriceBook,
    block_bootstrap,
    cohort_stats,
    dedupe,
    evaluate,
)


def bars(closes, start_day=1, highs=None, lows=None):
    """Bars dated 2026-01-DD ascending."""
    out = []
    for i, c in enumerate(closes):
        d = start_day + i
        out.append({
            "date": f"2026-01-{d:02d}",
            "close": c,
            "high": (highs[i] if highs else c),
            "low": (lows[i] if lows else c),
        })
    return out


# ── core measurement ─────────────────────────────────────────────────────────

def test_return_is_close_to_close_from_the_observation_date():
    book = PriceBook.from_mapping({"X": bars([100, 101, 102, 103, 104, 110])})
    oc = book.outcome("X", "2026-01-01", 5)
    assert oc.ret == pytest.approx(10.0)      # 100 -> 110


def test_mae_and_mfe_use_intraday_excursions_after_the_entry_bar():
    book = PriceBook.from_mapping({"X": bars(
        [100, 101, 102], highs=[100, 120, 102], lows=[100, 80, 102])})
    oc = book.outcome("X", "2026-01-01", 2)
    assert oc.mfe == pytest.approx(20.0) and oc.mae == pytest.approx(-20.0)
    # Bar 0's own high/low must NOT count — the entry bar is the base, not an excursion.
    book2 = PriceBook.from_mapping({"X": bars(
        [100, 101, 102], highs=[999, 101, 102], lows=[1, 101, 102])})
    oc2 = book2.outcome("X", "2026-01-01", 2)
    assert oc2.mfe == pytest.approx(2.0) and oc2.mae == pytest.approx(1.0)


def test_explicit_base_overrides_the_close():
    """Exceptionalism measures from the row's recorded cmp, not the bar close."""
    book = PriceBook.from_mapping({"X": bars([100, 110])})
    assert book.outcome("X", "2026-01-01", 1, base=50.0).ret == pytest.approx(120.0)


def test_nse_prefix_is_tolerated():
    book = PriceBook.from_mapping({"X": bars([100, 110])})
    assert book.outcome("NSE:X", "2026-01-01", 1).ret == pytest.approx(10.0)


# ── maturity gating / look-ahead ─────────────────────────────────────────────

def test_immature_horizon_returns_none_never_a_partial_window():
    book = PriceBook.from_mapping({"X": bars([100, 101, 102])})   # only 2 bars after
    assert book.outcome("X", "2026-01-01", 2) is not None
    assert book.outcome("X", "2026-01-01", 3) is None
    assert book.outcome("X", "2026-01-01", 20) is None


def test_no_look_ahead_bars_before_the_date_are_ignored():
    """A rally BEFORE the observation must not leak into the result."""
    book = PriceBook.from_mapping({"X": bars([10, 20, 50, 100, 101, 102])})
    oc = book.outcome("X", "2026-01-04", 2)     # base = 100
    assert oc.ret == pytest.approx(2.0)


def test_evaluate_counts_immature_per_horizon():
    book = PriceBook.from_mapping({"X": bars([100, 101, 102, 103])})
    priced, cov = evaluate([Observation("X", "2026-01-01", "A")], book, horizons=(2, 10))
    assert cov.priced == 1
    assert cov.immature[10] == 1 and cov.immature[2] == 0
    assert 2 in priced[0][1] and 10 not in priced[0][1]


# ── survivorship / coverage ──────────────────────────────────────────────────

def test_missing_symbol_is_reported_as_survivorship_not_dropped():
    book = PriceBook.from_mapping({"X": bars([100, 110])})
    obs = [Observation("X", "2026-01-01", "A"), Observation("DELISTED", "2026-01-01", "A")]
    priced, cov = evaluate(obs, book, horizons=(1,))
    assert len(priced) == 1
    assert cov.total == 2 and cov.priced == 1 and cov.no_series == 1
    d = cov.to_dict()
    assert d["unpriceable"] == 1 and d["priced_pct"] == 50.0


def test_non_trading_date_is_distinguished_from_missing_symbol():
    book = PriceBook.from_mapping({"X": bars([100, 110])})
    _priced, cov = evaluate([Observation("X", "2026-06-15", "A")], book, horizons=(1,))
    assert cov.no_bar_on_date == 1 and cov.no_series == 0


def test_zero_or_negative_base_is_refused():
    book = PriceBook.from_mapping({"X": [{"date": "2026-01-01", "close": 0},
                                         {"date": "2026-01-02", "close": 5}]})
    assert book.outcome("X", "2026-01-01", 1) is None


def test_nan_prices_do_not_leak():
    book = PriceBook.from_mapping({"X": [{"date": "2026-01-01", "close": 100},
                                         {"date": "2026-01-02", "close": float("nan")}]})
    assert book.outcome("X", "2026-01-01", 1) is None


# ── cohorts / dedupe ─────────────────────────────────────────────────────────

def test_dedupe_keeps_one_row_per_date_symbol_with_precedence():
    obs = [Observation("X", "2026-01-01", "CUT"), Observation("X", "2026-01-01", "SELECTED"),
           Observation("X", "2026-01-02", "CUT")]
    kept = dedupe(obs, precedence=("SELECTED", "CUT"))
    assert len(kept) == 2
    assert {o.cohort for o in kept if o.date == "2026-01-01"} == {"SELECTED"}


def test_cohort_stats_shape():
    book = PriceBook.from_mapping({"UP": bars([100, 110]), "DN": bars([100, 90])})
    priced, _ = evaluate([Observation("UP", "2026-01-01", "A"),
                          Observation("DN", "2026-01-01", "B")], book, horizons=(1,))
    s = cohort_stats(priced, 1)
    assert s["A"]["n"] == 1 and s["A"]["win_rate_pct"] == 100.0
    assert s["B"]["mean"] == pytest.approx(-10.0)


# ── date-block bootstrap ─────────────────────────────────────────────────────

def _two_cohort_book(days, a_ret, b_ret):
    series, obs = {}, []
    for i, day in enumerate(days):
        for tag, r in (("A", a_ret), ("B", b_ret)):
            sym = f"{tag}{i}"
            series[sym] = [{"date": day, "close": 100.0}, {"date": "2026-12-31", "close": 100.0 * (1 + r / 100)}]
            obs.append(Observation(sym, day, tag))
    return PriceBook.from_mapping(series), obs


def test_block_bootstrap_resamples_days_and_is_deterministic():
    days = [f"2026-02-{d:02d}" for d in range(1, 13)]
    book, obs = _two_cohort_book(days, a_ret=0.0, b_ret=5.0)
    priced, _ = evaluate(obs, book, horizons=(1,))
    r1 = block_bootstrap(priced, "A", "B", 1, iters=500, seed=7)
    r2 = block_bootstrap(priced, "A", "B", 1, iters=500, seed=7)
    assert r1 == r2, "same seed must reproduce"
    assert r1["days"] == len(days)
    assert r1["diff"] == pytest.approx(5.0, abs=0.01)
    assert r1["significant"] is True


def test_block_bootstrap_withholds_on_too_few_days():
    book, obs = _two_cohort_book(["2026-02-01", "2026-02-02"], 0.0, 5.0)
    priced, _ = evaluate(obs, book, horizons=(1,))
    out = block_bootstrap(priced, "A", "B", 1, iters=100)
    assert out["withheld"] is True and out["days"] == 2


def test_block_bootstrap_finds_no_edge_when_cohorts_match():
    days = [f"2026-03-{d:02d}" for d in range(1, 16)]
    book, obs = _two_cohort_book(days, a_ret=3.0, b_ret=3.0)
    priced, _ = evaluate(obs, book, horizons=(1,))
    out = block_bootstrap(priced, "A", "B", 1, iters=1000, seed=11)
    assert out["significant"] is False
    assert out["ci95"][0] <= 0 <= out["ci95"][1]


# ── parity with the original ceiling-study arithmetic ────────────────────────

def test_parity_with_original_ceiling_study_formula():
    """The extracted module must reproduce the inline arithmetic it replaces.

    Original (scripts/ceiling_cohort_study.py):
        rec[f"f{n}"] = (closes[j] / closes[i] - 1) * 100 if j < len(closes) and closes[i]
    """
    closes = [100.0, 103.5, 99.25, 107.0, 112.5, 95.0]
    book = PriceBook.from_mapping({"X": bars(closes)})
    for n in range(1, len(closes)):
        expected = (closes[n] / closes[0] - 1) * 100
        assert book.outcome("X", "2026-01-01", n).ret == pytest.approx(expected, abs=1e-6)
    assert book.outcome("X", "2026-01-01", len(closes)) is None     # immature, as before


def test_parity_with_exceptionalism_attach_forward_returns():
    """Reproduces scripts/exceptionalism_calibration.attach_forward_returns.

    Original: base = row.cmp; ret from bars[h].close; mae/mfe over bars[1:h+1];
    `len(bars) <= h` ⇒ pending.
    """
    closes = [100.0, 104.0, 98.0, 106.0]
    highs = [100.0, 108.0, 99.0, 107.0]
    lows = [100.0, 97.0, 92.0, 105.0]
    book = PriceBook.from_mapping({"X": bars(closes, highs=highs, lows=lows)})
    cmp_base, h = 101.0, 3
    oc = book.outcome("X", "2026-01-01", h, base=cmp_base)
    assert oc.ret == pytest.approx((closes[h] - cmp_base) / cmp_base * 100.0, abs=1e-6)
    assert oc.mfe == pytest.approx((max(highs[1:h + 1]) - cmp_base) / cmp_base * 100.0, abs=1e-6)
    assert oc.mae == pytest.approx((min(lows[1:h + 1]) - cmp_base) / cmp_base * 100.0, abs=1e-6)
    assert book.outcome("X", "2026-01-01", len(closes), base=cmp_base) is None


def test_fetcher_source_matches_mapping_source():
    """A lazy fetcher and an in-memory mapping must agree exactly."""
    data = bars([100.0, 105.0, 110.0])
    mapped = PriceBook.from_mapping({"X": data})
    fetched = PriceBook.from_fetcher(lambda sym, frm: [b for b in data if b["date"] >= frm])
    for n in (1, 2):
        assert mapped.outcome("X", "2026-01-01", n).ret == fetched.outcome("X", "2026-01-01", n).ret


# ── enrich_rows: the drop-in for the stale `forward_returns` join ────────────

def test_enrich_rows_populates_the_same_keys_the_sql_join_did():
    from services.forward_eval import enrich_rows
    book = PriceBook.from_mapping({"X": bars([100.0] + [110.0] * 25)})
    rows = [{"symbol": "X", "date": "2026-01-01"}]
    cov = enrich_rows(rows, book, windows=(20,))
    assert rows[0]["fwd_20d_pct"] == pytest.approx(10.0)
    assert cov.priced == 1 and cov.to_dict()["unpriceable"] == 0


def test_enrich_rows_reports_unpriceable_instead_of_hiding_it():
    from services.forward_eval import enrich_rows
    book = PriceBook.from_mapping({"X": bars([100.0, 110.0])})
    rows = [{"symbol": "GONE", "date": "2026-01-01"}, {"symbol": "X", "date": "2026-01-01"}]
    cov = enrich_rows(rows, book, windows=(20,))
    # Same shape an unlabelled LEFT JOIN produced: the key exists and is None.
    assert rows[0]["fwd_20d_pct"] is None and rows[1]["fwd_20d_pct"] is None
    assert cov.no_series == 1 and cov.immature[20] == 1 and cov.priced == 0


def test_enrich_rows_excess_is_benchmark_relative():
    from services.forward_eval import enrich_rows
    book = PriceBook.from_mapping({"X": bars([100.0, 100.0, 112.0])})
    bench = bars([100.0, 100.0, 105.0])          # benchmark +5% over the window
    rows = [{"symbol": "X", "date": "2026-01-01"}]
    enrich_rows(rows, book, windows=(2,), benchmark=bench)
    assert rows[0]["fwd_2d_pct"] == pytest.approx(12.0)
    assert rows[0]["excess_2d_pct"] == pytest.approx(7.0)


def test_enrich_rows_excess_is_none_when_benchmark_lacks_the_date():
    from services.forward_eval import enrich_rows
    book = PriceBook.from_mapping({"X": bars([100.0, 100.0, 112.0])})
    rows = [{"symbol": "X", "date": "2026-01-01"}]
    enrich_rows(rows, book, windows=(2,), benchmark=bars([100.0, 105.0], start_day=20))
    assert rows[0]["fwd_2d_pct"] is not None and rows[0]["excess_2d_pct"] is None


def test_market_closure_is_separated_from_a_symbol_gap():
    """A date no symbol traded is a holiday, not a data defect (PR #225 check 2)."""
    def b(day, close):
        return {"date": day, "close": close, "high": close, "low": close}

    book = PriceBook.from_mapping({
        "AAA": [b("2026-01-01", 100), b("2026-01-02", 101), b("2026-01-05", 102)],
        "BBB": [b("2026-01-01", 50), b("2026-01-05", 51)],
    })
    # 2026-01-03 is closed for everyone; 2026-01-02 is a genuine gap in BBB only.
    assert book.market_closed_on("2026-01-03") is True
    assert book.market_closed_on("2026-01-02") is False

    obs = [Observation(symbol="AAA", date="2026-01-03"),
           Observation(symbol="BBB", date="2026-01-02")]
    _priced, cov = evaluate(obs, book, horizons=(1,))
    c = cov.to_dict()
    assert c["no_bar_on_date"] == 2
    assert c["no_bar_market_closed"] == 1
    assert c["no_bar_symbol_gap"] == 1


def test_fetcher_book_never_claims_a_market_closure():
    """No snapshot means no calendar — silence beats a guess."""
    book = PriceBook.from_fetcher(lambda _s, _d: [])
    assert book.market_closed_on("2026-01-03") is False


def test_enrich_emits_every_join_column_even_when_nothing_matures():
    """A LEFT JOIN yields its columns on every row; so must this.

    Regression: phase1_calibration indexes r["mae_10d_pct"] directly. When the
    horizon had not matured the key was absent and the script died with KeyError
    on real production data — invisible to fixtures where everything matures.
    """
    from services.forward_eval import enrich_rows

    book = PriceBook.from_mapping({"X": bars([100, 101])})      # only 2 bars
    rows = [{"symbol": "X", "date": "2026-01-01"},               # immature
            {"symbol": "GONE", "date": "2026-01-01"},            # absent symbol
            {"symbol": "X", "date": "2026-01-09"}]               # closed date
    cov = enrich_rows(rows, book, windows=(10,), mae_windows=(5,))
    outcome_keys = ("fwd_10d_pct", "excess_10d_pct", "mae_10d_pct",
                    "mfe_10d_pct", "mae_5d_pct", "mfe_5d_pct")
    for r in rows:
        for key in ("base_close", *outcome_keys):
            assert key in r, f"{key} missing on {r['symbol']}"
        for key in outcome_keys:
            assert r[key] is None, f"{key} should be null on {r['symbol']}"
    # base_close is known whenever the observation date itself traded — that is
    # what the table column held; it is null only when there is no bar at all.
    assert rows[0]["base_close"] == pytest.approx(100.0)
    assert rows[1]["base_close"] is None
    assert rows[2]["base_close"] is None
    assert cov.priced == 0


def test_mae_windows_do_not_add_a_forward_series():
    """mae_windows asks for excursions only — not another return column."""
    from services.forward_eval import enrich_rows

    book = PriceBook.from_mapping({"X": bars([100, 90, 120, 103, 104, 110, 111])})
    rows = [{"symbol": "X", "date": "2026-01-01"}]
    enrich_rows(rows, book, windows=(5,), mae_windows=(2,))
    r = rows[0]
    assert r["fwd_5d_pct"] == pytest.approx(10.0)
    assert r["fwd_2d_pct"] is None            # seeded, never filled
    assert r["mae_2d_pct"] == pytest.approx(-10.0)   # the dip to 90
    assert r["mfe_2d_pct"] == pytest.approx(20.0)    # the spike to 120
    assert r["base_close"] == pytest.approx(100.0)


def test_merge_coverage_keeps_the_breakdown():
    """Summing only total/priced/unpriceable discarded the survivorship detail."""
    from services.forward_eval import merge_coverage

    a = Coverage(total=10, priced=4, no_series=2, no_bar_on_date=3,
                 market_closed=2, immature={5: 1, 20: 3}).to_dict()
    b = Coverage(total=5, priced=1, no_series=0, no_bar_on_date=1,
                 market_closed=0, immature={5: 0, 20: 2}).to_dict()
    m = merge_coverage([a, b], label="runs_enriched")
    assert m["runs_enriched"] == 2
    assert (m["total"], m["priced"]) == (15, 5)
    assert m["priced_pct"] == 33.3
    assert m["no_series"] == 2
    assert m["no_bar_on_date"] == 4
    assert m["no_bar_market_closed"] == 2
    assert m["no_bar_symbol_gap"] == 2
    assert m["immature_by_horizon"] == {5: 1, 20: 5}
    assert merge_coverage([]) == {}
