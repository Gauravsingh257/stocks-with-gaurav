# Content Creation — Multi-Agent Instagram Carousel System

Automated multi-agent pipeline for generating Instagram carousel posts
(pre-market & post-market reports) for Indian stock market updates.

Brand: **StocksWithGaurav**

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR                                 │
│                   (pipeline/orchestrator.py)                        │
│  Drives the full pipeline: PRE_MARKET (08:00) / POST_MARKET (16:00)│
└──────┬──────┬──────┬──────┬──────┬──────┬──────┬────────────────────┘
       │      │      │      │      │      │      │
       ▼      │      │      │      │      │      │
  ┌─────────┐ │      │      │      │      │      │
  │  DATA   │ │      │      │      │      │      │
  │  AGENT  │─┘      │      │      │      │      │
  │ (fetch) │        │      │      │      │      │
  └────┬────┘        │      │      │      │      │
       │ MarketData  │      │      │      │      │
       ▼             │      │      │      │      │
  ┌──────────┐       │      │      │      │      │
  │ ANALYSIS │       │      │      │      │      │
  │  AGENT   │───────┘      │      │      │      │
  │(insights)│              │      │      │      │
  └────┬─────┘              │      │      │      │
       │ MarketAnalysis     │      │      │      │
       ▼                    │      │      │      │
  ┌────────────┐            │      │      │      │
  │STOCK PICKER│            │      │      │      │
  │   AGENT    │────────────┘      │      │      │
  │ (top 3-4)  │                   │      │      │
  └────┬───────┘                   │      │      │
       │ StockPicks                │      │      │
       ▼                           │      │      │
  ┌──────────┐                     │      │      │
  │ CONTENT  │                     │      │      │
  │  AGENT   │─────────────────────┘      │      │
  │(carousel)│                            │      │
  └────┬─────┘                            │      │
       │ CarouselContent                  │      │
       ▼                                  │      │
  ┌──────────┐                            │      │
  │  DESIGN  │                            │      │
  │  AGENT   │────────────────────────────┘      │
  │ (images) │                                   │
  └────┬─────┘                                   │
       │ DesignOutput                            │
       ▼                                         │
  ┌──────────┐                                   │
  │    QA    │                                   │
  │  AGENT   │───────────────────────────────────┘
  │(validate)│
  └────┬─────┘
       │ QAResult (pass/fail + fixes)
       ▼
  ┌──────────┐      ┌──────────┐
  │PUBLISHER │      │  LOGGER  │
  │  AGENT   │─────▶│  AGENT   │
  │(post IG) │      │ (store)  │
  └──────────┘      └──────────┘
```

---

## Data Flow Between Agents

```
Orchestrator
  │
  ├─▶ DataAgent.run(mode=PRE_MARKET)
  │     └─▶ Returns: MarketData (indices, global, news, FII/DII, sector)
  │
  ├─▶ AnalysisAgent.run(market_data)
  │     └─▶ Returns: MarketAnalysis (sentiment, key_levels, themes, outlook)
  │
  ├─▶ StockPickerAgent.run(market_data, analysis)
  │     └─▶ Returns: StockPicks (3-4 stocks with rationale)
  │
  ├─▶ ContentAgent.run(mode, analysis, picks)
  │     └─▶ Returns: CarouselContent (5-7 slides with text)
  │
  ├─▶ DesignAgent.run(carousel_content)
  │     └─▶ Returns: DesignOutput (PNG paths for each slide)
  │
  ├─▶ QAAgent.run(carousel_content, design_output)
  │     └─▶ Returns: QAResult (passed, issues, fixes)
  │
  ├─▶ PublisherAgent.run(design_output, carousel_content)  # if QA passed
  │     └─▶ Returns: PublishResult (post_id, status)
  │
  └─▶ LoggerAgent.log(pipeline_run)
        └─▶ Returns: LogEntry (run_id, metrics, status)
```

---

## Folder Structure

```
Content Creation/
├── README.md                    # This file
├── main.py                      # Entry point — run pre/post market pipeline
├── config/
│   ├── __init__.py
│   └── settings.py              # All env vars, API keys, constants
├── models/
│   ├── __init__.py
│   └── contracts.py             # Pydantic models (JSON contracts between agents)
├── agents/
│   ├── __init__.py
│   ├── base.py                  # BaseContentAgent ABC
│   ├── data_agent.py            # Fetch market data from APIs
│   ├── analysis_agent.py        # Derive insights from raw data
│   ├── stock_picker_agent.py    # Select top 3-4 stocks
│   ├── content_agent.py         # Generate carousel slide text
│   ├── design_agent.py          # Render slides to 1080x1080 PNGs
│   ├── qa_agent.py              # Validate content quality
│   ├── publisher_agent.py       # Post to Instagram via Graph API
│   └── logger_agent.py          # Persist logs and metrics
├── pipeline/
│   ├── __init__.py
│   └── orchestrator.py          # Sequential pipeline runner
├── templates/
│   ├── slide_cover.html         # Jinja2 HTML template — cover slide
│   ├── slide_data.html          # Jinja2 HTML template — data slide
│   ├── slide_stock.html         # Jinja2 HTML template — stock card
│   ├── slide_outlook.html       # Jinja2 HTML template — outlook
│   └── slide_cta.html           # Jinja2 HTML template — CTA/disclaimer
├── output/                      # Generated images (gitignored)
├── logs/                        # Pipeline run logs (gitignored)
└── tests/
    ├── __init__.py
    └── test_pipeline.py         # Smoke tests
```

---

## JSON Contracts Between Agents

See `models/contracts.py` for full Pydantic schemas. Summary:

| Contract          | Producer       | Consumer           | Key Fields                                            |
|-------------------|----------------|--------------------|-------------------------------------------------------|
| MarketData        | DataAgent      | AnalysisAgent      | indices[], global_markets[], news[], fii_dii, sectors |
| MarketAnalysis    | AnalysisAgent  | StockPicker,Content| sentiment, key_levels, themes[], outlook, risk_level  |
| StockPicks        | StockPicker    | ContentAgent       | picks[] (symbol, change%, rationale, setup_type)      |
| CarouselContent   | ContentAgent   | DesignAgent, QA    | slides[] (headline, body, data_points), caption       |
| DesignOutput      | DesignAgent    | QA, Publisher      | slide_paths[], thumbnail_path                         |
| QAResult          | QAAgent        | Orchestrator       | passed, issues[], suggested_fixes[]                   |
| PublishResult     | Publisher      | Logger             | post_id, platform, status, published_at               |
| PipelineRun       | Orchestrator   | Logger             | run_id, mode, duration, agent_timings, status         |

---

## Tech Stack

| Component        | Technology                                        |
|------------------|---------------------------------------------------|
| Language         | Python 3.11+                                      |
| Data Models      | Pydantic v2 (JSON contracts)                      |
| HTTP Client      | httpx (async-ready, timeout-safe)                 |
| HTML → PNG       | Playwright (headless Chromium, 1080×1080)          |
| HTML Templates   | Jinja2                                            |
| Image Post-proc  | Pillow (optional compositing)                     |
| Instagram API    | Meta Graph API (Business Account)                 |
| Scheduling       | APScheduler / cron                                |
| LLM (optional)   | OpenAI GPT-4o-mini (content enrichment)           |
| Logging          | Python logging + JSON lines                       |
| Config           | python-dotenv + Pydantic settings                 |
| Testing          | pytest                                            |

---

## Quick Start

```bash
# 1. Install dependencies
pip install pydantic httpx jinja2 playwright pillow python-dotenv apscheduler

# 2. Install Playwright browser
playwright install chromium

# 3. Set environment variables (see config/settings.py)
cp .env.example .env

# 4. Run pre-market pipeline
python -m "Content Creation.main" --mode pre_market

# 5. Run post-market pipeline
python -m "Content Creation.main" --mode post_market
```
