"""
Content Creation / agents / analysis_agent.py

AnalysisAgent — quantitative market analysis from raw data.

Zero guesswork. Every output is backed by a data point:

  sentiment  ← weighted composite of index %, global %, FII/DII flows, VIX, breadth
  drivers    ← top 3 factors ranked by absolute contribution to sentiment score
  sectors    ← sorted by change_pct with strength classification
  risk_level ← VIX-based with index move confirmation
  regime     ← trending / volatile / sideways from NIFTY + VIX cross-read

Input:  MarketData  (from DataAgent)
Output: MarketAnalysis  (typed Pydantic contract)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.base import BaseContentAgent
from models.contracts import (
    KeyLevel,
    MarketAnalysis,
    MarketData,
    RiskLevel,
    Sentiment,
)

log = logging.getLogger("content_creation.agents.analysis")


# ═══════════════════════════════════════════════════════════════════════════
#  Internal: scored driver — tracks contribution to final sentiment
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _ScoredDriver:
    """A single sentiment factor with its contribution."""
    label: str          # human-readable, e.g. "NIFTY -2.1%"
    weight: float       # how much this factor counted (0-1)
    raw_score: float    # factor score before weighting (-1 to +1)

    @property
    def contribution(self) -> float:
        return self.weight * self.raw_score


# ═══════════════════════════════════════════════════════════════════════════
#  WEIGHTS — tune here, not buried inside methods
# ═══════════════════════════════════════════════════════════════════════════

_W_INDEX   = 0.35   # domestic index movement
_W_GLOBAL  = 0.20   # global market cues
_W_FII_DII = 0.20   # institutional flows
_W_VIX     = 0.15   # fear gauge
_W_BREADTH = 0.10   # advance-decline ratio


class AnalysisAgent(BaseContentAgent):
    name = "AnalysisAgent"
    description = "Quantitative market analysis — data-backed sentiment, drivers, sectors"

    def run(self, *, market_data: MarketData) -> MarketAnalysis:
        # ── 1. Score every factor individually ────────────────────────────
        drivers = self._score_all_factors(market_data)

        # ── 2. Composite sentiment ────────────────────────────────────────
        total = sum(d.contribution for d in drivers)
        score = round(max(-1.0, min(1.0, total)), 3)
        sentiment = self._score_to_sentiment(score)

        # ── 3. Top 3 drivers by absolute contribution ────────────────────
        ranked = sorted(drivers, key=lambda d: abs(d.contribution), reverse=True)
        top_drivers = [d.label for d in ranked[:3] if abs(d.contribution) > 0.005]

        # ── 4. Sector analysis ────────────────────────────────────────────
        sector_themes = self._analyze_sectors(market_data)

        # ── 5. Key levels ─────────────────────────────────────────────────
        key_levels = self._compute_key_levels(market_data)

        # ── 6. Risk + regime ──────────────────────────────────────────────
        risk = self._assess_risk(market_data, score)
        regime = self._detect_regime(market_data)

        # ── 7. Build output ───────────────────────────────────────────────
        all_themes = top_drivers + sector_themes
        # Deduplicate while preserving order
        seen = set()
        themes: list[str] = []
        for t in all_themes:
            key = t.lower()
            if key not in seen:
                seen.add(key)
                themes.append(t)

        analysis = MarketAnalysis(
            overall_sentiment=sentiment,
            sentiment_score=score,
            key_levels=key_levels,
            themes=themes[:6],
            risk_level=risk,
            market_regime=regime,
            outlook_text=self._generate_outlook(sentiment, score, top_drivers, risk, market_data),
            summary=self._generate_summary(sentiment, market_data),
        )

        log.info(
            "Analysis: %s (%.3f) | risk=%s | regime=%s | drivers=%s",
            sentiment.value, score, risk.value, regime, top_drivers,
        )
        return analysis

    # ══════════════════════════════════════════════════════════════════════
    #  FACTOR SCORING — every factor returns a _ScoredDriver
    # ══════════════════════════════════════════════════════════════════════

    def _score_all_factors(self, data: MarketData) -> list[_ScoredDriver]:
        drivers: list[_ScoredDriver] = []
        drivers.extend(self._score_indices(data))
        drivers.extend(self._score_global(data))
        drivers.extend(self._score_fii_dii(data))
        drivers.extend(self._score_vix(data))
        drivers.extend(self._score_breadth(data))
        return drivers

    # ── Index movement (35%) ──────────────────────────────────────────────

    def _score_indices(self, data: MarketData) -> list[_ScoredDriver]:
        non_vix = [i for i in data.indices if "VIX" not in i.name.upper()]
        if not non_vix:
            return []

        # Lead index: NIFTY 50 if available, else first
        nifty = next((i for i in non_vix if "NIFTY 50" in i.name), non_vix[0])
        pct = nifty.change_pct
        # Normalize: ±3% → ±1.0
        raw = max(-1.0, min(1.0, pct / 3.0))

        direction = "up" if pct > 0 else "down"
        label = f"{nifty.name} {direction} {abs(pct):.1f}%"
        return [_ScoredDriver(label=label, weight=_W_INDEX, raw_score=raw)]

    # ── Global cues (20%) ─────────────────────────────────────────────────

    def _score_global(self, data: MarketData) -> list[_ScoredDriver]:
        if not data.global_markets:
            return []

        avg = sum(m.change_pct for m in data.global_markets) / len(data.global_markets)
        raw = max(-1.0, min(1.0, avg / 2.0))

        # Find the biggest mover for the label
        biggest = max(data.global_markets, key=lambda m: abs(m.change_pct))
        if abs(avg) < 0.1:
            label = "Global markets flat"
        elif avg > 0:
            label = f"Global positive ({biggest.name} +{biggest.change_pct:.1f}%)"
        else:
            label = f"Global weak ({biggest.name} {biggest.change_pct:.1f}%)"

        return [_ScoredDriver(label=label, weight=_W_GLOBAL, raw_score=raw)]

    # ── FII/DII flows (20%) ──────────────────────────────────────────────

    def _score_fii_dii(self, data: MarketData) -> list[_ScoredDriver]:
        fii = data.fii_dii.fii_net
        dii = data.fii_dii.dii_net

        # No data available (both zero and no trend string) — skip
        if fii == 0 and dii == 0 and not data.fii_dii.fii_trend:
            return []

        # FII: ±2000 Cr → ±1.0
        fii_raw = max(-1.0, min(1.0, fii / 2000))
        # DII dampens or amplifies: if DII buys while FII sells, net effect is milder
        dii_raw = max(-1.0, min(1.0, dii / 2000))
        combined = (fii_raw * 0.6 + dii_raw * 0.4)

        parts = []
        if fii != 0:
            parts.append(f"FII {'buying' if fii > 0 else 'selling'} ₹{abs(fii):,.0f}Cr")
        if dii != 0:
            parts.append(f"DII {'buying' if dii > 0 else 'selling'} ₹{abs(dii):,.0f}Cr")
        label = " | ".join(parts) if parts else "Institutional flows neutral"

        return [_ScoredDriver(label=label, weight=_W_FII_DII, raw_score=combined)]

    # ── VIX / fear gauge (15%) ────────────────────────────────────────────

    def _score_vix(self, data: MarketData) -> list[_ScoredDriver]:
        if not data.vix:
            return []

        vix = data.vix
        # VIX scoring: 12=calm → +0.5, 15=normal → 0, 20=fear → -0.5, 30=panic → -1.0
        if vix <= 12:
            raw = 0.5
        elif vix <= 15:
            raw = 0.2
        elif vix <= 18:
            raw = 0.0
        elif vix <= 22:
            raw = -0.4
        elif vix <= 28:
            raw = -0.7
        else:
            raw = -1.0

        label = f"VIX at {vix:.1f}"
        if vix > 22:
            label += " - elevated fear"
        elif vix < 13:
            label += " - low volatility"

        return [_ScoredDriver(label=label, weight=_W_VIX, raw_score=raw)]

    # ── Advance-Decline breadth (10%) ─────────────────────────────────────

    def _score_breadth(self, data: MarketData) -> list[_ScoredDriver]:
        if not data.advance_decline:
            return []

        parts = data.advance_decline.split(":")
        if len(parts) != 2:
            return []
        try:
            adv, dec = int(parts[0]), int(parts[1])
        except ValueError:
            return []

        total = adv + dec
        if total == 0:
            return []

        ratio = adv / total  # 0.0 to 1.0
        # Map: 0.3 → -1.0, 0.5 → 0.0, 0.7 → +1.0
        raw = max(-1.0, min(1.0, (ratio - 0.5) * 5.0))

        label = f"Breadth {adv}:{dec} ({'broad buying' if ratio > 0.55 else 'broad selling' if ratio < 0.45 else 'mixed'})"
        return [_ScoredDriver(label=label, weight=_W_BREADTH, raw_score=raw)]

    # ══════════════════════════════════════════════════════════════════════
    #  SENTIMENT CLASSIFICATION
    # ══════════════════════════════════════════════════════════════════════

    def _score_to_sentiment(self, score: float) -> Sentiment:
        if score > 0.25:
            return Sentiment.BULLISH
        if score < -0.25:
            return Sentiment.BEARISH
        if abs(score) < 0.08:
            return Sentiment.NEUTRAL
        return Sentiment.MIXED

    # ══════════════════════════════════════════════════════════════════════
    #  SECTOR ANALYSIS — ranked by move size, classified
    # ══════════════════════════════════════════════════════════════════════

    def _analyze_sectors(self, data: MarketData) -> list[str]:
        if not data.sectors:
            return []

        sorted_sec = sorted(data.sectors, key=lambda s: s.change_pct, reverse=True)
        themes: list[str] = []

        best = sorted_sec[0]
        worst = sorted_sec[-1]

        # Only mention sectors with meaningful moves (> 0.5%)
        if best.change_pct > 0.5:
            themes.append(f"{best.name} leading (+{best.change_pct:.1f}%)")
        if worst.change_pct < -0.5:
            themes.append(f"{worst.name} weakest ({worst.change_pct:.1f}%)")

        # Broad-based move?
        greens = sum(1 for s in data.sectors if s.change_pct > 0.2)
        reds = sum(1 for s in data.sectors if s.change_pct < -0.2)
        total = len(data.sectors)
        if total > 0:
            if greens / total > 0.7:
                themes.append("Broad-based sectoral rally")
            elif reds / total > 0.7:
                themes.append("Broad-based sectoral selloff")

        return themes

    # ══════════════════════════════════════════════════════════════════════
    #  KEY LEVELS — pivot ± ATR-style bands
    # ══════════════════════════════════════════════════════════════════════

    def _compute_key_levels(self, data: MarketData) -> list[KeyLevel]:
        levels: list[KeyLevel] = []
        for idx in data.indices:
            if "VIX" in idx.name.upper() or idx.value <= 0:
                continue
            val = idx.value
            move = abs(idx.change) if idx.change else val * 0.005
            # Use today's move as a volatility proxy for support/resistance bands
            # Minimum band = 1% of value for meaningful S/R
            band = max(move, val * 0.01)
            levels.append(KeyLevel(
                index=idx.name,
                support=round(val - band, 2),
                resistance=round(val + band, 2),
                pivot=round(val, 2),
            ))
        return levels

    # ══════════════════════════════════════════════════════════════════════
    #  RISK ASSESSMENT — VIX + index move cross-check
    # ══════════════════════════════════════════════════════════════════════

    def _assess_risk(self, data: MarketData, score: float) -> RiskLevel:
        vix = data.vix or 15.0
        nifty = next((i for i in data.indices if "NIFTY 50" in i.name), None)
        nifty_move = abs(nifty.change_pct) if nifty else 0

        # Multi-factor risk
        if vix > 25 or nifty_move > 2.5:
            return RiskLevel.EXTREME
        if vix > 20 or nifty_move > 1.5:
            return RiskLevel.HIGH
        if vix > 15 or nifty_move > 0.8:
            return RiskLevel.MODERATE
        return RiskLevel.LOW

    # ══════════════════════════════════════════════════════════════════════
    #  MARKET REGIME
    # ══════════════════════════════════════════════════════════════════════

    def _detect_regime(self, data: MarketData) -> str:
        nifty = next((i for i in data.indices if "NIFTY 50" in i.name), None)
        if not nifty:
            return "unknown"

        pct = nifty.change_pct
        vix = data.vix or 15.0

        # Strong directional move + high VIX → trending
        if pct > 0.8 and vix < 20:
            return "trending_up"
        if pct < -0.8 and vix < 20:
            return "trending_down"
        if vix > 20:
            return "volatile"
        return "sideways"

    # ══════════════════════════════════════════════════════════════════════
    #  TEXT GENERATION — concise, data-anchored
    # ══════════════════════════════════════════════════════════════════════

    def _generate_outlook(
        self,
        sentiment: Sentiment,
        score: float,
        drivers: list[str],
        risk: RiskLevel,
        data: MarketData,
    ) -> str:
        label_map = {
            Sentiment.BULLISH:  "Bullish bias",
            Sentiment.BEARISH:  "Bearish pressure",
            Sentiment.NEUTRAL:  "Neutral stance",
            Sentiment.MIXED:    "Mixed signals",
        }
        base = label_map.get(sentiment, "Unclear")
        base += f" (score {score:+.2f})"

        if drivers:
            base += ". Driven by: " + "; ".join(drivers[:3])

        base += f". Risk: {risk.value}."
        return base

    def _generate_summary(self, sentiment: Sentiment, data: MarketData) -> str:
        parts: list[str] = []
        for idx in data.indices:
            if "VIX" not in idx.name.upper():
                arrow = "▲" if idx.change_pct >= 0 else "▼"
                parts.append(f"{idx.name}: {idx.value:,.0f} ({arrow}{abs(idx.change_pct):.1f}%)")

        summary = " | ".join(parts)
        summary += f" | {sentiment.value.upper()}"
        return summary
