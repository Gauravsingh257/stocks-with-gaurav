"""
tests/conftest.py — Shared pytest fixtures for the trading system.
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["BACKTEST_MODE"] = "1"
os.environ["PAPER_TRADING"] = "1"


@pytest.fixture
def sample_candles():
    """Generate synthetic 5-minute OHLCV candles for testing."""
    import random
    from datetime import datetime, timedelta

    candles = []
    base_price = 22000.0
    dt = datetime(2026, 1, 2, 9, 15)

    for i in range(500):
        change = random.uniform(-50, 50)
        o = base_price + change
        h = o + random.uniform(5, 30)
        l = o - random.uniform(5, 30)
        c = random.uniform(l, h)
        v = random.randint(50000, 500000)

        candles.append({
            "date": dt.isoformat(),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": v,
        })

        base_price = c
        dt += timedelta(minutes=5)
        if dt.hour >= 16:
            dt = dt.replace(hour=9, minute=15) + timedelta(days=1)
            if dt.weekday() >= 5:
                dt += timedelta(days=7 - dt.weekday())

    return candles


@pytest.fixture
def sample_htf_candles(sample_candles):
    """Resample 5-min candles to 1-hour."""
    from backtest.engine import resample_to_htf
    return resample_to_htf(sample_candles, htf_minutes=60)


@pytest.fixture
def sample_signal():
    """A standard test signal."""
    return {
        "symbol": "NSE:NIFTY 50",
        "direction": "LONG",
        "setup": "SETUP-A",
        "entry": 22000.0,
        "sl": 21950.0,
        "target": 22100.0,
        "rr": 2.0,
        "ob": (21940, 21960),
        "fvg": (21970, 21990),
    }


@pytest.fixture
def backtest_config():
    """Default backtest configuration for tests."""
    from backtest.engine import BacktestConfig
    return BacktestConfig(
        enable_setup_a=True,
        enable_setup_b=False,
        enable_setup_c=True,
        enable_setup_d=False,
        apply_costs=False,
    )
