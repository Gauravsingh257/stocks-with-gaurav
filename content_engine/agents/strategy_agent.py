"""
content_engine/agents/strategy_agent.py

Strategy Content Engine — generates 5 high-quality, non-repetitive trading strategy posts daily.

Pipeline:
  1. REGIME DETECT      — detect market regime (trending/sideways/volatile) from NIFTY data
  2. HISTORY FILTER     — exclude strategies posted in the last 7 days
  3. REGIME SELECTION   — prioritise strategies that fit today's market condition
  4. CONTENT BUILD      — convert each to structured Telegram post
  5. AI ENHANCEMENT     — polish via OpenAI (optional, graceful fallback)
  6. VARIATION ENGINE   — synonym/phrasing shuffles for freshness
  7. RANKING            — score and return TOP 5
  8. SEND               — render image in a smart style, fire to content channel
  9. HISTORY RECORD     — save today's posted strategies to dedup store

run(settings) → sends posts, returns result dict
generate_strategy_posts() → returns list of post dicts (usable standalone)
"""

from __future__ import annotations

import logging
import random
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from content_engine.config.settings import ContentEngineSettings
from content_engine.services.formatter import escape_md, format_error_alert
from content_engine.services.telegram_service import send_message, send_plain, send_image
from content_engine.services.image_generator import (
    generate_post_for_strategy,
    create_visual_strategy_diagram,
    generate_cheatsheet_post,
)
from content_engine.state.post_history import PostHistory
from content_engine.state.market_regime import MarketRegime, get_regime

log = logging.getLogger("content_engine.strategy_agent")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1 — STRATEGY DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGY_DB: list[dict[str, Any]] = [
    {
        "id": "order_block",
        "name": "Order Block (OB)",
        "emoji": "🟦",
        "concept": "Institutions place large orders leaving imbalanced candles; price returns to these zones before continuing the trend.",
        "entry_rules": [
            "Identify the last bearish candle before a strong bullish impulse (bullish OB) or vice versa",
            "Wait for price to return to the OB zone (50–100% retracement)",
            "Look for a strong rejection candle (engulfing, pin bar, hammer) at the zone",
            "Optional: confluence with FVG or HTF structure",
            "Enter on the close of the rejection candle or on the next candle open",
        ],
        "stop_loss": "Below the OB low (bullish OB) or above OB high (bearish OB) — typically 5–10 points on NIFTY",
        "target": "2R minimum; next liquidity pool or previous swing high/low",
        "best_market": "Trending market with clear BOS/CHoCH on 15-min or 1-hr chart",
        "risk_level": "medium",
        "smc_score": 10,
        "tags": ["SMC", "Intraday", "High Probability"],
    },
    {
        "id": "fvg",
        "name": "Fair Value Gap (FVG)",
        "emoji": "🕳️",
        "concept": "A 3-candle imbalance where middle candle's body is not overlapped by wicks; price magnetically fills the gap before continuation.",
        "entry_rules": [
            "Identify a 3-candle pattern where candle 1 wick and candle 3 wick do NOT overlap",
            "The gap between candle 1 high and candle 3 low (bullish FVG) is the zone",
            "Wait for price to pull back INTO the FVG — 50% of the gap is ideal entry",
            "Enter only if higher timeframe trend aligns (15-min trend = use 5-min FVG)",
            "Volume spike on the impulse candle validates the FVG strength",
        ],
        "stop_loss": "Below the full FVG (bullish) or above the full FVG (bearish) — invalidated if price fully closes through",
        "target": "Previous swing high/low; 1.5R to 3R depending on structure",
        "best_market": "Strongly trending or high-momentum intraday sessions",
        "risk_level": "medium",
        "smc_score": 9,
        "tags": ["SMC", "Intraday", "Gap Fill"],
    },
    {
        "id": "liquidity_grab",
        "name": "Liquidity Grab (Stop Hunt)",
        "emoji": "🎣",
        "concept": "Smart money sweeps retail stop-losses clustered beyond swing highs/lows before reversing sharply in the opposite direction.",
        "entry_rules": [
            "Locate equal highs or equal lows — these are retail stop clusters",
            "Wait for a sharp wick that pierces beyond these levels (stop hunt candle)",
            "The candle must CLOSE back inside the range — confirming the fake-out",
            "Enter on the close of the reversal candle or the next candle open",
            "Best when combined with OB or FVG in the reversal zone",
        ],
        "stop_loss": "Beyond the wick extreme — if price pushes further, the thesis is wrong",
        "target": "Opposite liquidity pool; minimum 2R",
        "best_market": "Ranging or consolidating market near key swing levels",
        "risk_level": "high",
        "smc_score": 9,
        "tags": ["SMC", "Reversal", "Stop Hunt"],
    },
    {
        "id": "breaker_block",
        "name": "Breaker Block",
        "emoji": "💥",
        "concept": "A failed Order Block that gets violated by a BOS; it flips polarity and becomes a strong supply/demand zone from the opposite direction.",
        "entry_rules": [
            "Identify an OB that price breaks through with momentum (BOS confirmed)",
            "The broken OB is now a Breaker Block — mark the zone",
            "Wait for price to return to retest the Breaker Block",
            "Enter on rejection candle confirming the flip (old support → resistance, or vice versa)",
            "Higher conviction if a CHoCH preceded the BOS",
        ],
        "stop_loss": "Beyond the Breaker Block zone (full candle range of the original OB)",
        "target": "3R or the next key liquidity / structure target",
        "best_market": "Post-reversal trending markets; after clear CHoCH on 15-min",
        "risk_level": "medium",
        "smc_score": 8,
        "tags": ["SMC", "Advanced", "Structure"],
    },
    {
        "id": "trend_pullback",
        "name": "Trend Pullback (EMA Bounce)",
        "emoji": "📉➡️📈",
        "concept": "In a strong trend, price pulls back to a dynamic EMA level before resuming — ride the continuation with the institutional flow.",
        "entry_rules": [
            "Confirm trend: price is trading above 20 EMA and 50 EMA (bullish) or below both (bearish)",
            "Wait for a 3–7 candle pullback to the 20 EMA on a 15-min chart",
            "Look for a rejection candle at the EMA — engulfing or doji with follow-through",
            "RSI should be between 40–60 during the pullback (not oversold extreme)",
            "Enter on the break of the rejection candle's high (bullish) or low (bearish)",
        ],
        "stop_loss": "Below 50 EMA for bullish trades; 5-min close below 20 EMA is early exit signal",
        "target": "Previous swing high; 2R minimum; trail stop after 1R secured",
        "best_market": "Clear trending market; avoid during news events or choppy sessions",
        "risk_level": "low",
        "smc_score": 7,
        "tags": ["Trend Following", "EMA", "Intraday"],
    },
    {
        "id": "range_breakout",
        "name": "Range Breakout",
        "emoji": "📦💨",
        "concept": "Price consolidates in a defined range building energy; a breakout with volume signals institutional participation and a directional move.",
        "entry_rules": [
            "Identify a clear consolidation box: at least 3 touches of both support and resistance",
            "Range must be at least 30–40 points wide on NIFTY to be worth trading",
            "Enter on a 15-min candle CLOSE above resistance (bullish) or below support (bearish)",
            "Volume on the breakout candle should be above the 20-period average",
            "Avoid fakeouts: wait for the close, not the wick",
        ],
        "stop_loss": "Back inside the range — 10–15 points below the breakout level",
        "target": "Range height projected from the breakout point (measured move)",
        "best_market": "Post-news or early-session breakouts; avoid if range is less than 1 hour old",
        "risk_level": "medium",
        "smc_score": 7,
        "tags": ["Breakout", "Intraday", "Volume"],
    },
    {
        "id": "vwap_strategy",
        "name": "VWAP Reclaim / Rejection",
        "emoji": "〰️",
        "concept": "VWAP is institutional fair value for the day; reclaiming it signals bullish intent, failing it signals bearish pressure.",
        "entry_rules": [
            "Bullish: price dips below VWAP, then reclaims with a strong green candle close above",
            "Bearish: price pushes above VWAP, then rejects with a strong red candle close below",
            "Best used in the first 90 minutes of the session (09:15–10:45 IST)",
            "Confluence: watch if VWAP aligns with an OB or yesterday's POC",
            "Enter on the candle that closes back through VWAP with momentum",
        ],
        "stop_loss": "On a re-cross of VWAP in the opposite direction (5-min close)",
        "target": "Previous high/low of the day; or 1.5x the initial move off VWAP",
        "best_market": "High-volume intraday sessions; works best in index futures (NIFTY/BN)",
        "risk_level": "low",
        "smc_score": 7,
        "tags": ["VWAP", "Intraday", "Institutional"],
    },
    {
        "id": "orb",
        "name": "Opening Range Breakout (ORB)",
        "emoji": "🔔",
        "concept": "The first 15-minute candle sets the session's battleground; a breakout above or below defines the day's directional bias.",
        "entry_rules": [
            "Mark the high and low of the first 15-minute candle (09:15–09:30 IST)",
            "Bullish: enter when a subsequent 15-min candle CLOSES above the ORB high",
            "Bearish: enter when a candle CLOSES below the ORB low",
            "Avoid entries if the ORB range is too narrow (< 40 pts on NIFTY — choppy)",
            "Best when pre-market gap direction aligns with the breakout direction",
        ],
        "stop_loss": "Mid-point of the ORB range; or a close back inside the ORB",
        "target": "1× to 2× ORB range projected from the breakout point",
        "best_market": "High-gap open days, post-major event sessions, strong trending days",
        "risk_level": "medium",
        "smc_score": 8,
        "tags": ["ORB", "Intraday", "Session Open"],
    },
    {
        "id": "sr_flip",
        "name": "Support → Resistance Flip",
        "emoji": "🔄",
        "concept": "A broken support level becomes the new resistance (and vice versa); price returns to the flipped zone and rejects — a high-probability entry.",
        "entry_rules": [
            "Identify a clear support level that has been tested at least twice",
            "Wait for a decisive break below with momentum (not just a wick)",
            "After the break, wait for a pullback to the former support zone",
            "Enter short on the first rejection candle at the flipped resistance",
            "Stronger signal if zone aligns with OB or FVG from the breakdown",
        ],
        "stop_loss": "Above the flipped zone (zone high + small buffer)",
        "target": "Next support level below; measured move from the breakdown",
        "best_market": "Trending markets; works on all timeframes from 5-min to daily",
        "risk_level": "low",
        "smc_score": 8,
        "tags": ["S/R Flip", "Structure", "High Probability"],
    },
    {
        "id": "trendline_break",
        "name": "Trendline Break & Retest",
        "emoji": "📐",
        "concept": "A valid trendline connects at least 3 swing points; a break with momentum signals trend exhaustion; the retest offers a lower-risk entry.",
        "entry_rules": [
            "Draw trendline connecting at least 3 swing lows (uptrend) or highs (downtrend)",
            "Wait for a clear break — candle BODY closes beyond the trendline",
            "Let price pull back to retest the broken trendline from the other side",
            "Enter on rejection candle at the trendline retest",
            "Higher timeframe trendline breaks are more significant",
        ],
        "stop_loss": "Back inside the trendline by more than 10 points (failed retest)",
        "target": "2R–3R; or the opposite swing extreme",
        "best_market": "Mature trends showing first signs of exhaustion; 15-min or 1-hr charts",
        "risk_level": "medium",
        "smc_score": 7,
        "tags": ["Trendline", "Reversal", "Retest"],
    },
    {
        "id": "consolidation_breakout",
        "name": "Consolidation Breakout (Flag/Pennant)",
        "emoji": "🚩",
        "concept": "After a strong impulse move, price consolidates in a tight pattern (flag or pennant); the breakout resumes the original impulse direction.",
        "entry_rules": [
            "Identify a sharp impulse move followed by a low-volatility, sideways or slightly counter-trend consolidation",
            "Consolidation should last 5–15 candles on the trading timeframe",
            "Enter on the breakout candle close in the direction of the original impulse",
            "Volume should contract during consolidation and expand on breakout",
            "Do not chase — if entry candle is more than 15 points wide on NIFTY, skip",
        ],
        "stop_loss": "Below the consolidation low (bullish) or above the consolidation high (bearish)",
        "target": "Impulse length projected from the consolidation breakout point",
        "best_market": "Strong trending intraday sessions; morning momentum plays",
        "risk_level": "low",
        "smc_score": 7,
        "tags": ["Momentum", "Continuation", "Intraday"],
    },
    {
        "id": "momentum_scalp",
        "name": "Momentum Scalping",
        "emoji": "⚡",
        "concept": "Capture 20–40 point moves on NIFTY during high-momentum bursts using tight stops and fast execution — quality over quantity.",
        "entry_rules": [
            "Momentum burst: 2–3 consecutive strong candles in one direction on 3-min chart",
            "Enter only with the burst — never counter-trend on momentum",
            "Price must be above VWAP for longs, below VWAP for shorts",
            "Use 1-min chart for precise entry — enter on the first small pullback (1–2 candles)",
            "Max 2 scalp trades per session to avoid overtrading",
        ],
        "stop_loss": "Tight: 10–15 points on NIFTY; 25–35 points on BANKNIFTY. Move to breakeven quickly.",
        "target": "15–30 points NIFTY / 40–60 points BANKNIFTY. Exit at first sign of stall.",
        "best_market": "First 45 minutes (09:15–10:00) or post-news momentum bursts",
        "risk_level": "high",
        "smc_score": 6,
        "tags": ["Scalping", "Momentum", "Fast Execution"],
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4 — VARIATION ENGINE
# Phrasing pools for synonyms — keeps posts fresh across days
# ═══════════════════════════════════════════════════════════════════════════════

_ENTRY_HEADERS = ["📍 Entry Rules", "📍 Setup Entry", "📍 How to Enter", "📍 Entry Conditions"]
_SL_HEADERS    = ["🛑 Stop Loss", "🛑 SL Placement", "🛑 Stop Placement"]
_TGT_HEADERS   = ["🎯 Target", "🎯 Profit Target", "🎯 Exit Zone"]
_MKT_HEADERS   = ["📈 Best Market", "📈 Ideal Conditions", "📈 When to Use"]
_RISK_LABELS   = {
    "low": ["🟢 Low Risk", "🟢 Conservative"],
    "medium": ["🟡 Medium Risk", "🟡 Moderate"],
    "high": ["🔴 High Risk", "🔴 Aggressive"],
}
_CLOSERS = [
    "📌 Always confirm on HTF before entry.",
    "📌 No confirmation = no trade.",
    "📌 Manage risk first. Profits follow.",
    "📌 Wait for the setup — don't force entries.",
    "📌 Risk 1% per trade. Consistency beats greed.",
]


def _pick(pool: list[str]) -> str:
    return random.choice(pool)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2 — CONTENT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def _build_raw_post(strategy: dict[str, Any]) -> str:
    """Convert a strategy dict into a structured Telegram-ready string."""
    # Pick variation headers
    entry_hdr = _pick(_ENTRY_HEADERS)
    sl_hdr    = _pick(_SL_HEADERS)
    tgt_hdr   = _pick(_TGT_HEADERS)
    mkt_hdr   = _pick(_MKT_HEADERS)
    risk_lbl  = _pick(_RISK_LABELS[strategy["risk_level"]])
    closer    = _pick(_CLOSERS)

    # Shuffle entry rules slightly — show 3–4 of them (avoids wall of text)
    rules = deepcopy(strategy["entry_rules"])
    random.shuffle(rules)
    displayed_rules = rules[:4]

    lines = [
        f"{strategy['emoji']} *STRATEGY: {strategy['name'].upper()}*",
        "",
        f"🧠 *Concept:*",
        f"{strategy['concept']}",
        "",
        f"{entry_hdr}:",
        *[f"• {rule}" for rule in displayed_rules],
        "",
        f"{sl_hdr}:",
        f"{strategy['stop_loss']}",
        "",
        f"{tgt_hdr}:",
        f"{strategy['target']}",
        "",
        f"{mkt_hdr}:",
        f"{strategy['best_market']}",
        "",
        f"⚖️ Risk Level: {risk_lbl}",
        "",
        closer,
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3 — AI ENHANCEMENT
# ═══════════════════════════════════════════════════════════════════════════════

_AI_SYSTEM_PROMPT = """You are an expert Indian stock market educator who writes crisp, 
high-value trading strategy content for a Telegram channel focused on SMC and price action.

Rules you MUST follow:
- Keep ALL existing section headers (emoji headers like 🧠 Concept, 📍 Entry, etc.)
- Keep bullet points (•) — do NOT convert to paragraphs
- Improve clarity, wording, and engagement — not structure
- Maximum 8 lines of content (excluding headers)
- No "guaranteed profit" or unrealistic claims
- No hallucinated strategies or indicators
- Use plain Telegram-compatible text — no HTML or markdown code blocks"""

_AI_USER_PROMPT = """Improve the wording and clarity of this trading strategy post.
Keep ALL section headers and bullet structure EXACTLY as-is.
Only improve the text inside each section.

POST:
{raw_post}

Return ONLY the improved post. No explanations."""


def _enhance_with_openai(raw_post: str, settings: ContentEngineSettings) -> str:
    """
    Send the raw post to OpenAI for phrasing improvements.
    Returns the raw post unchanged if API call fails.
    """
    try:
        import openai
    except ImportError:
        log.warning("openai package not installed — skipping AI enhancement")
        return raw_post

    try:
        client = openai.OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _AI_SYSTEM_PROMPT},
                {"role": "user", "content": _AI_USER_PROMPT.format(raw_post=raw_post)},
            ],
            temperature=0.6,
            max_tokens=500,
        )
        enhanced = response.choices[0].message.content.strip()
        if len(enhanced) < 100:
            # Suspiciously short — something went wrong
            log.warning("OpenAI returned suspiciously short content — using raw")
            return raw_post
        log.debug("AI enhanced post (%d → %d chars)", len(raw_post), len(enhanced))
        return enhanced
    except Exception as exc:
        log.warning("OpenAI enhancement failed: %s — using raw content", exc)
        return raw_post


# ═══════════════════════════════════════════════════════════════════════════════
# PART 5 — REGIME-AWARE RANKING
# ═══════════════════════════════════════════════════════════════════════════════

def _score_strategy(strategy: dict[str, Any], regime: MarketRegime | None = None) -> float:
    """
    Score each strategy for daily ranking.
    Incorporates market regime preference and base quality score.
    """
    smc_score = strategy.get("smc_score", 5)

    # Clarity bonus
    clarity_bonus = 1.0 if len(strategy["concept"]) > 60 else 0.0

    # Intraday bonus — index traders are the primary audience
    intraday_tags = {"Intraday", "ORB", "VWAP", "Scalping", "Momentum"}
    intraday_bonus = 1.5 if any(t in strategy["tags"] for t in intraday_tags) else 0.0

    # SMC bonus
    smc_bonus = 2.0 if "SMC" in strategy["tags"] else 0.0

    # Regime fit bonus — strategies that match today's market get a significant boost
    regime_bonus = 0.0
    if regime and strategy["id"] in regime.preferred_strategies:
        idx = regime.preferred_strategies.index(strategy["id"])
        # Top-ranked preferred strategy gets +3, last gets +0.5
        regime_bonus = max(0.5, 3.0 - idx * 0.4)

    # Random jitter so rankings rotate daily
    jitter = random.uniform(0, 0.8)

    return smc_score + clarity_bonus + intraday_bonus + smc_bonus + regime_bonus + jitter


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def generate_strategy_posts(
    settings: ContentEngineSettings | None = None,
    count: int = 5,
    use_ai: bool = True,
    regime: MarketRegime | None = None,
    history: PostHistory | None = None,
) -> list[dict[str, Any]]:
    """
    Full pipeline: regime-filter → history-dedup → select → build → enhance → rank → top N.

    Args:
        settings:  ContentEngineSettings (optional — AI skipped if None)
        count:     number of posts to return (default 5)
        use_ai:    whether to attempt OpenAI enhancement
        regime:    pre-fetched MarketRegime (fetched internally if None)
        history:   PostHistory instance (created internally if None)

    Returns:
        List of dicts: {title, content, tags, score, strategy_id, regime_label}
    """
    # Step 1: Detect regime
    if regime is None:
        try:
            regime = get_regime()
        except Exception as exc:
            log.warning("Regime detection failed: %s — using UNKNOWN", exc)
            regime = MarketRegime(label="UNKNOWN")

    log.info("Regime: %s | bias: %s | score: %.2f", regime.label, regime.bias, regime.score)

    # Step 2: Load history and filter out recently-posted strategies
    if history is None:
        history = PostHistory()

    pool = history.filter_unseen(STRATEGY_DB)
    log.info(
        "Strategy pool: %d available (excluded %d recently posted)",
        len(pool), len(STRATEGY_DB) - len(pool),
    )

    # Step 3: Score with regime awareness
    scored = [(s, _score_strategy(s, regime)) for s in pool]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Step 4: Weighted sample — biased toward top scorers but still varied
    weights     = [sc for _, sc in scored]
    strats      = [s  for s, _ in scored]
    sample_size = min(len(strats), random.randint(count + 2, count + 4))
    selected    = random.choices(strats, weights=weights, k=sample_size)

    # Deduplicate preserving order
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for s in selected:
        if s["id"] not in seen_ids:
            seen_ids.add(s["id"])
            unique.append(s)

    # Step 5: Build posts
    posts: list[dict[str, Any]] = []
    for strategy in unique:
        raw = _build_raw_post(strategy)

        if use_ai and settings and settings.openai_api_key:
            content = _enhance_with_openai(raw, settings)
            time.sleep(0.5)
        else:
            content = raw

        posts.append({
            "strategy_id":  strategy["id"],
            "title":        f"{strategy['emoji']} {strategy['name'].upper()}",
            "content":      content,
            "tags":         strategy["tags"],
            "score":        _score_strategy(strategy, regime),
            "regime_label": regime.label,
            "regime_bias":  regime.bias,
        })

    # Step 6: Re-rank and return top count
    posts.sort(key=lambda p: p["score"], reverse=True)
    return posts[:count]


# ── Regime-aware image style selector ────────────────────────────────────────

def _choose_image_style(post: dict[str, Any], regime: MarketRegime | None) -> str:
    """
    Pick the best image style for a strategy post given the current regime.

    Trending regime → visual diagram (shows the move clearly)
    Sideways/Mixed  → cheatsheet (reference card, rules matter more)
    Volatile        → diagram or text
    else            → use random dispatcher
    """
    if regime is None:
        return "random"
    label = regime.label
    if label == "TRENDING":
        # Alternate diagram and cheatsheet for visual variety
        return "diagram" if random.random() < 0.6 else "cheatsheet"
    if label in ("SIDEWAYS", "MIXED"):
        return "cheatsheet" if random.random() < 0.55 else "diagram"
    if label == "VOLATILE":
        return "diagram" if random.random() < 0.7 else "random"
    return "random"


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM SEND LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def _build_caption(post: dict[str, Any]) -> str:
    """Short Markdown caption attached to the image (max ~1024 chars for photos)."""
    tags = "  ".join(f"#{t.replace(' ', '_')}" for t in post.get("tags", []))
    title = post.get("title", "Strategy")
    # Strip leading emoji from title for clean caption
    title_clean = title.lstrip("🟦🕳️🎣💥📉➡️📈📦💨〰️🔔🔄📐🚩⚡ ")
    return f"*{title_clean}*\n\n{tags}" if tags else f"*{title_clean}*"


def run(settings: ContentEngineSettings) -> dict[str, Any]:
    """
    Execute the full strategy content pipeline.

    Flow:
        1. Detect market regime (NIFTY via Yahoo Finance)
        2. Load 7-day post history, filter out recently-posted strategies
        3. Generate 5 regime-aware posts
        4. Render image in a style that fits the regime
        5. Send image with caption to Telegram
        6. Record posted strategy IDs in history

    Returns:
        result dict: {agent, status, posts_sent, images_generated, regime, errors}
    """
    result: dict[str, Any] = {
        "agent":            "strategy_agent",
        "status":           "ok",
        "posts_sent":       0,
        "images_generated": 0,
        "regime":           "UNKNOWN",
        "errors":           [],
    }

    # ── Step 1: Regime + history ───────────────────────────────────────────────
    try:
        regime = get_regime()
        result["regime"] = regime.label
        log.info("Regime: %s (%s) | NIFTY %.0f (%+.1f%%)",
                 regime.label, regime.bias, regime.nifty_price, regime.nifty_change_pct)
    except Exception as exc:
        log.warning("Regime detection failed: %s", exc)
        regime = MarketRegime(label="UNKNOWN")

    history = PostHistory()

    # ── Step 2: Generate posts ────────────────────────────────────────────────
    log.info("strategy_agent: generating regime-aware posts...")
    try:
        posts = generate_strategy_posts(
            settings=settings,
            count=5,
            use_ai=True,
            regime=regime,
            history=history,
        )
    except Exception as exc:
        log.error("Post generation failed: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
        return result

    log.info("Generated %d posts — regime=%s — rendering and sending", len(posts), regime.label)

    posted_ids: list[str] = []

    for i, post in enumerate(posts, start=1):
        # ── Step 3: Choose image style based on regime ─────────────────────
        style = _choose_image_style(post, regime)

        # ── Step 4: Render image ────────────────────────────────────────────
        print(f"[DEBUG] Generating image for: {post['title']} (style={style!r})")
        try:
            image_path = generate_post_for_strategy(post, force_type=style if style != "random" else None)
            result["images_generated"] += 1
            print(f"[DEBUG] Image generated at: {image_path}")
            log.info("Image rendered %d/%d [%s]: %s", i, len(posts), style, image_path)
        except Exception as exc:
            log.error("Image generation failed for '%s': %s", post["title"], exc)
            result["errors"].append(f"img:{post['title']}: {exc}")
            try:
                send_message(settings, post["content"], parse_mode="Markdown")
                result["posts_sent"] += 1
                posted_ids.append(post["strategy_id"])
            except Exception as send_exc:
                log.error("Text fallback also failed: %s", send_exc)
                result["errors"].append(f"txt:{post['title']}: {send_exc}")
            continue

        # ── Step 5: Send image ──────────────────────────────────────────────
        print(f"[DEBUG] Sending image: {image_path}")
        try:
            caption = _build_caption(post)
            send_image(settings, image_path, caption=caption)
            result["posts_sent"] += 1
            posted_ids.append(post["strategy_id"])
            log.info("Sent %d/%d: %s", i, len(posts), post["title"])
        except Exception as exc:
            log.error("sendPhoto failed for '%s': %s", post["title"], exc)
            result["errors"].append(f"send:{post['title']}: {exc}")
            try:
                send_plain(settings, format_error_alert("strategy_agent", exc))
            except Exception:
                pass

        if i < len(posts):
            time.sleep(3)

    # ── Step 6: Record what was successfully posted ────────────────────────
    if posted_ids:
        history.record_batch(posted_ids)
        log.info("Recorded %d strategy IDs in post history", len(posted_ids))

    if result["errors"]:
        result["status"] = "partial" if result["posts_sent"] > 0 else "error"

    log.info(
        "strategy_agent done — regime=%s, images=%d, sent=%d/%d, errors=%d",
        regime.label, result["images_generated"],
        result["posts_sent"], len(posts), len(result["errors"]),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PART 7 — TEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import io
    import sys
    from dotenv import load_dotenv

    load_dotenv()

    # Force UTF-8 output so emoji render correctly on all platforms
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # Run without settings → no AI, no Telegram — pure content preview
    posts = generate_strategy_posts(settings=None, count=5, use_ai=False)

    print(f"\n{'='*60}")
    print(f"  STRATEGY CONTENT ENGINE — {datetime.now().strftime('%d %b %Y')}")
    print(f"  Generated {len(posts)} posts")
    print(f"{'='*60}\n")

    for i, p in enumerate(posts, start=1):
        print(f"POST {i} | Score: {p['score']:.1f} | Tags: {', '.join(p['tags'])}")
        print("-" * 60)
        print(p["content"])
        print("-" * 60)
        print()
