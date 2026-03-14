# Cursor IDE — Setup & Optimization Checklist

## Environment Setup

- [x] Python 3.11 virtual environment (`.venv/`)
- [x] `requirements.txt` with all trading libraries
- [x] `pyproject.toml` for ruff, pytest, mypy configuration
- [x] `.env.example` template for environment variables
- [x] `config/settings.py` — Pydantic-based centralized configuration

### Libraries Installed
- [x] **ccxt** — Multi-exchange cryptocurrency trading
- [x] **kiteconnect** — Zerodha broker API
- [x] **pandas / numpy / scipy** — Data analysis
- [x] **ta** — Technical analysis indicators
- [x] **backtrader** — Backtesting framework
- [x] **fastapi / uvicorn** — API server
- [x] **python-dotenv / pydantic-settings** — Environment management
- [x] **scikit-learn / xgboost / optuna** — Machine learning
- [x] **yfinance** — Yahoo Finance data
- [x] **loguru** — Advanced logging

## Project Structure

- [x] `strategies/` — Modular strategy definitions with `BaseStrategy` interface
- [x] `signals/` — Signal pipeline with multi-strategy aggregation
- [x] `data/` — Data ingestion pipeline with caching
- [x] `config/` — Centralized settings
- [x] `scripts/` — Automation scripts
- [x] `logs/` — Structured log output
- [x] `reports/` — Performance report output

### Pre-existing (preserved)
- [x] `agents/` — AI trading agents (Risk, Trade Manager, Pre/Post Market)
- [x] `ai_learning/` — ML-based strategy learning and optimization
- [x] `backtest/` — Candle-by-candle backtesting engine
- [x] `engine/` — Live trading engine with SMC detection
- [x] `dashboard/` — Full-stack monitoring dashboard
- [x] `smc_trading_engine/` — Core SMC detection library
- [x] `tests/` — Test suite

## Automation Scripts

- [x] `scripts/run_backtest.py` — CLI backtest runner with args
- [x] `scripts/generate_signals.py` — Signal scanner and exporter
- [x] `scripts/evaluate_performance.py` — Performance metrics and reporting
- [x] `scripts/trade_logger.py` — Dual-format trade logging (CSV + SQLite)
- [x] `scripts/start_dev.ps1` — Full-stack development launcher

## Cursor AI Configuration

- [x] `.cursor/rules/trading-system.mdc` — System architecture context
- [x] `.cursor/rules/cursor-ai-config.mdc` — Claude Opus optimization
- [x] `.cursor/rules/python-best-practices.mdc` — Python coding standards
- [x] `.cursor/rules/file-patterns.mdc` — File-specific change guidelines

### Cursor Features to Enable (in Cursor Settings UI)
- [ ] **Cursor Tab** → Enable for Python (AI autocomplete)
- [ ] **Codebase Indexing** → Enable (Settings > Features > Codebase Indexing)
- [ ] **Docs** → Add custom docs for kiteconnect, ccxt, backtrader
- [ ] **Agent Mode** → Enable for multi-file edits
- [ ] **Long Context** → Enable for large file analysis
- [ ] **YOLO Mode** → Keep disabled (safety for trading code)

## Developer Productivity

### Debugging
- [x] `.vscode/launch.json` — 7 debug configurations
  - Dashboard Backend (FastAPI with reload)
  - Run Backtest
  - Run Signal Scanner
  - Live Trading Engine
  - AI Learning Pipeline
  - Run Tests
  - Debug Current File

### Tasks
- [x] `.vscode/tasks.json` — Quick-run tasks
  - Run Backtest
  - Generate Signals
  - Evaluate Performance
  - Run Tests
  - Start Dashboard (Full Stack)
  - Lint & Format

### Testing
- [x] `tests/conftest.py` — Shared fixtures (sample candles, signals)
- [x] `tests/test_strategies.py` — Strategy framework tests
- [x] `tests/test_signal_pipeline.py` — Pipeline tests
- [x] `tests/test_config_settings.py` — Configuration tests
- [x] pytest configured in `pyproject.toml` with markers

### Logging
- [x] `utils/logging_config.py` — Rotating file + console logging
  - `logs/app.log` — General (10MB rotating)
  - `logs/trading.log` — Trade events (daily rotation)
  - `logs/errors.log` — Errors only (5MB rotating)
  - `logs/debug.log` — Debug level (when enabled)

### Git
- [x] `.gitignore` — Comprehensive exclusions
- [x] Git repository initialized

### Code Quality
- [x] Ruff linter/formatter configured
- [x] MyPy type checking configured
- [x] Editor format-on-save enabled

## Post-Setup Actions

1. **Copy `.env.example` to `.env`** and fill in your actual credentials:
   ```
   copy .env.example .env
   ```

2. **Select Python interpreter in Cursor**:
   - Press `Ctrl+Shift+P` → "Python: Select Interpreter"
   - Choose `.venv\Scripts\python.exe`

3. **Enable Codebase Indexing**:
   - Cursor Settings → Features → Codebase Indexing → ON

4. **Install Cursor extensions**:
   - Python (ms-python.python)
   - Pylance (ms-python.vscode-pylance)
   - Ruff (charliermarsh.ruff)
   - GitLens (eamodio.gitlens)

5. **Run initial test suite**:
   ```
   .venv\Scripts\python.exe -m pytest tests/ -v
   ```

6. **Verify FastAPI server starts**:
   ```
   .venv\Scripts\python.exe -m uvicorn dashboard.backend.main:app --port 8000
   ```

## Architecture Improvement Recommendations

### Modular Architecture
- Strategy Pattern: All strategies implement `BaseStrategy` interface
- Pipeline Pattern: `SignalPipeline` aggregates multiple strategies
- Agent Pattern: `BaseAgent` with standardized lifecycle
- Repository Pattern: `DataIngestion` abstracts data sources

### Scalable AI Agent Design
- Each agent is stateless (reads state, produces results)
- Agent actions go through approval queue (human-in-the-loop)
- Scheduled execution via APScheduler with market hours guard
- Findings/metrics are structured (not free-text)

### Clean Code Practices
- Type hints on all public interfaces
- Pydantic models for configuration validation
- Structured logging (no print statements)
- Environment-based configuration (no hardcoded secrets)
- Comprehensive .gitignore for trading artifacts
- Test fixtures with realistic synthetic data

### Future Enhancements
- [ ] Add pre-commit hooks (ruff + mypy)
- [ ] Add GitHub Actions CI pipeline
- [ ] Implement strategy backtesting CLI with parameter grid search
- [ ] Add WebSocket-based real-time signal dashboard
- [ ] Implement A/B testing framework for strategy variants
- [ ] Add Docker containerization for deployment
- [ ] Implement feature store for ML pipeline
- [ ] Add Prometheus metrics for production monitoring
