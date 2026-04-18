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
        rev = f"Revenue: {f.raw_revenue_growth_pct:+.1f}% YoY {'— strong growth' if f.raw_revenue_growth_pct > 15 else '— steady' if f.raw_revenue_growth_pct > 5 else '— sluggish'} (live)"
    else:
        rev = f"Revenue growth est. {_yoy_from_score(f.revenue_growth)} (no live data)"

    # Earnings growth
    if f.raw_earnings_growth_pct is not None:
        earn = f"Earnings: {f.raw_earnings_growth_pct:+.1f}% YoY {'— accelerating' if f.raw_earnings_growth_pct > 20 else '— healthy' if f.raw_earnings_growth_pct > 8 else '— tepid'} (live)"
    else:
        earn = f"Earnings growth est. {_yoy_from_score(f.earnings_growth, base=7.0, scale=28.0)} (no live data)"

    # PE & PB
    if f.raw_pe is not None and f.raw_pb is not None:
        sector = f"PE {f.raw_pe}x · PB {f.raw_pb}x — {_descriptor(f.sector_strength, 'attractive valuation', 'fairly valued', 'stretched valuation')}"
    elif f.raw_pe is not None:
        sector = f"PE {f.raw_pe}x — {_descriptor(f.sector_strength, 'reasonable entry', 'fairly valued', 'expensive')}"
    else:
        sector = f"Sector strength est. {_pct(f.sector_strength)} (no valuation data)"

    # Promoter
    if f.raw_promoter_pct is not None:
        promoter = f"Insider holding: {f.raw_promoter_pct:.1f}% — {'high conviction' if f.raw_promoter_pct >= 50 else 'moderate, watch pledging'}"
    else:
        promoter = f"Promoter stability est. {_pct(f.promoter_holdings)} (no live data)"

    # Institutional
    if f.raw_institutional_pct is not None:
        inst = f"Institutional: {f.raw_institutional_pct:.1f}% — {'strong FII/DII backing' if f.raw_institutional_pct >= 20 else 'limited institutional interest'}"
    else:
        inst = f"Institutional accumulation est. {_pct(f.institutional_accumulation)} (no live data)"

    delivery = f"Delivery trend: {'accumulation phase' if f.delivery_volume_trend >= 0.58 else 'mixed participation'} ({_pct(f.delivery_volume_trend)})"

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
        rev_cagr = f"Revenue: {f.raw_revenue_growth_pct:+.1f}% YoY — {'strong expansion' if f.raw_revenue_growth_pct > 15 else 'steady growth' if f.raw_revenue_growth_pct > 5 else 'slow, needs catalyst'} (live)"
    else:
        rev_cagr = f"Revenue growth est. {_yoy_from_score(f.revenue_growth, base=8.0, scale=24.0)} (no live data)"

    if f.raw_earnings_growth_pct is not None:
        profit = f"Earnings: {f.raw_earnings_growth_pct:+.1f}% YoY — {'highly profitable' if f.raw_earnings_growth_pct > 18 else 'decent growth' if f.raw_earnings_growth_pct > 5 else 'weak earnings'} (live)"
    else:
        profit = f"Earnings growth est. {_yoy_from_score(f.earnings_growth, base=9.0, scale=26.0)} (no live data)"

    if f.raw_roe_pct is not None:
        roce_roe = f"ROE: {f.raw_roe_pct:.1f}% — {'capital-efficient compounder' if f.raw_roe_pct >= 18 else 'average returns' if f.raw_roe_pct >= 10 else 'below-par capital efficiency'}"
    else:
        roce_roe = f"ROCE/ROE quality est. {_pct((f.roce + f.roe) / 2)} (no live data)"

    if f.raw_debt_equity is not None:
        debt = f"D/E: {f.raw_debt_equity:.2f}x — {'low leverage' if f.raw_debt_equity < 0.5 else 'manageable debt' if f.raw_debt_equity < 1.5 else 'high leverage risk'}"
    else:
        debt = f"Debt quality est. {_pct(f.debt_quality)} (no live data)"

    if f.raw_pe is not None:
        sector = f"PE: {f.raw_pe}x — {_descriptor(f.sector_strength, 'value zone', 'fairly priced', 'growth must sustain at this PE')}"
    else:
        sector = f"Sector growth est. {_pct(f.sector_strength)} (no live data)"

    if f.raw_institutional_pct is not None:
        inst = f"Institutional: {f.raw_institutional_pct:.1f}% — {'strong smart-money backing' if f.raw_institutional_pct >= 20 else 'moderate interest'}"
    else:
        inst = f"Institutional flows est. {_pct(f.institutional_accumulation)} (no live data)"

    return {
        "revenue_cagr": rev_cagr,
        "profit_growth": profit,
        "roce_roe": roce_roe,
        "debt_levels": debt,
        "sector_growth": sector,
        "management_quality": f"Management quality est. {_pct(f.management_quality)} (no live data)",
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

    # Label source: hash-based scores get "estimated" prefix
    _t_src = "" if getattr(technical, "data_source", "hash") != "hash" else " (est.)"

    technical_signals = {
        "trend": f"{'Strong uptrend' if technical.trend_structure >= 0.8 else 'Constructive uptrend' if technical.trend_structure >= 0.6 else 'Weak structure'} — trend score {_pct(technical.trend_structure)}{_t_src}",
        "breakout": f"{'High' if technical.fvg_quality >= 0.7 else 'Moderate' if technical.fvg_quality >= 0.5 else 'Low'} breakout pressure near {resistance_days}-day resistance{_t_src}",
        "volume": f"Volume {volume_multiple}x above baseline — {'strong accumulation' if volume_multiple >= 2.0 else 'mild expansion' if volume_multiple >= 1.5 else 'below average'}{_t_src}",
        "rsi": f"RSI ~{rsi} — {'overbought zone, watch for pullback' if rsi >= 70 else 'bullish momentum' if rsi >= 55 else 'neutral, awaiting trigger'}{_t_src}",
        "macd": f"MACD spread {momentum_spread:+.2f} — {'bullish crossover' if momentum_spread > 0.1 else 'flat, no clear signal' if momentum_spread > -0.1 else 'bearish divergence'}{_t_src}",
        "relative_strength": f"{'Outperforming' if relative_strength > 5 else 'Inline with' if relative_strength > -2 else 'Underperforming'} NIFTY by {relative_strength:+.1f}%{_t_src}",
    }

    fundamental_signals = _build_fundamental_signals_swing(fundamental)

    # Sentiment signals are synthetic — label clearly
    _s_note = " (est. — no live feed)" if getattr(sentiment, "data_source", "synthetic") == "synthetic" else ""
    sentiment_signals = {
        "news_sentiment": f"News sentiment: {'bullish' if sentiment.financial_news >= 0.7 else 'neutral' if sentiment.financial_news >= 0.4 else 'bearish'}{_s_note}",
        "earnings_event": f"Earnings outlook: {'positive catalyst expected' if sentiment.earnings_event_bias >= 0.7 else 'neutral' if sentiment.earnings_event_bias >= 0.4 else 'risk of miss'}{_s_note}",
        "sector_rotation": f"Sector flows: {'inflows' if sentiment.sector_rotation >= 0.65 else 'stable' if sentiment.sector_rotation >= 0.4 else 'rotation away'}{_s_note}",
        "macro_sentiment": f"Macro backdrop: {'supportive' if sentiment.macro_sentiment >= 0.65 else 'neutral' if sentiment.macro_sentiment >= 0.4 else 'headwinds'}{_s_note}",
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
    _t_src = "" if getattr(technical, "data_source", "hash") != "hash" else " (est.)"
    technical_signals = {
        "accumulation": f"Accumulation score {long_term_acc} — {'strong institutional buying' if long_term_acc >= 70 else 'moderate accumulation' if long_term_acc >= 45 else 'weak accumulation'}{_t_src}",
        "breakout_structure": f"Higher-TF structure: {'bullish breakout' if technical.trend_structure >= 0.7 else 'consolidation' if technical.trend_structure >= 0.5 else 'no clear pattern'}{_t_src}",
        "relative_strength": f"{'Outperforming' if rs > 5 else 'Inline with' if rs > -2 else 'Underperforming'} index by {rs:+.1f}%{_t_src}",
    }
    fundamental_signals = _build_fundamental_signals_longterm(fundamental)
    _s_note = " (est.)" if getattr(sentiment, "data_source", "synthetic") == "synthetic" else ""
    sentiment_signals = {
        "industry_tailwinds": f"Industry tailwinds: {'strong' if (fundamental.sector_strength + sentiment.sector_rotation) / 2 >= 0.65 else 'moderate' if (fundamental.sector_strength + sentiment.sector_rotation) / 2 >= 0.4 else 'weak'}{_s_note}",
        "policy_impact": f"Policy sensitivity: {'favorable' if sentiment.macro_sentiment >= 0.65 else 'neutral' if sentiment.macro_sentiment >= 0.4 else 'adverse'}{_s_note}",
        "macro_sentiment": f"Macro context: {'supportive' if sentiment.macro_sentiment >= 0.65 else 'neutral' if sentiment.macro_sentiment >= 0.4 else 'challenging'}{_s_note}",
        "news_sentiment": f"News: {'positive flow' if sentiment.financial_news >= 0.65 else 'mixed signals' if sentiment.financial_news >= 0.4 else 'negative tone'}{_s_note}",
    }
    return SignalEvidence(
        symbol=symbol,
        technical_signals=technical_signals,
        fundamental_signals=fundamental_signals,
        sentiment_signals=sentiment_signals,
    )
