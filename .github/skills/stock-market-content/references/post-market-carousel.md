# Post-Market Carousel Template

## Overview
5–7 slide Instagram carousel posted daily at 3:30–4:30 PM IST.
Covers market close, sector performance, top gainers/losers, FII/DII, key events impact, and tomorrow's outlook.

---

## Slide 1 — Market Close Cover

**Layout:**
- Dark background with subtle BSE building / bull-bear imagery
- Date badge: top-right (white text, "DD MONTH")
- Brand logo: top-left
- Index data panel (center-top):
  - NIFTY: 23,519.35 | -601.45 | -2.6%
  - SENSEX: 72,740.03 | -1,792.93 | -2.41%
- Main headline (bottom-center, large ALL CAPS):
  - RED day: `MONDAY HORROR SHOW` / `BRUTAL SELL OFF` / `BLOODBATH ON D-STREET`
  - GREEN day: `BULLS CHARGE BACK` / `MASSIVE RALLY` / `GREEN ACROSS THE BOARD`
  - FLAT day: `RANGEBOUND SESSION` / `MARKETS CONSOLIDATE`
- Subheadline in accent color: 1-line sentiment summary
- Swipe indicator right side

**Headline selection logic:**
| NIFTY change | Tone | Example headlines |
|---|---|---|
| > +2% | Euphoric | `MASSIVE BULL RUN!` `NIFTY SOARS [X] POINTS` |
| +0.5% to +2% | Positive | `BULLS IN CONTROL` `GREEN DAY ON D-STREET` |
| -0.5% to +0.5% | Neutral | `RANGEBOUND SESSION` `FLAT CLOSE` |
| -0.5% to -2% | Negative | `SELL OFF CONTINUES` `BEARS TIGHTEN GRIP` |
| < -2% | Panic | `BLOODBATH!` `₹[X] LAKH CR WIPED OUT` `WHY MARKET CRASHED TODAY?` |

**Data format:**
```json
{
  "slide_type": "market_close_cover",
  "indices": [
    {"name": "NIFTY", "close": 23519.35, "change": -601.45, "pct": -2.6},
    {"name": "SENSEX", "close": 72740.03, "change": -1792.93, "pct": -2.41}
  ],
  "headline": "MONDAY HORROR SHOW",
  "subheadline": "D-Street Witnesses Massive Sell-Off As U.S.-Iran Conflict Grips Global Markets",
  "market_cap_impact": "BSE-listed cos erase market cap of more than ₹13 lakh cr today",
  "swipe": true
}
```

---

## Slide 2 — Key Factors / Reasons (for RED or GREEN days)

**Layout:**
- Header: `[N] KEY FACTORS BEHIND TODAY'S MARKET [CRASH/RALLY]` in yellow+white
- Numbered factors (2-4 per slide, use 2 slides if 4+ factors):
  - Factor title in YELLOW/GREEN bold (e.g., `1) Iran-Israel Clash`)
  - 2-3 bullet points in WHITE explaining the factor
  - Important phrases/numbers highlighted in RED/GREEN/CYAN
  - Analyst quotes in CYAN italic if available

**Example factors for crash:**
1. **Iran-Israel Clash** — escalating geopolitical tension, oil price spike
2. **Rise in crude oil prices** — Brent at $75, negative for India's import bill
3. **SEBI tightens F&O measures** — weekly expiries limited, higher contract sizes
4. **China factor** — FII outflows to Chinese markets after stimulus

**For flat/boring days, skip this slide** and replace with sector performance.

**Data format:**
```json
{
  "slide_type": "key_factors",
  "context": "crash",
  "factors": [
    {
      "number": 1,
      "title": "Iran-Israel Clash",
      "points": [
        "Indian stocks declined amid rising concerns over escalating hostilities",
        "Iranian missile attacks targeting Tel Aviv, Israel warns imminent response",
        "Oil prices surge on supply disruption fears"
      ],
      "highlighted_phrases": ["Iranian missile attacks", "imminent response"]
    }
  ]
}
```

---

## Slide 3 — Sector Performance Heatmap

**Layout:**
- Header: `SECTOR PERFORMANCE` in yellow
- Visual: Grid or list of sectors with % change
- Color-coded: Deep green to deep red based on intensity
- Format per sector:

| Sector | Change | Color |
|--------|--------|-------|
| NIFTY IT | +2.3% | Bright green |
| NIFTY BANK | -3.1% | Bright red |
| NIFTY PHARMA | +0.8% | Light green |

- Sort by change descending (best → worst)
- Show 8–12 sectors

**Data format:**
```json
{
  "slide_type": "sector_performance",
  "sectors": [
    {"name": "NIFTY IT", "change_pct": 2.3, "direction": "up"},
    {"name": "NIFTY BANK", "change_pct": -3.1, "direction": "down"},
    {"name": "NIFTY PHARMA", "change_pct": 0.8, "direction": "up"},
    {"name": "NIFTY AUTO", "change_pct": -1.5, "direction": "down"},
    {"name": "NIFTY METAL", "change_pct": -2.8, "direction": "down"},
    {"name": "NIFTY FMCG", "change_pct": 0.2, "direction": "up"},
    {"name": "NIFTY ENERGY", "change_pct": -1.9, "direction": "down"},
    {"name": "NIFTY REALTY", "change_pct": -0.9, "direction": "down"}
  ]
}
```

---

## Slide 4 — Top Gainers & Losers

**Layout:**
- Split into two halves or two sections:

**TOP GAINERS** (green header):
| # | Stock | Close | Change (%) |
|---|-------|-------|------------|
| 1 | HCLTECH | ₹1,420 | +3.2% |
| 2 | INFY | ₹1,890 | +2.8% |

**TOP LOSERS** (red header):
| # | Stock | Close | Change (%) |
|---|-------|-------|------------|
| 1 | ADANI PORTS | ₹1,180 | -5.4% |
| 2 | TATAMOTORS | ₹720 | -4.1% |

- 5 stocks each
- Stock names in CYAN/WHITE, changes in GREEN/RED

**Data format:**
```json
{
  "slide_type": "gainers_losers",
  "gainers": [
    {"rank": 1, "stock": "HCLTECH", "close": 1420, "change_pct": 3.2}
  ],
  "losers": [
    {"rank": 1, "stock": "ADANI PORTS", "close": 1180, "change_pct": -5.4}
  ]
}
```

---

## Slide 5 — Stocks With High Delivery (%)

**Layout:**
- Header: `STOCKS WITH HIGH DELIVERY (%)` in yellow with red/white highlights
- Side badge: `TOP 10` in green, `NIFTY 500 Stocks only` subtext
- Numbered list:

```
1. Hexaware Tech.      82.64%
2. Ipca Laboratories    81.74%
3. Shyam Metalics       81.32%
...
10. Whirlpool India     72.86%
```

- Stock names in WHITE, percentages in GREEN
- High delivery = institutional activity indicator (mention this in caption)

**Data format:**
```json
{
  "slide_type": "high_delivery",
  "title": "STOCKS WITH HIGH DELIVERY (%)",
  "scope": "NIFTY 500 Stocks only",
  "stocks": [
    {"rank": 1, "name": "Hexaware Tech.", "delivery_pct": 82.64},
    {"rank": 2, "name": "Ipca Laboratories", "delivery_pct": 81.74}
  ]
}
```

---

## Slide 6 — FII / DII + Options Data

**Layout:**
- Header: `INSTITUTIONAL ACTIVITY` in yellow
- FII/DII table (same format as pre-market slide 4)
- Additional section: `OPTIONS DATA` (if available)
  - PCR (Put-Call Ratio)
  - Max Pain level
  - Top OI Buildup (calls and puts)
- Commentary: 1-line interpretation in grey text

**Data format:**
```json
{
  "slide_type": "institutional_options",
  "fii_dii": {
    "fii": {"buy": 14500, "sell": 17000, "net": -2500},
    "dii": {"buy": 13000, "sell": 10500, "net": 2500}
  },
  "options": {
    "pcr": 0.85,
    "max_pain": 23500,
    "note": "High put writing at 23,000 — strong support visible"
  }
}
```

---

## Slide 7 — Tomorrow's Outlook / CTA

**Layout:**
- Header: `WHAT TO WATCH TOMORROW` in yellow
- 3–5 bullet points:
  - Key levels: "NIFTY above 23,600 → bullish; below 23,200 → bearish"
  - Events: "Fed minutes release at 11:30 PM IST"
  - Stocks: "Q4 results: TCS, INFY, HDFC BANK"
  - Global: "US CPI data at 6 PM IST — high volatility expected"
- Bias indicator: BULLISH / BEARISH / NEUTRAL tag in colored pill
- Brand handles + disclaimer at bottom

**Data format:**
```json
{
  "slide_type": "tomorrow_outlook",
  "bias": "bearish",
  "watchlist": [
    "NIFTY: Above 23,600 → bullish reversal possible; Below 23,200 → panic selling continues",
    "Fed minutes release at 11:30 PM IST — hawkish tone could extend sell-off",
    "Q4 results: TCS (Apr 10), INFY (Apr 11) — IT sector direction",
    "Crude oil above $75 → OMC weakness continues"
  ],
  "disclaimer": "Educational purposes only. Not SEBI registered. Not for Buy/Sell Recommendations."
}
```

---

## Caption Template

```
📉 POST MARKET WRAP — [DD/MM/YY]

[Emotional 1-liner matching the day: "Bloodbath on D-Street as NIFTY crashes 600 points"]

What happened today:
1️⃣ Market close + headline
2️⃣ Key factors behind the move
3️⃣ Sector performance
4️⃣ Top gainers & losers
5️⃣ High delivery stocks 
6️⃣ FII/DII + options snapshot
7️⃣ Tomorrow's outlook

Save this for reference 📌
Tag a friend who missed today's market 🤝

Follow @StocksWithGaurav for daily wraps 📊

#StocksWithGaurav #PostMarketWrap #IndianStockMarket #MarketUpdate #Nifty50 #BankNifty
#NSE #BSE #SensexNifty #StockMarketIndia #TodayMarket #MarketCrash #TradingCommunity
#FII #DII #TopGainers #TopLosers #SectorPerformance #HighDelivery #IntradayTrading
#SwingTrading #StockMarketTips #WealthCreation #TechnicalAnalysis #TradeLikeAPro
#MarketAnalysis #StockAlert
```
