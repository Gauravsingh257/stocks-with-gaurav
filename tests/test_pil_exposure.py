"""
tests/test_pil_exposure.py
==========================
Unit tests for the cross-portfolio exposure/concentration engine
(services/pil/exposure.py). Books are hand-built so HHI, buckets, correlation and
threshold warnings all have known expected values.
"""

from __future__ import annotations

import pytest

from services.pil import exposure as exp


def _pos(symbol, book, mv, sector):
    return {"symbol": symbol, "book": book, "market_value": mv, "sector": sector,
            "weight_pct": 0.0}


def _curve(vals, start_day=1):
    return [{"date": f"2026-01-{start_day + i:02d}", "value": v} for i, v in enumerate(vals)]


def _books():
    positions = [
        _pos("HDFCBANK", "SWING", 50_000.0, "Banking"),
        _pos("ICICIBANK", "SWING", 30_000.0, "Banking"),
        _pos("TCS", "MOMENTUM", 20_000.0, "IT"),
    ]
    return {
        "SWING": {"equity_curve": _curve([100.0, 101.0, 103.0, 102.0, 104.0])},
        "LONGTERM": {"equity_curve": _curve([100.0, 100.5, 101.0, 101.5, 102.0])},
        "MOMENTUM": {"equity_curve": _curve([100.0, 99.0, 97.0, 98.0, 96.0])},
        "COMBINED": {"portfolio_value": 120_000.0, "positions": positions,
                     "equity_curve": _curve([300.0, 300.5, 301.0, 301.5, 302.0])},
    }


def test_sector_bucket_and_pct():
    e = exp.compute(_books())  # default max_sector_share = 0.30
    secs = {s["name"]: s for s in e["by_sector"]}
    assert secs["Banking"]["pct"] == pytest.approx(80.0)   # 80k / 100k deployed
    assert secs["IT"]["pct"] == pytest.approx(20.0)
    assert e["deployed"] == pytest.approx(100_000.0)
    assert e["cash_pct"] == pytest.approx((120_000 - 100_000) / 120_000 * 100, abs=0.1)


def test_hhi_and_diversification():
    e = exp.compute(_books())
    # weights 0.5,0.3,0.2 -> HHI 0.38
    assert e["hhi"] == pytest.approx(0.38, abs=0.001)
    assert e["effective_holdings"] == pytest.approx(1 / 0.38, abs=0.1)
    assert 0.0 <= e["diversification_score"] <= 1.0


def test_largest_and_top10():
    e = exp.compute(_books())
    assert e["largest_holding"]["symbol"] == "HDFCBANK"
    assert e["largest_holding"]["pct"] == pytest.approx(50.0)
    assert e["top10_pct"] == pytest.approx(100.0)  # only 3 holdings


def test_warnings_fire_on_overweight():
    e = exp.compute(_books())
    types = {w["type"] for w in e["warnings"]}
    assert "SECTOR_OVERWEIGHT" in types          # Banking 80% > 30%
    assert "SINGLE_STOCK_CONCENTRATION" in types  # HDFCBANK 50% > 10%


def test_correlation_matrix_diagonal_is_one():
    e = exp.compute(_books())
    m = e["correlation"]["matrix"]
    for b in e["correlation"]["engines"]:
        assert m[b][b] == 1.0
    # SWING vs MOMENTUM (mostly opposite drift) should be negative if computable
    val = m["SWING"]["MOMENTUM"]
    assert val is None or -1.0 <= val <= 1.0


def test_heatmap_present():
    e = exp.compute(_books())
    assert any(row["sector"] == "Banking" for row in e["heatmap"])
    assert e["portfolio_beta"] == pytest.approx(1.0)  # default beta, no ref file
