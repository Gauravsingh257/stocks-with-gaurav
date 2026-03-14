"""
dashboard/backend/routes/charts.py
OHLC + Zone overlay endpoints for the Charts page.

GET /api/ohlc/{symbol}    ?interval=15m  &days=5
GET /api/zones/{symbol}
GET /api/chart-symbols

Kite resolution:
  - Reads API key from kite_credentials.py
  - Reads access_token from access_token.txt (root workspace)
  - Lazy-initialises a KiteConnect client (one shared instance)
  - 30-second in-process cache to avoid hammering the API

Interval mapping (Kite → dashboard param):
  5m   → 5minute
  15m  → 15minute
  1h   → 60minute
  1D   → day
"""

import logging
import os
import sys
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("dashboard.charts")

router = APIRouter(prefix="/api", tags=["charts"])

# ── Kite resolution ────────────────────────────────────────────────────────────
_WORKSPACE = str(Path(__file__).resolve().parents[3])   # C:\Users\...\Trading Algo
if _WORKSPACE not in sys.path:
    sys.path.insert(0, _WORKSPACE)

# Interval map: frontend string → Kite historical interval string
INTERVAL_MAP = {
    "5m":   "5minute",
    "15m":  "15minute",
    "1h":   "60minute",
    "4h":   "day",       # Kite has no native 4h; use day as closest
    "1D":   "day",
    # also accept Kite strings directly
    "5minute":  "5minute",
    "15minute": "15minute",
    "60minute": "60minute",
    "day":      "day",
}

# Days to fetch per interval (balance completeness vs API cost)
DAYS_FOR_INTERVAL = {
    "5minute":  3,
    "15minute": 7,
    "60minute": 30,
    "day":      365,
}

# Well-known NSE indices and their exchange:tradingsymbol strings
INDEX_SYMBOLS = {
    "NIFTY 50":   "NSE:NIFTY 50",
    "NIFTY BANK": "NSE:NIFTY BANK",
    "NIFTY FIN":  "NSE:NIFTY FIN SERVICE",
    "SENSEX":     "BSE:SENSEX",
}

# ── Shared Kite client ────────────────────────────────────────────────────────
_kite      = None
_kite_lock = Lock()


_kite_token_mtime: float = 0  # mtime of access_token.txt when _kite was created


def _get_kite():
    """Lazy-init KiteConnect. Uses KITE_ACCESS_TOKEN env or access_token.txt."""
    global _kite, _kite_token_mtime
    with _kite_lock:
        token_path = Path(_WORKSPACE) / "access_token.txt"
        # Auto-detect token file change (when not using env)
        if _kite is not None and token_path.exists() and not os.getenv("KITE_ACCESS_TOKEN"):
            current_mtime = token_path.stat().st_mtime
            if current_mtime != _kite_token_mtime:
                logger.info("[Charts] access_token.txt changed — refreshing")
                _kite = None
                _token_cache.clear()
                _ohlc_cache.clear()

        if _kite is not None:
            return _kite
        try:
            from kiteconnect import KiteConnect
            from config.kite_auth import get_api_key, get_access_token

            api_key = get_api_key()
            access_token = get_access_token()
            if not api_key or not access_token:
                raise ValueError("KITE_API_KEY and KITE_ACCESS_TOKEN (or access_token.txt) required")

            k = KiteConnect(api_key=api_key)
            k.set_access_token(access_token)
            _kite = k
            _kite_token_mtime = token_path.stat().st_mtime if token_path.exists() else 0
            logger.info("[Charts] Kite client initialised")
            return _kite
        except Exception as e:
            logger.warning("[Charts] Kite unavailable: %s", e)
            return None


def _reset_kite():
    """Force re-init on next call (e.g. after token refresh)."""
    global _kite, _kite_token_mtime
    with _kite_lock:
        _kite = None
        _kite_token_mtime = 0
        _token_cache.clear()
        _ohlc_cache.clear()
        logger.info("[Charts] Kite client reset")


# ── Token cache (symbol → instrument_token) ──────────────────────────────────
_token_cache: dict[str, int] = {}

def _get_instrument_token(kite, symbol: str) -> int | None:
    """Resolve NSE:SYMBOL or bare SYMBOL to instrument token."""
    if symbol in _token_cache:
        return _token_cache[symbol]

    # Normalise: if no exchange prefix, add NSE
    lookup = symbol if ":" in symbol else f"NSE:{symbol}"

    try:
        ltp_data = kite.ltp(lookup)
        if ltp_data:
            token = list(ltp_data.values())[0]["instrument_token"]
            _token_cache[symbol] = token
            return token
    except Exception as e:
        logger.warning("[Charts] Token lookup failed for %s: %s", symbol, e)
    return None


# ── OHLC cache (symbol, interval) → {data, ts} ────────────────────────────────
_ohlc_cache: dict[tuple, dict] = {}
_OHLC_TTL   = 30          # seconds for intraday
_OHLC_TTL_D = 300         # seconds for daily

def _ohlc_ttl(kite_interval: str) -> int:
    return _OHLC_TTL_D if kite_interval == "day" else _OHLC_TTL


# ── OHLC fetch / cache ────────────────────────────────────────────────────────
def _fetch_ohlc(symbol: str, kite_interval: str, days: int) -> list[dict]:
    """Fetch OHLC from Kite with cache. Returns list of {time, open, high, low, close, volume}."""
    cache_key = (symbol, kite_interval)
    cached    = _ohlc_cache.get(cache_key)
    ttl       = _ohlc_ttl(kite_interval)

    if cached and (_time.time() - cached["ts"]) < ttl:
        return cached["data"]

    kite = _get_kite()
    if kite is None:
        raise RuntimeError("Kite client unavailable — check access_token.txt")

    token = _get_instrument_token(kite, symbol)
    if token is None:
        raise RuntimeError(f"Could not resolve instrument token for: {symbol}")

    from_dt = datetime.now() - timedelta(days=days)
    to_dt   = datetime.now()

    raw = kite.historical_data(token, from_dt, to_dt, kite_interval)

    candles = []
    for bar in raw:
        dt = bar["date"]
        if hasattr(dt, "timestamp"):
            ts = int(dt.timestamp())
        else:
            ts = int(datetime.fromisoformat(str(dt)).timestamp())
        candles.append({
            "time":   ts,
            "open":   round(float(bar["open"]),  2),
            "high":   round(float(bar["high"]),  2),
            "low":    round(float(bar["low"]),   2),
            "close":  round(float(bar["close"]), 2),
            "volume": int(bar.get("volume", 0)),
        })

    candles.sort(key=lambda c: c["time"])

    _ohlc_cache[cache_key] = {"data": candles, "ts": _time.time()}
    return candles


# ── Helpers ────────────────────────────────────────────────────────────────────
def _clean_symbol(symbol: str) -> str:
    """Strip exchange prefix and clean whitespace."""
    return symbol.replace("NSE:", "").replace("BSE:", "").strip()


def _kite_symbol(symbol: str) -> str:
    """Add NSE: prefix if bare symbol given."""
    s = symbol.strip()
    return s if ":" in s else f"NSE:{s}"


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/chart-symbols")
def chart_symbols():
    """List tradeable symbols the engine monitors."""
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot
        snap    = get_engine_snapshot()
        zone_sx = list((snap.get("zone_state") or {}).keys())
    except Exception:
        zone_sx = []

    # Merge with well-known indices; deduplicate
    base = ["NIFTY 50", "NIFTY BANK"]
    all_syms = list(dict.fromkeys(base + [_clean_symbol(s) for s in zone_sx]))

    return {
        "symbols":   all_syms,
        "intervals": list(INTERVAL_MAP.keys()),
    }


@router.get("/ohlc/{symbol:path}")
def ohlc(
    symbol:   str,
    interval: str = Query("15m", description="5m | 15m | 1h | 1D"),
    days:     int = Query(0,   ge=0, le=365,
                          description="Override days to fetch (0 = auto)"),
):
    """
    Return OHLC candles for a symbol.
    symbol can be bare ('NIFTY 50') or prefixed ('NSE:NIFTY 50').
    """
    kite_interval = INTERVAL_MAP.get(interval)
    if kite_interval is None:
        raise HTTPException(status_code=400, detail=f"Unknown interval: {interval}. Use: {list(INTERVAL_MAP.keys())}")

    fetch_days = days or DAYS_FOR_INTERVAL.get(kite_interval, 7)
    kite_sym   = _kite_symbol(symbol)

    try:
        candles = _fetch_ohlc(kite_sym, kite_interval, fetch_days)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[Charts] OHLC error for %s %s", symbol, interval)
        # Surface Kite API errors (invalid token, rate limit etc.)
        raise HTTPException(status_code=502, detail=f"Kite API error: {exc}")

    return {
        "symbol":   symbol,
        "interval": interval,
        "kite_interval": kite_interval,
        "count":    len(candles),
        "candles":  candles,
        "cached_at": _ohlc_cache.get((_kite_sym := _kite_symbol(symbol), kite_interval), {}).get("ts"),
    }


@router.get("/zones/{symbol:path}")
def zones(symbol: str):
    """
    Return active OB / FVG zones for a symbol from ZONE_STATE.
    Augments with active trade SL / target lines.
    """
    from dashboard.backend.state_bridge import get_engine_snapshot

    snap       = get_engine_snapshot()
    zone_state = snap.get("zone_state") or {}
    clean      = _clean_symbol(symbol)

    # Try exact match then case-insensitive
    raw_zones = (
        zone_state.get(clean)
        or zone_state.get(symbol)
        or zone_state.get(f"NSE:{clean}")
        or {}
    )

    zones_out: list[dict] = []

    for direction, zone in (raw_zones or {}).items():
        if zone is None:
            continue
        zones_out.append({
            "direction":  direction,
            "top":        zone.get("top")    or zone.get("high"),
            "bottom":     zone.get("bottom") or zone.get("low"),
            "zone_type":  zone.get("zone_type", "OB"),
            "strength":   zone.get("strength", 1.0),
            "tapped":     zone.get("tapped", False),
            "formed_at":  str(zone.get("formed_at", "")),
            "raw":        zone,
        })

    # Active trade lines for this symbol
    active_lines: list[dict] = []
    for trade in (snap.get("active_trades") or []):
        sym = _clean_symbol(str(trade.get("symbol", "")))
        if sym != clean:
            continue
        sl  = trade.get("sl") or trade.get("stop_loss")
        tp  = trade.get("target") or trade.get("tp")
        ep  = trade.get("entry")
        if sl:
            active_lines.append({"type": "sl",     "price": float(sl), "label": f"SL {sl}",     "color": "#ff4757"})
        if tp:
            active_lines.append({"type": "target", "price": float(tp), "label": f"TP {tp}",     "color": "#00e096"})
        if ep:
            active_lines.append({"type": "entry",  "price": float(ep), "label": f"Entry {ep}", "color": "#00d4ff"})

    return {
        "symbol":       clean,
        "zones":        zones_out,
        "active_lines": active_lines,
        "engine_live":  snap.get("engine_live", False),
    }


@router.post("/reset-kite")
def reset_kite():
    """Force Kite client to re-read access_token.txt on next request."""
    _reset_kite()
    return {"ok": True, "message": "Kite client reset — will re-init on next chart request"}
