---
name: stock-market-content
description: "Generate Indian stock market social media content — Instagram carousels, Telegram posts, reels scripts. Use when: creating pre-market report, post-market wrap, market crash analysis, brokerage calls, stocks news, world markets update, high delivery stocks, sector analysis, trending topics carousel. Produces ready-to-post text with image layout specs for the StocksWithGaurav content engine."
argument-hint: "pre-market carousel, post-market wrap, crash analysis, news carousel, brokerage calls"
---

# Stock Market Content Generator

## Brand Identity

- **Account**: StocksWithGaurav / @StocksWithGaurav
- **Platforms**: Instagram (primary), Telegram (daily), Twitter/X
- **Niche**: Indian stock market — NSE/BSE, NIFTY, BANKNIFTY, FNO, SMC-based analysis
- **Tone**: Bold, data-first, news-anchor energy — NOT bookish or academic
- **Visual style**: Dark background (#0a0a0a to #1a1a1a), bold colored headlines, data tables, swipe indicators

## When to Use

- User asks to create any stock market social media content
- Pre-market or post-market report generation
- Market crash / rally analysis posts
- News carousel creation
- Brokerage call summaries
- Sector / thematic stock lists
- Weekly content planning

## Content Categories

See the reference docs for detailed slide-by-slide templates:

| Category | Reference | Slides | When |
|----------|-----------|--------|------|
| Pre-Market Report | [pre-market-carousel.md](./references/pre-market-carousel.md) | 5–7 | Daily 7:30–8:30 AM IST |
| Post-Market Wrap | [post-market-carousel.md](./references/post-market-carousel.md) | 5–7 | Daily 3:30–4:30 PM IST |
| Breaking News | Style guide below | 1–3 | As needed |
| Brokerage Calls | Style guide below | 3–5 | After market hours |
| Stock Lists | Style guide below | 1–2 | Thematic / trending |
| Market Crash/Rally | Style guide below | 3–5 | Event-driven |

## Procedure

### Step 1 — Determine Content Type

Ask or infer:
- **What type?** Pre-market / post-market / news / crash analysis / brokerage / stock list
- **Date?** Default to today
- **Which data?** Specific stocks, indices, or "use latest available"

### Step 2 — Gather Market Data

For pre/post-market, collect these data points (from user input, yfinance, or news APIs):

**Always needed:**
- NIFTY 50 close/change/% 
- BANKNIFTY close/change/%
- SENSEX close/change/%

**Pre-market also needs:**
- US Markets (Dow, S&P 500, Nasdaq) — previous close
- Asian Markets (Gift Nifty, Nikkei, Shanghai) — current
- SGX Nifty / Gift Nifty futures
- Key stocks in news today

**Post-market also needs:**
- Top gainers / losers (5 each)
- Sector performance (green/red)
- FII/DII data (buy/sell in crores)
- Stocks hitting 52-week high/low
- High delivery % stocks (top 10)

### Step 3 — Write Content Per Slide

Follow the slide-by-slide templates in the reference docs. Key rules:

**Headlines:**
- ALL CAPS for primary headline
- Use color coding: GREEN (#00ff00) for bullish, RED (#ff0000) for bearish, YELLOW (#ffff00) for highlights, WHITE for body text, CYAN (#00ffcc) for data
- Max 6 words per headline
- Numbers are always colored (green if positive, red if negative)

**Body text:**
- Bullet points, not paragraphs
- Stock names in GREEN/CYAN with highlight
- Numbers with + or - prefix and color
- Max 12 bullet points per slide
- Each bullet = 1 line, no wrapping ideally

**Data tables:**
- Column headers in YELLOW
- Stock names left-aligned, numbers right-aligned
- Alternating subtle row backgrounds for readability
- Use ₹ symbol for Indian prices, % for changes

### Step 4 — Generate Caption

For Instagram caption, follow this structure:
```
[Topic] [Date DD/MM/YY]

[2-3 line summary of what the carousel covers]

Save this for reference 📌
Share with your trading buddy 🤝

Like & Follow @StocksWithGaurav for daily updates 📊

#StocksWithGaurav #IndianStockMarket #NSE #BSE #Nifty50 #BankNifty
[+ 15-20 relevant hashtags from content_engine/instagram_captions.py banks]
```

### Step 5 — Output Format

Return content as structured JSON that the content engine can consume:

```json
{
  "content_type": "pre_market_carousel",
  "date": "2026-03-29",
  "slides": [
    {
      "slide_number": 1,
      "slide_type": "cover",
      "headline": "PRE MARKET REPORT",
      "subheadline": "🇮🇳 Indian Market Updates",
      "date_display": "29/03/26",
      "featured_stock": {
        "name": "WAAREE ENERGIES",
        "news": "Receives LoA from SECI for 300 MW wind project"
      }
    },
    {
      "slide_number": 2,
      "slide_type": "world_markets",
      "sections": [
        {
          "title": "U.S MARKETS",
          "columns": ["CLOSE PRICE", "(Chg%)"],
          "rows": [
            {"name": "Dow Jones", "price": "45,960.11", "change": "-1.01%"}
          ]
        }
      ]
    }
  ],
  "caption": "...",
  "hashtags": [...]
}
```

### Step 6 — Review Checklist

Before finalizing:
- [ ] Every number has + or - sign and is color-coded
- [ ] Headlines are ALL CAPS, max 6 words
- [ ] "SWIPE 👉" indicator on slide 1
- [ ] Brand watermark/logo position noted (top-right)
- [ ] Date displayed on slide 1 (DD/MM/YY format)
- [ ] Disclaimer on last slide: "Educational purposes only, Not for Buy/Sell Recommendations"
- [ ] Source attribution if using external data
- [ ] No guaranteed profit claims anywhere
- [ ] Caption has hashtags (25-28 total)
- [ ] Each slide has ≤ 12 data items for readability

## Style Guide — Quick Reference

### Visual Layout Constants (1080×1080)
- **Background**: #0a0a0a to #1a1a1a dark gradient
- **Primary accent**: #00ff00 (green for bullish), #ff0000 (red for bearish)
- **Highlight**: #ffff00 (yellow for section headers)
- **Data text**: #00ffcc (cyan)
- **Body text**: #ffffff (white), #cccccc (grey for secondary)
- **Brand bar**: Bottom 60px — Telegram + Instagram handles
- **Swipe indicator**: Red arrow + "SWIPE 👉" on right side
- **Logo**: Top-right corner, semi-transparent watermark
- **Date badge**: Top-right, white text in dark pill

### Headline Patterns That Perform

| Pattern | Example | Use When |
|---------|---------|----------|
| FEAR headline | "WHY INDIAN STOCKMARKET CRASH TODAY?" | Red day, -1.5%+ |
| GREED headline | "MASSIVE RALLY! NIFTY CROSSES 23,000" | Green day, +1.5%+ |
| Data headline | "STOCKS WITH HIGH DELIVERY (%)" | Data-focused slides |
| News headline | "4 KEY FACTORS BEHIND TODAY'S MARKET CRASH" | News analysis |
| List headline | "LIST OF DEFENCE STOCKS" | Sector/thematic |
| Breaking headline | "ANOTHER BRUTAL DAY?" | Event-driven urgency |

### Content-Type Specific Angles

**Market Crash Posts (high engagement):**
1. Lead: "WHY [INDEX] CRASHED TODAY?" — emotional hook
2. Number the factors (e.g., "4 KEY FACTORS")
3. Each factor gets a bold colored subheading
4. Include global context (US markets, oil, geopolitics)
5. End with "what to watch tomorrow" — forward-looking

**Brokerage Calls:**
1. Header: "BROKERAGE CALL" in yellow/white banner
2. Format: "[Broker] keeps '[rating]' on [Stock], target ₹[X]"
3. 3-4 bullet points per call explaining rationale
4. Stock name highlighted in green/cyan
5. Multiple calls per slide (3-4 calls fit well)

**Stock Lists / Thematic:**
1. Bold title with highlighted keyword
2. Grid layout for logos if available
3. Simple numbered list with 1-line descriptions
4. Always attribute source

### Engagement Boosters

- "Save this for later 📌" in caption
- "Share with your trading buddy 🤝" 
- Numbered lists (people love ranking data)
- "SWIPE for more 👉" on every non-final slide
- Ask a question in caption: "Which stock are you watching?"
- Use the word "TODAY" or current date for urgency

## Integration with Content Engine

This skill outputs JSON that can be consumed by:
- `content_engine/services/image_generator.py` — for PIL-based rendering
- `content_engine/services/html_renderer.py` — for HTML→PNG rendering
- `content_engine/agents/narrative_agent.py` — for text post generation
- `content_engine/services/telegram_service.py` — for Telegram delivery

The existing scheduler runs at 08:30 (strategy), 09:00 (news), 15:45 (EOD synthesis).
Pre-market content should be ready by 08:00 IST. Post-market by 16:00 IST.
