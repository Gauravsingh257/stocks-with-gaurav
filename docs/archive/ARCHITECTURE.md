# Trading Algo — System Architecture

## Directory Structure

```
Trading Algo/
├── .cursor/rules/          # Cursor AI rules (repo context, coding standards)
├── .vscode/                # IDE configuration (debug, tasks, settings)
├── .venv/                  # Python virtual environment
│
├── agents/                 # Autonomous AI Trading Agents
│   ├── base.py             # BaseAgent abstract class + AgentResult
│   ├── runner.py           # APScheduler-based agent orchestrator
│   ├── risk_sentinel.py    # Real-time risk monitoring (every 1 min)
│   ├── trade_manager.py    # Trade lifecycle management (every 5 min)
│   ├── pre_market.py       # Pre-market analysis (08:45 daily)
│   ├── post_market.py      # Post-market review (15:30 daily)
│   └── oi_intelligence_agent.py  # Open Interest analysis
│
├── ai_learning/            # Machine Learning & Pattern Recognition
│   ├── agents/             # Strategy generation & optimization agents
│   ├── data/               # Feature extraction, schemas, trade store
│   ├── learning/           # Pattern clustering, unsupervised learning
│   ├── optimization/       # Backtester integration, Monte Carlo
│   ├── strategy/           # Rule engine for generated strategies
│   └── pipeline.py         # End-to-end ML training pipeline
│
├── backtest/               # Backtesting Framework
│   ├── engine.py           # Candle-by-candle backtest engine
│   ├── runner.py           # Multi-symbol backtest orchestrator
│   ├── data_fetcher.py     # Historical data retrieval
│   ├── data_store.py       # Local data storage
│   └── cost_model.py       # Transaction cost modeling
│
├── config/                 # Centralized Configuration
│   └── settings.py         # Pydantic-based settings (loads .env)
│
├── dashboard/              # Full-Stack Monitoring Dashboard
│   ├── backend/            # FastAPI + WebSocket server
│   │   ├── main.py         # App entry point
│   │   ├── routes/         # REST API endpoints
│   │   ├── db/             # Database schema & queries
│   │   └── websocket.py    # Real-time data broadcast
│   └── frontend/           # Next.js + TypeScript UI
│       ├── app/            # Pages (agents, analytics, charts, etc.)
│       ├── components/     # Shared UI components
│       └── lib/            # API client, WebSocket hooks
│
├── data/                   # Data Pipeline
│   ├── ingestion.py        # Multi-source data fetcher
│   ├── raw/                # Raw downloaded data
│   ├── processed/          # Cleaned & resampled data
│   └── cache/              # Runtime data cache (parquet)
│
├── engine/                 # Live Trading Engine
│   ├── config.py           # All constants, flags, mutable state
│   ├── indicators.py       # EMA, ATR, ADX calculations
│   ├── displacement_detector.py
│   ├── liquidity_engine.py
│   ├── market_state_engine.py
│   ├── oi_sentiment.py     # Open Interest sentiment scoring
│   ├── options.py          # Options signal engine
│   └── smc_zone_tap.py     # SMC zone detection & tap logic
│
├── scripts/                # Automation & CLI Tools
│   ├── run_backtest.py     # CLI backtest runner
│   ├── generate_signals.py # Signal scan & export
│   ├── evaluate_performance.py  # Performance reporting
│   ├── trade_logger.py     # Structured trade logging
│   └── start_dev.ps1       # Full-stack dev launcher
│
├── signals/                # Signal Generation Pipeline
│   ├── pipeline.py         # Multi-strategy aggregation
│   └── output/             # Generated signal files (gitignored)
│
├── smc_trading_engine/     # Core SMC Detection Library
│   ├── smc/                # BOS, CHoCH, FVG, Order Blocks, Liquidity
│   ├── strategy/           # Entry models, risk mgmt, signal generator
│   ├── regime/             # Market regime classification
│   ├── execution/          # Live & paper trading execution
│   ├── data/               # Data fetching & resampling
│   └── backtest/           # SMC-specific backtest engine
│
├── strategies/             # Modular Strategy Definitions
│   └── base_strategy.py    # Abstract strategy interface
│
├── tests/                  # Test Suite
│   ├── conftest.py         # Shared fixtures
│   ├── test_backtest.py    # Backtest engine tests
│   ├── test_strategies.py  # Strategy framework tests
│   ├── test_signal_pipeline.py  # Pipeline tests
│   └── test_config_settings.py  # Config tests
│
├── utils/                  # Shared Utilities
│   ├── logging_config.py   # Structured logging setup
│   └── state_db.py         # State persistence
│
├── logs/                   # Log files (gitignored)
├── reports/                # Performance reports (gitignored)
│
├── .env.example            # Environment variable template
├── .gitignore              # Git exclusions
├── pyproject.toml          # Python project config (ruff, pytest, mypy)
├── requirements.txt        # Python dependencies
└── ARCHITECTURE.md         # This file
```

## Data Flow

```
Market Data (Kite/yfinance)
    │
    ▼
Data Ingestion (data/ingestion.py)
    │
    ├─► Cache (data/cache/*.parquet)
    │
    ▼
SMC Detection (smc_detectors.py / smc_trading_engine/smc/)
    │
    ├─► Order Blocks, FVG, BOS, CHoCH, Liquidity Sweeps
    │
    ▼
Signal Generation (signals/pipeline.py)
    │
    ├─► Confluence Scoring (5-10 point scale)
    ├─► Regime Filtering
    ├─► Time-of-Day Filtering
    │
    ▼
Risk Management (engine/config.py circuit breakers)
    │
    ├─► Position Sizing
    ├─► Daily Loss Limits (-3R)
    ├─► Concurrent Trade Caps
    │
    ▼
Execution (Trade Manager Agent → Kite Connect)
    │
    ├─► Human Approval Queue
    ├─► Telegram Alerts
    │
    ▼
Trade Logging (scripts/trade_logger.py)
    │
    ├─► CSV Ledger
    ├─► SQLite DB
    ├─► Dashboard WebSocket
    │
    ▼
Performance Analysis (scripts/evaluate_performance.py)
    │
    ├─► Win Rate, Profit Factor, Sharpe
    ├─► Drawdown Analysis
    └─► Per-Setup Breakdown
```

## Agent Architecture

Each agent extends `BaseAgent` and follows this contract:
1. Reads state via `snapshot()` (read-only engine state)
2. Analyzes data and adds findings to `AgentResult`
3. Queues actions requiring human approval
4. Never directly executes trades or mutates engine state
5. Logs all runs to `agent_logs` table

## Key Design Decisions

- **Morning-only stock trading** (9:15–12:00): Backtest evidence shows afternoon sessions are net negative
- **SMC confluence minimum 5/10**: Filters out low-quality setups
- **SL checked before TP**: Conservative backtest assumption (worst-case intra-bar)
- **Transaction cost modeling**: All backtests include realistic slippage + brokerage
- **Circuit breakers**: Automatic halt at -3R daily or 3 consecutive losses
