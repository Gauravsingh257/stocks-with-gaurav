"""
Content Creation / agents / stock_picker_agent.py

StockPickerAgent — selects 3–4 stocks backed by data, not randomness.

Selection is scored. Each stock earns points from three signals:
  1. Sector strength  — stock's sector is among top/bottom movers  (0–40 pts)
  2. News catalyst    — stock or company name appears in headlines  (0–30 pts)
  3. Momentum proxy   — sector change_pct magnitude as momentum    (0–30 pts)

The top-scoring stocks win, with a diversity filter (max 1 per sector).

Input:  MarketData + MarketAnalysis
Output: StockPicks (3–4 picks with data-backed rationale)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import yfinance as yf

from agents.base import BaseContentAgent
from models.contracts import (
    MarketAnalysis,
    MarketData,
    PipelineMode,
    Sentiment,
    StockPick,
    StockPicks,
)

log = logging.getLogger("content_creation.agents.stock_picker")

# ═══════════════════════════════════════════════════════════════════════════
#  STOCK UNIVERSE — FNO majors with sector tags
#  In production: load from stock_universe_fno.json + live screener.
# ═══════════════════════════════════════════════════════════════════════════

_STOCK_DATABASE: list[dict] = [
    {"symbol": "RELIANCE",   "name": "Reliance Industries",      "sector": "Energy"},
    {"symbol": "TCS",        "name": "Tata Consultancy Services", "sector": "IT"},
    {"symbol": "HDFCBANK",   "name": "HDFC Bank",                "sector": "Bank"},
    {"symbol": "INFY",       "name": "Infosys",                  "sector": "IT"},
    {"symbol": "ICICIBANK",  "name": "ICICI Bank",               "sector": "Bank"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel",            "sector": "Telecom"},
    {"symbol": "SBIN",       "name": "State Bank of India",      "sector": "Bank"},
    {"symbol": "ITC",        "name": "ITC Limited",              "sector": "FMCG"},
    {"symbol": "TATAMOTORS", "name": "Tata Motors",              "sector": "Auto"},
    {"symbol": "MARUTI",     "name": "Maruti Suzuki",            "sector": "Auto"},
    {"symbol": "SUNPHARMA",  "name": "Sun Pharma",               "sector": "Pharma"},
    {"symbol": "WIPRO",      "name": "Wipro",                    "sector": "IT"},
    {"symbol": "HCLTECH",    "name": "HCL Technologies",         "sector": "IT"},
    {"symbol": "ADANIENT",   "name": "Adani Enterprises",        "sector": "Conglomerate"},
    {"symbol": "TATASTEEL",  "name": "Tata Steel",               "sector": "Metal"},
    {"symbol": "JSWSTEEL",   "name": "JSW Steel",                "sector": "Metal"},
    {"symbol": "LT",         "name": "Larsen & Toubro",          "sector": "Infrastructure"},
    {"symbol": "AXISBANK",   "name": "Axis Bank",                "sector": "Bank"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance",            "sector": "NBFC"},
    {"symbol": "TITAN",      "name": "Titan Company",            "sector": "Consumer"},
    {"symbol": "ULTRACEMCO", "name": "UltraTech Cement",         "sector": "Cement"},
    {"symbol": "DRREDDY",    "name": "Dr. Reddy's",              "sector": "Pharma"},
    {"symbol": "POWERGRID",  "name": "Power Grid Corp",          "sector": "Power"},
    {"symbol": "NTPC",       "name": "NTPC Limited",             "sector": "Power"},
    {"symbol": "M&M",        "name": "Mahindra & Mahindra",      "sector": "Auto"},
    {"symbol": "HINDALCO",   "name": "Hindalco Industries",      "sector": "Metal"},
    {"symbol": "ONGC",       "name": "ONGC",                     "sector": "Energy"},
    {"symbol": "COALINDIA",  "name": "Coal India",               "sector": "Mining"},
    {"symbol": "TECHM",      "name": "Tech Mahindra",            "sector": "IT"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints",             "sector": "Consumer"},
]

# Sector → leader stock (highest market cap / most representative)
_SECTOR_LEADERS: dict[str, str] = {
    "IT":             "TCS",
    "Bank":           "HDFCBANK",
    "Energy":         "RELIANCE",
    "Auto":           "TATAMOTORS",
    "Pharma":         "SUNPHARMA",
    "Metal":          "TATASTEEL",
    "FMCG":           "ITC",
    "Telecom":        "BHARTIARTL",
    "NBFC":           "BAJFINANCE",
    "Consumer":       "TITAN",
    "Infrastructure": "LT",
    "Power":          "NTPC",
    "Cement":         "ULTRACEMCO",
    "Conglomerate":   "ADANIENT",
    "Mining":         "COALINDIA",
    "Realty":         "LT",       # proxy
}


# ═══════════════════════════════════════════════════════════════════════════
#  Internal scored candidate
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _ScoredStock:
    symbol: str
    name: str
    sector: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    setup_type: str = "momentum"


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT
# ═══════════════════════════════════════════════════════════════════════════

class StockPickerAgent(BaseContentAgent):
    name = "StockPickerAgent"
    description = "Data-scored stock selection — sector strength, news catalysts, momentum"

    def run(
        self,
        *,
        market_data: MarketData,
        analysis: MarketAnalysis,
    ) -> StockPicks:
        # ── 1. Build sector strength lookup ───────────────────────────────
        sector_pct: dict[str, float] = {}
        for s in market_data.sectors:
            sector_pct[s.name] = s.change_pct

        # ── 2. Build news mention set ─────────────────────────────────────
        news_mentions = self._scan_news(market_data)

        # ── 3. Score every stock ──────────────────────────────────────────
        scored: list[_ScoredStock] = []
        for stock in _STOCK_DATABASE:
            s = _ScoredStock(
                symbol=stock["symbol"],
                name=stock["name"],
                sector=stock["sector"],
            )
            self._score_sector_strength(s, sector_pct, analysis)
            self._score_news_catalyst(s, news_mentions)
            self._score_momentum(s, sector_pct, analysis)
            scored.append(s)

        # ── 4. Sort by total score descending ─────────────────────────────
        scored.sort(key=lambda c: c.score, reverse=True)

        # ── 5. Diversity filter: max 1 per sector ────────────────────────
        selected = self._diverse_top_n(scored, n=4)

        # ── 6. Map to StockPick contracts (with price levels) ─────────
        mode = market_data.mode
        picks: list[StockPick] = []
        for s in selected:
            rationale = "; ".join(s.reasons) if s.reasons else self._fallback_reason(s, analysis, mode)
            price, sl, target = self._fetch_stock_levels(s.symbol, s.setup_type)
            picks.append(StockPick(
                symbol=s.symbol,
                name=s.name,
                sector=s.sector,
                setup_type=s.setup_type,
                rationale=rationale,
                price=price,
                stop_loss=sl,
                target=target,
            ))

        result = StockPicks(
            picks=picks,
            selection_criteria="Scored: sector strength (40) + news catalyst (30) + momentum (30)",
            market_context=analysis.summary or "",
        )

        log.info(
            "StockPicker: %d picks — %s",
            len(picks),
            [(p.symbol, round(s.score, 1)) for p, s in zip(picks, selected)],
        )
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  SCORING FUNCTIONS — deterministic, no randomness
    # ══════════════════════════════════════════════════════════════════════

    def _score_sector_strength(
        self,
        stock: _ScoredStock,
        sector_pct: dict[str, float],
        analysis: MarketAnalysis,
    ) -> None:
        """
        0–40 points.
        Stock gets max points if its sector is the strongest mover.
        Sector leader within that sector gets a bonus.
        """
        pct = sector_pct.get(stock.sector, 0.0)
        if not sector_pct:
            return

        # Rank this sector's absolute move vs. all sectors
        abs_moves = sorted(
            [abs(v) for v in sector_pct.values()], reverse=True
        )
        rank = abs_moves.index(abs(pct)) if abs(pct) in abs_moves else len(abs_moves)
        total = len(abs_moves)

        # Top sector → 40, second → 30, etc.  Scale linearly.
        if total > 0:
            position_score = max(0, 40 * (1 - rank / total))
        else:
            position_score = 0

        # Leader bonus: +5 if this stock is the sector leader
        is_leader = _SECTOR_LEADERS.get(stock.sector) == stock.symbol
        leader_bonus = 5 if is_leader else 0

        points = position_score + leader_bonus
        if points > 2 and abs(pct) >= 0.1:  # skip 0.0% — no data
            direction = "+" if pct > 0 else ""
            stock.reasons.append(f"{stock.sector} sector {direction}{pct:.1f}%")
            if is_leader:
                # Direction-aware label: negative sector = top decliner, positive = top gainer
                leader_label = "top decliner" if pct < 0 else "top gainer"
                stock.reasons[-1] += f" ({leader_label})"
            stock.score += points

    def _score_news_catalyst(
        self,
        stock: _ScoredStock,
        news_mentions: dict[str, str],
    ) -> None:
        """
        0–30 points.
        Direct name/symbol match in headlines → 30 pts.
        """
        headline = news_mentions.get(stock.symbol)
        if headline:
            # Skip generic market-level headlines (Sensex/Nifty mentions not stock-specific)
            _market_words = ("sensex", "nifty", "market", "index", "bse", "nse")
            is_generic = any(w in headline.lower() for w in _market_words)
            if not is_generic:
                stock.score += 30
                stock.reasons.append(f"In news: {headline[:60]}")
                stock.setup_type = "news_catalyst"

    def _score_momentum(
        self,
        stock: _ScoredStock,
        sector_pct: dict[str, float],
        analysis: MarketAnalysis,
    ) -> None:
        """
        0–30 points.
        Based on the magnitude of sector move + alignment with market regime.
        Setup types are varied based on actual sector behavior.
        """
        pct = sector_pct.get(stock.sector, 0.0)
        abs_pct = abs(pct)
        if abs_pct >= 3.0:
            momentum_pts = 20
        elif abs_pct >= 2.0:
            momentum_pts = 15
        elif abs_pct >= 1.0:
            momentum_pts = 10
        else:
            momentum_pts = 3

        regime = analysis.market_regime
        if regime == "trending_up" and pct > 0.5:
            momentum_pts += 10
            stock.setup_type = "momentum"
        elif regime == "trending_down" and pct < -0.5:
            momentum_pts += 10
            # Vary between breakdown and reversal based on magnitude
            stock.setup_type = "breakdown" if abs_pct >= 2.0 else "reversal"
        elif regime == "volatile":
            if abs_pct > 1.5:
                momentum_pts += 8
            # Vary setup based on sector direction and magnitude
            if pct > 1.0:
                stock.setup_type = "breakout"
            elif pct < -1.0:
                stock.setup_type = "breakdown"
            elif pct > 0:
                stock.setup_type = "momentum"
            else:
                stock.setup_type = "reversal"

        if momentum_pts > 5:
            stock.score += momentum_pts
            if not any("sector" in r.lower() for r in stock.reasons):
                stock.reasons.append(f"Momentum: {stock.sector} {pct:+.1f}%")

    # ══════════════════════════════════════════════════════════════════════
    #  NEWS SCANNING — extract stock mentions from headlines
    # ══════════════════════════════════════════════════════════════════════

    def _scan_news(self, data: MarketData) -> dict[str, str]:
        """
        Returns {symbol: headline} for stocks mentioned in news.
        Scans symbols AND company names.
        """
        mentions: dict[str, str] = {}
        for news_item in data.news[:8]:
            text = news_item.headline.upper()
            for stock in _STOCK_DATABASE:
                if stock["symbol"] in mentions:
                    continue  # already matched
                if stock["symbol"] in text or stock["name"].upper() in text:
                    mentions[stock["symbol"]] = news_item.headline
        return mentions

    # ══════════════════════════════════════════════════════════════════════
    #  DIVERSITY FILTER — max 1 per sector, take top N
    # ══════════════════════════════════════════════════════════════════════

    def _diverse_top_n(self, scored: list[_ScoredStock], n: int = 4) -> list[_ScoredStock]:
        """Pick top-N unique sectors from the sorted list."""
        seen: set[str] = set()
        result: list[_ScoredStock] = []
        for s in scored:
            if s.sector not in seen and s.score > 0:
                seen.add(s.sector)
                result.append(s)
            if len(result) >= n:
                break
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  PRICE + LEVELS — lightweight yfinance fetch for SL/target
    # ══════════════════════════════════════════════════════════════════════

    def _fetch_stock_levels(self, symbol: str, setup_type: str) -> tuple[float, float, float]:
        """
        Fetch current price and compute SL/target from recent high/low.
        Returns (price, stop_loss, target). Falls back to (0,0,0) on error.
        """
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            hist = ticker.history(period="1mo")
            if hist.empty:
                return 0.0, 0.0, 0.0

            # Get last valid close (skip NaN rows at end)
            valid_close = hist["Close"].dropna()
            if valid_close.empty:
                return 0.0, 0.0, 0.0

            price = round(float(valid_close.iloc[-1]), 2)
            recent_low = round(float(hist["Low"].dropna().min()), 2)
            recent_high = round(float(hist["High"].dropna().max()), 2)

            # SL below recent low, target above recent high
            # Adjust based on setup type
            if setup_type in ("breakdown", "reversal"):
                # More conservative: SL tighter, target at recent high
                sl = round(recent_low * 0.98, 2)  # 2% below recent low
                target = round(recent_high, 2)
            elif setup_type in ("breakout", "momentum"):
                # SL at recent support, target extended
                sl = round(recent_low, 2)
                target = round(price + (price - recent_low) * 1.5, 2)
            else:
                # Default: SL at recent low, target at recent high
                sl = round(recent_low, 2)
                target = round(recent_high, 2)

            return price, sl, target

        except Exception as e:
            log.warning("Failed to fetch levels for %s: %s", symbol, e)
            return 0.0, 0.0, 0.0

    # ══════════════════════════════════════════════════════════════════════
    #  FALLBACK REASON — should rarely trigger since scoring adds reasons
    # ══════════════════════════════════════════════════════════════════════

    def _fallback_reason(
        self,
        stock: _ScoredStock,
        analysis: MarketAnalysis,
        mode: PipelineMode,
    ) -> str:
        sentiment = analysis.overall_sentiment.value
        if mode == PipelineMode.PRE_MARKET:
            return f"Watch {stock.symbol} — {stock.setup_type} in {stock.sector}. Sentiment: {sentiment}."
        return f"{stock.symbol} — {stock.setup_type} in {stock.sector}. Day sentiment: {sentiment}."
