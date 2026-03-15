"""
config/kite_auth.py — Centralized Kite API key + token resolution.

Resolution order:
  1. KITE_ACCESS_TOKEN env var (for Railway/cloud — update daily from zerodha_login)
  2. access_token.txt file (for local dev)

API key: KITE_API_KEY env, else kite_credentials.API_KEY

Note: KITE_API_SECRET is only used during login (zerodha_login.py) to exchange
request_token for access_token. Runtime only needs KITE_API_KEY + KITE_ACCESS_TOKEN.
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
    """Kite access token: env KITE_ACCESS_TOKEN first, else access_token.txt."""
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if token:
        return token
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
