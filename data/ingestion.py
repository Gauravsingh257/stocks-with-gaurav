"""
data/ingestion.py — Market data ingestion pipeline.

Handles fetching historical and real-time data from multiple sources
(Kite Connect, yfinance, ccxt) with caching and rate limiting.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


class DataIngestion:
    """
    Unified data ingestion layer supporting multiple data sources.
    Implements disk caching to avoid redundant API calls.
    """

    def __init__(self, source: str = "kite"):
        self.source = source
        self._kite = None

    def fetch_historical(
        self,
        symbol: str,
        interval: str = "5minute",
        days: int = 60,
        end_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data.

        Args:
            symbol: Instrument identifier (e.g. "NSE:NIFTY 50")
            interval: Candle interval ("minute", "5minute", "15minute", "60minute", "day")
            days: Number of days of history
            end_date: End date (defaults to now)

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
        """
        end = end_date or datetime.now()
        start = end - timedelta(days=days)

        cache_key = f"{symbol}_{interval}_{start.date()}_{end.date()}".replace(" ", "_").replace(":", "_")
        cache_path = CACHE_DIR / f"{cache_key}.parquet"

        if cache_path.exists():
            logger.debug("Cache hit: %s", cache_key)
            return pd.read_parquet(cache_path)

        logger.info("Fetching %s %s data (%d days)", symbol, interval, days)

        if self.source == "kite":
            df = self._fetch_kite(symbol, interval, start, end)
        elif self.source == "yfinance":
            df = self._fetch_yfinance(symbol, interval, start, end)
        else:
            raise ValueError(f"Unknown source: {self.source}")

        if not df.empty:
            df.to_parquet(cache_path)
            logger.info("Cached %d candles to %s", len(df), cache_path.name)

        return df

    def _fetch_kite(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> pd.DataFrame:
        try:
            from kiteconnect import KiteConnect
            from config.settings import settings

            if not self._kite:
                self._kite = KiteConnect(api_key=settings.kite_api_key)
                self._kite.set_access_token(settings.kite_access_token)

            data = self._kite.historical_data(
                instrument_token=self._resolve_token(symbol),
                from_date=start,
                to_date=end,
                interval=interval,
            )
            return pd.DataFrame(data)
        except Exception:
            logger.exception("Kite fetch failed for %s", symbol)
            return pd.DataFrame()

    def _fetch_yfinance(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> pd.DataFrame:
        try:
            import yfinance as yf

            yf_interval_map = {
                "minute": "1m", "5minute": "5m", "15minute": "15m",
                "60minute": "1h", "day": "1d",
            }
            yf_name_map = {
                "NIFTY 50": "^NSEI",
                "NIFTY BANK": "^NSEBANK",
            }
            clean = symbol.replace("NSE:", "")
            yf_symbol = yf_name_map.get(clean, clean + ".NS")
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(
                start=start, end=end,
                interval=yf_interval_map.get(interval, "5m"),
            )
            df = df.reset_index().rename(columns={
                "Date": "date", "Datetime": "date",
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            return df[["date", "open", "high", "low", "close", "volume"]]
        except Exception:
            logger.exception("yfinance fetch failed for %s", symbol)
            return pd.DataFrame()

    def _resolve_token(self, symbol: str) -> int:
        """Resolve instrument token from symbol string. Override for production."""
        raise NotImplementedError("Implement instrument token resolution for Kite")

    def clear_cache(self, older_than_days: int = 7) -> int:
        """Remove cached files older than N days."""
        cutoff = datetime.now() - timedelta(days=older_than_days)
        removed = 0
        for f in CACHE_DIR.glob("*.parquet"):
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                removed += 1
        logger.info("Cleared %d cached files older than %d days", removed, older_than_days)
        return removed
