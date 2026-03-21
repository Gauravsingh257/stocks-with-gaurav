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


def _fmt_real_or_score(raw: float | None, label: str, unit: str, fallback_score: float, fallback_label: str) -> str:
    if raw is not None:
        return f"{label}: {raw}{unit}"
    return f"{fallback_label} score {_pct(fallback_score)}."


def _build_fundamental_signals_swing(fundamental: "FundamentalSnapshot") -> dict[str, str]:
    f = fundamental
    # Revenue growth
    if f.raw_revenue_growth_pct is not None:
        rev = f"Revenue growth: {f.raw_revenue_growth_pct:+.1f}% YoY (live from yfinance)."
    else:
        rev = f"Estimated revenue growth {_yoy_from_score(f.revenue_growth)} from factor score {_pct(f.revenue_growth)}."

    # Earnings growth
    if f.raw_earnings_growth_pct is not None:
        earn = f"Earnings growth: {f.raw_earnings_growth_pct:+.1f}% YoY (live from yfinance)."
    else:
        earn = f"Estimated earnings growth {_yoy_from_score(f.earnings_growth, base=7.0, scale=28.0)} from score {_pct(f.earnings_growth)}."

    # PE & PB for sector context
    if f.raw_pe is not None and f.raw_pb is not None:
        sector = f"Valuation: PE {f.raw_pe}x | PB {f.raw_pb}x — {_descriptor(f.sector_strength, 'attractively priced vs peers', 'fairly valued', 'stretched valuation, risk to downside')}."
    elif f.raw_pe is not None:
        sector = f"PE ratio {f.raw_pe}x — {_descriptor(f.sector_strength, 'reasonable entry valuation', 'fairly valued', 'expensive vs historical')}."
    else:
        sector = f"Sector strength score {_pct(f.sector_strength)} suggests {_descriptor(f.sector_strength, 'sector outperformance', 'stable sector support', 'sector underperformance risk')}."

    # Promoter
    if f.raw_promoter_pct is not None:
        promoter = f"Promoter holding: {f.raw_promoter_pct:.1f}% — {'high conviction, low dilution risk' if f.raw_promoter_pct >= 50 else 'moderate holding, watch for pledging'}."
    else:
        promoter = f"Promoter stability score {_pct(f.promoter_holdings)} with {'stable' if f.promoter_holdings >= 0.55 else 'watchlist'} holding trend."

    # Institutional
    if f.raw_institutional_pct is not None:
        inst = f"Institutional holding: {f.raw_institutional_pct:.1f}% — {'strong FII/DII accumulation' if f.raw_institutional_pct >= 20 else 'limited institutional interest'}."
    else:
        inst = f"Institutional accumulation score {_pct(f.institutional_accumulation)}."

    delivery = f"Delivery trend score {_pct(f.delivery_volume_trend)} indicates {'accumulation' if f.delivery_volume_trend >= 0.58 else 'mixed participation'}."

    return {
        "revenue_growth": rev,
        "profit_growth": earn,
        "sector_strength": sector,
        "promoter_holding": promoter,
        "institutional_accumulation": inst,
        "delivery_volume": delivery,
    }


def _build_fundamental_signals_longterm(fundamental: "FundamentalSnapshot") -> dict[str, str]:
    f = fundamental

    if f.raw_revenue_growth_pct is not None:
        rev_cagr = f"Revenue growth: {f.raw_revenue_growth_pct:+.1f}% YoY — {'strong expansion' if f.raw_revenue_growth_pct > 15 else 'steady growth' if f.raw_revenue_growth_pct > 5 else 'slow growth, watch triggers'}."
    else:
        rev_cagr = f"Revenue CAGR proxy {_yoy_from_score(f.revenue_growth, base=8.0, scale=24.0)}."

    if f.raw_earnings_growth_pct is not None:
        profit = f"Earnings growth: {f.raw_earnings_growth_pct:+.1f}% YoY — {'strong profitability' if f.raw_earnings_growth_pct > 18 else 'moderate earnings expansion' if f.raw_earnings_growth_pct > 5 else 'weak earnings, requires catalyst'}."
    else:
        profit = f"Profit growth proxy {_yoy_from_score(f.earnings_growth, base=9.0, scale=26.0)}."

    if f.raw_roe_pct is not None:
        roce_roe = f"ROE: {f.raw_roe_pct:.1f}% — {'capital-efficient compounder' if f.raw_roe_pct >= 18 else 'average capital returns' if f.raw_roe_pct >= 10 else 'below-average returns on equity'}."
    else:
        roce_roe = f"ROCE/ROE quality composite {_pct((f.roce + f.roe) / 2)}."

    if f.raw_debt_equity is not None:
        debt = f"Debt/Equity: {f.raw_debt_equity:.2f}x — {'low leverage, resilient balance sheet' if f.raw_debt_equity < 0.5 else 'moderate debt, manageable' if f.raw_debt_equity < 1.5 else 'high leverage, watch interest coverage'}."
    else:
        debt = f"Debt quality score {_pct(f.debt_quality)}."

    if f.raw_pe is not None:
        sector = f"PE: {f.raw_pe}x — {_descriptor(f.sector_strength, 'value zone, strong margin of safety', 'fairly priced for long-term entry', 'high PE, growth must sustain')}."
    else:
        sector = f"Sector growth score {_pct(f.sector_strength)}."

    if f.raw_institutional_pct is not None:
        inst = f"Institutional holding: {f.raw_institutional_pct:.1f}% — {'significant smart-money accumulation' if f.raw_institutional_pct >= 20 else 'moderate institutional presence'}."
    else:
        inst = f"Institutional accumulation {_pct(f.institutional_accumulation)}."

    return {
        "revenue_cagr": rev_cagr,
        "profit_growth": profit,
        "roce_roe": roce_roe,
        "debt_levels": debt,
        "sector_growth": sector,
        "management_quality": f"Management quality score {_pct(f.management_quality)}.",
        "institutional_flows": inst,
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

    fundamental_signals = _build_fundamental_signals_swing(fundamental)

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
    fundamental_signals = _build_fundamental_signals_longterm(fundamental)
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
