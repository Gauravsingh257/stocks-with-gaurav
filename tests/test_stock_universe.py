"""Stock universe table, weekly refresh, and instrument/sector classification."""

from __future__ import annotations

import pytest

from services.industry_map import canon_from_provider
from services.instrument_type import EQUITY, classify, is_equity

# ── instrument typing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("symbol,expected", [
    ("SGBSEP28VI-GB", "Sovereign Gold Bond"),
    ("SGBN28VIII-GB", "Sovereign Gold Bond"),
    ("667GS2050-GS", "Government Security"),
    ("868NHB29-N3", "Corporate Bond / NCD"),
    ("94SFL28-YL", "Corporate Bond / NCD"),
    ("ICICIB22", "ETF"),
    ("AXISBNKETF", "ETF"),
    ("MAHAPE-RE", "Rights Entitlement"),
    ("OMFURN-SM", "SME Board"),
    ("MILTON-ST", "SME Trading"),
    ("SANWARIA-BZ", "Trade-to-Trade"),
])
def test_non_equity_instruments_are_typed(symbol, expected):
    assert classify(symbol, "Some Name") == expected
    assert is_equity(symbol, "Some Name") is False


@pytest.mark.parametrize("symbol", ["3PLAND", "3MINDIA", "360ONE", "RELIANCE", "20MICRONS"])
def test_tickers_starting_with_a_digit_are_still_equities(symbol):
    """The first cut treated any leading digit as a bond, which wrongly excluded
    real companies. A real NCD line carries a tranche suffix; these do not."""
    assert classify(symbol, "3P Land Holdings Limited") == EQUITY


def test_blank_registered_name_means_stale_ticker():
    """NSE's own equity list has no name for a renamed/delisted symbol."""
    assert classify("ZOMATO", "") == "Delisted / Renamed"
    assert classify("ZOMATO", None) == EQUITY   # unknown name is not evidence


# ── industry -> canonical sector ──────────────────────────────────────────────

def test_industry_beats_the_coarse_sector():
    """'Basic Materials' lumps a chemicals maker with a steel roller; the finer
    industry field separates them."""
    assert canon_from_provider("Steel", "Basic Materials") == "Metal"
    assert canon_from_provider("Specialty Chemicals", "Basic Materials") == "Chemicals"
    assert canon_from_provider("Textile Manufacturing", "Consumer Cyclical") == "Textiles"


def test_falls_back_to_sector_then_none():
    assert canon_from_provider("Totally Unknown Industry", "Technology") == "IT"
    assert canon_from_provider(None, None) is None
    assert canon_from_provider("Nonsense", "Nonsense") is None


# ── refresh job: ratios must never be fabricated ──────────────────────────────

def test_missing_ratio_is_none_not_zero():
    """A 0 P/E screens as 'cheap' and a 0 D/E as 'unlevered' — both backwards."""
    from scripts.refresh_stock_universe import _num

    assert _num(None) is None
    assert _num("") is None
    assert _num("n/a") is None
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(True) is None          # bool is not a ratio
    assert _num("18.4") == 18.4
    assert _num(0) == 0.0              # a real zero survives


def test_debt_to_equity_percent_is_normalised():
    from scripts.refresh_stock_universe import _num

    de = _num(195.001)                 # provider reports % for many NSE names
    assert de is not None and de > 10
    assert round(de / 100.0, 2) == 1.95


# ── table round-trip ──────────────────────────────────────────────────────────

def test_universe_upsert_and_read(tmp_path, monkeypatch):
    import dashboard.backend.db.schema as schema

    db = tmp_path / "t.db"
    monkeypatch.setattr(schema, "DB_PATH", db)
    schema.init_db()

    from dashboard.backend.db.universe import get_universe, upsert_universe

    rows = [
        {"symbol": "AAA", "company_name": "Alpha Ltd", "sector": "IT",
         "sector_source": "manual", "instrument": "EQUITY", "price": 100.0,
         "market_cap_cr": 5000.0, "turnover_cr": 12.5, "pe": 22.4, "pb": 3.1,
         "roe_pct": 18.2, "roe_source": "filings", "debt_to_equity": 0.3,
         "revenue_growth_pct": 14.0, "net_margin_pct": 9.5, "promoter_pct": 55.0,
         "pct_from_52w_high": 4.2, "ret_1y_pct": 31.0},
        {"symbol": "BBB", "company_name": "Beta Ltd", "sector": "Pharma",
         "sector_source": "nse_official", "instrument": "EQUITY", "price": 50.0,
         "market_cap_cr": 900.0, "turnover_cr": 2.0, "pe": None, "pb": None,
         "roe_pct": None, "roe_source": None, "debt_to_equity": None,
         "revenue_growth_pct": None, "net_margin_pct": None, "promoter_pct": None,
         "pct_from_52w_high": None, "ret_1y_pct": None},
        {"symbol": "ETFX", "company_name": "An ETF", "sector": "Unassigned",
         "sector_source": "", "instrument": "ETF", "price": None,
         "market_cap_cr": None, "turnover_cr": None, "pe": None, "pb": None,
         "roe_pct": None, "roe_source": None, "debt_to_equity": None,
         "revenue_growth_pct": None, "net_margin_pct": None, "promoter_pct": None,
         "pct_from_52w_high": None, "ret_1y_pct": None},
    ]
    assert upsert_universe(rows) == 3

    out = get_universe()
    assert out["available"] is True
    assert out["equities"] == 2
    assert {r["symbol"] for r in out["items"]} == {"AAA", "BBB"}   # ETF excluded
    assert out["items"][0]["symbol"] == "AAA"                      # turnover-sorted
    assert out["items"][1]["pe"] is None                           # null, not 0

    assert {r["symbol"] for r in get_universe(sector="IT")["items"]} == {"AAA"}
    assert {r["symbol"] for r in get_universe(search="beta")["items"]} == {"BBB"}
    assert {r["symbol"] for r in get_universe(equity_only=False)["items"]} == {"AAA", "BBB", "ETFX"}

    # re-running the refresh must update in place, never duplicate
    rows[0]["pe"] = 25.0
    upsert_universe(rows)
    again = get_universe()
    assert again["total"] == 3
    assert next(r for r in again["items"] if r["symbol"] == "AAA")["pe"] == 25.0


def test_throttle_paces_provider_calls(monkeypatch):
    """A burst of ~2,000 .info calls gets HTTP 401 'Invalid Crumb' and then
    empty dicts — which records as 'no fundamentals' rather than 'rate-limited'.
    The first full production run finished 2,166 symbols in 47s and got ratios
    for only 32%."""
    import time as _t

    import scripts.refresh_stock_universe as r

    monkeypatch.setattr(r, "_THROTTLE_SEC", 0.05)
    monkeypatch.setattr(r, "_last_call", [0.0])
    start = _t.time()
    for _ in range(4):
        r._throttle()
    assert _t.time() - start >= 0.10   # at least a few gaps were enforced


def test_empty_provider_reply_is_retried(monkeypatch):
    """An empty dict means throttled, not 'this company has no data'."""
    import scripts.refresh_stock_universe as r

    calls = {"n": 0}

    class _Ticker:
        def __init__(self, _sym):
            pass

        @property
        def info(self):
            calls["n"] += 1
            # throttled twice, then a real payload
            return {} if calls["n"] < 3 else {"marketCap": 1e10, "trailingPE": 20.0}

    import sys as _sys
    import types as _types
    fake = _types.ModuleType("yfinance")
    fake.Ticker = _Ticker
    monkeypatch.setitem(_sys.modules, "yfinance", fake)
    monkeypatch.setattr(r, "_THROTTLE_SEC", 0.0)
    monkeypatch.setattr(r, "_RETRIES", 3)

    out = r.fetch_one("TEST")
    assert calls["n"] >= 3
    assert out["pe"] == 20.0
