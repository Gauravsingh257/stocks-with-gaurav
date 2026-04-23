"""
services/price_resolver.py

Single source of truth for current market price (CMP) across the dashboard.

Resolution priority (per symbol):
  1. ws_cache       — Redis `ltp:` key, populated by the engine WebSocket tick stream.
                      Age = 0..LTP_TTL (300s). Most accurate during market hours.
  2. kite_live      — kite.ltp() REST call, only invoked during market hours and
                      only for symbols not already covered by ws_cache.
  3. yf_delayed     — yfinance fast_info, ~15 min delayed. Used after-hours or
                      when Kite is unavailable.
  4. scan_snapshot  — fallback to the price stored at scan time (if caller
                      supplies it). Marked with the highest age.

Returns one entry per requested symbol: ``{"price": float, "source": str,
"age_sec": int}``. Symbols that cannot be resolved through any source are
omitted.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Iterable

log = logging.getLogger("services.price_resolver")

_IST = timezone(timedelta(hours=5, minutes=30))

SOURCE_WS_CACHE = "ws_cache"
SOURCE_KITE_LIVE = "kite_live"
SOURCE_YF_DELAYED = "yf_delayed"
SOURCE_SCAN_SNAPSHOT = "scan_snapshot"

# Max acceptable age for ws_cache before we attempt a fresher source (seconds).
# Even a "stale" ws_cache value within LTP_TTL is acceptable; we only fall
# through if the key is missing entirely.
_DEFAULT_FALLBACK_PRICE_AGE_SEC = 24 * 3600  # 1 day for scan_snapshot fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_market_hours() -> bool:
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover - py<3.9
        from backports.zoneinfo import ZoneInfo  # type: ignore
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


def _normalize(symbol: str) -> str:
    return symbol.replace("NSE:", "").replace("BSE:", "").strip().upper()


# ---------------------------------------------------------------------------
# Source readers (each returns dict[symbol] -> (price, age_sec) or empty)
# ---------------------------------------------------------------------------

def _read_ws_cache(symbols: list[str]) -> dict[str, tuple[float, int]]:
    """Read live LTP from Redis populated by the engine WebSocket."""
    try:
        from dashboard.backend.cache import get_ltp_with_age
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("ws_cache import failed: %s", exc)
        return {}
    out: dict[str, tuple[float, int]] = {}
    for sym in symbols:
        try:
            res = get_ltp_with_age(_normalize(sym))
        except Exception as exc:  # pragma: no cover
            log.debug("ws_cache get_ltp_with_age(%s) failed: %s", sym, exc)
            continue
        if res is not None:
            price, age = res
            if price > 0:
                out[sym] = (float(price), int(age))
    return out


def _read_kite(symbols: list[str]) -> dict[str, tuple[float, int]]:
    """Fetch LTP from Kite REST API. Age is ~0 (just fetched)."""
    if not symbols:
        return {}
    try:
        from config.kite_auth import get_api_key, get_access_token
        from kiteconnect import KiteConnect
    except ImportError:
        return {}

    api_key = get_api_key()
    token = get_access_token()
    if not api_key or not token:
        return {}

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
        instruments = [f"NSE:{_normalize(s)}" for s in symbols]
        ltp_data = kite.ltp(instruments)
    except Exception as exc:
        log.debug("Kite LTP fetch failed: %s", exc)
        return {}

    out: dict[str, tuple[float, int]] = {}
    # Reverse map: NSE:SYM -> original requested symbol
    req_by_norm = {_normalize(s): s for s in symbols}
    for inst, data in (ltp_data or {}).items():
        norm = inst.replace("NSE:", "")
        orig = req_by_norm.get(norm)
        if not orig:
            continue
        price = data.get("last_price") if isinstance(data, dict) else None
        if price and price > 0:
            out[orig] = (float(price), 0)
    return out


def _read_yfinance(symbols: list[str]) -> dict[str, tuple[float, int]]:
    """Fallback to yfinance fast_info (~15 min delayed)."""
    if not symbols:
        return {}
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        return {}

    out: dict[str, tuple[float, int]] = {}
    for sym in symbols:
        try:
            ticker_sym = _normalize(sym)
            if not ticker_sym.endswith(".NS"):
                ticker_sym = f"{ticker_sym}.NS"
            t = yf.Ticker(ticker_sym)
            price = t.fast_info.get("lastPrice") or t.fast_info.get("regularMarketPrice")
            if price and price > 0:
                # During market hours yf is ~15 min delayed; otherwise EOD-ish.
                age = 900 if _is_market_hours() else 3600
                out[sym] = (float(price), age)
        except Exception as exc:
            log.debug("yfinance fetch failed for %s: %s", sym, exc)
    return out


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------

def resolve_cmp(
    symbols: Iterable[str],
    scan_cmp_map: dict[str, float] | None = None,
    use_kite: bool | None = None,
    use_yf: bool = True,
) -> dict[str, dict]:
    """Resolve current market price for a batch of symbols.

    Parameters
    ----------
    symbols : iterable of NSE symbol strings (with or without ``NSE:`` prefix).
    scan_cmp_map : optional ``{symbol: price}`` of last-known scan prices used
        as a final fallback (source = ``scan_snapshot``).
    use_kite : if None, auto-decide based on market hours. Pass False to skip
        the Kite REST call (e.g. tests, pure off-hours).
    use_yf : whether to fall back to yfinance.

    Returns
    -------
    dict[symbol] -> {"price": float, "source": str, "age_sec": int}.
    Symbols with no resolvable price are omitted.
    """
    symbols = [s for s in symbols if s]
    if not symbols:
        return {}

    if use_kite is None:
        use_kite = _is_market_hours()

    resolved: dict[str, dict] = {}

    # 1. ws_cache
    ws = _read_ws_cache(symbols)
    for sym, (price, age) in ws.items():
        resolved[sym] = {"price": price, "source": SOURCE_WS_CACHE, "age_sec": age}

    # 2. kite_live for the rest
    missing = [s for s in symbols if s not in resolved]
    if missing and use_kite:
        kite_prices = _read_kite(missing)
        for sym, (price, age) in kite_prices.items():
            resolved[sym] = {"price": price, "source": SOURCE_KITE_LIVE, "age_sec": age}

    # 3. yfinance fallback
    missing = [s for s in symbols if s not in resolved]
    if missing and use_yf:
        yf_prices = _read_yfinance(missing)
        for sym, (price, age) in yf_prices.items():
            resolved[sym] = {"price": price, "source": SOURCE_YF_DELAYED, "age_sec": age}

    # 4. scan_snapshot fallback
    if scan_cmp_map:
        missing = [s for s in symbols if s not in resolved]
        for sym in missing:
            sp = scan_cmp_map.get(sym)
            if sp and sp > 0:
                resolved[sym] = {
                    "price": float(sp),
                    "source": SOURCE_SCAN_SNAPSHOT,
                    "age_sec": _DEFAULT_FALLBACK_PRICE_AGE_SEC,
                }

    return resolved


def resolve_cmp_simple(symbols: Iterable[str]) -> dict[str, float]:
    """Backward-compat helper: returns ``{symbol: price}`` only.

    Mirrors the legacy ``services.trade_tracker._fetch_cmp_batch`` signature so
    callers can swap in a one-line change.
    """
    res = resolve_cmp(symbols)
    return {sym: v["price"] for sym, v in res.items()}


# ---------------------------------------------------------------------------
# Phase 4B: daily OHLC fetcher for outcome tracking
# ---------------------------------------------------------------------------

def _resolve_instrument_tokens(symbols: list[str]) -> dict[str, int]:
    """Map NSE symbols → instrument tokens via cached nfo_instruments.json or
    a fresh kite.instruments() call. Returns {symbol: token}."""
    import json
    import os as _os

    # Try cached file first to avoid an API call.
    cache_path = "nfo_instruments.json"
    cache: list[dict] = []
    try:
        if _os.path.exists(cache_path):
            with open(cache_path, "r") as fh:
                cache = json.load(fh)
    except Exception:
        cache = []

    out: dict[str, int] = {}
    requested = {_normalize(s) for s in symbols}
    if cache:
        for inst in cache:
            ts = inst.get("tradingsymbol", "")
            if ts in requested and inst.get("exchange") == "NSE":
                tok = inst.get("instrument_token")
                if tok:
                    out[ts] = int(tok)
        if len(out) == len(requested):
            return out

    # Fallback: fresh kite.instruments() — only for any still-missing symbols.
    missing = requested - set(out.keys())
    if not missing:
        return out
    try:
        from config.kite_auth import get_api_key, get_access_token
        from kiteconnect import KiteConnect
    except ImportError:
        return out
    api_key = get_api_key()
    token = get_access_token()
    if not api_key or not token:
        return out
    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
        for inst in kite.instruments("NSE"):
            ts = inst.get("tradingsymbol", "")
            if ts in missing:
                tok = inst.get("instrument_token")
                if tok:
                    out[ts] = int(tok)
    except Exception as exc:
        log.debug("kite.instruments() failed: %s", exc)
    return out


def fetch_daily_ohlc(
    symbols: Iterable[str],
    on_date: datetime | None = None,
) -> dict[str, dict]:
    """Fetch a single daily OHLC bar per symbol via Kite ``historical_data``.

    Used by the recommendation outcome tracker to detect TARGET_HIT / STOP_HIT
    across the trading day. Returns
    ``{symbol: {"open": float, "high": float, "low": float, "close": float}}``
    omitting symbols that could not be resolved.

    Parameters
    ----------
    symbols : Iterable[str]
        NSE tradingsymbols (without exchange prefix).
    on_date : datetime, optional
        Calendar date to fetch. Defaults to today (IST).
    """
    syms = [_normalize(s) for s in symbols if s]
    if not syms:
        return {}

    target = on_date or datetime.now(_IST)
    # Kite expects naive datetimes in exchange tz; use the date boundaries.
    day_start = datetime(target.year, target.month, target.day, 9, 0)
    day_end = datetime(target.year, target.month, target.day, 15, 30)

    try:
        from config.kite_auth import get_api_key, get_access_token
        from kiteconnect import KiteConnect
    except ImportError:
        return {}
    api_key = get_api_key()
    token = get_access_token()
    if not api_key or not token:
        return {}

    token_map = _resolve_instrument_tokens(syms)
    if not token_map:
        return {}

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(token)
    except Exception as exc:
        log.debug("Kite init failed: %s", exc)
        return {}

    out: dict[str, dict] = {}
    # Kite has no bulk historical endpoint — loop per symbol. Batch-friendly
    # callers should chunk their input list externally.
    for sym, tok in token_map.items():
        try:
            bars = kite.historical_data(tok, day_start, day_end, "day")
        except Exception as exc:
            log.debug("historical_data(%s) failed: %s", sym, exc)
            continue
        if not bars:
            continue
        bar = bars[-1]  # last bar of the requested day
        try:
            out[sym] = {
                "open": float(bar.get("open", 0) or 0),
                "high": float(bar.get("high", 0) or 0),
                "low": float(bar.get("low", 0) or 0),
                "close": float(bar.get("close", 0) or 0),
            }
        except (TypeError, ValueError):
            continue
    return out


__all__ = [
    "SOURCE_WS_CACHE",
    "SOURCE_KITE_LIVE",
    "SOURCE_YF_DELAYED",
    "SOURCE_SCAN_SNAPSHOT",
    "resolve_cmp",
    "resolve_cmp_simple",
    "fetch_daily_ohlc",
]
