"""
Content Creation / agents / copywriter_agent.py

CopywriterAgent — generates a ready-to-post text file alongside each
carousel run, containing:
  - Instagram: viral hook, caption, and hashtags
  - YouTube:   viral hook, description, and tags

All copy is derived from the actual pipeline data (sentiment, stock picks,
index levels, news headlines) so every day's file is unique and relevant.
"""
from __future__ import annotations

import logging
import textwrap
from datetime import datetime
from pathlib import Path

from agents.base import BaseContentAgent
from models.contracts import (
    CarouselContent,
    MarketAnalysis,
    MarketData,
    PipelineMode,
    Sentiment,
    StockPicks,
)

log = logging.getLogger("content_creation.agents.copywriter")


# ── helpers ───────────────────────────────────────────────────────────────────

def _pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _sentiment_emoji(s: Sentiment) -> str:
    return {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(s.value, "🟡")


def _sentiment_word(s: Sentiment) -> str:
    return {"bullish": "BULLISH", "bearish": "BEARISH", "neutral": "NEUTRAL"}.get(s.value, "NEUTRAL")


def _nifty_val(market_data: MarketData) -> tuple[float, float]:
    for idx in market_data.indices:
        if "NIFTY" in idx.name.upper() and "BANK" not in idx.name.upper():
            return idx.value, idx.change_pct
    return 0.0, 0.0


def _top_sector(market_data: MarketData) -> str:
    if not market_data.sectors:
        return "markets"
    s = max(market_data.sectors, key=lambda x: abs(x.change_pct))
    direction = "up" if s.change_pct > 0 else "down"
    return f"{s.name} {direction} {abs(s.change_pct):.1f}%"


def _stock_list(picks: StockPicks, max_n: int = 3) -> list[str]:
    return [p.symbol for p in picks.picks[:max_n]]


def _top_news(market_data: MarketData) -> str:
    for n in market_data.news[:3]:
        h = n.headline.strip()
        if h:
            return h[:80]
    return "markets on the move"


# ── Instagram copy ────────────────────────────────────────────────────────────

def _instagram_copy(
    mode: PipelineMode,
    analysis: MarketAnalysis,
    market_data: MarketData,
    picks: StockPicks,
    carousel: CarouselContent,
    date_str: str,
) -> str:
    nifty_val, nifty_pct = _nifty_val(market_data)
    sentiment = analysis.overall_sentiment
    emoji = _sentiment_emoji(sentiment)
    sent_word = _sentiment_word(sentiment)
    stocks = _stock_list(picks)
    sector_str = _top_sector(market_data)
    top_news = _top_news(market_data)

    is_pre = mode == PipelineMode.PRE_MARKET

    # ── Viral hooks (pick based on sentiment + mode) ──────────────────────
    if is_pre:
        if sentiment == Sentiment.BEARISH:
            hooks = [
                "⚠️ Bears are in charge today. Are YOU prepared?",
                f"📉 NIFTY looks weak at {nifty_val:,.0f} — here's what to do.",
                "🔴 Pre-market alert: Danger zones you MUST know before 9:15 AM",
            ]
        elif sentiment == Sentiment.BULLISH:
            hooks = [
                "🚀 Bulls are back! NIFTY setting up for a BIG move.",
                "📈 Pre-market edge: 3 stocks that could LEAD today's rally",
                "🟢 Smart money is already positioned — are you?",
            ]
        else:
            hooks = [
                "⚡ Market at crossroads — one wrong move can cost you.",
                "🎯 Pre-market playbook: What I'm watching before 9:15 AM",
                f"🔍 Nifty at {nifty_val:,.0f} — breakout or breakdown? Watch these levels.",
            ]
    else:  # post market
        if sentiment == Sentiment.BEARISH:
            hooks = [
                "📉 Market closed RED — here's exactly what happened today.",
                f"🔴 {nifty_pct:.1f}% down. Winners & losers from today's session.",
                f"💀 Brutal session! {sector_str}. Full post-market breakdown inside.",
            ]
        elif sentiment == Sentiment.BULLISH:
            hooks = [
                "📈 Market closes GREEN! Today's biggest winners revealed.",
                f"🟢 Bulls dominated! NIFTY {_pct(nifty_pct)} — full wrap inside.",
                f"🚀 {sector_str.upper()} — today's market story in 10 slides.",
            ]
        elif sentiment == Sentiment.MIXED:
            hooks = [
                f"🔥 Volatile day! NIFTY {_pct(nifty_pct)} but the intraday story was INSANE.",
                f"⚡ {sector_str.upper()} — choppy session with hidden opportunities.",
                "🎯 Dip got bought hard — here's what smart traders did today.",
            ]
        else:
            hooks = [
                "📊 Flat close — but the structure is setting up for a big move.",
                f"🎯 NIFTY {_pct(nifty_pct)} — range locked. Here's the breakout playbook.",
                "🔍 Quiet day on screen — but institutional footprints tell a different story.",
            ]

    hook = hooks[hash(date_str) % len(hooks)]

    # ── Build stock levels summary for caption ────────────────────────────
    pick_lines = []
    for p in picks.picks[:3]:
        line = f"  {p.symbol}"
        if p.stop_loss > 0 and p.target > 0:
            line += f" | SL: {p.stop_loss:,.0f} | T: {p.target:,.0f}"
        pick_lines.append(line)
    stocks_detail = "\n".join(pick_lines) if pick_lines else "See slides"

    # ── Caption ───────────────────────────────────────────────────────────
    mode_label = "Pre-Market Report" if is_pre else "Post-Market Wrap"
    stock_str  = ", ".join(f"#{s}" for s in stocks) if stocks else "#Nifty #Sensex"
    themes_str = " | ".join(analysis.themes[:2]) if analysis.themes else "Market wrap"

    caption = textwrap.dedent(f"""
        {hook}

        {emoji} Today's {mode_label} — {date_str}

        📌 Sentiment: {sent_word}
        📍 NIFTY: {nifty_val:,.0f} ({_pct(nifty_pct)})
        🏭 Key sector: {sector_str}

        🎯 Stock picks with EXACT levels:
{stocks_detail}

        👉 Swipe all {carousel.total_slides} slides for the full breakdown.

        Save this for market open 📌
        Comment: Bounce or Breakdown? 👇

        Follow @stockswithgaurav for daily analysis 🔔

        —
        ⚠️ Educational only. Not SEBI registered. DYOR.
    """).strip()

    # ── Hashtags ──────────────────────────────────────────────────────────
    stock_tags = [f"#{s}" for s in stocks]
    base_tags = [
        "#StocksWithGaurav", "#NiftyPrediction", "#NiftyAnalysis",
        "#StockMarketIndia", "#IndianStockMarket", "#NSE", "#BSE",
        "#TradingTips", "#Sensex", "#OptionTrading", "#StockTips",
        f"#{'PreMarket' if is_pre else 'PostMarket'}", "#DailyAnalysis",
        "#MarketToday", "#TradingView", "#TechnicalAnalysis",
        "#SwingTrading", "#IntraDay", "#ShareMarket", "#Nifty50",
        "#StockMarket", "#ProfitableTrading", "#MarketAnalysis",
    ]
    all_tags = (stock_tags + base_tags)[:30]  # Instagram allows 30 max
    hashtag_block = " ".join(all_tags)

    return "\n".join([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  INSTAGRAM",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📌 VIRAL HOOK (first line — use as opening slide text or caption opener):",
        hook,
        "",
        "📝 CAPTION (copy-paste ready):",
        caption,
        "",
        "🏷️  HASHTAGS (30 tags):",
        hashtag_block,
    ])


# ── YouTube copy ──────────────────────────────────────────────────────────────

def _youtube_copy(
    mode: PipelineMode,
    analysis: MarketAnalysis,
    market_data: MarketData,
    picks: StockPicks,
    date_str: str,
) -> str:
    nifty_val, nifty_pct = _nifty_val(market_data)
    sentiment = analysis.overall_sentiment
    sent_word = _sentiment_word(sentiment)
    stocks = _stock_list(picks)
    sector_str = _top_sector(market_data)
    top_news = _top_news(market_data)
    is_pre = mode == PipelineMode.PRE_MARKET
    mode_label = "Pre-Market Analysis" if is_pre else "Post-Market Analysis"

    # ── YouTube title (A/B options) ───────────────────────────────────────
    if is_pre:
        if sentiment == Sentiment.BEARISH:
            titles = [
                f"🔴 NIFTY Pre-Market Analysis {date_str} | Bears in Control | Stocks to Watch",
                f"📉 Pre-Market Report {date_str} | NIFTY {nifty_val:,.0f} | {sent_word} Setup",
                f"⚠️ Market Alert {date_str} | Pre-Market Outlook | Top Stocks Today",
            ]
        elif sentiment == Sentiment.BULLISH:
            titles = [
                f"🟢 NIFTY Pre-Market Analysis {date_str} | Bullish Setup | Stocks to Buy",
                f"📈 Pre-Market Report {date_str} | NIFTY {nifty_val:,.0f} | {sent_word} Momentum",
                f"🚀 Market Outlook {date_str} | Pre-Market Edge | Top Stock Picks",
            ]
        else:
            titles = [
                f"🎯 NIFTY Pre-Market Analysis {date_str} | Key Levels | Stocks to Watch",
                f"📊 Pre-Market Report {date_str} | NIFTY at {nifty_val:,.0f} | What to Expect",
                f"🔍 Market Outlook {date_str} | Neutral Setup | Trading Plan Inside",
            ]
    else:
        if sentiment == Sentiment.BEARISH:
            titles = [
                f"🔴 NIFTY Post-Market Wrap {date_str} | {_pct(nifty_pct)} | Today's Analysis",
                f"📉 Market Closed RED {date_str} | Post-Market Review | Winners & Losers",
                f"⚡ Post-Market {date_str} | Bearish Session | What's Next for NIFTY",
            ]
        elif sentiment == Sentiment.BULLISH:
            titles = [
                f"🟢 NIFTY Post-Market Wrap {date_str} | {_pct(nifty_pct)} | Bull Run Continues?",
                f"📈 Market Closed GREEN {date_str} | Post-Market Analysis | Top Movers",
                f"🚀 Post-Market {date_str} | Bullish Momentum | Stocks That Flew Today",
            ]
        else:
            titles = [
                f"📊 NIFTY Post-Market Wrap {date_str} | Flat Session | What Next?",
                f"🎯 Post-Market Analysis {date_str} | NIFTY {_pct(nifty_pct)} | Key Takeaways",
                f"🔄 Market Wrap {date_str} | Mixed Session | Levels to Watch Tomorrow",
            ]

    title    = titles[0]
    title_b  = titles[1] if len(titles) > 1 else title
    title_c  = titles[2] if len(titles) > 2 else title

    # ── Description ───────────────────────────────────────────────────────
    stock_str_plain = ", ".join(stocks) if stocks else "top setups"
    themes_str = " | ".join(analysis.themes[:3]) if analysis.themes else "Market outlook"

    description = textwrap.dedent(f"""
        📊 {mode_label} for {date_str} | StocksWithGaurav

        In this video, I cover the complete market outlook {'before' if is_pre else 'after'} today's session:

        ✅ NIFTY at {nifty_val:,.0f} ({_pct(nifty_pct)}) — key support & resistance
        ✅ Market Sentiment: {sent_word}
        ✅ Sector in Focus: {sector_str}
        ✅ Stock Picks: {stock_str_plain}
        ✅ Today's Theme: {themes_str}
        ✅ In Focus: {top_news}

        🎯 Stocks Discussed: {stock_str_plain}
        📌 Key Levels: Watch the pinned comment for updated S/R zones.

        ————————————————————————
        📲 Follow for Daily Analysis:
        ▶ YouTube:   youtube.com/@StocksWithGaurav
        📸 Instagram: @stockswithgauravshree
        💬 Telegram:  @stockswithgaurav
        ————————————————————————

        ⏱️ Timestamps:
        00:00 — Market Overview
        01:30 — NIFTY & Levels
        03:00 — Sector Analysis
        05:00 — Stock Picks & Setups
        08:00 — Trading Plan

        ⚠️ Disclaimer: This video is for educational purposes only.
        Not SEBI registered investment advice.

        #StocksWithGaurav #NiftyAnalysis #StockMarketIndia
    """).strip()

    # ── Tags ──────────────────────────────────────────────────────────────
    stock_tags_yt = ", ".join(stocks) if stocks else ""
    tags = ", ".join(filter(None, [
        "nifty analysis", "nifty prediction", "indian stock market today",
        "stock market india", "pre market analysis" if is_pre else "post market analysis",
        "nifty 50", "bank nifty", "sensex today", "stocks to buy today",
        "trading tips india", "technical analysis", "swing trading",
        "intraday trading", "stock market", "nse bse",
        "stocks with gaurav", "share market today", "market outlook",
        stock_tags_yt,
    ]))

    return "\n".join([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  YOUTUBE",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📌 TITLE OPTIONS (A/B/C test — pick one):",
        f"  A) {title}",
        f"  B) {title_b}",
        f"  C) {title_c}",
        "",
        "📝 DESCRIPTION (copy-paste ready):",
        description,
        "",
        "🏷️  TAGS:",
        tags,
    ])


# ── Main agent ────────────────────────────────────────────────────────────────

class CopywriterAgent(BaseContentAgent):
    name = "CopywriterAgent"
    description = "Generates Instagram and YouTube copy for each carousel run"

    def run(
        self,
        *,
        mode: PipelineMode,
        analysis: MarketAnalysis,
        market_data: MarketData,
        picks: StockPicks,
        carousel: CarouselContent,
        output_dir: Path,
    ) -> dict:
        date_str = datetime.now().strftime("%Y-%m-%d")
        mode_label = "pre_market" if mode == PipelineMode.PRE_MARKET else "post_market"

        ig_copy  = _instagram_copy(mode, analysis, market_data, picks, carousel, date_str)
        yt_copy  = _youtube_copy(mode, analysis, market_data, picks, date_str)

        divider = "\n\n" + "═" * 60 + "\n\n"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")

        full_text = "\n".join([
            f"StocksWithGaurav — {mode_label.upper().replace('_', ' ')} COPY KIT",
            f"Generated: {timestamp}",
            f"Slides: {carousel.total_slides} | Sentiment: {analysis.overall_sentiment.value}",
            "",
            ig_copy,
            divider,
            yt_copy,
        ])

        out_path = output_dir / f"copy_{mode_label}.txt"
        out_path.write_text(full_text, encoding="utf-8")
        log.info("Copy kit saved: %s", out_path)

        return {"copy_path": str(out_path)}
