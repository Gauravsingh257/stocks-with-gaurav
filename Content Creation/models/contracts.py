"""
Content Creation / models / contracts.py

Pydantic v2 models defining the JSON contracts passed between agents.
Every agent produces and consumes typed, validated data — no raw dicts.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────

class PipelineMode(str, Enum):
    PRE_MARKET = "pre_market"
    POST_MARKET = "post_market"


class Sentiment(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class SlideType(str, Enum):
    COVER = "cover"
    DATA = "data"
    TABLE = "table"
    STOCK_CARD = "stock_card"
    OUTLOOK = "outlook"
    CTA = "cta"
    DISCLAIMER = "disclaimer"
    NEWS = "news"
    DRIVERS_SENTIMENT = "drivers_sentiment"
    INSIGHT_CTA = "insight_cta"


class RiskLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    EXTREME = "extreme"


# ── Data Agent Output ─────────────────────────────────────────────────────

class IndexData(BaseModel):
    name: str
    value: float
    change: float = 0.0
    change_pct: float = 0.0
    trend: str = ""  # "up" | "down" | "flat"


class GlobalMarket(BaseModel):
    name: str
    value: float = 0.0
    change_pct: float = 0.0


class NewsItem(BaseModel):
    headline: str
    source: str = ""
    url: str = ""
    image_url: str = ""
    sentiment: Sentiment = Sentiment.NEUTRAL
    impact: str = "low"  # low | medium | high


class FIIDIIData(BaseModel):
    fii_net: float = 0.0  # crores
    dii_net: float = 0.0
    fii_trend: str = ""  # "buying" | "selling"
    dii_trend: str = ""


class SectorData(BaseModel):
    name: str
    change_pct: float = 0.0
    top_gainer: str = ""
    top_loser: str = ""


class MarketData(BaseModel):
    """Output of DataAgent — raw market data."""
    timestamp: datetime = Field(default_factory=datetime.now)
    mode: PipelineMode = PipelineMode.PRE_MARKET
    indices: list[IndexData] = Field(default_factory=list)
    global_markets: list[GlobalMarket] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list, max_length=10)
    fii_dii: FIIDIIData = Field(default_factory=FIIDIIData)
    sectors: list[SectorData] = Field(default_factory=list)
    vix: float = 0.0
    advance_decline: str = ""  # e.g. "1200:800"
    raw_extras: dict = Field(default_factory=dict)


# ── Analysis Agent Output ─────────────────────────────────────────────────

class KeyLevel(BaseModel):
    index: str
    support: float
    resistance: float
    pivot: float = 0.0


class MarketAnalysis(BaseModel):
    """Output of AnalysisAgent — derived insights."""
    timestamp: datetime = Field(default_factory=datetime.now)
    overall_sentiment: Sentiment = Sentiment.NEUTRAL
    sentiment_score: float = 0.0  # -1.0 (extreme bear) to +1.0 (extreme bull)
    key_levels: list[KeyLevel] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)  # ["FII selling", "IT rally"]
    risk_level: RiskLevel = RiskLevel.MODERATE
    outlook_text: str = ""
    summary: str = ""
    market_regime: str = ""  # "trending_up" | "trending_down" | "sideways" | "volatile"


# ── Stock Picker Agent Output ────────────────────────────────────────────

class StockPick(BaseModel):
    symbol: str
    name: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    rationale: str = ""
    setup_type: str = ""  # "breakout" | "reversal" | "momentum" | "value"
    sector: str = ""
    target: float = 0.0
    stop_loss: float = 0.0


class StockPicks(BaseModel):
    """Output of StockPickerAgent — top 3-4 stocks."""
    timestamp: datetime = Field(default_factory=datetime.now)
    picks: list[StockPick] = Field(default_factory=list, max_length=5)
    selection_criteria: str = ""
    market_context: str = ""


# ── Content Agent Output ─────────────────────────────────────────────────

class SlideSection(BaseModel):
    label: str
    value: str
    color: str = ""  # hex color hint for design agent


class Slide(BaseModel):
    slide_number: int
    slide_type: SlideType
    headline: str
    subheadline: str = ""
    body: str = ""
    sections: list[SlideSection] = Field(default_factory=list)
    data_points: list[dict] = Field(default_factory=list)
    footer: str = ""
    accent_color: str = ""  # hex


class CarouselContent(BaseModel):
    """Output of ContentAgent — slide text + structure."""
    content_type: str = "pre_market_carousel"
    date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    mode: PipelineMode = PipelineMode.PRE_MARKET
    slides: list[Slide] = Field(default_factory=list)
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    total_slides: int = 0


# ── Design Agent Output ──────────────────────────────────────────────────

class DesignOutput(BaseModel):
    """Output of DesignAgent — rendered image paths."""
    slide_paths: list[str] = Field(default_factory=list)
    thumbnail_path: str = ""
    total_slides: int = 0
    resolution: str = "1080x1080"


# ── QA Agent Output ──────────────────────────────────────────────────────

class QAIssue(BaseModel):
    slide_number: int = 0
    severity: str = "warning"  # info | warning | error
    issue: str = ""
    suggestion: str = ""


class QAResult(BaseModel):
    """Output of QAAgent — validation report."""
    passed: bool = True
    score: float = 100.0  # 0-100 quality score
    issues: list[QAIssue] = Field(default_factory=list)
    checks_performed: list[str] = Field(default_factory=list)


# ── Publisher Agent Output ───────────────────────────────────────────────

class PublishResult(BaseModel):
    """Output of PublisherAgent."""
    success: bool = False
    platform: str = "instagram"
    post_id: str = ""
    post_url: str = ""
    published_at: Optional[datetime] = None
    error: str = ""


# ── Logger / Pipeline Run ────────────────────────────────────────────────

class AgentTiming(BaseModel):
    agent_name: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    status: str = "OK"
    error: str = ""


class PipelineRun(BaseModel):
    """Full pipeline execution record."""
    run_id: str
    mode: PipelineMode
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    status: str = "OK"  # OK | PARTIAL | FAILED
    agent_timings: list[AgentTiming] = Field(default_factory=list)
    slides_generated: int = 0
    published: bool = False
    error: str = ""
