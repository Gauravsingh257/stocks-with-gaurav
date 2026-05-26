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
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("dashboard.realtime")

# Repo root for config/kite_auth
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Token to dashboard symbol (Redis key suffix).
# Extended 2026-05-25: subscribe to the full macro strip Kite makes
# available. Each (Kite ltp_symbol → dashboard_suffix → user_label)
# is declared in INSTRUMENT_SPEC below; whichever Kite resolves
# successfully get subscribed, others are silently skipped (e.g. user
# doesn't have MCX or CDS segment access).
TOKEN_TO_SYMBOL: dict[int, str] = {}
SYMBOL_TO_LABEL: dict[str, str] = {
    "NIFTY":      "NIFTY 50",
    "BANKNIFTY":  "NIFTY BANK",
    "INDIA_VIX":  "INDIA VIX",
    "USDINR":     "USDINR",
    "GIFTNIFTY":  "GIFT NIFTY",
    "GOLDM":      "GOLD",
    "CRUDEOIL":   "CRUDE OIL",
}

# Kite quote symbol → dashboard Redis suffix.
# - NSE: index spot / equity
# - NFO: F&O continuous index futures
# - MCX: commodities (we use mini/standard with continuous future symbol)
# - CDS: currency derivatives (USDINR future)
INSTRUMENT_SPEC: list[tuple[str, str]] = [
    ("NSE:NIFTY 50",         "NIFTY"),
    ("NSE:NIFTY BANK",       "BANKNIFTY"),
    ("NSE:INDIA VIX",        "INDIA_VIX"),
    # Currency + commodity + GIFT-NIFTY require explicit segment access
    # on the user's Zerodha account. If kite.ltp fails to resolve the
    # token, the subscription is skipped — no error.
    ("CDS:USDINR",           "USDINR"),
    ("NSE:GIFTNIFTY",        "GIFTNIFTY"),
    ("MCX:GOLDM",            "GOLDM"),
    ("MCX:CRUDEOIL",         "CRUDEOIL"),
]

# Throttle LTP publish to avoid flooding WS (ms)
LTP_PUBLISH_INTERVAL_MS = 200
_last_publish_ts: float = 0
_last_ltp: dict[str, float] = {}

# Current 1m candle being built per symbol. Candle time is always minute boundary (epoch).
# Structure: { "minute": int, "open": float, "high": float, "low": float, "close": float, "volume": int }
_current_1m: dict[str, dict] = {}


def _get_instrument_tokens() -> dict[int, str]:
    """Resolve the configured macro-strip symbols to instrument tokens.

    Returns {instrument_token: redis_suffix}. Resolves each Kite symbol
    INDIVIDUALLY so that one missing segment (e.g. user has no MCX
    access) does NOT prevent the others from being subscribed. Logs each
    success/failure at INFO level for production observability."""
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

        for kite_sym, suffix in INSTRUMENT_SPEC:
            try:
                ltp_data = kite.ltp([kite_sym])
                if not isinstance(ltp_data, dict) or not ltp_data:
                    log.info("Realtime: %s — no data (skipped)", kite_sym)
                    continue
                # ltp_data shape: {"NSE:NIFTY 50": {"instrument_token": 256265, ...}}
                payload = next(iter(ltp_data.values()), None)
                if not isinstance(payload, dict):
                    continue
                token = payload.get("instrument_token")
                if token is None:
                    log.info("Realtime: %s — no instrument_token (skipped)", kite_sym)
                    continue
                out[int(token)] = suffix
                log.info("Realtime: resolved %s → suffix=%s token=%s",
                         kite_sym, suffix, token)
            except Exception as exc:
                # One symbol failing (e.g. CDS:USDINR for a user without
                # CDS segment access) must not block the others.
                log.info("Realtime: %s — resolution failed (%s) (skipped)",
                         kite_sym, exc)
                continue
        log.info("Realtime: subscription set = %s", list(out.values()))
    except Exception as e:
        log.warning("Realtime: token resolution failed — %s", e)
    return out


def _on_ticks(ws, ticks):
    """Called from KiteTicker thread. Write LTP to Redis, publish throttled, aggregate ticks."""
    if _reconnect_requested.is_set():
        try:
            ws.close()
        except Exception:
            pass
        return
    global _last_publish_ts, _last_ltp
    from dashboard.backend.cache import (
        set_ltp,
        publish_ltp_update,
        upsert_candle,
    )

    # Use integer second; minute bucket = exact boundary aligned with Kite historical
    now_ts = int(time.time())
    minute_ts = (now_ts // 60) * 60  # 09:15:02 -> 09:15:00 (epoch)
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

        # ── Tick-to-candle aggregation (minute boundary only) ──
        cur = _current_1m.get(symbol)
        if cur is None or cur["minute"] != minute_ts:
            # Minute changed (or first tick): finalize previous candle, then start new one
            if cur is not None:
                # Finalize previous minute candle (timestamp = minute boundary)
                candle_1m = {
                    "time": cur["minute"],
                    "open": round(cur["open"], 2),
                    "high": round(cur["high"], 2),
                    "low": round(cur["low"], 2),
                    "close": round(cur["close"], 2),
                    "volume": cur["volume"],
                }
                upsert_candle(symbol, "1m", candle_1m)
                _aggregate_to_5m_15m(symbol, candle_1m)
            # Start new candle for current minute
            _current_1m[symbol] = {
                "minute": minute_ts,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
        else:
            # Same minute: update high, low, close, volume
            cur["high"] = max(cur["high"], price)
            cur["low"] = min(cur["low"], price)
            cur["close"] = price
            cur["volume"] += volume

    # Heartbeat: this batch produced at least one valid tick → WS is alive.
    # Written even when payload_ltp is empty (every tick path counts).
    _write_heartbeat("connected")

    if not payload_ltp:
        return
    _last_ltp.update(payload_ltp)

    # Throttle publish
    now_ms = time.time() * 1000
    if now_ms - _last_publish_ts >= LTP_PUBLISH_INTERVAL_MS:
        _last_publish_ts = now_ms
        publish_ltp_update(payload_ltp)


def _aggregate_to_5m_15m(symbol: str, candle_1m: dict) -> None:
    """After writing a 1m candle, optionally build 5m and 15m if bucket is complete. Uses minute-boundary timestamps."""
    from dashboard.backend.cache import get_candle_list, upsert_candle

    t = int(candle_1m["time"])  # already minute boundary
    list_1m = get_candle_list(symbol, "1m")

    # 5m: one candle per 5m bucket (timestamp = bucket boundary, e.g. 1710500700)
    bucket_5m = (t // 300) * 300
    in_bucket_5 = [c for c in list_1m if (int(c["time"]) // 300) * 300 == bucket_5m]
    if len(in_bucket_5) >= 5:
        take = in_bucket_5[-5:]
        o = take[0]["open"]
        c = take[-1]["close"]
        h = max(x["high"] for x in take)
        l = min(x["low"] for x in take)
        vol = sum(int(x.get("volume", 0)) for x in take)
        upsert_candle(symbol, "5m", {"time": bucket_5m, "open": o, "high": h, "low": l, "close": c, "volume": vol})

    # 15m: one candle per 15m bucket (minute boundary)
    bucket_15m = (t // 900) * 900
    in_bucket_15 = [c for c in list_1m if (int(c["time"]) // 900) * 900 == bucket_15m]
    if len(in_bucket_15) >= 15:
        take = in_bucket_15[-15:]
        o = take[0]["open"]
        c = take[-1]["close"]
        h = max(x["high"] for x in take)
        l = min(x["low"] for x in take)
        vol = sum(int(x.get("volume", 0)) for x in take)
        upsert_candle(symbol, "15m", {"time": bucket_15m, "open": o, "high": h, "low": l, "close": c, "volume": vol})


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
            # WARNING (was DEBUG — invisible at production INFO level).
            # The 2026-05-26 Monday outage looked identical to this: thread
            # alive but no ticks because token resolution returned {}.
            log.warning(
                "Realtime: no instrument tokens resolved — Kite session or "
                "kite.ltp may be broken. Sleeping 60s before retry."
            )
            _write_heartbeat("disconnected")
            time.sleep(60)
            continue
        TOKEN_TO_SYMBOL.clear()
        TOKEN_TO_SYMBOL.update(new_map)
        tokens = list(TOKEN_TO_SYMBOL.keys())

        def on_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_LTP, tokens)  # minimal payload
            log.info("Realtime: KiteTicker connected + subscribed to %s tokens: %s",
                     len(tokens), tokens)
            _write_heartbeat("connected")

        def on_close(ws, code, reason):
            # Bumped from INFO to WARNING — disconnect during market hours is
            # actionable. Heartbeat status reflects the disconnect so
            # /api/system/health can surface it.
            log.warning("Realtime: KiteTicker WebSocket CLOSED code=%s reason=%s", code, reason)
            _write_heartbeat("disconnected")

        def on_error(ws, code, reason):
            log.warning("Realtime: KiteTicker WebSocket ERROR code=%s reason=%s", code, reason)
            _write_heartbeat("error")

        kws = KiteTicker(api_key, access_token)
        kws.on_ticks = _on_ticks
        kws.on_connect = on_connect
        kws.on_close = on_close
        kws.on_error = on_error
        _reconnect_requested.clear()
        # Publish the active instance so the side-channel watchdog can
        # force-close it when the tick-deadlock is detected.
        _active_kws_ref["kws"] = kws
        try:
            kws.connect(threaded=False)
        except Exception as e:
            log.warning("Realtime: KiteTicker disconnected — %s", e)
        finally:
            _active_kws_ref["kws"] = None
        if _reconnect_requested.is_set():
            log.info("Realtime: reconnect requested — will use new token")
        time.sleep(5)


_realtime_thread: threading.Thread | None = None
_realtime_watchdog_thread: threading.Thread | None = None
_reconnect_requested = threading.Event()

# Reference to the currently-active KiteTicker instance so the watchdog
# can force-close it when the tick deadlock is detected (2026-05-26
# incident — see _realtime_watchdog docstring).
_active_kws_ref: dict = {"kws": None}


# ── Observability heartbeat ──────────────────────────────────────────
# 2026-05-26 incident: Monday's macro strip served Friday's prices ALL
# DAY because the KiteTicker thread silently died and no one knew.
# Fix: every successful tick batch writes `realtime:ws_heartbeat` to
# Redis with a 120s TTL. `/api/system/health` checks this key — when
# it's missing during market hours, we KNOW the WS is dead and can
# alert/restart. Same key TTL during off-hours expires naturally
# (expected, no alert).
_HEARTBEAT_KEY = "realtime:ws_heartbeat"
_HEARTBEAT_TTL_SEC = 120
_HEARTBEAT_STATUS_KEY = "realtime:ws_status"  # "connected"|"disconnected"|"error"


def _write_heartbeat(status: str = "connected") -> None:
    """Best-effort heartbeat write to Redis. Never raises."""
    try:
        import redis as _redis
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            return
        r = _redis.from_url(url, decode_responses=True, socket_timeout=2)
        r.set(_HEARTBEAT_KEY, str(int(time.time())), ex=_HEARTBEAT_TTL_SEC)
        r.set(_HEARTBEAT_STATUS_KEY, status, ex=_HEARTBEAT_TTL_SEC)
    except Exception:
        # Heartbeat must NEVER raise into the tick handler — silent fail
        # is better than killing the WS thread on a Redis hiccup.
        pass


def request_reconnect() -> None:
    """Request the realtime tick stream to reconnect (e.g. after token update via /api/kite/callback)."""
    _reconnect_requested.set()


def _realtime_watchdog() -> None:
    """Side-channel deadlock breaker for the KiteTicker WS thread.

    2026-05-26 INCIDENT: KiteTicker connected at web-service boot
    (00:50 IST) with the then-current Kite token. At some point Kite
    invalidated that token (session reset / expiry / network glitch).
    No ticks arrived, but kws.connect() did NOT return — the WS
    appeared "connected" at TCP level while delivering zero data.
    The reconnect-on-token-update flag (_reconnect_requested) is
    only checked inside _on_ticks, so with zero ticks it could never
    fire. Classic chicken-and-egg deadlock: needed reconnect to
    receive ticks; needed ticks to trigger reconnect. Web service
    served stale Friday data for the entire market session until
    a manual redeploy.

    This watchdog runs in its own thread and breaks the deadlock:
      - Every 60s during market hours, check realtime:ws_heartbeat age
      - If > 90s (no ticks for that long while market is open),
        force-close the active KiteTicker instance
      - Closing it makes kws.connect() in _run_ticker return →
        outer while True loops → re-reads access_token from Redis
        (fresh after morning auto-login) → instantiates new KiteTicker
        with fresh token → reconnects cleanly

    Never raises (errors logged + swallowed). Sleep-interval based,
    no APScheduler dependency (lives in the same realtime module so
    we can ship the fix as one atomic change)."""
    log.info("Realtime watchdog: started")
    while True:
        try:
            time.sleep(60)
            # Quiet during off-hours — no ticks expected, false-positive risk
            try:
                from zoneinfo import ZoneInfo
                from datetime import datetime, time as _dtime
                now = datetime.now(ZoneInfo("Asia/Kolkata"))
                if now.weekday() >= 5:
                    continue
                t = now.time()
                if not (_dtime(9, 15) <= t <= _dtime(15, 31)):
                    continue
            except Exception:
                # If tz check fails for any reason, default to NOT
                # firing the watchdog (safer than spurious reconnects)
                continue
            # Inside market hours: check heartbeat freshness
            import redis as _redis
            url = os.getenv("REDIS_URL", "").strip()
            if not url:
                continue
            r = _redis.from_url(url, decode_responses=True, socket_timeout=3)
            raw = r.get(_HEARTBEAT_KEY)
            stale = True
            if raw:
                try:
                    age = int(time.time()) - int(raw)
                    stale = age > 90
                except Exception:
                    stale = True
            if not stale:
                continue
            # Heartbeat stale during market hours → tick deadlock suspected.
            log.warning(
                "Realtime watchdog: heartbeat stale during market hours — "
                "forcing KiteTicker reconnect (deadlock breaker)"
            )
            _write_heartbeat("disconnected")
            _reconnect_requested.set()
            kws = _active_kws_ref.get("kws")
            if kws is not None:
                try:
                    kws.close()
                    log.info("Realtime watchdog: forced close of active KiteTicker")
                except Exception as exc:
                    log.warning("Realtime watchdog: close failed (will retry): %s", exc)
        except Exception:
            # Swallow ALL exceptions — the watchdog must never die. A
            # dead watchdog brings back the original bug.
            log.exception("Realtime watchdog: tick failed (continuing)")


def start_realtime_service() -> None:
    """Start the market data service in a daemon thread. Safe to call if Redis/Kite unavailable.

    Also starts a side-channel watchdog thread that breaks the tick-
    deadlock scenario (see _realtime_watchdog docstring)."""
    global _realtime_thread, _realtime_watchdog_thread
    if _realtime_thread is not None and _realtime_thread.is_alive():
        return
    if not os.getenv("REDIS_URL", "").strip():
        log.warning("Realtime: REDIS_URL not set — tick service not started")
        return
    _realtime_thread = threading.Thread(target=_run_ticker, daemon=True)
    _realtime_thread.start()
    log.info("Realtime: market data service thread started")

    # Watchdog: separate thread, started alongside main ticker
    if _realtime_watchdog_thread is None or not _realtime_watchdog_thread.is_alive():
        _realtime_watchdog_thread = threading.Thread(
            target=_realtime_watchdog, daemon=True
        )
        _realtime_watchdog_thread.start()
        log.info("Realtime: deadlock-breaker watchdog thread started")


def stop_realtime_service() -> None:
    """No-op (daemon thread exits with process). Kept for API symmetry."""
    pass
