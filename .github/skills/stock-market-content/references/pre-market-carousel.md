# Pre-Market Carousel Template

## Overview
5–7 slide Instagram carousel posted daily at 7:30–8:30 AM IST.
Covers world markets, Gift Nifty, FII/DII, key stocks in news, and levels to watch.

---

## Slide 1 — Cover

**Layout:**
- Full dark background (#0a0a0a)
- Brand logo: top-left (semi-transparent watermark)
- Date badge: top-right (white text, dark pill, "DD MONTH" e.g. "29 MARCH")
- Main headline: center-left, ALL CAPS, 2 lines max
  - Line 1: `PRE MARKET` in large bold white
  - Line 2: `REPORT` or `UPDATE` in yellow/green accent
- Sub-badge: `🇮🇳 INDIAN MARKET UPDATES` under headline
- Featured news: Bottom half — 1-2 sentence brief on biggest stock/macro news today
  - Stock name in CYAN (#00ffcc)
  - News text in WHITE
- Swipe indicator: Right edge → `SWIPE 👉`
- Brand bar: Bottom → `📷 / 📩 / STOCKSWITHGAURAV`

**Example data:**
```json
{
  "slide_type": "cover",
  "headline": "PRE MARKET REPORT",
  "date_display": "29 MARCH",
  "badge": "🇮🇳 INDIAN MARKET UPDATES",
  "featured_item": {
    "stock": "WAAREE ENERGIES",
    "news": "Receives LoA from SECI for 300 MW wind project"
  },
  "swipe": true
}
```

---

## Slide 2 — World Markets (U.S. + Asian)

**Layout:**
- Section header: `WORLD MARKETS` in yellow, large
- Two sub-sections stacked:

**U.S. MARKETS** (header in yellow underline)
| Column | Alignment | Color |
|--------|-----------|-------|
| Market Name | Left | White |
| CLOSE PRICE | Right | Cyan |
| (Chg%) | Right | Green (+) / Red (-) |

Markets: Dow Jones, S&P 500, Nasdaq

**ASIAN MARKETS** (header in yellow underline)
Markets: Nikkei 225, Shanghai Comp, Hang Seng, Gift Nifty

**Example data:**
```json
{
  "slide_type": "world_markets",
  "sections": [
    {
      "title": "U.S MARKETS",
      "columns": ["CLOSE PRICE", "(Chg%)"],
      "rows": [
        {"name": "Dow Jones", "price": "42,583", "change": "+1.56%", "direction": "up"},
        {"name": "S&P 500", "price": "5,767", "change": "+2.12%", "direction": "up"},
        {"name": "Nasdaq", "price": "18,188", "change": "+2.61%", "direction": "up"}
      ]
    },
    {
      "title": "ASIAN MARKETS",
      "columns": ["VALUE", "(Chg%)"],
      "rows": [
        {"name": "Nikkei 225", "price": "37,120", "change": "-0.45%", "direction": "down"},
        {"name": "Shanghai", "price": "3,350", "change": "+0.12%", "direction": "up"},
        {"name": "Hang Seng", "price": "23,426", "change": "+0.50%", "direction": "up"},
        {"name": "Gift Nifty", "price": "23,490", "change": "+0.35%", "direction": "up"}
      ]
    }
  ]
}
```

---

## Slide 3 — Key Global Events / Macro Cues

**Layout:**
- Header: `KEY GLOBAL EVENTS` or `GLOBAL CUES` in yellow
- Numbered list (1–5 items):
  - Each item: Bold keyword in CYAN + description in WHITE
  - If bullish/positive → green dot prefix ●
  - If bearish/negative → red dot prefix ●
  - Neutral → white dot ○

**Example items:**
1. 🟢 **US MARKETS SURGE**: S&P 500 rallied +2.12%, led by tech recovery after tariff exemptions
2. 🔴 **CRUDE OIL RISES**: Brent at $72.3/bbl, +1.2% — pressure on OMCs
3. 🟢 **FII FLOWS**: FIIs net bought ₹2,450 Cr on Friday, first positive day in 8 sessions
4. 🔴 **BOND YIELDS**: US 10Y yield at 4.45%, highest in 3 weeks — emerging market concern
5. 🟢 **RUPEE STRENGTH**: INR at 83.20 vs USD, appreciating on strong FII inflows

**Data format:**
```json
{
  "slide_type": "global_events",
  "items": [
    {
      "sentiment": "bullish",
      "keyword": "US MARKETS SURGE",
      "detail": "S&P 500 rallied +2.12%, led by tech recovery after tariff exemptions"
    }
  ]
}
```

---

## Slide 4 — FII / DII Activity

**Layout:**
- Header: `FII / DII ACTIVITY` in yellow
- Two-row table:

| | Buy (₹ Cr) | Sell (₹ Cr) | Net (₹ Cr) |
|---|---|---|---|
| **FII** | 14,500 | 12,050 | +2,450 |
| **DII** | 11,200 | 13,800 | -2,600 |

- Net values color-coded: GREEN for net buy, RED for net sell
- Optional 1-line commentary below: "FII turned buyers after 8 consecutive selling sessions"
- Include monthly cumulative if significant

**Data format:**
```json
{
  "slide_type": "fii_dii",
  "data": {
    "fii": {"buy": 14500, "sell": 12050, "net": 2450},
    "dii": {"buy": 11200, "sell": 13800, "net": -2600}
  },
  "commentary": "FII turned buyers after 8 consecutive selling sessions"
}
```

---

## Slide 5 — Stocks In News / Key Earnings

**Layout:**
- Header: `STOCKS IN NEWS` or `KEY EARNINGS TODAY` in yellow
- List of 5–8 stocks, each with:
  - Stock name in CYAN (bold)
  - 1-line news/catalyst in WHITE
  - Optional: price target or rating in GREEN/RED pill

**Rules:**
- Only include genuinely market-moving news (earnings, block deals, FDA approvals, govt orders, upgrades/downgrades)
- No "might" or "could" — factual only
- Attribute brokerage if a call: "Motilal Oswal: Buy HDFCBANK, target ₹1,900"

**Data format:**
```json
{
  "slide_type": "stocks_in_news",
  "items": [
    {
      "stock": "WAAREE ENERGIES",
      "news": "Receives LoA from SECI for 300 MW wind project",
      "sentiment": "bullish"
    },
    {
      "stock": "TATA STEEL",
      "news": "To raise ₹3,000 Cr via NCDs for capex",
      "sentiment": "neutral"
    }
  ]
}
```

---

## Slide 6 — Levels to Watch (NIFTY / BANKNIFTY)

**Layout:**
- Header: `LEVELS TO WATCH` in yellow
- Two sections side-by-side or stacked:

**NIFTY 50:**
| Level | Value |
|-------|-------|
| Resistance 2 | 23,800 |
| Resistance 1 | 23,600 |
| Pivot | 23,400 |
| Support 1 | 23,200 |
| Support 2 | 23,000 |

**BANKNIFTY:**
| Same format |

- Resistance in RED, Support in GREEN, Pivot in YELLOW
- Optional: "Open above 23,600 → bullish; Below 23,200 → bearish" in small grey text

**Data format:**
```json
{
  "slide_type": "levels",
  "indices": [
    {
      "name": "NIFTY 50",
      "resistance_2": 23800,
      "resistance_1": 23600,
      "pivot": 23400,
      "support_1": 23200,
      "support_2": 23000
    },
    {
      "name": "BANKNIFTY",
      "resistance_2": 51500,
      "resistance_1": 51000,
      "pivot": 50500,
      "support_1": 50000,
      "support_2": 49500
    }
  ]
}
```

---

## Slide 7 — Disclaimer / CTA (Final slide)

**Layout:**
- Dark centered layout
- Main text: `FOLLOW FOR DAILY MARKET UPDATES` in white
- Logo in center, large
- Social handles: Instagram + Telegram icons with handles
- Disclaimer at bottom in small grey text: `⚠️ Educational purposes only. Not SEBI registered. Not for Buy/Sell Recommendations.`
- Optional: "Turn on Post Notifications 🔔" in yellow

**Data format:**
```json
{
  "slide_type": "cta_disclaimer",
  "cta": "FOLLOW FOR DAILY MARKET UPDATES",
  "handles": {
    "instagram": "@StocksWithGaurav",
    "telegram": "@StocksWithGaurav"
  },
  "disclaimer": "Educational purposes only. Not SEBI registered. Not for Buy/Sell Recommendations."
}
```

---

## Caption Template

```
📊 PRE MARKET REPORT — [DD/MM/YY]

[1 line summary: "Global markets up, Gift Nifty positive — bullish open expected"]

Slide by slide:
1️⃣ Cover + Featured news
2️⃣ World Markets snapshot
3️⃣ Global cues that matter today
4️⃣ FII/DII activity
5️⃣ Stocks in focus today
6️⃣ Key levels for NIFTY & BANKNIFTY

Save this for market open 📌
Share with your trading buddy 🤝

Follow @StocksWithGaurav for daily market updates 📊

#StocksWithGaurav #PreMarketReport #IndianStockMarket #Nifty50 #BankNifty #NSE #BSE
#MarketUpdate #TradingWithSMC #SmartMoneyConcepts #StockMarketIndia #FII #DII
#GiftNifty #MarketOutlook #IntradayTrading #SwingTrading #TechnicalAnalysis
#StockMarketForBeginners #WealthCreation #TradingCommunity #StockAlert
#NiftyAnalysis #SensexNifty #MarketNews #TradeLikeAPro #StockMarketTips
```
