"""Regression: ranked ideas must be appended to the result list (not dropped)."""

from __future__ import annotations

import asyncio

import pandas as pd

from services.ranking_engine import (
    FactorRow,
    RankedIdea,
    _collect_ideas_from_pool,
)


def _dummy_idea(symbol: str = "NSE:TESTCO") -> RankedIdea:
    return RankedIdea(
        symbol=symbol,
        rank=0,
        rank_score=0.9,
        confidence_score=90.0,
        entry_price=150.0,
        stop_loss=140.0,
        targets=[165.0, 180.0],
        setup="SMC_SWING_BULL_BOS",
        expected_holding_period="1-8 weeks",
        technical_signals={"weekly_trend": "ok"},
        fundamental_signals={},
        sentiment_signals={},
        technical_factors={"trend": 0.7},
        fundamental_factors={"growth": 0.6},
        sentiment_factors={"news_sentiment": 0.5},
        reasoning="test",
        fair_value_estimate=None,
        entry_zone=None,
        long_term_target=None,
        risk_factors=None,
        entry_type="MARKET",
        scan_cmp=150.0,
        sector=None,
    )


def test_collect_ideas_from_pool_appends_materialized_ideas(monkeypatch):
    row = FactorRow(
        symbol="NSE:TESTCO",
        factors={"trend": 0.7, "momentum": 0.6, "breakout": 0.5, "mtf_alignment": 0.6,
                 "liquidity": 0.7, "volume_expansion": 0.6, "growth": 0.6, "quality": 0.6,
                 "balance_sheet": 0.6, "institutional_accumulation": 0.5,
                 "news_sentiment": 0.5, "sector_rotation": 0.5, "macro_sentiment": 0.5},
        technical_score=0.7,
        fundamental_score=0.65,
        sentiment_score=0.55,
        liquidity_score=0.7,
    )
    scored = [(row, 0.88)]
    evidence_map = {
        "NSE:TESTCO": (
            {"weekly_trend": "x"},
            {"pe": "ok"},
            {},
            "WEEKLY_CROSS_SECTIONAL_SWING",
        ),
    }

    async def _fake_fetch(_ingestion, _symbol):
        return pd.DataFrame(
            [
                {"date": "2024-01-01", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1e6},
                {"date": "2024-01-02", "open": 100.5, "high": 102, "low": 100, "close": 101.5, "volume": 1e6},
            ]
        )

    async def _fake_mat(*_a, **_k):
        return _dummy_idea()

    monkeypatch.setattr("services.ranking_engine._fetch_daily_df", _fake_fetch)
    monkeypatch.setattr("services.ranking_engine._materialize_swing_idea", _fake_mat)

    async def _run():
        return await _collect_ideas_from_pool(
            "SWING",
            top_k=3,
            scored=scored,
            evidence_map=evidence_map,
            fund_map=None,
        )

    out = asyncio.run(_run())
    assert len(out) == 1
    assert out[0].symbol == "NSE:TESTCO"
    assert out[0].rank == 1
