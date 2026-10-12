"""The shared forward-return evaluator: bias controls and parity.

Replaces four separate implementations (two of which read the stale
`forward_returns` table). These tests pin the properties that made
`scripts/ceiling_cohort_study.py` trustworthy.
"""

import pytest

from services.forward_eval import (
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
