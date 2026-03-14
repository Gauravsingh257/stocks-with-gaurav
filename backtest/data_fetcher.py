"""
backtest/data_fetcher.py — Kite API Data Fetcher
=================================================
Fetches historical candles from Zerodha Kite and stores them in the DataStore.
Run this when you have a live Kite connection to populate backtest data.

Usage:
    python -m backtest.data_fetcher --months 6
    python -m backtest.data_fetcher --symbols "NSE:NIFTY 50,NSE:NIFTY BANK" --months 3
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timedelta
from typing import List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest.data_store import DataStore

logger = logging.getLogger(__name__)


# Kite API intervals and their corresponding candle durations
TIMEFRAME_MAP = {
    "5minute":  {"interval": "5minute",  "max_days_per_call": 60},
    "15minute": {"interval": "15minute", "max_days_per_call": 100},
    "30minute": {"interval": "30minute", "max_days_per_call": 100},
    "60minute": {"interval": "60minute", "max_days_per_call": 365},
    "day":      {"interval": "day",      "max_days_per_call": 365 * 4},
}

# Timeframes needed for the backtester (must match engine's fetch_multitf)
REQUIRED_TIMEFRAMES = ["5minute", "15minute", "60minute", "day"]


class KiteDataFetcher:
    """Fetch historical candles from Kite API into DataStore."""

    def __init__(self, kite, store: Optional[DataStore] = None):
        """
        Args:
            kite: KiteConnect instance (already authenticated)
            store: DataStore instance (creates default if None)
        """
        self.kite = kite
        self.store = store or DataStore()
        self._token_cache = {}
        self._last_api_call = 0.0
        self._api_delay = 0.35  # seconds between API calls (Kite rate limit: 3/sec)

    def _throttle(self):
        """Respect Kite API rate limits."""
        elapsed = time.time() - self._last_api_call
        if elapsed < self._api_delay:
            time.sleep(self._api_delay - elapsed)
        self._last_api_call = time.time()

    def _get_token(self, symbol: str) -> Optional[int]:
        """Get instrument token for a symbol."""
        if symbol in self._token_cache:
            return self._token_cache[symbol]
        try:
            data = self.kite.ltp(symbol)
            if symbol in data:
                token = data[symbol]["instrument_token"]
                self._token_cache[symbol] = token
                return token
        except Exception as e:
            logger.error(f"Token lookup failed for {symbol}: {e}")
        return None

    def fetch_symbol(self, symbol: str, months: int = 6,
                     timeframes: Optional[List[str]] = None,
                     end_date: Optional[datetime] = None) -> dict:
        """
        Fetch all timeframes for a symbol and store them.

        Args:
            symbol: e.g. "NSE:NIFTY 50"
            months: how many months of history to fetch
            timeframes: list of timeframe strings (default: REQUIRED_TIMEFRAMES)
            end_date: end date for the fetch (default: now)

        Returns:
            dict with counts per timeframe
        """
        token = self._get_token(symbol)
        if not token:
            logger.error(f"Cannot fetch {symbol} — no instrument token")
            return {}

        if end_date is None:
            end_date = datetime.now()
        start_date = end_date - timedelta(days=months * 30)

        timeframes = timeframes or REQUIRED_TIMEFRAMES
        results = {}

        for tf in timeframes:
            tf_info = TIMEFRAME_MAP.get(tf)
            if not tf_info:
                logger.warning(f"Unknown timeframe: {tf}")
                continue

            max_days = tf_info["max_days_per_call"]
            interval = tf_info["interval"]
            all_candles = []

            # Chunk the date range to respect Kite's per-call limits
            chunk_start = start_date
            while chunk_start < end_date:
                chunk_end = min(chunk_start + timedelta(days=max_days), end_date)

                self._throttle()
                try:
                    data = self.kite.historical_data(
                        token, chunk_start, chunk_end, interval
                    )
                    if data:
                        all_candles.extend(data)
                        logger.debug(f"  {symbol}/{tf}: {len(data)} candles "
                                     f"({chunk_start.date()} → {chunk_end.date()})")
                except Exception as e:
                    logger.error(f"API error {symbol}/{tf} "
                                 f"({chunk_start.date()} → {chunk_end.date()}): {e}")
                    time.sleep(1)

                chunk_start = chunk_end + timedelta(seconds=1)

            # Store
            if all_candles:
                count = self.store.insert_candles(symbol, tf, all_candles)
                results[tf] = count
                logger.info(f"[OK] {symbol}/{tf}: {count} candles stored")
            else:
                results[tf] = 0
                logger.warning(f"[FAIL] {symbol}/{tf}: no data returned")

        return results

    def fetch_universe(self, symbols: List[str], months: int = 6,
                       timeframes: Optional[List[str]] = None) -> dict:
        """
        Fetch data for all symbols in a universe.

        Returns:
            dict: {symbol: {timeframe: count}}
        """
        total = len(symbols)
        all_results = {}

        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{total}] Fetching {symbol}...")
            try:
                result = self.fetch_symbol(symbol, months=months, timeframes=timeframes)
                all_results[symbol] = result
            except Exception as e:
                logger.error(f"Failed to fetch {symbol}: {e}")
                all_results[symbol] = {"error": str(e)}

        return all_results

    def fetch_default_universe(self, months: int = 6):
        """Fetch data for INDEX_SYMBOLS + stock universe."""
        symbols = ["NSE:NIFTY 50", "NSE:NIFTY BANK"]

        # Load stock universe
        universe_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "stock_universe_500.json"
        )
        if os.path.exists(universe_path):
            with open(universe_path, "r") as f:
                stock_list = json.load(f)
                if isinstance(stock_list, list):
                    symbols.extend(stock_list[:50])  # Top 50 stocks for manageable fetch

        logger.info(f"Fetching {len(symbols)} symbols × {months} months...")
        return self.fetch_universe(symbols, months=months)


def main():
    parser = argparse.ArgumentParser(description="Fetch Kite historical data for backtesting")
    parser.add_argument("--months", type=int, default=6, help="Months of history (default: 6)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols (default: index + top 50 stocks)")
    parser.add_argument("--timeframes", type=str, default=None,
                        help="Comma-separated timeframes (default: 5minute,15minute,60minute,day)")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite DB")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # Import Kite connection from engine
    try:
        from kite_credentials import API_KEY
        from kiteconnect import KiteConnect

        token_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "access_token.txt")
        if not os.path.exists(token_path):
            print("ERROR: access_token.txt not found. Run zerodha_login.py first.")
            sys.exit(1)

        with open(token_path, "r") as f:
            access_token = f.read().strip()

        if not access_token:
            print("ERROR: access_token.txt is empty. Run zerodha_login.py first.")
            sys.exit(1)

        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)

        # Quick health check — verify connection
        test_ltp = kite.ltp("NSE:NIFTY 50")
        if not test_ltp:
            print("ERROR: Kite API returned empty LTP. Token may be expired.")
            sys.exit(1)
        print("[OK] Kite connected")
    except ImportError as e:
        print(f"ERROR: Missing dependency: {e}")
        print("  Install with: pip install kiteconnect")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Cannot connect to Kite: {e}")
        print("  Ensure zerodha_login.py has been run today.")
        sys.exit(1)

    store = DataStore(args.db) if args.db else DataStore()
    fetcher = KiteDataFetcher(kite, store)

    timeframes = args.timeframes.split(",") if args.timeframes else None

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
        results = fetcher.fetch_universe(symbols, months=args.months, timeframes=timeframes)
    else:
        results = fetcher.fetch_default_universe(months=args.months)

    # Summary
    print("\n" + "=" * 60)
    print("DATA FETCH SUMMARY")
    print("=" * 60)
    total_candles = 0
    for sym, tf_counts in results.items():
        if isinstance(tf_counts, dict) and "error" not in tf_counts:
            sym_total = sum(tf_counts.values())
            total_candles += sym_total
            print(f"  {sym}: {sym_total} candles")
    print(f"\nTotal: {total_candles} candles across {len(results)} symbols")

    store.close()


if __name__ == "__main__":
    main()
