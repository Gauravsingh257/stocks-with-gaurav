"""The on-demand analyzer's display identity comes from the stock_universe snapshot.

/api/search-stock feeds the analysis card on /stock/<symbol>. It used to show the
ticker as the company name and the provider's own sector and P/E, so the same page
said "Chemicals · P/E 222x" in the key metrics and "Basic Materials · PE 213.9" in
the card. These tests pin the split: ``reference``/``name`` come from the canonical
universe row, while ``fundamentals`` (the confidence input) and every scoring output
are unchanged whether or not a reference exists.
"""

from __future__ import annotations

import pytest

import dashboard.backend.db.universe as universe_db
import services.stock_search_analysis as ssa

FCL_UNIVERSE_ROW = {
    "symbol": "FCL",
    "company_name": "Fineotex Chemical Limited",
    "sector": "Chemicals",
    "sector_source": "override",
    "instrument": "EQUITY",
    "price": 57.72,
    "market_cap_cr": 6721.5,
    "turnover_cr": 12.3,
    "pe": 222.00002,
    "pb": 5.440664,
    "roe_pct": None,
    "debt_to_equity": 0.01,
    "revenue_growth_pct": 174.8,
    "net_margin_pct": 12.09,
    "roe_source": None,
    "promoter_pct": 67.25,
    "pct_from_52w_high": 7.5,
    "ret_1y_pct": 140.52,
    "refreshed_at": "2026-09-12 03:39:16",
}

PROVIDER_FUNDAMENTALS = {
    "score": 61.0,
    "pe_ratio": 213.9,
    "roe_pct": None,
    "roce_pct": None,
    "revenue_growth_pct": 174.8,
    "debt_equity": 0.87,
    "market_cap_cr": 6725,
    "promoter_pct": 67.2,
    "sector": "Basic Materials",
    "industry": "Specialty Chemicals",
    "data_source": "yfinance",
}


@pytest.fixture
def offline_analyzer(monkeypatch):
    """Stub every provider call so analyze_stock runs deterministically offline."""
    monkeypatch.setattr(ssa, "_fetch_ohlc", lambda *a, **k: None)
    monkeypatch.setattr(ssa, "df_to_candles", lambda df: [])
    monkeypatch.setattr(ssa, "_cmp", lambda symbol, fallback: (54.32, "kite_live", 0))
    monkeypatch.setattr(ssa, "_fundamentals", lambda symbol: dict(PROVIDER_FUNDAMENTALS))
    ssa._analysis_cache.clear()
    yield
    ssa._analysis_cache.clear()


def _analyze(monkeypatch, universe_lookup):
    monkeypatch.setattr(universe_db, "get_symbol", universe_lookup)
    ssa._analysis_cache.clear()
    return ssa.analyze_stock("FCL")


def test_identity_and_ratios_come_from_the_universe_snapshot(monkeypatch, offline_analyzer):
    result = _analyze(monkeypatch, lambda s: dict(FCL_UNIVERSE_ROW))

    assert result["name"] == "Fineotex Chemical Limited"
    ref = result["reference"]
    assert ref["source"] == "stock_universe"
    assert ref["as_of"] == "2026-09-12 03:39:16"
    assert ref["company_name"] == "Fineotex Chemical Limited"
    assert ref["sector"] == "Chemicals"
    assert ref["pe"] == pytest.approx(222.00002)
    assert ref["market_cap_cr"] == pytest.approx(6721.5)
    assert ref["debt_to_equity"] == pytest.approx(0.01)
    assert ref["price"] == pytest.approx(57.72)


def test_scoring_is_identical_with_or_without_a_reference(monkeypatch, offline_analyzer):
    with_ref = _analyze(monkeypatch, lambda s: dict(FCL_UNIVERSE_ROW))
    without_ref = _analyze(monkeypatch, lambda s: None)

    for key in (
        "confidence_score", "recommendation", "setup_type", "horizon", "entry_zone",
        "stop_loss", "target", "risk_reward", "criteria_not_met", "cmp", "cmp_source",
    ):
        assert with_ref[key] == without_ref[key], key
    # The confidence input stays the analyzer's own provider fetch, untouched.
    assert with_ref["fundamentals"] == PROVIDER_FUNDAMENTALS
    assert without_ref["fundamentals"] == PROVIDER_FUNDAMENTALS


def test_symbol_outside_the_universe_keeps_the_ticker_and_no_reference(monkeypatch, offline_analyzer):
    result = _analyze(monkeypatch, lambda s: None)
    assert result["name"] == "FCL"
    assert result["reference"] is None


def test_a_failing_universe_read_never_breaks_the_analysis(monkeypatch, offline_analyzer):
    def boom(symbol):
        raise RuntimeError("database locked")

    result = _analyze(monkeypatch, boom)
    assert result["reference"] is None
    assert result["name"] == "FCL"
    assert result["cmp"] == pytest.approx(54.32)
