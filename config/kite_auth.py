"""
config/kite_auth.py — Centralized Kite API key + token resolution.

Resolution order:
  1. KITE_ACCESS_TOKEN env var (for Railway/cloud — update daily from zerodha_login)
  2. access_token.txt file (for local dev)

API key: KITE_API_KEY env, else kite_credentials.API_KEY
"""
import os
from pathlib import Path

# Workspace root
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
