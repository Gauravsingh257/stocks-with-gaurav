from services.reasoning_engine import generate_evidence_reasoning


def test_reasoning_is_deterministic():
    technical = {
        "trend": "Daily structure score 74.0% indicates a confirmed uptrend.",
        "volume": "Volume expansion at 2.2x baseline (62.0% score).",
    }
    fundamental = {
        "revenue_growth": "Estimated revenue growth 18.5% YoY from factor score 48.0%.",
        "profit_growth": "Estimated profit growth 22.2% YoY from earnings score 54.0%.",
    }
    sentiment = {
        "news_sentiment": "Financial-news sentiment score 67.0% with positive earnings/news bias.",
    }
    r1, used1 = generate_evidence_reasoning("NSE:CIPLA", technical, fundamental, sentiment)
    r2, used2 = generate_evidence_reasoning("NSE:CIPLA", technical, fundamental, sentiment)
    assert r1 == r2
    assert used1 == used2
    assert "CIPLA" in r1


def test_reasoning_requires_minimum_factors():
    technical = {"trend": "Daily structure score 74.0% indicates a confirmed uptrend."}
    fundamental = {}
    sentiment = {}
    reasoning, used = generate_evidence_reasoning("NSE:CIPLA", technical, fundamental, sentiment, min_factors=3)
    assert reasoning == ""
    assert len(used) == 1
