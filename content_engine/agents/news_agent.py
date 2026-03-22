"""
content_engine/agents/news_agent.py

Market Intelligence Engine — generates 5–8 high-impact, tradeable news posts daily.

Pipeline:
  1. INGESTION   — fetch from NewsAPI + GNews (multi-query for broader coverage)
  2. DEDUP       — remove duplicates by title similarity
  3. FILTERING   — keep only market-moving, tradeable news
  4. EXTRACTION  — rule-based impact analysis per article
  5. AI REFINE   — polish via OpenAI (graceful fallback to rule-based)
  6. RANKING     — score by impact × relevance; return TOP 5–8
  7. FORMAT      — Telegram-ready posts, one per news item
  8. SEND        — fire to content channel with rate limiting

run(settings)          → sends posts, returns result dict
get_top_market_news()  → returns list of intelligence dicts (standalone use)
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime
from typing import Any

from content_engine.config.settings import ContentEngineSettings
from content_engine.services.scraper import fetch_market_news
from content_engine.services.formatter import format_error_alert
from content_engine.services.telegram_service import send_message, send_plain, send_image
from content_engine.services.image_generator import generate_news_image, generate_headline_post
from content_engine.state.post_history import NewsHistory

log = logging.getLogger("content_engine.news_agent")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1 — DATA INGESTION
# Multi-query strategy for broader, more relevant coverage
# ═══════════════════════════════════════════════════════════════════════════════

_NEWS_QUERIES = [
    "India stock earnings results NSE BSE quarterly",
    "RBI SEBI policy rate hike repo inflation India",
    "India merger acquisition FII DII investment",
    "NIFTY BANKNIFTY Sensex market rally crash today",
    "India oil sector energy banking IT results",
    "global markets US Fed rate dollar impact India",
]


def fetch_all_news(settings: ContentEngineSettings) -> list[dict[str, Any]]:
    """
    Fetch from multiple targeted queries to maximize coverage.
    Runs each query and merges results; deduplication happens next.
    Returns raw article list: [{title, description, source, url, published_at}]
    """
    all_articles: list[dict[str, Any]] = []

    # Use first 3 queries to stay within free-tier rate limits
    for query in _NEWS_QUERIES[:3]:
        try:
            articles = fetch_market_news(settings, query=query, max_results=10)
            all_articles.extend(articles)
            log.debug("Query '%s...' → %d articles", query[:30], len(articles))
        except Exception as exc:
            log.warning("News fetch failed for query '%s': %s", query[:40], exc)

    log.info("Raw articles fetched: %d total (pre-dedup)", len(all_articles))
    return all_articles


def _dedup(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove near-duplicate articles using title word overlap.
    Keeps the first occurrence of each near-duplicate cluster.
    """
    seen_tokens: list[set[str]] = []
    unique: list[dict[str, Any]] = []

    for article in articles:
        title = article.get("title", "").lower()
        # Tokenise to word-level set (ignore stop words)
        stop = {"a", "an", "the", "in", "on", "at", "of", "to", "is", "are",
                "and", "or", "for", "with", "as", "by", "its", "it"}
        tokens = {w for w in re.findall(r"[a-z]+", title) if w not in stop and len(w) > 2}

        # If 60%+ of tokens overlap with any seen article, it's a duplicate
        is_dup = False
        for seen in seen_tokens:
            if not seen:
                continue
            overlap = len(tokens & seen) / max(len(tokens), 1)
            if overlap >= 0.6:
                is_dup = True
                break

        if not is_dup:
            seen_tokens.append(tokens)
            unique.append(article)

    log.info("After dedup: %d unique articles (removed %d)", len(unique), len(articles) - len(unique))
    return unique


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2 — SMART FILTERING
# Keep only news that has a clear market impact signal
# ═══════════════════════════════════════════════════════════════════════════════

# Keywords that strongly signal market-relevant news
_INCLUDE_SIGNALS: list[tuple[list[str], int]] = [
    # (keyword list, weight)
    (["earnings", "results", "profit", "revenue", "quarterly", "q1", "q2", "q3", "q4"], 10),
    (["merger", "acquisition", "takeover", "stake", "buyout", "deal", "bid"], 10),
    (["rbi", "sebi", "policy", "rate", "repo", "inflation", "gdp", "budget"], 9),
    (["resign", "ceo", "chairman", "management", "board", "cfo", "director"], 9),
    (["contract", "order", "wins", "government tender", "defence order"], 8),
    (["ipo", "fpo", "listing", "demat", "qip", "block deal"], 8),
    (["fii", "dii", "foreign investor", "institutional buying", "institutional selling"], 7),
    (["oil", "crude", "opec", "energy", "fuel price", "petrol", "diesel"], 7),
    (["us fed", "federal reserve", "interest rate", "us inflation", "dollar"], 7),
    (["nifty", "banknifty", "sensex", "nse", "bse", "midcap", "smallcap"], 6),
    (["sector rally", "sector crash", "banking sector", "it sector", "pharma sector"], 6),
    (["war", "geopolitical", "ukraine", "middle east", "china tension"], 5),
]

# Patterns that signal low-value, non-tradeable content
_EXCLUDE_PATTERNS = [
    r"opinion[:\s]",
    r"analysis[:\s].*long[- ]term",
    r"five[ -]year",
    r"invest(?:ing|ment) tip",
    r"personal finance",
    r"mutual fund.*sip",
    r"best stock.*2025|2026|2027",
    r"how to invest",
    r"beginner",
    r"top \d+ stocks? to buy",
    r"explained[:\s]",
]
_EXCLUDE_RE = re.compile("|".join(_EXCLUDE_PATTERNS), re.IGNORECASE)


def filter_market_relevant_news(
    articles: list[dict[str, Any]],
    min_score: int = 5,
) -> list[dict[str, Any]]:
    """
    Score each article and keep only those above min_score.
    Returns articles with an added '_relevance_score' field.
    """
    scored: list[dict[str, Any]] = []

    for article in articles:
        title = article.get("title", "").lower()
        desc  = article.get("description", "").lower()
        text  = f"{title} {desc}"

        # Hard exclude
        if _EXCLUDE_RE.search(text):
            continue

        # Score by include signals
        score = 0
        matched_signals: list[str] = []
        for keywords, weight in _INCLUDE_SIGNALS:
            for kw in keywords:
                if kw in text:
                    score += weight
                    matched_signals.append(kw)
                    break  # one match per signal group is enough

        if score >= min_score:
            article["_relevance_score"] = score
            article["_matched_signals"] = matched_signals[:3]  # top 3 for debug
            scored.append(article)

    scored.sort(key=lambda a: a["_relevance_score"], reverse=True)
    log.info("Filtered: %d articles kept (min_score=%d)", len(scored), min_score)
    return scored


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3 — RULE-BASED IMPACT EXTRACTION
# Fast heuristics run BEFORE (and as fallback for) AI
# ═══════════════════════════════════════════════════════════════════════════════

# Rule: (regex pattern, stock_hint, impact_bias, confidence, trade_idea_template)
_IMPACT_RULES: list[tuple[str, str, str, str, str]] = [
    # Earnings beats
    (r"profit.{0,30}(surge|jump|rise|beat|up|grew)",
     "EARNINGS BEAT",
     "Bullish — strong earnings signal institutional accumulation",
     "high",
     "Watch for gap-up or momentum continuation above pre-result high"),

    # Earnings miss
    (r"profit.{0,30}(fall|drop|miss|decline|down|slump)",
     "EARNINGS MISS",
     "Bearish — weak earnings invite selling pressure",
     "high",
     "Watch for gap-down; avoid catching the knife; wait for support retest"),

    # CEO/Chairman resignation
    (r"(ceo|chairman|cfo|md|director).{0,20}(resign|quit|step.?down|exit)",
     "MANAGEMENT CHANGE",
     "Bearish short-term — governance uncertainty increases risk",
     "high",
     "Avoid fresh longs; if below key support, short with tight SL"),

    # New CEO/appointment
    (r"(appoint|names|elect).{0,30}(ceo|chairman|cfo|md)",
     "LEADERSHIP CHANGE",
     "Neutral to mildly bullish — watch for market reaction in first hour",
     "medium",
     "Wait for direction confirmation; no pre-emptive entry"),

    # RBI rate cut
    (r"rbi.{0,20}(cut|lower|reduce).{0,20}rate",
     "RBI RATE CUT",
     "Bullish for Banking, Real Estate, and Auto sectors",
     "high",
     "Watch BANKNIFTY for breakout above resistance; HDFC, ICICI in focus"),

    # RBI rate hike
    (r"rbi.{0,20}(hike|raise|increase).{0,20}rate",
     "RBI RATE HIKE",
     "Bearish for rate-sensitive sectors (banking, real estate)",
     "high",
     "Watch BANKNIFTY for breakdown; NBFCs, housing finance stocks at risk"),

    # Oil price up
    (r"(oil|crude).{0,20}(rise|surge|up|jump|high)",
     "OIL SECTOR",
     "Bullish for ONGC, Oil India, Reliance; bearish for aviation, paints, tyres",
     "medium",
     "Long oil sector stocks on dips; short aviation/paints on rallies"),

    # Oil price down
    (r"(oil|crude).{0,20}(fall|drop|down|decline|low)",
     "OIL SECTOR",
     "Bearish for upstream oil; bullish for aviation, paints, logistics",
     "medium",
     "Watch IndiGo, Asian Paints for breakout; ONGC may face headwinds"),

    # FII buying
    (r"fii.{0,30}(buy|purchas|inflow|net buyer)",
     "FII FLOW",
     "Bullish — institutional inflows support broader market rally",
     "medium",
     "Bullish bias on index; watch for breakouts in large-cap leaders"),

    # FII selling
    (r"fii.{0,30}(sell|outflow|net seller|exit)",
     "FII FLOW",
     "Bearish — institutional outflows create selling pressure",
     "medium",
     "Defensive posture; reduce exposure; watch key index support levels"),

    # Merger / acquisition
    (r"(acquire|merger|takeover|buyout|deal|stake).{0,30}(billion|crore|million|%)",
     "M&A EVENT",
     "Bullish for target company; mixed for acquirer (depends on deal size)",
     "high",
     "Target stock likely to gap up; watch acquirer for dilution risk"),

    # US Fed / rate concern
    (r"(fed|federal reserve).{0,30}(hike|raise|hawkish|higher for longer)",
     "GLOBAL MACRO",
     "Bearish for Indian markets — strong dollar weighs on FII flows",
     "medium",
     "Cautious stance; watch USD/INR; IT sector (dollar revenue) may benefit"),

    # IT sector / dollar tailwind
    (r"(infosys|tcs|wipro|hcl|tech mahindra).{0,30}(result|revenue|deal|order)",
     "IT SECTOR",
     "Sector-specific — earnings direction drives IT index",
     "high",
     "Trade the reporting stock; watch NIFTY IT index for sector momentum"),

    # Government contract / defence order
    (r"(defence|government|ministry).{0,30}(order|contract|tender|approve)",
     "GOVT ORDER",
     "Bullish for the company receiving the contract",
     "high",
     "Buy on dip post-announcement; momentum likely intraday and next session"),

    # IPO listing
    (r"(ipo|listing).{0,20}(debut|list|premium|grey market|gmp)",
     "IPO LISTING",
     "Listing-day volatility expected — direction depends on GMP and market mood",
     "medium",
     "Wait for first 30 minutes before entering; avoid FOMO at open"),
]

_COMPILED_RULES = [(re.compile(pat, re.IGNORECASE), stock, impact, conf, trade)
                   for pat, stock, impact, conf, trade in _IMPACT_RULES]


def _extract_rule_based(article: dict[str, Any]) -> dict[str, Any]:
    """
    Apply rule-based logic to extract structured intelligence from an article.
    Returns a populated intelligence dict; defaults to 'neutral' if no rule matches.
    """
    title = article.get("title", "")
    desc  = article.get("description", "")
    text  = f"{title} {desc}"

    for pattern, stock_hint, impact, confidence, trade_idea in _COMPILED_RULES:
        if pattern.search(text):
            return {
                "stock": stock_hint,
                "event": title[:120],
                "impact": impact,
                "trade_idea": trade_idea,
                "confidence": confidence,
                "source": article.get("source", ""),
                "url": article.get("url", ""),
                "_relevance_score": article.get("_relevance_score", 5),
            }

    # No specific rule matched — generic but still relevant
    return {
        "stock": "BROAD MARKET",
        "event": title[:120],
        "impact": "Neutral — monitor for follow-through",
        "trade_idea": "No direct trade trigger; watch sector index for reaction",
        "confidence": "low",
        "source": article.get("source", ""),
        "url": article.get("url", ""),
        "_relevance_score": article.get("_relevance_score", 5),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4 — AI ENHANCEMENT
# Refines rule-based output; preserves structure strictly
# ═══════════════════════════════════════════════════════════════════════════════

_AI_SYSTEM = """You are a professional Indian equity trader writing a morning briefing.
For each news item, provide:
1. EVENT: one sentence — what happened (factual, no fluff)
2. IMPACT: one sentence — bullish/bearish/neutral and why (sector/stock specific)
3. TRADE IDEA: one actionable sentence — what to watch or do

Rules:
- Maximum 3 sentences total
- No guaranteed profits
- No vague phrases like "market may react"
- India-specific context always
- Keep the STOCK field unchanged
- Return ONLY the three labeled lines, nothing else"""

_AI_USER = """News headline: {title}
Description: {desc}
Current rule-based analysis:
  Stock: {stock}
  Impact: {impact}
  Trade Idea: {trade_idea}

Refine into sharper, more specific trader language. Keep same structure."""


def _enhance_with_openai(
    intelligence: dict[str, Any],
    article: dict[str, Any],
    settings: ContentEngineSettings,
) -> dict[str, Any]:
    """
    Send one intelligence item to OpenAI for refinement.
    Returns the original dict (possibly with improved event/impact/trade_idea).
    Falls back to rule-based on any failure.
    """
    try:
        import openai
    except ImportError:
        return intelligence

    prompt = _AI_USER.format(
        title=article.get("title", "")[:200],
        desc=article.get("description", "")[:200],
        stock=intelligence["stock"],
        impact=intelligence["impact"],
        trade_idea=intelligence["trade_idea"],
    )

    try:
        client = openai.OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _AI_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=180,
        )
        raw = resp.choices[0].message.content.strip()

        # Parse structured response — expect labeled lines
        refined = dict(intelligence)  # start from rule-based as base
        for line in raw.splitlines():
            line = line.strip()
            if line.lower().startswith("event:"):
                refined["event"] = line.split(":", 1)[1].strip()
            elif line.lower().startswith("impact:"):
                refined["impact"] = line.split(":", 1)[1].strip()
            elif line.lower().startswith("trade idea:"):
                refined["trade_idea"] = line.split(":", 1)[1].strip()

        log.debug("AI refined: %s", refined["stock"])
        return refined

    except Exception as exc:
        log.warning("OpenAI refinement failed for '%s': %s", intelligence["stock"], exc)
        return intelligence


# ═══════════════════════════════════════════════════════════════════════════════
# PART 5 — RANKING
# ═══════════════════════════════════════════════════════════════════════════════

_CONFIDENCE_WEIGHT = {"high": 3, "medium": 2, "low": 1}

_HIGH_IMPACT_STOCKS = {
    "EARNINGS BEAT", "EARNINGS MISS", "RBI RATE CUT", "RBI RATE HIKE",
    "M&A EVENT", "GOVT ORDER", "IT SECTOR",
}


def _rank_intelligence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score each intelligence item and return sorted by score descending."""
    for item in items:
        conf_score  = _CONFIDENCE_WEIGHT.get(item.get("confidence", "low"), 1)
        rel_score   = item.get("_relevance_score", 5) / 10  # normalise to ~0–1
        impact_bonus = 2 if item.get("stock") in _HIGH_IMPACT_STOCKS else 0
        item["_final_score"] = conf_score + rel_score + impact_bonus

    items.sort(key=lambda x: x["_final_score"], reverse=True)
    return items


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_top_market_news(
    settings: ContentEngineSettings | None = None,
    top_n: int = 6,
    use_ai: bool = True,
) -> list[dict[str, Any]]:
    """
    Full pipeline → returns list of intelligence dicts, ready for display or sending.

    Each dict:
        stock, event, impact, trade_idea, confidence, source, url, _final_score
    """
    # Settings-free mode: skip scraping, return demo data for testing
    if settings is None:
        return _demo_intelligence()

    # Step 1: Ingest
    raw_articles = fetch_all_news(settings)
    if not raw_articles:
        log.warning("No articles fetched — returning empty intelligence list")
        return []

    # Step 2: Dedup
    unique = _dedup(raw_articles)

    # Step 3: Filter
    relevant = filter_market_relevant_news(unique)
    if not relevant:
        log.warning("No market-relevant articles after filtering")
        return []

    # Step 4: Rule-based extraction
    intelligence_items: list[dict[str, Any]] = []
    for article in relevant[:15]:  # cap at 15 before AI to manage costs
        item = _extract_rule_based(article)
        intelligence_items.append((item, article))

    # Step 5: AI refinement (optional)
    refined_items: list[dict[str, Any]] = []
    for item, article in intelligence_items:
        if use_ai and settings.openai_api_key:
            item = _enhance_with_openai(item, article, settings)
            time.sleep(0.3)  # rate limit buffer
        refined_items.append(item)

    # Step 6: Rank and top-N
    ranked = _rank_intelligence(refined_items)
    return ranked[:top_n]


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

_CONFIDENCE_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
_IMPACT_EMOJI_MAP = [
    ("bullish", "📈"),
    ("bearish", "📉"),
    ("neutral", "↔️"),
]


def _format_intelligence_post(item: dict[str, Any], index: int) -> str:
    """Convert one intelligence dict into a Telegram Markdown post."""
    conf_emoji  = _CONFIDENCE_EMOJI.get(item.get("confidence", "low"), "⚪")
    impact_text = item.get("impact", "")
    impact_emoji = "📊"
    for keyword, emoji in _IMPACT_EMOJI_MAP:
        if keyword in impact_text.lower():
            impact_emoji = emoji
            break

    stock  = item.get("stock", "MARKET")
    event  = item.get("event", "")[:150]
    impact = impact_text[:200]
    trade  = item.get("trade_idea", "")[:150]
    conf   = item.get("confidence", "low").upper()
    source = item.get("source", "")

    lines = [
        f"{impact_emoji} *MARKET INTEL #{index}* — {stock}",
        "",
        f"📰 *Event:*",
        event,
        "",
        f"*Impact:*",
        impact,
        "",
        f"💡 *Trade Idea:*",
        trade,
        "",
        f"{conf_emoji} Confidence: {conf}" + (f"  |  _{source}_" if source else ""),
    ]
    return "\n".join(lines)


def _format_briefing_header(count: int, date: datetime) -> str:
    date_str = date.strftime("%d %b %Y, %A")
    return (
        f"🗞 *MORNING MARKET BRIEFING — {date_str}*\n\n"
        f"Top {count} tradeable news items for today.\n"
        f"Filter: earnings · policy · M&A · FII · global macro\n"
        f"{'─' * 30}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DEMO DATA — used when no API keys available (test mode)
# ═══════════════════════════════════════════════════════════════════════════════

def _demo_intelligence() -> list[dict[str, Any]]:
    """Return realistic demo items for offline testing and CI."""
    return [
        {
            "stock": "EARNINGS BEAT",
            "event": "HDFC Bank Q3 net profit surges 18% YoY, beats analyst estimates on NIM expansion",
            "impact": "Bullish — strong NIM and low NPAs signal continued institutional accumulation",
            "trade_idea": "Watch HDFCBANK above 1720 for breakout; BANKNIFTY likely positive bias",
            "confidence": "high",
            "source": "Economic Times",
            "url": "",
            "_final_score": 6.0,
        },
        {
            "stock": "RBI RATE CUT",
            "event": "RBI MPC cuts repo rate by 25bps to 6.25%, stance remains accommodative",
            "impact": "Bullish for banking, real estate, and auto sectors; INR may weaken slightly",
            "trade_idea": "Long BANKNIFTY above resistance; watch HDFC, ICICI, SBI for sector rotation",
            "confidence": "high",
            "source": "Bloomberg Quint",
            "url": "",
            "_final_score": 5.5,
        },
        {
            "stock": "OIL SECTOR",
            "event": "Brent crude surges to $91/bbl on OPEC supply cuts and Middle East tensions",
            "impact": "Bullish ONGC, Oil India; bearish IndiGo, Asian Paints — cost pressure builds",
            "trade_idea": "Long upstream oil stocks on dips; avoid aviation longs until crude stabilises",
            "confidence": "medium",
            "source": "Reuters",
            "url": "",
            "_final_score": 4.0,
        },
        {
            "stock": "FII FLOW",
            "event": "FIIs net sellers for 3rd consecutive session; sold Rs 4,200 Cr in cash equities",
            "impact": "Bearish short-term — sustained selling pressure keeps index capped at resistance",
            "trade_idea": "Reduce index longs; wait for DII buying confirmation before adding risk",
            "confidence": "medium",
            "source": "NSE Data",
            "url": "",
            "_final_score": 3.5,
        },
        {
            "stock": "IT SECTOR",
            "event": "Infosys raises FY26 revenue guidance to 5.5–6.0% citing large deal pipeline",
            "impact": "Bullish for IT sector; strong deal wins signal revenue visibility ahead",
            "trade_idea": "Long INFY above 1890; NIFTY IT index breakout above 40,000 is the trigger",
            "confidence": "high",
            "source": "Moneycontrol",
            "url": "",
            "_final_score": 5.8,
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# run() — main agent entry point called by scheduler / main.py
# ═══════════════════════════════════════════════════════════════════════════════

def _build_news_caption(item: dict[str, Any], index: int) -> str:
    """Short Markdown caption for the news photo (≤1024 chars)."""
    stock = item.get("stock", "MARKET")
    conf  = item.get("confidence", "low").upper()
    return f"*#{index} — {stock}*\nConfidence: {conf}"


def _impact_to_style(item: dict[str, Any]) -> str:
    """
    Choose the best image style for a news item based on its impact level.

    High confidence / strong keywords  → headline (big bold text, maximum attention)
    Medium / sector-wide               → news card (structured detail)
    Low / neutral                      → news card
    """
    event      = item.get("event", "").lower()
    impact     = item.get("impact", "").lower()
    confidence = item.get("confidence", "low").lower()
    score      = item.get("_final_score", 0.0)

    # Explicit high-impact signals → headline style
    high_impact_keywords = (
        "crash", "surge", "spike", "soar", "plunge", "collapse", "rally",
        "record", "historic", "cut rate", "hike rate", "ban", "seizure",
        "acquisition", "merger", "resign", "resign", "cbi", "sebi action",
        "ipo allot", "ipo", "delist",
    )
    is_high_impact = (
        confidence == "high"
        and score >= 5.0
        and any(kw in event or kw in impact for kw in high_impact_keywords)
    )

    if is_high_impact:
        return "headline"
    return "news_card"


def run(settings: ContentEngineSettings) -> dict[str, Any]:
    """
    Execute the full news intelligence pipeline.

    Flow:
        1. Fetch + filter + extract + rank (intelligence pipeline)
        2. Dedup against 7-day news history — skip already-posted stories
        3. Send briefing header as text
        4. For each item: choose style by impact level → render image → send
        5. Record sent events in NewsHistory

    Returns:
        result dict: {agent, status, posts_sent, images_generated, intel_count, errors}
    """
    result: dict[str, Any] = {
        "agent":            "news_agent",
        "status":           "ok",
        "posts_sent":       0,
        "images_generated": 0,
        "intel_count":      0,
        "errors":           [],
    }

    log.info("news_agent: starting market intelligence pipeline...")

    # ── Step 1: Intelligence pipeline ─────────────────────────────────────────
    try:
        intel_list = get_top_market_news(settings=settings, top_n=8, use_ai=True)
    except Exception as exc:
        log.error("Intelligence pipeline failed: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
        return result

    # ── Step 2: Deduplicate against news history ───────────────────────────────
    news_hist  = NewsHistory()
    fresh_list = news_hist.filter_unseen(intel_list, event_key="event")
    skipped    = len(intel_list) - len(fresh_list)
    if skipped:
        log.info("NewsHistory: skipped %d already-posted items", skipped)

    intel_list = fresh_list
    result["intel_count"] = len(intel_list)

    if not intel_list:
        log.warning("No fresh tradeable news found today — skipping Telegram send")
        result["status"] = "no_content"
        return result

    # ── Step 3: Briefing header ────────────────────────────────────────────────
    now    = datetime.now()
    header = _format_briefing_header(len(intel_list), now)
    try:
        send_message(settings, header, parse_mode="Markdown")
        time.sleep(1.5)
    except Exception as exc:
        log.warning("Failed to send briefing header: %s", exc)

    sent_events: list[str] = []

    # ── Step 4: Render and send each item ─────────────────────────────────────
    for i, item in enumerate(intel_list, start=1):
        # Choose style based on impact
        style = _impact_to_style(item)
        print(f"[DEBUG] Generating image for: {item['stock']} (style={style!r})")

        try:
            if style == "headline":
                image_path = generate_headline_post(item)
            else:
                image_path = generate_news_image(item)
            result["images_generated"] += 1
            print(f"[DEBUG] Image generated at: {image_path}")
            log.info("News image #%d rendered [%s]: %s", i, style, image_path)
        except Exception as exc:
            log.error("Image generation failed for #%d '%s': %s", i, item["stock"], exc)
            result["errors"].append(f"img:#{i} {item['stock']}: {exc}")
            try:
                fallback_text = _format_intelligence_post(item, i)
                send_message(settings, fallback_text, parse_mode="Markdown")
                result["posts_sent"] += 1
                sent_events.append(item.get("event", ""))
            except Exception as send_exc:
                log.error("Text fallback also failed: %s", send_exc)
                result["errors"].append(f"txt:#{i}: {send_exc}")
            continue

        print(f"[DEBUG] Sending image: {image_path}")
        try:
            caption = _build_news_caption(item, i)
            send_image(settings, image_path, caption=caption)
            result["posts_sent"] += 1
            sent_events.append(item.get("event", ""))
            log.info(
                "Sent #%d [%s]: %s (conf=%s, score=%.1f)",
                i, style, item["stock"],
                item.get("confidence", "?"),
                item.get("_final_score", 0),
            )
        except Exception as exc:
            log.error("sendPhoto failed for #%d '%s': %s", i, item["stock"], exc)
            result["errors"].append(f"send:#{i} {item['stock']}: {exc}")
            try:
                send_plain(settings, format_error_alert("news_agent", exc))
            except Exception:
                pass

        if i < len(intel_list):
            time.sleep(3)

    # ── Step 5: Record successfully sent events ────────────────────────────────
    for event in sent_events:
        if event:
            news_hist.record(event)
    if sent_events:
        log.info("NewsHistory: recorded %d events", len(sent_events))

    if result["errors"]:
        result["status"] = "partial" if result["posts_sent"] > 0 else "error"

    log.info(
        "news_agent done — images=%d, sent=%d/%d, errors=%d",
        result["images_generated"], result["posts_sent"], len(intel_list), len(result["errors"]),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PART 7 — TEST RUNNER
# python -m content_engine.agents.news_agent
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # No settings = demo mode (no API keys needed)
    news = get_top_market_news(settings=None)

    print(f"\n{'='*65}")
    print(f"  MARKET INTELLIGENCE ENGINE — {datetime.now().strftime('%d %b %Y')}")
    print(f"  {len(news)} Tradeable News Items")
    print(f"{'='*65}\n")

    for i, n in enumerate(news, start=1):
        score = n.get("_final_score", 0)
        conf  = n.get("confidence", "?").upper()
        print(f"[#{i}] {n['stock']}  |  Confidence: {conf}  |  Score: {score:.1f}")
        print(f"  EVENT      : {n['event']}")
        print(f"  IMPACT     : {n['impact']}")
        print(f"  TRADE IDEA : {n['trade_idea']}")
        print(f"  SOURCE     : {n.get('source', 'N/A')}")
        print("-" * 65)
        print()
