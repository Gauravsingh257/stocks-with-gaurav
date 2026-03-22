"""
Local image preview — generates PNGs into content_engine/generated_posts/
without Telegram, scheduler, or API keys (except yfinance for real charts).

Each mode **randomizes content every run** (different stories, tip themes, diagram
subset, strategy mix, chart symbol/history — not the same fixed samples).

Run from project root:
    python -m content_engine.preview_local strategy
    python -m content_engine.preview_local news
    python -m content_engine.preview_local tips
    python -m content_engine.preview_local diagrams
    python -m content_engine.preview_local all
"""

from __future__ import annotations

import argparse
import io
import random
import sys
from pathlib import Path

# Windows console: avoid UnicodeEncodeError on emoji in prints
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Varied news headlines for preview (rotates each run) ─────────────────────
_NEWS_PREVIEW_POOL: list[dict[str, str]] = [
    {
        "stock": "HDFC BANK",
        "event": "Q3 profit beats estimates; NIM expands QoQ",
        "impact": "Bullish — earnings quality ahead of street; watch continuation",
        "trade_idea": "Add on dips above 20-DMA with tight trail",
        "confidence": "high",
        "source": "Preview",
    },
    {
        "stock": "RELIANCE",
        "event": "Jio ARPU up; retail & digital EBITDA surprise",
        "impact": "Bullish — conglomerate cash flows supporting rerating",
        "trade_idea": "Look for flag breakout with volume",
        "confidence": "high",
        "source": "Preview",
    },
    {
        "stock": "TCS",
        "event": "Large deal wins in North America; margin resilient",
        "impact": "Bullish — IT demand commentary improving",
        "trade_idea": "Buy dips to VWAP on 15m with SL below swing",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "INFY",
        "event": "FY26 margin band raised; automation mix higher",
        "impact": "Bullish — guidance surprise vs prior quarter",
        "trade_idea": "Scalp continuation above prior day high",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "SBIN",
        "event": "NPA ratio falls; credit growth in double digits",
        "impact": "Bullish — PSU bank cycle visibility improving",
        "trade_idea": "ORB continuation on strong ADR",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "BHARTI AIRTEL",
        "event": "ARPU hike roadmap; 5G capex payback faster",
        "impact": "Bullish — tariff tailwind + FCF inflection",
        "trade_idea": "Range breakout retest entry",
        "confidence": "high",
        "source": "Preview",
    },
    {
        "stock": "RBI POLICY",
        "event": "Hawkish tone; dot plot shifts higher for FY26",
        "impact": "Bearish near-term for rate-sensitive & NBFC pack",
        "trade_idea": "Reduce leverage; hedge index longs into event",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "CRUDE / INR",
        "event": "Oil spikes on supply shock; rupee weakens past key level",
        "impact": "Bearish for importers; energy & aviation under pressure",
        "trade_idea": "Short weak names on rally; avoid catching falling knives",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "PHARMA INDEX",
        "event": "USFDA clearances cluster; ANDA pipeline accelerates",
        "impact": "Bullish — regulatory overhang clearing for mid-caps",
        "trade_idea": "Stock-specific longs on chart confirmation",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "AUTO OEMs",
        "event": "Festive wholesale beats estimates; inventory lean",
        "impact": "Bullish — demand visibility into next quarter",
        "trade_idea": "Pullback to demand zone on leaders",
        "confidence": "high",
        "source": "Preview",
    },
    {
        "stock": "NIFTY IT",
        "event": "Profit booking after strong results; US yields rise",
        "impact": "Neutral-to-bearish short-term; stock-picking over index",
        "trade_idea": "Wait for liquidity sweep + reclaim for longs",
        "confidence": "low",
        "source": "Preview",
    },
    {
        "stock": "INFLATION PRINT",
        "event": "CPI within RBI comfort band; core cooling",
        "impact": "Bullish for duration & growth names — policy path eases",
        "trade_idea": "Add quality mid-caps on dips",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "METALS PACK",
        "event": "China stimulus chatter lifts base metals",
        "impact": "Bullish cyclical trade — iron/steel names in focus",
        "trade_idea": "Breakout + retest on high-beta names only",
        "confidence": "medium",
        "source": "Preview",
    },
    {
        "stock": "FII FLOWS",
        "event": "Net selling in cash for 5 sessions; DII absorbs",
        "impact": "Neutral — index range-bound; stock-specific moves",
        "trade_idea": "Fade extremes; trade mean reversion on index",
        "confidence": "low",
        "source": "Preview",
    },
]


def _pick_random_theme() -> str | None:
    return random.choice(("light", "dark", None))


def _sample_strategy_post(strategy_id: str, name: str, concept: str) -> dict:
    return {
        "strategy_id": strategy_id,
        "title": name,
        "content": (
            f"{name}\n\n"
            f"Concept:\n{concept}\n\n"
            "📍 Entry Rules:\n"
            "• Wait for price at the zone\n"
            "• Confirm with rejection candle\n\n"
            "🛑 Stop Loss:\nBelow invalidation\n\n"
            "🎯 Target:\n2R–3R or next liquidity\n\n"
            "📈 Best Market:\nTrending with HTF alignment\n\n"
            "⚖️ Risk Level: Medium\n\n"
            "📌 Confirm on HTF before entry."
        ),
        "tags": ["SMC", "Intraday"],
    }


def run_strategy() -> list[str]:
    """
    Generate strategy-style images (real chart, diagram, cheatsheet, text).

    Each run **randomizes** which SMC setups appear and (for real charts) picks a
    **random liquid symbol** + **random historical 5m window** from Yahoo so you
    do not get the same NIFTY screenshot every time.

    Lock symbol (optional): ``set CONTENT_CHART_SYMBOL=^NSEI`` before running.
    """
    from content_engine.services.image_generator import (
        generate_post_for_strategy,
        generate_strategy_image,
    )

    out: list[str] = []
    # All supported strategy ids — different mix every run
    pool: list[tuple[str, str, str]] = [
        ("order_block", "ORDER BLOCK (OB)", "Institutions leave imbalanced candles; price returns to OB before trend resumes."),
        ("fvg", "FAIR VALUE GAP (FVG)", "3-candle imbalance; gap acts as a magnet before continuation."),
        ("vwap_strategy", "VWAP RECLAIM", "VWAP = institutional fair value; reclaim signals bias shift."),
        ("trend_pullback", "TREND PULLBACK", "Enter on pullback to EMA/structure in a clear higher-timeframe trend."),
        ("orb", "OPENING RANGE BREAKOUT", "Trade the break of the first range with volume confirmation."),
        ("range_breakout", "RANGE BREAKOUT", "Consolidation energy releases on a directional breakout."),
        ("sr_flip", "S/R FLIP", "Former resistance becomes support (or vice versa) — trade the retest."),
        ("trendline_break", "TRENDLINE BREAK", "Break of a key diagonal with follow-through and retest."),
        ("consolidation_breakout", "CONSOLIDATION BREAKOUT", "Flag/pennant continuation after a strong impulse."),
        ("momentum_scalp", "MOMENTUM SCALP", "Quick burst in direction of micro-structure and volume."),
        ("liquidity_grab", "LIQUIDITY GRAB", "Stop hunt beyond a swing, then reversal — trade the reclaim."),
        ("breaker_block", "BREAKER BLOCK", "Failed structure flips role; enter on retest of the breaker."),
    ]
    random.shuffle(pool)
    n = min(len(pool), max(4, random.randint(4, 7)))
    samples = [_sample_strategy_post(sid, title, concept) for sid, title, concept in pool[:n]]

    print(
        "[preview] Strategy posts — random subset of setups; charts use random symbol + history slice..."
    )
    for post in samples:
        for force in ("real_chart", "diagram", "cheatsheet"):
            try:
                path = generate_post_for_strategy(
                    post,
                    force_type=force,
                    randomize_chart_history=True,
                    randomize_instrument=True,
                )
                out.append(path)
                print(f"  OK  {force:12} -> {Path(path).name}")
            except Exception as e:
                print(f"  ERR {force:12} -> {e}")

    print("[preview] Classic text card (generate_strategy_image)...")
    try:
        path = generate_strategy_image(random.choice(samples))
        out.append(path)
        print(f"  OK  text_card    -> {Path(path).name}")
    except Exception as e:
        print(f"  ERR text_card    -> {e}")

    return out


def run_news() -> list[str]:
    """
    Generate news intel cards + headline cards from a **shuffled pool** so each run
    uses different stocks/events (not always HDFC + RBI).
    """
    from content_engine.services.image_generator import generate_news_image, generate_headline_post

    out: list[str] = []
    pool = list(_NEWS_PREVIEW_POOL)
    random.shuffle(pool)
    k = min(len(pool), random.randint(3, 6))
    batch = pool[:k]

    print(f"[preview] News — {len(batch)} random stories (Part 1=intel, Part 2=headline)...")
    for i, item in enumerate(batch):
        label = f"story_{i + 1}"
        th = _pick_random_theme()
        intel = {**item, "part": 1, "total_parts": 2}
        viral = {**item, "part": 2, "total_parts": 2}
        try:
            path = generate_news_image(intel)
            out.append(path)
            print(f"  OK  news_{label:10} ({item.get('stock', '?')[:16]}) p1/2 -> {Path(path).name}")
        except Exception as e:
            print(f"  ERR news_{label} -> {e}")
        try:
            path = generate_headline_post(viral, theme_name=th)
            out.append(path)
            print(f"  OK  headline_{label:7} theme={th!r} p2/2 -> {Path(path).name}")
        except Exception as e:
            print(f"  ERR headline_{label} -> {e}")
    return out


def run_tips() -> list[str]:
    """Generate quick-tips cards — **random themes** each run (not always #0–#2)."""
    from content_engine.services.image_generator import generate_quick_tips_post, quick_tips_theme_count

    out: list[str] = []
    n = quick_tips_theme_count()
    k = min(n, random.randint(3, max(3, min(6, n))))
    indices = random.sample(range(n), k=k)

    print(f"[preview] Quick tips — random {k} of {n} themes, random light/dark...")
    for idx in indices:
        th = _pick_random_theme()
        try:
            path = generate_quick_tips_post(tips_index=idx, theme_name=th)
            out.append(path)
            print(f"  OK  tips idx={idx} theme={th!r} -> {Path(path).name}")
        except Exception as e:
            print(f"  ERR tips idx={idx} -> {e}")
    return out


def run_diagrams() -> list[str]:
    """
    Generate diagram templates — **random subset + shuffled order** each run
    (full set still possible when sample size = all).
    """
    from content_engine.services.image_generator import (
        create_visual_strategy_diagram,
        list_diagram_strategy_ids,
    )

    out: list[str] = []
    ids = list_diagram_strategy_ids()
    random.shuffle(ids)
    if len(ids) <= 4:
        picked = ids
    else:
        k = random.randint(4, len(ids))
        picked = ids[:k]

    print(f"[preview] Diagrams — {len(picked)} of {len(ids)} templates (shuffled)...")
    for name in picked:
        try:
            path = create_visual_strategy_diagram(name)
            out.append(path)
            print(f"  OK  {name} -> {Path(path).name}")
        except Exception as e:
            print(f"  ERR {name} -> {e}")
    return out


def run_all() -> None:
    run_strategy()
    print()
    run_news()
    print()
    run_tips()
    print()
    run_diagrams()
    here = Path(__file__).resolve().parent / "generated_posts"
    print()
    print(f"[preview] Done. Images folder: {here}")


def main() -> None:
    p = argparse.ArgumentParser(description="Generate preview images (no Telegram).")
    p.add_argument(
        "mode",
        choices=["strategy", "news", "tips", "diagrams", "all"],
        help="What to generate",
    )
    args = p.parse_args()

    if args.mode == "strategy":
        run_strategy()
    elif args.mode == "news":
        run_news()
    elif args.mode == "tips":
        run_tips()
    elif args.mode == "diagrams":
        run_diagrams()
    else:
        run_all()

    here = Path(__file__).resolve().parent / "generated_posts"
    print()
    print(f"[preview] Output: {here}")


if __name__ == "__main__":
    main()
