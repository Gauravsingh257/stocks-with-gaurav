"""
Content Creation / agents / content_agent.py

ContentAgent -- generates STRICT 9-slide Instagram carousel text.

FIXED FLOW:
  Slide 1  -> COVER               (emotional headline + subtitle + date)
  Slide 2  -> MARKET SNAPSHOT      (NIFTY / BANKNIFTY / SENSEX / VIX + S/R levels)
  Slide 3  -> DRIVERS + SENTIMENT  (key drivers top + sentiment strip bottom)
  Slide 4  -> GLOBAL + CRYPTO      (global indices + BTC/ETH + interpretation)
  Slide 5  -> HOT NEWS             (market-moving headlines with impact tags)
  Slide 6  -> ACTION PLAN          (4 insights + CTA engagement + disclaimer)
  Slide 7-9-> STOCK PICKS          (3 stocks, rich card + mini chart + bias)

RULES:
  - Max 8-12 words per line
  - NO unicode chars (replace -- with -)
  - Every number gets an insight with S/R levels
  - Every stock answers: Why? Opportunity? Bias?
  - Setup diversity: no 3 stocks with same setup label
  - Arrows: use unicode arrow char for clean rendering
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from agents.base import BaseContentAgent
from agents.chart_helper import generate_mini_chart
from config.settings import OUTPUT_DIR
from models.contracts import (
    CarouselContent,
    KeyLevel,
    MarketAnalysis,
    MarketData,
    PipelineMode,
    Sentiment,
    Slide,
    SlideSection,
    SlideType,
    StockPicks,
)

log = logging.getLogger("content_creation.agents.content")

# ═══════════════════════════════════════════════════════════════════════════
#  SANITIZE — replace unicode chars that break PIL fonts
# ═══════════════════════════════════════════════════════════════════════════

def _truncate_word_boundary(text: str, max_len: int) -> str:
    """Truncate text at word boundary, never mid-word. Strip trailing punctuation."""
    if len(text) <= max_len:
        return text.rstrip(" -")
    truncated = text[:max_len]
    # Find last space to break at word boundary
    last_space = truncated.rfind(" ")
    if last_space > max_len * 0.5:
        result = truncated[:last_space]
    else:
        result = truncated
    # Strip trailing dashes, spaces, colons
    result = result.rstrip(" -:,")
    return result + "..."


def _clean(text: str) -> str:
    """Replace em-dash, en-dash, special chars with ASCII equivalents."""
    text = text.replace("\u2014", " - ")  # em-dash
    text = text.replace("\u2013", "-")    # en-dash
    text = text.replace("\u25bc", "")     # down triangle
    text = text.replace("\u25b2", "")     # up triangle
    text = re.sub(r"[^\x00-\x7F]+", "", text)  # strip any remaining non-ASCII
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
#  HEADLINE BANKS (data-driven selection)
# ═══════════════════════════════════════════════════════════════════════════

_HOOKS: dict[Sentiment, dict[str, list[str]]] = {
    Sentiment.BULLISH: {
        "pre":  ["BULLS READY TO CHARGE", "GREEN OPEN EXPECTED", "BREAKOUT INCOMING?"],
        "post": ["BULLS DOMINATED TODAY", "GREEN CANDLES EVERYWHERE", "MARKET ON FIRE"],
    },
    Sentiment.BEARISH: {
        "pre":  ["BRACE FOR IMPACT", "BEARS IN CONTROL", "MARKET BLOODBATH"],
        "post": ["MARKET BLOODBATH", "BEARS CRUSHED IT", "RED DAY ALERT"],
    },
    Sentiment.NEUTRAL: {
        "pre":  ["MARKETS AT CROSSROAD", "WAIT & WATCH MODE", "RANGE BOUND DAY?"],
        "post": ["FLAT BUT NOT BORING", "SIDEWAYS GRIND", "NO CLEAR WINNER"],
    },
    Sentiment.MIXED: {
        "pre":  ["MIXED SIGNALS TODAY", "VOLATILITY EXPECTED", "TRICKY SESSION AHEAD"],
        "post": ["CHOPPY SESSION DONE", "DIP BOUGHT AGGRESSIVELY", "VOLATILE BUT HELD"],
    },
}

_HASHTAGS_CORE = [
    "#StocksWithGaurav", "#StockMarket", "#Nifty50",
    "#TradingStrategy", "#BankNifty", "#MarketUpdate",
]

_HASHTAGS_SENTIMENT = {
    Sentiment.BEARISH: ["#MarketCrash", "#BearMarket"],
    Sentiment.BULLISH: ["#BullRun", "#Breakout"],
    Sentiment.MIXED:   ["#Volatility", "#MarketSwings"],
    Sentiment.NEUTRAL: ["#RangeBound", "#Consolidation"],
}

_HASHTAGS_EXTRAS = [
    "#StockMarketIndia", "#SmartMoneyConcepts",
    "#PriceAction", "#SwingTrading",
]


def _pick_hook(sentiment: Sentiment, mode: PipelineMode, score: float) -> str:
    """Pick headline based on sentiment strength."""
    key = "pre" if mode == PipelineMode.PRE_MARKET else "post"
    hooks = _HOOKS.get(sentiment, _HOOKS[Sentiment.MIXED])[key]
    idx = 0 if abs(score) < 0.3 else (1 if abs(score) < 0.6 else 2)
    return hooks[min(idx, len(hooks) - 1)]


# ═══════════════════════════════════════════════════════════════════════════
#  INSIGHT GENERATORS — interpret numbers, not just show them
# ═══════════════════════════════════════════════════════════════════════════

def _index_insight(name: str, pct: float, value: float = 0, levels: list[KeyLevel] | None = None) -> str:
    """One-line insight for an index move, with S/R levels when available."""
    n = name.upper()
    ap = abs(pct)
    if "VIX" in n:
        if pct > 5:
            return "Fear spike - caution"
        elif pct > 0:
            return "Fear rising"
        else:
            return "Fear easing"

    # Find matching key level for this index
    level = None
    if levels:
        for kl in levels:
            if kl.index.upper() in n or n in kl.index.upper():
                level = kl
                break

    if level and value > 0:
        if value < level.support:
            return f"Below S: {level.support:,.0f} - weak"
        elif value > level.resistance:
            return f"Above R: {level.resistance:,.0f} - strong"
        elif ap >= 1.0:
            return f"S: {level.support:,.0f} / R: {level.resistance:,.0f}"
        else:
            return f"S: {level.support:,.0f} / R: {level.resistance:,.0f}"

    if ap >= 2.0:
        return "Broke key support" if pct < 0 else "Strong breakout"
    if ap >= 1.0:
        return "Weak momentum" if pct < 0 else "Buyers stepping in"
    return "Range-bound action"


def _global_insight(name: str, pct: float) -> str:
    """One-line insight for global market — cause->effect."""
    if abs(pct) >= 2.0:
        return "Liquidity exit" if pct < 0 else "Risk-on rally"
    if abs(pct) >= 1.0:
        return "Selling pressure" if pct < 0 else "Bullish momentum"
    if abs(pct) >= 0.3:
        return "Mild weakness" if pct < 0 else "Slight positive"
    return "Flat session"


def _sentiment_action(sent: str, risk: str, trend: str) -> list[str]:
    """Action-oriented lines for sentiment slide — trader-specific, not generic."""
    actions = []
    if sent == "BEARISH":
        actions.append("Short rallies into supply zones")
    elif sent == "BULLISH":
        actions.append("Buy dips near demand zones")
    elif sent == "MIXED":
        actions.append("No trades in mid-range")
    else:
        actions.append("Trade only at range extremes")

    if risk in ("EXTREME", "HIGH"):
        actions.append("Half position size | Tight stops")
    else:
        actions.append("Normal sizing | Trail stops")

    if "DOWN" in trend:
        actions.append("Avoid fresh longs below support")
    elif "UP" in trend:
        actions.append("Trail aggressively | Let winners run")
    elif "VOLATILE" in trend:
        actions.append("Breakout = participation | Else sit out")
    else:
        actions.append("Cash is alpha in no-man's land")
    return actions


class ContentAgent(BaseContentAgent):
    name = "ContentAgent"
    description = "Generates strict 9-slide Instagram carousel text"

    def run(
        self,
        *,
        mode: PipelineMode,
        analysis: MarketAnalysis,
        picks: StockPicks,
        market_data: MarketData | None = None,
    ) -> CarouselContent:
        date_str = datetime.now().strftime("%d %b %Y")
        slides: list[Slide] = []

        # Slide 1: COVER
        slides.append(self._slide_cover(1, mode, date_str, analysis, market_data))
        # Slide 2: MARKET SNAPSHOT (with VIX + insights + S/R levels)
        slides.append(self._slide_snapshot(2, market_data, analysis))
        # Slide 3: KEY DRIVERS + SENTIMENT (merged)
        slides.append(self._slide_drivers_sentiment(3, analysis, market_data))
        # Slide 4: GLOBAL + CRYPTO (with interpretations)
        slides.append(self._slide_global_crypto(4, market_data))
        # Slide 5: HOT NEWS (market-moving headlines)
        slides.append(self._slide_news(5, market_data))
        # Slide 6: INSIGHT + CTA (merged)
        slides.append(self._slide_insight_cta(6, analysis, market_data))
        # Slides 7-9: STOCK PICKS — prefer picks that have chart data available.
        date_str_dir = datetime.now().strftime("%Y-%m-%d")
        out_dir_str = str(OUTPUT_DIR / date_str_dir)
        _chart_ok: list = []
        _chart_fail: list = []
        for pick in picks.picks:
            probe = generate_mini_chart(
                pick.symbol,
                accent_color="#2D3561",
                support=pick.stop_loss if pick.stop_loss > 0 else 0,
                resistance=pick.target if pick.target > 0 else 0,
                output_dir=out_dir_str,
            )
            if probe:
                _chart_ok.append(pick)
            else:
                _chart_fail.append(pick)
            if len(_chart_ok) >= 3:
                break  # got enough chart-available picks

        # Fill up to 3 with non-chart picks if not enough chart-ok ones
        ordered_picks = (_chart_ok + _chart_fail)[:3]
        for i, pick in enumerate(ordered_picks):
            slides.append(self._slide_stock(7 + i, pick, analysis))

        caption = self._build_caption(mode, analysis, picks)
        hashtags = self._build_hashtags(analysis)

        content = CarouselContent(
            content_type=f"{mode.value}_carousel",
            date=datetime.now().strftime("%Y-%m-%d"),
            mode=mode,
            slides=slides,
            caption=caption,
            hashtags=hashtags,
            total_slides=len(slides),
        )
        log.info("ContentAgent: %d slides, %d hashtags", len(slides), len(hashtags))
        return content

    # ══════════════════════════════════════════════════════════════════════
    #  SLIDE 1: COVER — emotional hook + subtitle + date
    # ══════════════════════════════════════════════════════════════════════

    def _slide_cover(
        self, num: int, mode: PipelineMode, date: str, analysis: MarketAnalysis,
        market_data: MarketData | None = None,
    ) -> Slide:
        headline = _pick_hook(
            analysis.overall_sentiment, mode, analysis.sentiment_score
        )
        subtitle = "PRE-MARKET REPORT" if mode == PipelineMode.PRE_MARKET else "POST-MARKET WRAP"
        # Add NIFTY value to cover for instant hook
        nifty_line = ""
        if market_data and market_data.indices:
            nifty = next((i for i in market_data.indices if "NIFTY 50" in i.name), None)
            if nifty:
                sign = "+" if nifty.change_pct >= 0 else ""
                nifty_line = f"NIFTY {nifty.value:,.0f} ({sign}{nifty.change_pct:.1f}%)"

        # Generate NIFTY mini candlestick chart for the cover
        date_today = datetime.now().strftime("%Y-%m-%d")
        cover_chart = generate_mini_chart(
            "NIFTY_50",
            period="5d",
            interval="15m",
            accent_color=self._sentiment_accent(analysis.overall_sentiment),
            output_dir=str(OUTPUT_DIR / date_today),
        )

        return Slide(
            slide_number=num,
            slide_type=SlideType.COVER,
            headline=headline,
            subheadline=date,
            body=subtitle,
            sections=[SlideSection(label="NIFTY", value=nifty_line, color="#ffffff")] if nifty_line else [],
            data_points=[{"chart_path": cover_chart}] if cover_chart else [],
            accent_color=self._sentiment_accent(analysis.overall_sentiment),
        )

    # ══════════════════════════════════════════════════════════════════════
    #  SLIDE 2: MARKET SNAPSHOT — with VIX + per-row insights
    # ══════════════════════════════════════════════════════════════════════

    def _slide_snapshot(self, num: int, data: MarketData | None, analysis: MarketAnalysis | None = None) -> Slide:
        sections: list[SlideSection] = []
        levels = analysis.key_levels if analysis else []
        if data and data.indices:
            for idx in data.indices[:6]:
                is_vix = "VIX" in idx.name.upper()
                sign = "+" if idx.change_pct >= 0 else ""
                color = "#059669" if idx.change_pct >= 0 else "#DC2626"
                if is_vix:
                    color = "#DC2626" if idx.value > 20 else "#059669" if idx.value < 14 else "#D97706"
                insight = _index_insight(idx.name, idx.change_pct, idx.value, levels)
                # VIX gets 1 decimal precision; indices get integer
                val_fmt = f"{idx.value:,.1f}" if is_vix else f"{idx.value:,.0f}"
                sections.append(SlideSection(
                    label=idx.name,
                    value=f"{val_fmt} ({sign}{idx.change_pct:.1f}%) | {insight}",
                    color=color,
                ))
        if not sections:
            sections.append(SlideSection(
                label="NIFTY", value="Awaiting data", color="#888888"
            ))
        return Slide(
            slide_number=num,
            slide_type=SlideType.DATA,
            headline="MARKET SNAPSHOT",
            sections=sections[:6],
            accent_color="#2D3561",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  SLIDE 3: KEY DRIVERS + SENTIMENT (merged)
    # ══════════════════════════════════════════════════════════════════════

    def _slide_drivers_sentiment(
        self, num: int, analysis: MarketAnalysis, data: MarketData | None = None
    ) -> Slide:
        # Build driver effects DYNAMICALLY from actual data
        _driver_effects = self._build_driver_effects(analysis, data)

        driver_sections: list[SlideSection] = []
        for i, theme in enumerate(analysis.themes[:4]):
            title = _clean(" ".join(theme.split()[:8]))
            cause, effect = title, "Monitor for follow-through"
            for key, (c, e) in _driver_effects.items():
                if key in theme.lower():
                    cause, effect = c, e
                    break
            driver_sections.append(SlideSection(
                label=str(i + 1),
                value=f"{cause}\n{effect}",
                color="#ffffff",
            ))
        if not driver_sections:
            driver_sections.append(SlideSection(
                label="1", value="No major drivers\nCheck back at market open", color="#888888"
            ))

        # Sentiment data for bottom strip
        sent = analysis.overall_sentiment.value.upper()
        risk = analysis.risk_level.value.upper()
        if "down" in analysis.market_regime:
            trend = "DOWNTREND"
        elif "up" in analysis.market_regime:
            trend = "UPTREND"
        else:
            trend = analysis.market_regime.replace("_", " ").upper() or "SIDEWAYS"

        sent_color = {"BULLISH": "#059669", "BEARISH": "#DC2626",
                      "NEUTRAL": "#D97706", "MIXED": "#9333EA"}.get(sent, "#1A1A2E")
        risk_color = {"LOW": "#059669", "MODERATE": "#D97706",
                      "HIGH": "#EA580C", "EXTREME": "#DC2626"}.get(risk, "#1A1A2E")
        trend_color = {"DOWNTREND": "#DC2626", "UPTREND": "#059669"}.get(trend, "#D97706")

        vix_val = ""
        vix_color = "#D97706"
        if data and data.vix:
            vix_color = "#DC2626" if data.vix > 20 else "#059669" if data.vix < 14 else "#D97706"
            vix_label = "HIGH FEAR" if data.vix > 20 else "LOW VIX" if data.vix < 14 else "NORMAL"
            vix_val = f"{data.vix:.1f} ({vix_label})"

        # Action line
        actions = _sentiment_action(sent, risk, trend)
        action_text = " | ".join(actions)

        # Pack sentiment as extra data_points for the renderer
        sentiment_data = {
            "sentiment": sent, "sent_color": sent_color,
            "risk": risk, "risk_color": risk_color,
            "trend": trend, "trend_color": trend_color,
            "vix": vix_val, "vix_color": vix_color,
            "action": action_text,
        }

        return Slide(
            slide_number=num,
            slide_type=SlideType.DRIVERS_SENTIMENT,
            headline="KEY DRIVERS",
            sections=driver_sections,
            data_points=[{"sentiment_strip": sentiment_data}],
            accent_color="#D97706",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  BUILD DRIVER EFFECTS — data-aware (no hardcoded contradictions)
    # ══════════════════════════════════════════════════════════════════════

    def _build_driver_effects(self, analysis: MarketAnalysis, data: MarketData | None) -> dict[str, tuple[str, str]]:
        """Build cause->effect map from ACTUAL data to avoid contradictions."""
        effects: dict[str, tuple[str, str]] = {}

        # NIFTY — use real direction + levels
        nifty = None
        nifty_level = None
        if data:
            nifty = next((i for i in data.indices if "NIFTY 50" in i.name), None)
        if analysis.key_levels:
            nifty_level = next((kl for kl in analysis.key_levels
                                if "nifty" in kl.index.lower() and "bank" not in kl.index.lower()), None)

        if nifty:
            if nifty.change_pct > 0.5:
                if nifty_level:
                    effects["nifty"] = (f"NIFTY above {nifty_level.pivot:,.0f}",
                                        f"R: {nifty_level.resistance:,.0f} - breakout target")
                else:
                    effects["nifty"] = ("NIFTY recovery", "Buyers stepped in at lows")
            elif nifty.change_pct < -0.5:
                if nifty_level:
                    effects["nifty"] = (f"NIFTY below {nifty_level.pivot:,.0f}",
                                        f"S: {nifty_level.support:,.0f} broken - liquidation zone")
                else:
                    effects["nifty"] = ("NIFTY breakdown", "Triggered stop losses below key support")
            else:
                if nifty_level:
                    effects["nifty"] = (f"NIFTY at {nifty.value:,.0f}",
                                        f"Range {nifty_level.support:,.0f}-{nifty_level.resistance:,.0f} - breakout pending")
                else:
                    effects["nifty"] = ("NIFTY range-bound", "No clear direction - trade extremes only")

        # VIX — check actual direction
        if data and data.vix:
            vix_idx = next((i for i in data.indices if "VIX" in i.name.upper()), None)
            vix_pct = vix_idx.change_pct if vix_idx else 0
            if vix_pct > 5:
                effects["vix"] = ("VIX spike", "Options premiums inflated, hedging costs up")
            elif vix_pct > 0:
                effects["vix"] = ("VIX rising", "Fear building - premium expansion ahead")
            elif vix_pct < -5:
                effects["vix"] = (f"VIX crushed {abs(vix_pct):.1f}%",
                                   "Fear peaked and reversed - sellers exhausted")
            else:
                effects["vix"] = (f"VIX down {abs(vix_pct):.1f}%",
                                   "Fear easing - normalizing volatility")

        # FII/DII — check actual flows
        if data:
            fii = data.fii_dii.fii_net
            dii = data.fii_dii.dii_net
            if fii > 500:
                effects["fii"] = ("FII buying", f"Institutional inflows Rs{fii:,.0f}Cr - bullish signal")
            elif fii < -500:
                effects["fii"] = ("FII exit", f"Institutional selling Rs{abs(fii):,.0f}Cr - supply pressure")
            if dii > 500:
                effects["dii"] = ("DII buying", f"Domestic funds absorbing supply Rs{dii:,.0f}Cr")
            elif dii < -500:
                effects["dii"] = ("DII selling", "Domestic institutions booking profits")

        # Global — check actual direction
        if data and data.global_markets:
            avg_global = sum(m.change_pct for m in data.global_markets) / len(data.global_markets)
            worst = min(data.global_markets, key=lambda m: m.change_pct)
            best = max(data.global_markets, key=lambda m: m.change_pct)
            if avg_global > 0.5:
                effects["global"] = ("Global rally", f"{best.name} +{best.change_pct:.1f}% - positive cues for India")
            elif avg_global < -0.5:
                effects["global"] = ("Global sell-off", f"{worst.name} {worst.change_pct:.1f}% - overnight weakness")
            else:
                effects["global"] = ("Global flat", "No strong directional cue from global markets")

        # Sectors — use actual data to determine which are up/down
        if data and data.sectors:
            sorted_sec = sorted(data.sectors, key=lambda s: s.change_pct, reverse=True)
            for sec in sorted_sec:
                key = sec.name.lower().replace("nifty ", "").replace(" ", "")
                if sec.change_pct > 1.0:
                    effects[key] = (f"{sec.name} +{sec.change_pct:.1f}%",
                                    f"Sector leading - rotation into {sec.name.split()[-1]}")
                elif sec.change_pct < -1.0:
                    effects[key] = (f"{sec.name} {sec.change_pct:.1f}%",
                                    f"Sector weakness - avoid fresh longs in {sec.name.split()[-1]}")
                elif abs(sec.change_pct) > 0.3:
                    direction = "gaining" if sec.change_pct > 0 else "fading"
                    effects[key] = (f"{sec.name} {direction}",
                                    f"{sec.change_pct:+.1f}% - watch for trend confirmation")

        # BankNifty levels
        if analysis.key_levels:
            bn_level = next((kl for kl in analysis.key_levels if "banknifty" in kl.index.lower().replace(" ", "")), None)
            if bn_level:
                effects["bank"] = (f"BankNifty at {bn_level.pivot:,.0f}",
                                   f"S: {bn_level.support:,.0f} / R: {bn_level.resistance:,.0f} range")

        # Breadth
        if data and data.advance_decline:
            parts = data.advance_decline.split(":")
            if len(parts) == 2:
                try:
                    adv, dec = int(parts[0]), int(parts[1])
                    total = adv + dec
                    if total > 0:
                        ratio = adv / total
                        if ratio > 0.6:
                            effects["breadth"] = ("Broad buying", f"{adv} advances vs {dec} declines - healthy breadth")
                        elif ratio < 0.4:
                            effects["breadth"] = ("Breadth collapse", f"Only {adv} advances vs {dec} declines - broad weakness")
                        else:
                            effects["breadth"] = ("Mixed breadth", f"{adv}:{dec} - no clear majority")
                except ValueError:
                    pass

        return effects

    # ══════════════════════════════════════════════════════════════════════
    #  SLIDE 4: GLOBAL + CRYPTO — with interpretations
    # ══════════════════════════════════════════════════════════════════════

    def _slide_global_crypto(self, num: int, data: MarketData | None) -> Slide:
        sections: list[SlideSection] = []
        if data:
            for gm in data.global_markets[:5]:
                sign = "+" if gm.change_pct >= 0 else ""
                color = "#059669" if gm.change_pct >= 0 else "#DC2626"
                insight = _global_insight(gm.name, gm.change_pct)
                sections.append(SlideSection(
                    label=gm.name,
                    value=f"{sign}{gm.change_pct:.1f}% | {insight}",
                    color=color,
                ))
            # Crypto section — only show if we have data
            crypto = data.raw_extras.get("crypto", {})
            crypto_coins = crypto.get("coins", [])
            if crypto_coins:
                sections.append(SlideSection(
                    label="---", value="CRYPTO", color="#2563EB"
                ))
                for coin in crypto_coins[:3]:  # top 3 coins
                    chg = coin.get("change_24h_pct", 0)
                    sign = "+" if chg >= 0 else ""
                    color = "#059669" if chg >= 0 else "#DC2626"
                    price = coin.get("price_usd", 0)
                    # Use 2 decimal places for prices under $10 (e.g. XRP $1.05)
                    price_fmt = f"${price:,.2f}" if price < 10 else f"${price:,.0f}"
                    sections.append(SlideSection(
                        label=coin.get("symbol", "?"),
                        value=f"{price_fmt} ({sign}{chg:.1f}%)",
                        color=color,
                    ))
        if not sections:
            sections.append(SlideSection(
                label="Global", value="Data pending", color="#888888"
            ))
        return Slide(
            slide_number=num,
            slide_type=SlideType.DATA,
            headline="GLOBAL & CRYPTO",
            sections=sections[:9],
            accent_color="#2563EB",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  SLIDE 5: HOT NEWS — market-moving headlines with impact tags
    # ══════════════════════════════════════════════════════════════════════

    def _slide_news(self, num: int, data: MarketData | None) -> Slide:
        sections: list[SlideSection] = []
        if data and data.news:
            # Filter out clickbait / incomplete headlines
            quality_news = [
                n for n in data.news
                if not n.headline.rstrip().endswith((
                    " to", " the", " a", " an", " of", " in", " on",
                    " for", " and", " or", " is", " are", " was",
                    " with", " from", " by", " at", " amid", " as",
                    " after", " before", " that", " this", " its",
                    " how", " will", " may", " can", " has",
                ))
                and len(n.headline.strip()) >= 30
                and not any(cb in n.headline.lower() for cb in [
                    "check how", "here is why", "click here",
                    "you won't believe", "find out",
                ])
            ]
            # Fall back to unfiltered if filter removed everything
            pool = quality_news[:6] if quality_news else data.news[:6]
            for news in pool[:3]:  # 3 news max for premium layout
                headline = _truncate_word_boundary(_clean(news.headline), 120)
                source = news.source[:20] if news.source else "Market"
                # Impact tagging based on keywords
                h_lower = headline.lower()
                if any(w in h_lower for w in ["crash", "war", "crisis", "ban", "default", "panic", "bloodbath", "plunge"]):
                    impact, color = "HIGH IMPACT", "#DC2626"
                elif any(w in h_lower for w in ["rbi", "fed", "rate", "fii", "policy", "budget", "election", "tariff", "tax"]):
                    impact, color = "POLICY", "#D97706"
                elif any(w in h_lower for w in ["rally", "surge", "breakout", "record", "high", "soar", "jump", "buy", "rebound"]):
                    impact, color = "BULLISH", "#059669"
                elif any(w in h_lower for w in ["sell", "fall", "drop", "weak", "decline", "bear", "correction", "low"]):
                    impact, color = "BEARISH", "#DC2626"
                elif any(w in h_lower for w in ["result", "q3", "q4", "earnings", "profit", "revenue"]):
                    impact, color = "EARNINGS", "#2563EB"
                elif any(w in h_lower for w in ["outlook", "ahead", "expect", "predict", "things", "decide"]):
                    impact, color = "ANALYSIS", "#cc99ff"
                else:
                    impact, color = "MARKET NEWS", "#2563EB"

                sections.append(SlideSection(
                    label=impact,
                    value=f"{headline}\n{source}",
                    color=color,
                ))
        if not sections:
            sections.append(SlideSection(
                label="MARKET NEWS",
                value="No major headlines today\nSystem",
                color="#888888",
            ))
        return Slide(
            slide_number=num,
            slide_type=SlideType.NEWS,
            headline="HOT NEWS TODAY",
            sections=sections[:3],
            data_points=[{"image_url": n.image_url} for n in (data.news[:3] if data and data.news else [])],
            accent_color="#DC2626",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  SLIDE 6: INSIGHT + CTA (merged — "ACTION PLAN")
    # ══════════════════════════════════════════════════════════════════════

    def _slide_insight_cta(
        self, num: int, analysis: MarketAnalysis, data: MarketData | None
    ) -> Slide:
        insights: list[str] = []

        # Market direction with key level context
        sent = analysis.overall_sentiment.value.lower()
        score = analysis.sentiment_score

        # Add NIFTY key level context
        nifty_level = None
        for kl in analysis.key_levels:
            if "nifty" in kl.index.lower() and "bank" not in kl.index.lower():
                nifty_level = kl
                break

        # Level + Action + Reason format (using proper arrow)
        if sent == "bearish":
            if nifty_level:
                insights.append(f"NIFTY at {nifty_level.pivot:,.0f}: Short below {nifty_level.support:,.0f} - Liquidity sweep zone")
            else:
                insights.append("Selling pressure: Reduce exposure - Supply zone rejection")
        elif sent == "bullish":
            if nifty_level:
                insights.append(f"Hold above {nifty_level.pivot:,.0f}: Buy dips - Target {nifty_level.resistance:,.0f}")
            else:
                insights.append("Momentum positive: Add on dips - Demand zone holding")
        else:
            if nifty_level:
                insights.append(f"Range: {nifty_level.support:,.0f}-{nifty_level.resistance:,.0f}\nTrade extremes - Breakout pending")
            else:
                insights.append("No direction: Reduce size\nBreakout confirmation needed")

        # Sector rotation with action
        if data and data.sectors:
            sorted_sectors = sorted(data.sectors, key=lambda s: s.change_pct)
            worst = sorted_sectors[0]
            best = sorted_sectors[-1]
            if best.change_pct > 1 and worst.change_pct < -1:
                insights.append(f"{worst.name} fading\nRotate to {best.name} - Relative strength")
            elif worst.change_pct < -2:
                insights.append(f"{worst.name} ({worst.change_pct:+.1f}%)\nExit positions - Structure broken")
            elif best.change_pct > 1:
                insights.append(f"{best.name} ({best.change_pct:+.1f}%)\nAccumulate - Sector breakout forming")
            else:
                insights.append("All sectors weak\nReduce exposure - No safe haven")

        # Volatility with action
        if data and data.vix and data.vix > 22:
            insights.append(f"VIX {data.vix:.1f}: Tight stop losses\nHigh premium decay")
        elif data and data.vix and data.vix > 18:
            insights.append(f"VIX {data.vix:.1f}: Hedge positions\nVolatile swings ahead")
        elif abs(score) > 0.5:
            insights.append("Strong trend: Ride the move\nTrail stops aggressively")
        else:
            insights.append("Low conviction: Small positions\nBreakout entry only")

        # Capital management
        if data and data.fii_dii.fii_net != 0:
            fii = data.fii_dii.fii_net
            if abs(fii) > 1000:
                action = "buying" if fii > 0 else "selling"
                reason = "Support for bulls" if fii > 0 else "Distribution phase"
                insights.append(f"FII {action} Rs{abs(fii):,.0f}Cr\n{reason} - Follow smart money")
            else:
                insights.append("FII flows muted\nNo conviction - Protect capital")
        else:
            if sent == "bearish":
                insights.append("Capital at risk: Hedge with puts\nDon't catch falling knives")
            elif sent == "bullish":
                insights.append("Momentum intact: Trail stops\nLet winners run")
            else:
                insights.append("Uncertainty high: Cash is a position\nDeploy on breakout")

        sections = []
        for i, ins in enumerate(insights[:4]):
            sections.append(SlideSection(
                label=str(i + 1),
                value=ins,
                color="#2D3561",
            ))

        # CTA engagement data packed into data_points
        if analysis.overall_sentiment == Sentiment.BEARISH:
            cta_q = "Where do you see support?"
        elif analysis.overall_sentiment == Sentiment.BULLISH:
            cta_q = "Which stock are you buying?"
        else:
            cta_q = "Break up or break down?"
        cta_data = {
            "cta": True,
            "cta_items": [
                "Save this for market open",
                f"Comment: {cta_q}",
                "Follow @StocksWithGaurav",
            ],
        }

        return Slide(
            slide_number=num,
            slide_type=SlideType.INSIGHT_CTA,
            headline="ACTION PLAN",
            sections=sections,
            data_points=[cta_data],
            footer="Not SEBI registered. Educational only. DYOR.",
            accent_color="#2D3561",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  SLIDES 7-9: STOCK PICKS (rich card with bias)
    # ══════════════════════════════════════════════════════════════════════

    def _slide_stock(self, num: int, pick, analysis: MarketAnalysis) -> Slide:
        setup = (pick.setup_type or "watch").replace("_", " ").upper()
        # Normalize setup types to premium labels
        setup_map = {
            "VOLATILITY PLAY": "MOMENTUM",
            "MOMENTUM": "TREND",
            "NEWS CATALYST": "BREAKOUT",
            "REVERSAL": "REVERSAL",
            "BREAKOUT": "BREAKOUT",
            "BREAKDOWN": "BREAKDOWN",
        }
        setup = setup_map.get(setup, setup)

        # Diversity: rotate setups so all 3 stocks don't show the same label
        # Use slide number to vary: stock 1=original, stock 2=alternate, stock 3=alternate2
        _BEARISH_ROTATION = ["BREAKDOWN", "REVERSAL WATCH", "TREND FADE"]
        _BULLISH_ROTATION = ["BREAKOUT", "MOMENTUM", "DIP BUY"]
        _NEUTRAL_ROTATION = ["RANGE TRADE", "BREAKOUT WATCH", "SWING SETUP"]

        stock_idx = (num - 7) % 3  # 0, 1, 2 for the 3 stock slides
        if analysis.overall_sentiment == Sentiment.BEARISH:
            if setup in ("BREAKDOWN", "BREAKDOWN") and stock_idx > 0:
                setup = _BEARISH_ROTATION[stock_idx]
        elif analysis.overall_sentiment == Sentiment.BULLISH:
            if setup in ("BREAKOUT", "MOMENTUM") and stock_idx > 0:
                setup = _BULLISH_ROTATION[stock_idx]
        else:
            if stock_idx > 0:
                setup = _NEUTRAL_ROTATION[min(stock_idx, 2)]

        # Build rich WHY bullets (min 2-3)
        why_parts = [_clean(r.strip()) for r in pick.rationale.split(";")][:3] if pick.rationale else []
        # Add contextual bullets based on setup + sentiment
        _bearish_extras = [
            f"Near 52-week low zone - key level",
            f"Relative weakness vs {pick.sector} peers",
            f"Below key moving averages",
        ]
        _bullish_extras = [
            f"Relative strength vs {pick.sector} peers",
            f"Volume breakout candidate",
            f"Above key moving averages",
        ]
        _neutral_extras = [
            f"At decision zone - watch closely",
            f"Range-bound accumulation pattern",
            f"Sector rotation candidate",
        ]
        if analysis.overall_sentiment == Sentiment.BEARISH:
            extras = _bearish_extras
        elif analysis.overall_sentiment == Sentiment.BULLISH:
            extras = _bullish_extras
        else:
            extras = _neutral_extras
        # Use stock_idx to rotate extras so each stock gets different bullets
        for ei in range(3):
            if len(why_parts) >= 3:
                break
            why_parts.append(extras[(stock_idx + ei) % len(extras)])

        why_sections: list[SlideSection] = []
        for part in why_parts:
            words = part.split()[:10]
            why_sections.append(
                SlideSection(label="WHY", value=" ".join(words), color="#ffffff")
            )

        # Trade bias + Entry idea based on sentiment + setup
        if analysis.overall_sentiment == Sentiment.BEARISH:
            bias_map = {
                "BREAKDOWN": ("Short on bounce rejection", f"Enter below {pick.symbol} demand zone"),
                "REVERSAL WATCH": ("Buy near support zone", f"Enter at liquidity sweep low"),
                "TREND FADE": ("Short on rally weakness", f"Enter at supply zone rejection"),
                "REVERSAL": ("Buy near support zone", f"Enter at demand zone test"),
                "BREAKOUT": ("Buy above resistance", f"Enter on breakout confirmation"),
            }
            bias, entry = bias_map.get(setup, ("Short on bounce rejection", "Enter at supply zone"))
        elif analysis.overall_sentiment == Sentiment.BULLISH:
            bias_map = {
                "BREAKOUT": ("Buy above resistance", f"Enter on volume breakout"),
                "MOMENTUM": ("Buy on momentum", f"Enter on pullback to demand"),
                "DIP BUY": ("Buy on dip", f"Enter near support zone"),
            }
            bias, entry = bias_map.get(setup, ("Buy on dip near support", "Enter at demand zone"))
        else:
            bias, entry = "Trade on breakout", "Enter on range breakout"

        # Bias color: green for long/buy setups, red for short/sell setups
        _short_keywords = ("short", "sell", "fade", "breakdown")
        bias_color = "#DC2626" if any(k in bias.lower() for k in _short_keywords) else "#059669"

        # Build trade level sections with EXACT numbers
        level_sections: list[SlideSection] = []
        if pick.stop_loss > 0 and pick.target > 0 and pick.price > 0:
            # Determine entry from setup type
            if any(k in setup.lower() for k in ("breakout", "momentum")):
                entry_val = f"Above {pick.price:,.0f}"
            elif any(k in setup.lower() for k in ("dip", "reversal", "support")):
                entry_val = f"Near {pick.stop_loss * 1.01:,.0f}"
            elif any(k in setup.lower() for k in ("short", "fade", "breakdown")):
                entry_val = f"Below {pick.price:,.0f}"
            else:
                entry_val = f"At {pick.price:,.0f}"
            level_sections = [
                SlideSection(label="ENTRY", value=entry_val, color="#059669"),
                SlideSection(label="STOP LOSS", value=f"{pick.stop_loss:,.0f}", color="#DC2626"),
                SlideSection(label="TARGET", value=f"{pick.target:,.0f}", color="#2563EB"),
            ]
            # Calculate R:R
            risk_amt = abs(pick.price - pick.stop_loss)
            reward_amt = abs(pick.target - pick.price)
            if risk_amt > 0:
                rr = reward_amt / risk_amt
                level_sections.append(
                    SlideSection(label="R:R", value=f"1:{rr:.1f}", color="#D97706")
                )
        else:
            level_sections = [
                SlideSection(label="BIAS", value=bias, color=bias_color),
                SlideSection(label="ENTRY", value=entry, color="#D97706"),
            ]

        sections = [
            SlideSection(label="SECTOR", value=pick.sector, color="#2563EB"),
            SlideSection(label="SETUP", value=setup, color="#D97706"),
        ] + why_sections + level_sections

        # Generate mini chart for the stock with S/R levels marked
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_dir = str(OUTPUT_DIR / date_str)
        chart_path = generate_mini_chart(
            pick.symbol,
            accent_color="#2D3561",
            support=pick.stop_loss if pick.stop_loss > 0 else 0,
            resistance=pick.target if pick.target > 0 else 0,
            output_dir=out_dir,
        )

        return Slide(
            slide_number=num,
            slide_type=SlideType.STOCK_CARD,
            headline=pick.symbol,
            subheadline=pick.name,
            sections=sections,
            data_points=[{"chart_path": chart_path}] if chart_path else [],
            accent_color="#D97706",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  CAPTION (max 100 words)
    # ══════════════════════════════════════════════════════════════════════

    def _build_caption(
        self, mode: PipelineMode, analysis: MarketAnalysis, picks: StockPicks
    ) -> str:
        sent = analysis.overall_sentiment.value.upper()
        is_pre = mode == PipelineMode.PRE_MARKET
        hook_map = {
            Sentiment.BEARISH: "Markets under pressure." if is_pre else "Blood on the street.",
            Sentiment.BULLISH: "Bulls leading the charge." if is_pre else "Green day for traders!",
            Sentiment.NEUTRAL: "Sideways action expected." if is_pre else "Flat but key levels tested.",
            Sentiment.MIXED:   "Mixed signals today." if is_pre else "Wild session done.",
        }
        hook = hook_map.get(analysis.overall_sentiment, "Markets moving.")
        drivers = _clean(" | ".join(analysis.themes[:2])) if analysis.themes else ""
        stocks = ", ".join(p.symbol for p in picks.picks[:3])
        stocks_text = f"Picks: {stocks}." if stocks else ""
        caption = f"{hook} Sentiment: {sent}. {drivers} {stocks_text} Save + Follow @StocksWithGaurav"
        return " ".join(caption.split()[:100]).strip()

    # ══════════════════════════════════════════════════════════════════════
    #  HASHTAGS (10)
    # ══════════════════════════════════════════════════════════════════════

    def _build_hashtags(self, analysis: MarketAnalysis) -> list[str]:
        tags = list(_HASHTAGS_CORE)
        tags.extend(_HASHTAGS_SENTIMENT.get(analysis.overall_sentiment, []))
        for t in _HASHTAGS_EXTRAS:
            if t not in tags and len(tags) < 10:
                tags.append(t)
        return tags[:10]

    @staticmethod
    def _sentiment_accent(sentiment: Sentiment) -> str:
        return {
            Sentiment.BULLISH: "#059669",
            Sentiment.BEARISH: "#DC2626",
            Sentiment.NEUTRAL: "#D97706",
            Sentiment.MIXED:   "#9333EA",
        }.get(sentiment, "#2D3561")
