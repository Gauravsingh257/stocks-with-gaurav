# AI Trading Style Learning System — User Guide
## Architecture Overview

**Three-Agent Pipeline:**

| Agent | Name | Purpose | Input → Output |
|-------|------|---------|----------------|
| **Agent 1** | **PRISM** | Refracts raw trade history into distinct strategy clusters | Manual trades → `TradingStyleProfile` |
| **Agent 2** | **FORGE** | Forges learned patterns into executable algorithmic rules | `TradingStyleProfile` → `List[StrategyRule]` |
| **Agent 3** | **SHIELD** | Stress-tests and validates strategies against ruin/curve-fitting | `StrategyRule` + candles → `OptimizationResult` |

After optimization, the **Live Scanner** deploys learned strategies to generate real-time signals.

---

## Quick Start

### Step 1: Prepare Your Trades

Create a CSV file with your manual trades. See `ai_learning/sample_trades.csv` for the format:

```csv
trade_id,symbol,timeframe,direction,entry_price,sl_price,target_price,result,pnl_r,setup_type,session,notes,date,chart_image
T001,NSE:RELIANCE,15min,LONG,2450.50,2438.00,2475.00,WIN,1.96,OB_BOS,INDIA_MID,Order block bounce after BOS on 1H,2026-01-15,
```

**Minimum 15 trades** required for learning (50-100 recommended).

**Field descriptions:**

| Field | Required | Description |
|-------|----------|-------------|
| `trade_id` | Yes | Unique identifier (e.g., T001) |
| `symbol` | Yes | NSE symbol with prefix (e.g., NSE:RELIANCE) |
| `timeframe` | Yes | Entry timeframe (5min, 15min, 1H) |
| `direction` | Yes | LONG or SHORT |
| `entry_price` | Yes | Entry price |
| `sl_price` | Yes | Stop loss price |
| `target_price` | Yes | Target price |
| `result` | Yes | WIN or LOSS |
| `pnl_r` | Yes | P&L in R-multiples (e.g., 2.0 for 2R win, -1.0 for 1R loss) |
| `setup_type` | No | Your setup label (OB_BOS, CHoCH_FVG, etc.) |
| `session` | No | Trading session (KILLZONE_AM, INDIA_MID, etc.) |
| `notes` | No | Trade notes and reasoning |
| `date` | Yes | Trade date (YYYY-MM-DD) |
| `chart_image` | No | Path to chart screenshot |

### Step 2: Ingest Trades

```bash
# From your custom CSV
python -m ai_learning.cli ingest --source csv --file my_trades.csv

# Or from existing trade ledger
python -m ai_learning.cli ingest --source ledger
```

### Step 3: Run Full Pipeline

```bash
python -m ai_learning.cli pipeline --ingest-ledger
```

This runs all three agents sequentially:
1. Extracts 30-dimensional SMC feature vectors from each trade
2. Clusters trades using KMeans with silhouette-optimized K
3. Generates strategy rules from each cluster's dominant features
4. (Optional) Backtests and optimizes with historical candle data

### Step 4: Check Status

```bash
python -m ai_learning.cli status
```

### Step 5: Generate Signals

```bash
python -m ai_learning.cli scan
```

---

## Programmatic Usage

```python
from ai_learning.pipeline import TradingAIPipeline

pipeline = TradingAIPipeline()

# Ingest trades
pipeline.ingest_trades_csv("my_trades.csv")

# Run full pipeline
report = pipeline.run_full_pipeline()

# One-time scan
from smc_mtf_engine_v4 import fetch_ohlc
symbols = ["NSE:RELIANCE", "NSE:HDFCBANK", "NSE:INFY"]
signals = pipeline.scan_once(symbols, lambda s, i: fetch_ohlc(s, i, 200))

for sig in signals:
    print(sig.alert_text())
```

---

## Feature Vector (30 dimensions)

Each trade is converted to a 30-dimensional SMC feature vector:

| # | Feature | Description |
|---|---------|-------------|
| 1 | `htf_trend` | HTF trend alignment (1=aligned, 0=counter) |
| 2 | `ob_present` | Order block present (0/1) |
| 3 | `ob_type_bullish` | OB type (1=bullish, 0=bearish) |
| 4 | `ob_width_atr` | OB width as ATR multiple |
| 5 | `entry_in_ob` | Entry price inside OB zone (0/1) |
| 6 | `fvg_present` | Fair Value Gap present (0/1) |
| 7 | `fvg_filled_pct` | FVG fill percentage (0-1) |
| 8 | `displacement_present` | Displacement candle present (0/1) |
| 9 | `liq_sweep` | Liquidity sweep detected (0/1) |
| 10 | `liq_type_buy` | Sweep type (1=buyside, 0=sellside) |
| 11 | `equal_hl_present` | Equal highs/lows present (0/1) |
| 12 | `bos` | Break of Structure (0/1) |
| 13 | `choch` | Change of Character (0/1) |
| 14 | `structure_shift_recent` | Recent structure shift (0/1) |
| 15 | `zone_type_demand` | Zone type (1=demand, 0=supply) |
| 16 | `zone_strength` | Zone strength score (0-1) |
| 17 | `zone_touch_count` | Number of zone touches (normalized) |
| 18 | `entry_in_ote` | Entry in Optimal Trade Entry (0.618-0.786) |
| 19 | `session_killzone` | In killzone session (0/1) |
| 20 | `session_london` | In London overlap session (0/1) |
| 21 | `session_asia` | In Asia session (0/1) |
| 22 | `atr_percentile` | ATR percentile (0-1) |
| 23 | `range_expansion` | Range expansion vs 20-period avg (0/1) |
| 24 | `volatility_regime` | Vol regime (0=low, 0.5=normal, 1=high) |
| 25 | `engulfing` | Engulfing candle at entry (0/1) |
| 26 | `pin_bar` | Pin bar at entry (0/1) |
| 27 | `inside_bar` | Inside bar at entry (0/1) |
| 28 | `strong_close` | Strong close candle (0/1) |
| 29 | `confluence_count` | Number of confluences (normalized) |
| 30 | `multi_tf_alignment` | Multi-timeframe alignment score (0-1) |

---

## Generated Files

After running the pipeline, these files are created:

| File | Location | Description |
|------|----------|-------------|
| `style_profile.json` | `ai_learning/exports/` | Complete style profile |
| `ai_strategy_module.py` | `ai_learning/exports/` | Standalone strategy module |
| `ai_setup_detector.py` | `ai_learning/exports/` | Engine integration module |
| `strategy_rules.json` | `ai_learning/exports/` | Machine-readable rules |
| `optimization_report.json` | `ai_learning/exports/` | Optimization results |
| `pipeline_report.json` | `ai_learning/exports/` | Full pipeline report |
| `ai_learning_data.db` | `ai_learning/data/` | SQLite database |

---

## Engine Integration

The generated `ai_setup_detector.py` integrates directly with your existing engine:

```python
# In smc_mtf_engine_v4.py, add to scan loop:
from ai_learning.exports.ai_setup_detector import detect_ai_setup

# Inside scan_and_alert() function:
ai_signal = detect_ai_setup(symbol, tf_data)
if ai_signal:
    send_telegram(ai_signal["message"])
```

---

## Configuration

All parameters in `ai_learning/config.py`:

- **MIN_TRADES_FOR_LEARNING**: Minimum trades to start learning (default: 15)
- **MAX_CLUSTERS**: Maximum strategy clusters (default: 8)
- **SIMILARITY_THRESHOLD**: Pattern matching threshold (default: 0.70)
- **Performance thresholds**: MIN_WIN_RATE=0.45, MIN_PROFIT_FACTOR=1.3

---

## Retraining

As you accumulate more trades, retrain the system:

```python
pipeline = TradingAIPipeline()
pipeline.ingest_trades_csv("new_trades.csv")  # Add new trades
pipeline.retrain()  # Re-learn with all trades
```

Or via CLI:
```bash
python -m ai_learning.cli ingest --source csv --file new_trades.csv
python -m ai_learning.cli pipeline
```
