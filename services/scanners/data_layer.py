"""
services/scanners/data_layer.py — Bulk OHLC fetch for the scanner producer.

The ONLY component that calls Kite. Fetches one interval's candles for the whole
universe, respecting Kite's 3 req/sec historical-data cap via a shared throttle +
small thread pool, with per-symbol retry and graceful per-symbol failure (one bad
symbol never aborts the scan).

Runs exclusively in the cron worker (scripts/scanner_cron.py) — never on the web
request path. Reuses config.kite_auth for the token (same Redis source as engine).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

log = logging.getLogger("services.scanners.data_layer")

# Kite historical cap is 3 req/sec; 0.34s spacing keeps us just under it.
_MIN_SPACING_SEC = 0.34
_MAX_WORKERS = 3


class KiteOHLCFetcher:
    """Holds a Kite client + instrument-token map; fetches candles for symbols."""

    def __init__(self, kite=None):
        self._kite = kite or self._build_kite()
        self._tok_map: dict[str, int] = {}
        self._rate_lock = threading.Lock()
        self._last_call = 0.0

    @staticmethod
    def _build_kite():
        from config.kite_auth import get_access_token, get_api_key
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=get_api_key())
        kite.set_access_token(get_access_token())
        return kite

    def verify_token(self) -> str:
        """Raise if the token is not valid; returns the profile user name."""
        return self._kite.profile().get("user_name", "")

    def load_instruments(self) -> int:
        """Build tradingsymbol→instrument_token map for NSE equities."""
        inst = self._kite.instruments("NSE")
        self._tok_map = {
            r["tradingsymbol"]: r["instrument_token"]
            for r in inst
            if r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"
        }
        return len(self._tok_map)

    # Index underlyings carried in NFO that are NOT tradeable equities.
    _NFO_INDEX_NAMES = frozenset(
        {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}
    )

    def load_fno_underlyings(self) -> set[str]:
        """Current F&O *stock* underlyings from Kite's live NFO list.

        This is the gold-standard liquid/tradeable universe (a stock having
        listed derivatives = deep liquidity), and it is self-maintaining — when
        NSE adds/removes F&O names the scanner universe follows automatically.
        Index underlyings (NIFTY/BANKNIFTY/…) are excluded. Only names that also
        have an NSE-EQ token (cash-market tradeable) are returned.
        """
        try:
            nfo = self._kite.instruments("NFO")
        except Exception as exc:
            log.warning("load_fno_underlyings failed (%s)", exc)
            return set()
        names: set[str] = set()
        for r in nfo:
            name = str(r.get("name") or "").strip().upper()
            if not name or name in self._NFO_INDEX_NAMES:
                continue
            if self._tok_map and name not in self._tok_map:
                continue  # keep only cash-tradeable equities
            names.add(name)
        return names

    def _throttle(self) -> None:
        with self._rate_lock:
            dt = time.time() - self._last_call
            if dt < _MIN_SPACING_SEC:
                time.sleep(_MIN_SPACING_SEC - dt)
            self._last_call = time.time()

    def _fetch_one(self, symbol: str, interval: str, lookback_days: int, retries: int = 2) -> list[dict] | None:
        token = self._tok_map.get(symbol)
        if token is None:
            return None
        to_date = datetime.now()
        from_date = to_date - timedelta(days=lookback_days)
        for attempt in range(retries + 1):
            try:
                self._throttle()
                data = self._kite.historical_data(token, from_date, to_date, interval)
                return [
                    {
                        "date": str(d["date"])[:10],
                        "open": d["open"],
                        "high": d["high"],
                        "low": d["low"],
                        "close": d["close"],
                        "volume": d["volume"],
                    }
                    for d in data
                ]
            except Exception as exc:
                if attempt >= retries:
                    log.debug("ohlc fetch failed %s (%s)", symbol, exc)
                    return None
                time.sleep(0.5 * (attempt + 1))
        return None

    def fetch_universe(
        self, symbols: list[str], interval: str, lookback_days: int
    ) -> tuple[dict[str, list[dict]], int]:
        """Fetch candles for every symbol. Returns (symbol→candles, error_count).

        Symbols with no token or repeated failures are simply absent from the
        result map (counted in error_count) — never fatal.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        out: dict[str, list[dict]] = {}
        errors = 0
        targets = [s for s in symbols if s in self._tok_map]
        missing = len(symbols) - len(targets)
        if missing:
            log.info("data_layer: %d/%d symbols had no NSE-EQ token", missing, len(symbols))

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futs = {
                ex.submit(self._fetch_one, sym, interval, lookback_days): sym
                for sym in targets
            }
            for fut in as_completed(futs):
                sym = futs[fut]
                candles = fut.result()
                if candles:
                    out[sym] = candles
                else:
                    errors += 1
        return out, errors + missing
