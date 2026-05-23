"""
config/kite_auth.py — Centralized Kite API key + token resolution (SINGLE SOURCE OF TRUTH).

Token resolution order for get_access_token():
  1. Redis key "kite:access_token" (when REDIS_URL is set — used by Railway; set by morning_login callback or zerodha_login.py)
  2. KITE_ACCESS_TOKEN env var
  3. access_token.txt file (project root)

is_token_fresh(max_hours) → (is_fresh, reason). Single source of truth for
all "is the Kite token still valid?" checks. Replaces every duplicated
implementation in the codebase. Handles both tz-aware AND tz-naive
timestamps in Redis (auto_login writes IST tz-aware ISO strings) so a
consumer using naive datetime arithmetic cannot crash silently.

API key: KITE_API_KEY env, else kite_credentials.API_KEY

The live engine re-reads token from this module every 2 minutes, so after running
morning_login.bat or zerodha_login.py (with Redis updated), the engine picks up
the new token without restart. KITE_API_SECRET is only used at login to exchange
request_token → access_token.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("config.kite_auth")
_WORKSPACE = Path(__file__).resolve().parents[1]
_IST = timezone(timedelta(hours=5, minutes=30))


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


def _parse_token_ts(raw: str) -> datetime | None:
    """Parse the Redis `kite:token_ts` value robustly. Accepts either:
      - tz-AWARE ISO string (as auto_login writes today: "...+05:30")
      - tz-NAIVE ISO string (legacy / human-set values)
    In both cases returns a NAIVE IST datetime so callers can subtract
    against `datetime.now(IST).replace(tzinfo=None)` without the
    classic "can't subtract offset-naive and offset-aware datetimes"
    crash that hid for months in smc_mtf_engine_v4.check_token_age.
    Returns None on parse failure."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        # Convert to IST wall-clock, then drop tz — so comparison with
        # naive `now_ist()` is correct regardless of process timezone.
        dt = dt.astimezone(_IST).replace(tzinfo=None)
    return dt


def is_token_fresh(max_hours: int = 20) -> tuple[bool, str]:
    """SINGLE SOURCE OF TRUTH: is the Kite access token usable right now?

    Returns (is_fresh, reason). `reason` is a human-readable string for
    logging / Telegram alerts.

    Logic (cloud-first; legacy file path is best-effort):
      1. If Redis has `kite:access_token`:
          - If `kite:token_ts` also present and parseable:
              - If token's calendar date < today's IST date → STALE
                (Zerodha kills tokens at midnight IST)
              - Else if age_hours > max_hours → STALE
              - Else → FRESH (Redis canonical)
          - If token present but no timestamp → FRESH (trust profile())
      2. Else env var KITE_ACCESS_TOKEN → FRESH (legacy local path)
      3. Else access_token.txt mtime age (legacy local path)
      4. Else → NOT FRESH, no source

    Never raises — every failure returns (False, reason)."""
    now_ist = datetime.now(_IST).replace(tzinfo=None)

    # ── 1. Redis (canonical on Railway) ──────────────────────────────
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        try:
            import redis as _redis
            r = _redis.from_url(url, decode_responses=True, socket_timeout=5)
            tok = r.get("kite:access_token")
            if tok and tok.strip():
                ts_raw = r.get("kite:token_ts")
                token_time = _parse_token_ts(ts_raw or "")
                if token_time is None:
                    return True, "redis-token-no-ts"
                if token_time.date() < now_ist.date():
                    return False, (
                        f"redis-token-stale-day "
                        f"(token={token_time.date()} now={now_ist.date()})"
                    )
                age_h = (now_ist - token_time).total_seconds() / 3600
                if age_h > max_hours:
                    return False, f"redis-token-too-old ({age_h:.1f}h>{max_hours}h)"
                return True, f"redis-fresh ({age_h:.1f}h, exp at midnight IST)"
        except Exception as exc:
            log.warning("is_token_fresh: redis check failed: %s", exc)

    # ── 2. Env var (legacy / local dev) ──────────────────────────────
    if os.getenv("KITE_ACCESS_TOKEN", "").strip():
        return True, "env-var (no age check)"

    # ── 3. Local file (legacy / local dev) ───────────────────────────
    token_file = _WORKSPACE / "access_token.txt"
    if token_file.exists():
        try:
            mod_time = datetime.fromtimestamp(token_file.stat().st_mtime)
            age_h = (now_ist - mod_time).total_seconds() / 3600
            if age_h > max_hours:
                return False, f"file-too-old ({age_h:.1f}h>{max_hours}h)"
            return True, f"file ({age_h:.1f}h)"
        except Exception as exc:
            return False, f"file-read-error: {exc}"

    # ── 4. No source available ───────────────────────────────────────
    return False, "no-source (Redis empty, no env var, no access_token.txt)"


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
