"""
dashboard/backend/realtime.py
Real-time market data: Kite WebSocket tick stream → Redis LTP + tick-aggregated candles.

Runs in a daemon thread (same process as FastAPI). No new servers.
- Subscribes to NIFTY 50 and NIFTY BANK ticks.
- On every tick: writes LTP to Redis (ltp:NIFTY, ltp:BANKNIFTY), publishes to ltp_updates channel.
- Aggregates ticks into 1m/5m/15m candles and stores in Redis (candle:1m:NIFTY etc.).

Lightweight: MODE_LTP for minimal payload; candle aggregation only on minute boundary.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("dashboard.realtime")

# Repo root for config/kite_auth
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Token to dashboard symbol (Redis key suffix)
TOKEN_TO_SYMBOL: dict[int, str] = {}  # instrument_token -> "NIFTY" | "BANKNIFTY"
SYMBOL_TO_LABEL = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}

# Throttle LTP publish to avoid flooding WS (ms)
LTP_PUBLISH_INTERVAL_MS = 200
_last_publish_ts: float = 0
_last_ltp: dict[str, float] = {}

# 1m buffer: symbol -> list of (ts_unix, price, volume)
_minute_buffers: dict[str, list[tuple[int, float, int]]] = defaultdict(list)
_last_minute_ts: dict[str, int] = {}  # symbol -> last closed minute (unix)


def _get_instrument_tokens() -> dict[int, str]:
    """Resolve NIFTY 50 and NIFTY BANK to instrument_token via Kite REST. Returns {token: 'NIFTY'|'BANKNIFTY'}."""
    out: dict[int, str] = {}
    try:
        from kiteconnect import KiteConnect
        from config.kite_auth import get_api_key
        from dashboard.backend.kite_auth import get_access_token

        api_key = get_api_key()
        access_token = get_access_token()
        if not api_key or not access_token:
            log.debug("Realtime: Kite credentials missing — skip token resolution")
            return out

        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        ltp_data = kite.ltp(["NSE:NIFTY 50", "NSE:NIFTY BANK"])
        if not ltp_data:
            return out

        for key, val in ltp_data.items():
            if not isinstance(val, dict):
                continue
            token = val.get("instrument_token")
            if token is None:
                continue
            if "NIFTY 50" in key:
                out[int(token)] = "NIFTY"
            elif "NIFTY BANK" in key or "BANK" in key:
                out[int(token)] = "BANKNIFTY"
        log.info("Realtime: resolved tokens %s", out)
    except Exception as e:
        log.warning("Realtime: token resolution failed — %s", e)
    return out


def _on_ticks(ws, ticks):
    """Called from KiteTicker thread. Write LTP to Redis, publish throttled, aggregate ticks."""
    global _last_publish_ts, _last_ltp
    from dashboard.backend.cache import (
        set_ltp,
        publish_ltp_update,
        append_candle,
    )

    now_ts = int(time.time())
    payload_ltp: dict[str, float] = {}

    for t in ticks:
        token = t.get("instrument_token")
        if token is None:
            continue
        symbol = TOKEN_TO_SYMBOL.get(int(token))
        if not symbol:
            continue
        price = t.get("last_price")
        if price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        volume = 0
        if isinstance(t.get("volume"), (int, float)):
            volume = int(t["volume"])

        set_ltp(symbol, price)
        payload_ltp[SYMBOL_TO_LABEL[symbol]] = price

        # Aggregate into current minute buffer
        minute_ts = (now_ts // 60) * 60
        _minute_buffers[symbol].append((now_ts, price, volume))

        # Flush previous minute if we crossed boundary
        last_ts = _last_minute_ts.get(symbol)
        if last_ts is not None and minute_ts > last_ts:
            buf = _minute_buffers[symbol]
            # Ticks belonging to previous minute: last_ts <= ts < minute_ts
            prev_buf = [(ts, p, v) for ts, p, v in buf if last_ts <= ts < minute_ts]
            buf_curr = [(ts, p, v) for ts, p, v in buf if ts >= minute_ts]
            _minute_buffers[symbol] = buf_curr
            _last_minute_ts[symbol] = minute_ts

            if prev_buf:
                prices = [p for _, p, _ in prev_buf]
                vols = [v for _, _, v in prev_buf]
                candle_1m = {
                    "time": last_ts,
                    "open": round(prev_buf[0][1], 2),
                    "high": round(max(prices), 2),
                    "low": round(min(prices), 2),
                    "close": round(prev_buf[-1][1], 2),
                    "volume": sum(vols),
                }
                append_candle(symbol, "1m", candle_1m)
                _aggregate_to_5m_15m(symbol, candle_1m)

        if last_ts is None:
            _last_minute_ts[symbol] = minute_ts

    if not payload_ltp:
        return
    _last_ltp.update(payload_ltp)

    # Throttle publish
    now_ms = time.time() * 1000
    if now_ms - _last_publish_ts >= LTP_PUBLISH_INTERVAL_MS:
        _last_publish_ts = now_ms
        publish_ltp_update(payload_ltp)


def _aggregate_to_5m_15m(symbol: str, candle_1m: dict) -> None:
    """After appending a 1m candle, optionally flush 5m and 15m if bucket is complete."""
    from dashboard.backend.cache import get_candle_list, append_candle

    t = candle_1m["time"]
    list_1m = get_candle_list(symbol, "1m")

    # 5m: one candle per 5m bucket when we have 5 full 1m bars in that bucket
    bucket_5m = (t // 300) * 300
    in_bucket_5 = [c for c in list_1m if (c["time"] // 300) * 300 == bucket_5m]
    existing_5m = get_candle_list(symbol, "5m")
    if len(in_bucket_5) >= 5 and not any(c["time"] == bucket_5m for c in existing_5m):
        take = in_bucket_5[-5:]
        o, c = take[0]["open"], take[-1]["close"]
        h = max(x["high"] for x in take)
        l = min(x["low"] for x in take)
        vol = sum(x.get("volume", 0) for x in take)
        append_candle(symbol, "5m", {"time": bucket_5m, "open": o, "high": h, "low": l, "close": c, "volume": vol})

    # 15m
    bucket_15m = (t // 900) * 900
    in_bucket_15 = [c for c in list_1m if (c["time"] // 900) * 900 == bucket_15m]
    existing_15m = get_candle_list(symbol, "15m")
    if len(in_bucket_15) >= 15 and not any(c["time"] == bucket_15m for c in existing_15m):
        take = in_bucket_15[-15:]
        o, c = take[0]["open"], take[-1]["close"]
        h = max(x["high"] for x in take)
        l = min(x["low"] for x in take)
        vol = sum(x.get("volume", 0) for x in take)
        append_candle(symbol, "15m", {"time": bucket_15m, "open": o, "high": h, "low": l, "close": c, "volume": vol})


def _run_ticker() -> None:
    """Run KiteTicker in this thread (blocking)."""
    global TOKEN_TO_SYMBOL  # noqa: PLW0603
    try:
        from kiteconnect import KiteTicker
    except ImportError:
        log.warning("Realtime: kiteconnect not installed — tick stream disabled")
        return

    from config.kite_auth import get_api_key
    from dashboard.backend.kite_auth import get_access_token

    while True:
        access_token = get_access_token()
        if not access_token:
            log.debug("Realtime: no Kite token — sleeping 30s")
            time.sleep(30)
            continue

        api_key = get_api_key()
        if not api_key:
            time.sleep(30)
            continue

        new_map = _get_instrument_tokens()
        if not new_map:
            log.debug("Realtime: no instrument tokens — sleeping 60s")
            time.sleep(60)
            continue
        TOKEN_TO_SYMBOL.clear()
        TOKEN_TO_SYMBOL.update(new_map)
        tokens = list(TOKEN_TO_SYMBOL.keys())

        def on_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_LTP, tokens)  # minimal payload
            log.info("Realtime: subscribed to %s", tokens)

        def on_close(ws, code, reason):
            log.info("Realtime: WebSocket closed %s %s", code, reason)

        def on_error(ws, code, reason):
            log.warning("Realtime: WebSocket error %s %s", code, reason)

        kws = KiteTicker(api_key, access_token)
        kws.on_ticks = _on_ticks
        kws.on_connect = on_connect
        kws.on_close = on_close
        kws.on_error = on_error
        try:
            kws.connect(threaded=False)
        except Exception as e:
            log.warning("Realtime: KiteTicker disconnected — %s", e)
        time.sleep(5)


_realtime_thread: threading.Thread | None = None


def start_realtime_service() -> None:
    """Start the market data service in a daemon thread. Safe to call if Redis/Kite unavailable."""
    global _realtime_thread
    if _realtime_thread is not None and _realtime_thread.is_alive():
        return
    if not os.getenv("REDIS_URL", "").strip():
        log.debug("Realtime: REDIS_URL not set — tick service not started")
        return
    _realtime_thread = threading.Thread(target=_run_ticker, daemon=True)
    _realtime_thread.start()
    log.info("Realtime: market data service thread started")


def stop_realtime_service() -> None:
    """No-op (daemon thread exits with process). Kept for API symmetry."""
    pass
