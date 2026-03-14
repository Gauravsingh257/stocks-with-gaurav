import asyncio

from services.fundamental_analysis import analyze_fundamentals
from services.news_analysis import analyze_news_sentiment
from services.technical_scanner import scan_technical


def test_research_services_are_deterministic():
    symbols = ["NSE:HDFCBANK", "NSE:INFY", "NSE:TCS"]

    tech_a = asyncio.run(scan_technical(symbols))
    tech_b = asyncio.run(scan_technical(symbols))
    assert tech_a["NSE:HDFCBANK"].as_factors() == tech_b["NSE:HDFCBANK"].as_factors()

    fund_a = asyncio.run(analyze_fundamentals(symbols))
    fund_b = asyncio.run(analyze_fundamentals(symbols))
    assert fund_a["NSE:INFY"].as_factors() == fund_b["NSE:INFY"].as_factors()

    news_a = asyncio.run(analyze_news_sentiment(symbols))
    news_b = asyncio.run(analyze_news_sentiment(symbols))
    assert news_a["NSE:TCS"].as_factors() == news_b["NSE:TCS"].as_factors()


def test_scores_are_within_expected_bounds():
    symbols = ["NSE:HDFCBANK"]

    tech = asyncio.run(scan_technical(symbols))["NSE:HDFCBANK"]
    fund = asyncio.run(analyze_fundamentals(symbols))["NSE:HDFCBANK"]
    sent = asyncio.run(analyze_news_sentiment(symbols))["NSE:HDFCBANK"]

    assert 0.0 <= tech.technical_score <= 1.0
    assert 0.0 <= fund.fundamental_score <= 1.0
    assert 0.0 <= sent.sentiment_score <= 1.0
