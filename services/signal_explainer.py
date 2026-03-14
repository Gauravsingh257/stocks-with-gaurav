from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.fundamental_analysis import FundamentalSnapshot
from services.news_analysis import SentimentSnapshot
from services.technical_scanner import TechnicalSnapshot


def _pct(value: float) -> str:
    return f"{round(value * 100, 1)}%"


def _clip_rsi(raw: float) -> int:
    # Map 0..1 to realistic RSI band 35..75
    return int(round(35 + (raw * 40)))


def _yoy_from_score(score: float, base: float = 6.0, scale: float = 26.0) -> str:
    return f"{round(base + (score * scale), 1)}% YoY"


def _descriptor(score: float, bullish: str, neutral: str, weak: str) -> str:
    if score >= 0.7:
        return bullish
    if score >= 0.52:
        return neutral
    return weak


@dataclass(slots=True)
class SignalEvidence:
    symbol: str
    technical_signals: dict[str, str]
    fundamental_signals: dict[str, str]
    sentiment_signals: dict[str, str]

    def total_signal_count(self) -> int:
        return (
            len([v for v in self.technical_signals.values() if v])
            + len([v for v in self.fundamental_signals.values() if v])
            + len([v for v in self.sentiment_signals.values() if v])
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "technical_signals": self.technical_signals,
            "fundamental_signals": self.fundamental_signals,
            "sentiment_signals": self.sentiment_signals,
        }


def extract_swing_signals(
    symbol: str,
    technical: TechnicalSnapshot,
    fundamental: FundamentalSnapshot,
    sentiment: SentimentSnapshot,
) -> SignalEvidence:
    rsi = _clip_rsi(technical.rsi_momentum)
    relative_strength = round((technical.mtf_alignment - 0.5) * 20, 1)
    resistance_days = 20 if technical.trend_structure >= 0.68 else 30
    volume_multiple = round(1.0 + (technical.volume_expansion * 2.0), 1)
    momentum_spread = round((technical.macd_momentum - 0.5) * 2.0, 2)

    technical_signals = {
        "trend": f"Daily structure score {_pct(technical.trend_structure)} indicates {_descriptor(technical.trend_structure, 'a confirmed uptrend', 'a constructive uptrend', 'a weak uptrend')}.",
        "breakout": f"Breakout pressure score {_pct(technical.fvg_quality)} with a {resistance_days}-day resistance test in focus.",
        "volume": f"Volume expansion at {volume_multiple}x baseline ({_pct(technical.volume_expansion)} score).",
        "rsi": f"RSI {rsi} derived from momentum score {_pct(technical.rsi_momentum)}.",
        "macd": f"MACD momentum spread {momentum_spread:+.2f} from score {_pct(technical.macd_momentum)}.",
        "relative_strength": f"Multi-timeframe alignment {_pct(technical.mtf_alignment)} implies relative strength {relative_strength:+.1f}% vs NIFTY proxy.",
    }

    fundamental_signals = {
        "revenue_growth": f"Estimated revenue growth {_yoy_from_score(fundamental.revenue_growth)} from factor score {_pct(fundamental.revenue_growth)}.",
        "profit_growth": f"Estimated profit growth {_yoy_from_score(fundamental.earnings_growth, base=7.0, scale=28.0)} from earnings score {_pct(fundamental.earnings_growth)}.",
        "sector_strength": f"Sector strength score {_pct(fundamental.sector_strength)} suggests {_descriptor(fundamental.sector_strength, 'sector outperformance', 'stable sector support', 'sector underperformance risk')}.",
        "promoter_holding": f"Promoter stability score {_pct(fundamental.promoter_holdings)} with {'stable' if fundamental.promoter_holdings >= 0.55 else 'watchlist'} holding trend.",
        "institutional_accumulation": f"Institutional accumulation score {_pct(fundamental.institutional_accumulation)}.",
        "delivery_volume": f"Delivery trend score {_pct(fundamental.delivery_volume_trend)} indicates {'accumulation' if fundamental.delivery_volume_trend >= 0.58 else 'mixed participation'}.",
    }

    sentiment_signals = {
        "news_sentiment": f"Financial-news sentiment score {_pct(sentiment.financial_news)} with {_descriptor(sentiment.financial_news, 'positive earnings/news bias', 'balanced tone', 'cautious tone')}.",
        "earnings_event": f"Earnings-event bias score {_pct(sentiment.earnings_event_bias)}.",
        "sector_rotation": f"Sector rotation score {_pct(sentiment.sector_rotation)} indicates {'inflow' if sentiment.sector_rotation >= 0.58 else 'neutral flow'}.",
        "macro_sentiment": f"Macro sentiment score {_pct(sentiment.macro_sentiment)}.",
    }

    return SignalEvidence(
        symbol=symbol,
        technical_signals=technical_signals,
        fundamental_signals=fundamental_signals,
        sentiment_signals=sentiment_signals,
    )


def extract_longterm_signals(
    symbol: str,
    technical: TechnicalSnapshot,
    fundamental: FundamentalSnapshot,
    sentiment: SentimentSnapshot,
) -> SignalEvidence:
    long_term_acc = round((technical.order_block_quality * 100), 1)
    rs = round((technical.mtf_alignment - 0.5) * 16, 1)
    technical_signals = {
        "accumulation": f"Long-term accumulation score {long_term_acc} based on order-block quality {_pct(technical.order_block_quality)}.",
        "breakout_structure": f"Breakout structure score {_pct(technical.trend_structure)} over higher timeframe.",
        "relative_strength": f"Relative strength estimate {rs:+.1f}% vs index from alignment {_pct(technical.mtf_alignment)}.",
    }
    fundamental_signals = {
        "revenue_cagr": f"Revenue CAGR proxy {_yoy_from_score(fundamental.revenue_growth, base=8.0, scale=24.0)}.",
        "profit_growth": f"Profit growth proxy {_yoy_from_score(fundamental.earnings_growth, base=9.0, scale=26.0)}.",
        "roce_roe": f"ROCE/ROE quality composite {_pct((fundamental.roce + fundamental.roe) / 2)}.",
        "debt_levels": f"Debt quality score {_pct(fundamental.debt_quality)}.",
        "sector_growth": f"Sector growth score {_pct(fundamental.sector_strength)}.",
        "management_quality": f"Management quality score {_pct(fundamental.management_quality)}.",
        "institutional_flows": f"Institutional accumulation {_pct(fundamental.institutional_accumulation)}.",
    }
    sentiment_signals = {
        "industry_tailwinds": f"Industry tailwind score {_pct((fundamental.sector_strength + sentiment.sector_rotation) / 2)}.",
        "policy_impact": f"Policy sensitivity score {_pct(sentiment.macro_sentiment)}.",
        "macro_sentiment": f"Macro context {_pct(sentiment.macro_sentiment)}.",
        "news_sentiment": f"News sentiment {_pct(sentiment.financial_news)}.",
    }
    return SignalEvidence(
        symbol=symbol,
        technical_signals=technical_signals,
        fundamental_signals=fundamental_signals,
        sentiment_signals=sentiment_signals,
    )
