"""
config/kite_auth.py — Centralized Kite API key + token resolution (SINGLE SOURCE OF TRUTH).

Token resolution order for get_access_token():
  1. Redis key "kite:access_token" (when REDIS_URL is set — used by Railway; set by morning_login callback or zerodha_login.py)
  2. KITE_ACCESS_TOKEN env var
  3. access_token.txt file (project root)

API key: KITE_API_KEY env, else kite_credentials.API_KEY

The live engine re-reads token from this module every 2 minutes, so after running
morning_login.bat or zerodha_login.py (with Redis updated), the engine picks up
the new token without restart. KITE_API_SECRET is only used at login to exchange
request_token → access_token.
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("config.kite_auth")
_WORKSPACE = Path(__file__).resolve().parents[1]


def get_api_key() -> str:
    """Kite API key from env or kite_credentials."""
    key = os.getenv("KITE_API_KEY", "").strip()
    if key:
        return key
    try:
        from kite_credentials import API_KEY
        return API_KEY or ""
    except ImportError:
        return ""


def get_access_token() -> str:
    """Kite access token: Redis first (web login), env KITE_ACCESS_TOKEN, else access_token.txt."""
    # 1. Redis — token set by /api/kite/callback (web login flow)
    try:
        import redis as _redis
        url = os.getenv("REDIS_URL", "").strip()
        if url:
            r = _redis.from_url(url, decode_responses=True)
            tok = r.get("kite:access_token")
            if tok and tok.strip():
                return tok.strip()
    except Exception:
        pass
    # 2. Environment variable
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if token:
        return token
    # 3. Local file
    token_file = _WORKSPACE / "access_token.txt"
    if token_file.exists():
        try:
            return token_file.read_text().strip()
        except Exception:
            pass
    return ""


def is_kite_available() -> bool:
    """True if both API key and access token are present."""
    return bool(get_api_key() and get_access_token())


def log_kite_status() -> None:
    """Log whether Kite credentials are loaded (no secrets). Call at startup."""
    api_key = get_api_key()
    token = get_access_token()
    if api_key and token:
        log.info("Kite: API key and access token loaded (env or file)")
    else:
        if not api_key:
            log.warning("Kite: KITE_API_KEY not set — set in Railway Variables or kite_credentials")
        if not token:
            log.warning("Kite: KITE_ACCESS_TOKEN not set — set in Railway Variables or run zerodha_login.py")
