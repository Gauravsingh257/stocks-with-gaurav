from __future__ import annotations

from typing import Iterable


def _ordered_values(source: dict[str, str], keys: Iterable[str]) -> list[str]:
    return [source[k] for k in keys if k in source and source[k]]


def generate_evidence_reasoning(
    symbol: str,
    technical_signals: dict[str, str],
    fundamental_signals: dict[str, str],
    sentiment_signals: dict[str, str],
    *,
    min_factors: int = 3,
    max_factors: int = 6,
) -> tuple[str, list[str]]:
    """
    Deterministic reasoning from extracted evidence.
    Returns (reasoning_text, factors_used).
    """
    technical_order = ("trend", "breakout", "volume", "rsi", "macd", "relative_strength", "accumulation", "breakout_structure")
    fundamental_order = (
        "revenue_growth",
        "profit_growth",
        "sector_strength",
        "revenue_cagr",
        "roce_roe",
        "debt_levels",
        "management_quality",
        "institutional_accumulation",
        "institutional_flows",
    )
    sentiment_order = ("news_sentiment", "sector_rotation", "industry_tailwinds", "macro_sentiment", "earnings_event", "policy_impact")

    evidence: list[str] = []
    evidence.extend(_ordered_values(technical_signals, technical_order)[:3])
    evidence.extend(_ordered_values(fundamental_signals, fundamental_order)[:2])
    evidence.extend(_ordered_values(sentiment_signals, sentiment_order)[:2])
    evidence = evidence[:max_factors]

    if len(evidence) < min_factors:
        return "", evidence

    symbol_name = symbol.replace("NSE:", "").replace("NFO:", "")
    top_3 = ". ".join(evidence[:3])
    reasoning = f"{symbol_name} picked: {top_3}."
    return reasoning, evidence
