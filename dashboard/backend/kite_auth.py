"""
dashboard/backend/kite_auth.py
Kite Connect authentication for dashboard: login URL, token exchange, Redis-backed storage.

- get_login_url(): returns Zerodha login URL (redirect URI is whitelisted in Kite app).
- generate_access_token(request_token): exchanges request_token for access_token.
- store_access_token(token): stores access_token in Redis with 24h TTL.
- get_access_token(): reads from Redis first, then falls back to config.kite_auth (env/file).

No password or OTP is stored. Only access_token is stored in Redis.
"""

import logging
import os
from typing import Optional

log = logging.getLogger("dashboard.kite_auth")

# Redis key namespace (multi-account ready)
KITE_ACCESS_TOKEN_KEY = "kite:access_token"
KITE_LAST_LOGIN_KEY = "kite:last_login"
KITE_TOKEN_TTL_SECONDS = 86400  # 24 hours

_redis_client: Optional[object] = None
_redis_available = False


def _get_redis():
    """Lazy-init Redis client for kite token. decode_responses=True for string get/set."""
    global _redis_client, _redis_available
    if _redis_client is not None:
        return _redis_client if _redis_available else None
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        log.debug("REDIS_URL not set — kite token will not be stored in Redis")
        return None
    try:
        import redis
        _redis_client = redis.from_url(url, decode_responses=True)
        _redis_client.ping()
        _redis_available = True
        log.info("Kite auth: Redis connected for token storage")
        return _redis_client
    except Exception as e:
        log.warning("Kite auth: Redis unavailable (%s) — token storage disabled", e)
        _redis_available = False
        return None


def get_login_url(redirect_uri: Optional[str] = None) -> str:
    """Return the Kite (Zerodha) login URL. redirect_uri is configured in Zerodha app dashboard."""
    from kiteconnect import KiteConnect
    api_key = os.getenv("KITE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("KITE_API_KEY is not set")
    kite = KiteConnect(api_key=api_key)
    # KiteConnect.login_url() may accept optional redirect_params; redirect URI is whitelisted in app
    return kite.login_url()


def generate_access_token(request_token: str) -> str:
    """Exchange request_token for access_token via KiteConnect.generate_session."""
    from kiteconnect import KiteConnect
    api_key = os.getenv("KITE_API_KEY", "").strip()
    api_secret = os.getenv("KITE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise ValueError("KITE_API_KEY and KITE_API_SECRET must be set")
    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    return data["access_token"]


def store_access_token(token: str) -> None:
    """Store access_token in Redis with 24h TTL; set kite:last_login and kite:token_ts.
    kite:token_ts is read by the engine to detect fresh logins and unlock the signal window."""
    r = _get_redis()
    if r is None:
        msg = "Redis not available — set REDIS_URL on web service to store token from login"
        log.warning("Kite auth: %s", msg)
        raise RuntimeError(msg)
    try:
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
        r.set(KITE_ACCESS_TOKEN_KEY, token, ex=KITE_TOKEN_TTL_SECONDS)
        r.set(KITE_LAST_LOGIN_KEY, now_utc.isoformat(), ex=KITE_TOKEN_TTL_SECONDS)
        r.set("kite:token_ts", now_ist.replace(tzinfo=None).isoformat(), ex=KITE_TOKEN_TTL_SECONDS)
        log.info("Kite auth: access token + token_ts stored in Redis (TTL %ss)", KITE_TOKEN_TTL_SECONDS)
        try:
            from dashboard.backend.routes.system import invalidate_kite_status_cache
            invalidate_kite_status_cache()
        except Exception:
            pass
    except Exception as e:
        log.warning("Kite auth: failed to store token in Redis: %s", e)
        raise


def get_access_token_from_redis_only() -> Optional[str]:
    """Read access_token from Redis only (no env/file fallback). For worker token-change detection."""
    r = _get_redis()
    if r is None:
        return None
    try:
        token = r.get(KITE_ACCESS_TOKEN_KEY)
        if token and token.strip():
            return token.strip()
    except Exception as e:
        log.debug("Kite auth: Redis get failed: %s", e)
    return None


def get_access_token_ttl_seconds() -> Optional[int]:
    """Return seconds until Redis kite:access_token expires. None if key missing or no TTL."""
    r = _get_redis()
    if r is None:
        return None
    try:
        ttl = r.ttl(KITE_ACCESS_TOKEN_KEY)
        if ttl >= 0:
            return ttl
    except Exception as e:
        log.debug("Kite auth: Redis TTL failed: %s", e)
    return None


def get_last_login_utc() -> Optional[str]:
    """Return when the token was last stored in Redis (e.g. after URL login). ISO format UTC. None if not from Redis."""
    r = _get_redis()
    if r is None:
        return None
    try:
        return r.get(KITE_LAST_LOGIN_KEY)
    except Exception as e:
        log.debug("Kite auth: last_login get failed: %s", e)
    return None


def get_token_source() -> str:
    """Return 'redis' if token is in Redis (URL login), else 'env_or_file'."""
    if get_access_token_from_redis_only():
        return "redis"
    return "env_or_file"


def get_access_token() -> Optional[str]:
    """Read access_token from Redis. If missing, fall back to config.kite_auth (env or file)."""
    token = get_access_token_from_redis_only()
    if token:
        return token
    # Fallback: env or access_token.txt via config
    try:
        from config.kite_auth import get_access_token as _config_get_token
        t = _config_get_token()
        return t if t else None
    except Exception:
        return None
