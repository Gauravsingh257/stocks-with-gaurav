"""
auto_login.py — Fully automated Zerodha Kite login (no browser needed).

Flow:
  1. POST credentials to Kite web login endpoint → get request_id
  2. Generate TOTP from secret → POST 2FA → get request_token via redirect
  3. Exchange request_token → access_token via KiteConnect API
  4. Store token in Redis + .env + access_token.txt
  5. Verify token works (profile fetch)
  6. Send Telegram notification

Requirements:
  - .env must contain: KITE_API_KEY, KITE_API_SECRET, KITE_USER_ID,
    KITE_PASSWORD, KITE_TOTP_SECRET
  - Optional: REDIS_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Usage:
    python auto_login.py                  # Run once
    python auto_login.py --scheduled      # Suppress output, exit codes only

Schedule (Windows Task Scheduler):
    Trigger: Daily at 08:50 AM
    Action:  .venv/Scripts/python.exe
    Args:    auto_login.py --scheduled
    Start in: C:/Users/g6666/Trading Algo
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── Load .env ──────────────────────────────────────────────────────
_WORKSPACE = Path(__file__).resolve().parent
_env_path = _WORKSPACE / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Config from env ────────────────────────────────────────────────
API_KEY = os.getenv("KITE_API_KEY", "").strip()
API_SECRET = os.getenv("KITE_API_SECRET", "").strip()
USER_ID = os.getenv("KITE_USER_ID", "").strip()
PASSWORD = os.getenv("KITE_PASSWORD", "").strip()
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET", "").strip()
REDIS_URL = os.getenv("REDIS_URL", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# Railway backend URL must be set explicitly via env (no hard-coded fallback —
# old defaults silently broke when the Railway service was renamed).
BACKEND_URL = os.getenv("RAILWAY_BACKEND_URL", "").strip().rstrip("/")

# ── Logging ────────────────────────────────────────────────────────
_scheduled = "--scheduled" in sys.argv
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [auto_login] %(message)s",
    handlers=[
        logging.FileHandler(_WORKSPACE / "auto_login.log", encoding="utf-8"),
        *([] if _scheduled else [logging.StreamHandler()]),
    ],
)
log = logging.getLogger("auto_login")

# ── Constants ──────────────────────────────────────────────────────
KITE_LOGIN_URL = "https://kite.zerodha.com/api/login"
KITE_TWOFA_URL = "https://kite.zerodha.com/api/twofa"
KITE_CONNECT_LOGIN = "https://kite.zerodha.com/connect/login"
MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds
_IST = timezone(timedelta(hours=5, minutes=30))
_AUTO_LOGIN_DONE_PREFIX = "kite:login:done"
_AUTO_LOGIN_IN_PROGRESS_KEY = "kite:auto_login:in_progress"


# ── Helpers ────────────────────────────────────────────────────────

def _validate_config() -> list[str]:
    """Return list of missing config keys."""
    missing = []
    if not API_KEY:
        missing.append("KITE_API_KEY")
    if not API_SECRET:
        missing.append("KITE_API_SECRET")
    if not USER_ID:
        missing.append("KITE_USER_ID")
    if not PASSWORD:
        missing.append("KITE_PASSWORD")
    if not TOTP_SECRET:
        missing.append("KITE_TOTP_SECRET")
    return missing


def _generate_totp() -> str:
    """Generate current TOTP, waiting if near boundary to avoid expiry mid-request."""
    import pyotp

    totp = pyotp.TOTP(TOTP_SECRET)
    # If less than 3 seconds left in current window, wait for next
    remaining = totp.interval - (time.time() % totp.interval)
    if remaining < 3:
        log.info("TOTP near expiry (%.1fs left), waiting for next window...", remaining)
        time.sleep(remaining + 1)
    code = totp.now()
    log.info("TOTP generated: %s***", code[:3])
    return code


def _get_request_token() -> str:
    """
    Automated Kite web login: credentials → 2FA → request_token.
    Uses direct HTTP requests to Kite's web endpoints.
    """
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })

    # Step 1: POST login credentials
    log.info("Step 1: Posting credentials for user %s...", USER_ID)
    resp = session.post(KITE_LOGIN_URL, data={
        "user_id": USER_ID,
        "password": PASSWORD,
    }, timeout=20)

    if resp.status_code != 200:
        raise RuntimeError(f"Login POST failed: HTTP {resp.status_code} — {resp.text[:200]}")

    login_data = resp.json()
    if login_data.get("status") != "success":
        raise RuntimeError(f"Login failed: {login_data.get('message', login_data)}")

    request_id = login_data["data"]["request_id"]
    log.info("Step 1 OK: got request_id")

    # Step 2: Generate TOTP and submit 2FA
    totp_code = _generate_totp()
    log.info("Step 2: Submitting TOTP 2FA...")

    resp2 = session.post(KITE_TWOFA_URL, data={
        "user_id": USER_ID,
        "request_id": request_id,
        "twofa_value": totp_code,
        "twofa_type": "totp",
        "skip_session": "",
    }, timeout=20)

    # Some Zerodha accounts reject twofa_type="totp" — retry without it
    if resp2.status_code == 400:
        log.info("Retrying 2FA without twofa_type field...")
        resp2 = session.post(KITE_TWOFA_URL, data={
            "user_id": USER_ID,
            "request_id": request_id,
            "twofa_value": totp_code,
        }, timeout=20)

    if resp2.status_code != 200:
        raise RuntimeError(f"2FA POST failed: HTTP {resp2.status_code} — {resp2.text[:200]}")

    twofa_data = resp2.json()
    if twofa_data.get("status") != "success":
        raise RuntimeError(f"2FA failed: {twofa_data.get('message', twofa_data)}")

    log.info("Step 2 OK: 2FA accepted")

    # Step 3: Visit Connect login URL → redirects with request_token
    # Kite flow: /connect/login → /connect/finish?sess_id=... → callback?request_token=...
    log.info("Step 3: Getting request_token from redirect...")
    connect_url = f"{KITE_CONNECT_LOGIN}?v=3&api_key={API_KEY}"

    # Follow redirects manually to catch request_token at any hop
    resp3 = session.get(connect_url, allow_redirects=False)
    max_hops = 5
    for _hop in range(max_hops):
        if resp3.status_code in (301, 302, 303, 307):
            redirect_url = resp3.headers.get("Location", "")
            log.info("  Redirect %d: %s", _hop + 1, redirect_url[:120])

            # Check if this redirect contains request_token
            parsed = urlparse(redirect_url)
            params = parse_qs(parsed.query)
            request_token = params.get("request_token", [None])[0]
            if request_token:
                log.info("Step 3 OK: got request_token from redirect hop %d", _hop + 1)
                return request_token

            # Follow the redirect
            resp3 = session.get(redirect_url, allow_redirects=False)
        else:
            break

    # Check final response body for redirect_url
    if resp3.status_code == 200:
        try:
            body = resp3.json()
            redirect_url = body.get("data", {}).get("redirect_url", "")
            if redirect_url:
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)
                request_token = params.get("request_token", [None])[0]
                if request_token:
                    log.info("Step 3 OK: got request_token from response body")
                    return request_token
        except Exception:
            pass

    raise RuntimeError(
        f"Could not extract request_token after {max_hops} hops. "
        f"Last status: {resp3.status_code}"
    )


def _exchange_token(request_token: str) -> str:
    """Exchange request_token for access_token via KiteConnect."""
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]
    log.info("Token exchange OK: access_token obtained")
    return access_token


def _store_token(access_token: str) -> None:
    """Save access_token to all storage locations."""
    # 1. access_token.txt
    (_WORKSPACE / "access_token.txt").write_text(access_token, encoding="utf-8")
    log.info("Saved to access_token.txt")

    # 2. .env file
    try:
        if _env_path.exists():
            lines = _env_path.read_text(encoding="utf-8").splitlines(keepends=True)
            updated = False
            for i, line in enumerate(lines):
                if line.startswith("KITE_ACCESS_TOKEN="):
                    lines[i] = f"KITE_ACCESS_TOKEN={access_token}\n"
                    updated = True
                    break
            if not updated:
                lines.append(f"KITE_ACCESS_TOKEN={access_token}\n")
            _env_path.write_text("".join(lines), encoding="utf-8")
            log.info("Updated .env KITE_ACCESS_TOKEN")
    except Exception as e:
        log.warning("Could not update .env: %s", e)

    # 3. Redis (for Railway engine + dashboard) — with retry
    redis_ok = False
    if REDIS_URL:
        for _try in range(1, 4):
            try:
                import redis as _redis
                r = _redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=10)
                r.set("kite:access_token", access_token, ex=100800)  # 28h — buffer for cron delays
                from datetime import datetime, timezone, timedelta
                _IST = timezone(timedelta(hours=5, minutes=30))
                r.set("kite:token_ts", datetime.now(_IST).isoformat(), ex=100800)
                log.info("Stored in Redis (kite:access_token, TTL 24h)")
                redis_ok = True
                break
            except Exception as e:
                log.warning("Redis storage attempt %d/3 failed: %s", _try, e)
                if _try < 3:
                    time.sleep(5)

    # 4. Push to Railway backend (public endpoint → stores in Railway Redis) — with retry
    railway_ok = False
    if not BACKEND_URL:
        log.info("Skipping Railway backend push (RAILWAY_BACKEND_URL not set)")
        railway_ok = True  # don't flag as failure if intentionally disabled
    else:
        for _try in range(1, 4):
            try:
                import requests
                resp = requests.post(
                    f"{BACKEND_URL}/api/kite/store-token",
                    json={"access_token": access_token},
                    headers={"X-Sync-Key": os.getenv("TRADES_SYNC_KEY", "")},
                    timeout=15,
                )
                if resp.status_code == 200:
                    log.info("Pushed token to Railway backend (%s)", BACKEND_URL)
                    railway_ok = True
                    break
                else:
                    log.warning("Railway push attempt %d/3 returned %d: %s", _try, resp.status_code, resp.text[:100])
            except Exception as e:
                log.warning("Railway push attempt %d/3 failed: %s", _try, e)
            if _try < 3:
                time.sleep(5)

    # 5. Alert if remote sync failed
    sync_failures = []
    if REDIS_URL and not redis_ok:
        sync_failures.append("Redis")
    if BACKEND_URL and not railway_ok:
        sync_failures.append("Railway backend")
    if sync_failures:
        fail_msg = ", ".join(sync_failures)
        log.error("⚠️ Token saved locally but REMOTE SYNC FAILED: %s", fail_msg)
        _send_telegram(
            f"⚠️ <b>Token Sync FAILED</b>\n"
            f"Login OK but token NOT pushed to: <b>{fail_msg}</b>\n"
            f"Engine on Railway will NOT have a valid token!\n"
            f"<b>Action needed:</b> Check internet & re-run login, "
            f"or manually push via /api/kite/store-token"
        )


def _verify_token(access_token: str) -> bool:
    """Verify token works by fetching user profile."""
    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)
        profile = kite.profile()
        name = profile.get("user_name", "?")
        user_id = profile.get("user_id", "?")
        log.info("Token verified: %s (%s)", name, user_id)
        return True
    except Exception as e:
        log.error("Token verification FAILED: %s", e)
        return False


def _redis_client():
    if not REDIS_URL:
        return None
    try:
        import redis as _redis
        return _redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
            health_check_interval=30,
            retry_on_timeout=True,
        )
    except Exception as e:
        log.warning("Redis client init failed: %s", e)
        return None


def _today_ist() -> str:
    return datetime.now(_IST).date().isoformat()


def _login_done_key(date_str: str | None = None) -> str:
    return f"{_AUTO_LOGIN_DONE_PREFIX}:{date_str or _today_ist()}"


def _existing_session_active() -> bool:
    """Return True when today's Redis Kite token exists and verifies successfully."""
    r = _redis_client()
    if r is None:
        return False
    try:
        today = _today_ist()
        token = (r.get("kite:access_token") or "").strip()
        ts_raw = (r.get("kite:token_ts") or "").strip()
        if not token or not ts_raw:
            return False
        token_ts = datetime.fromisoformat(ts_raw)
        if token_ts.date().isoformat() != today:
            return False
        if _verify_token(token):
            r.set(_login_done_key(today), "1", ex=100800)
            log.info("Login skipped (already logged in today; key=%s)", _login_done_key(today))
            return True
    except Exception as e:
        log.warning("Existing session check failed: %s", e)
    return False


def _mark_auto_login_success() -> None:
    r = _redis_client()
    if r is None:
        return
    try:
        r.set(_login_done_key(), "1", ex=100800)
    except Exception as e:
        log.debug("Could not mark auto-login success date: %s", e)


def _send_telegram(message: str) -> None:
    """Send Telegram notification (best-effort, never throws)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception:
        pass


# ── Main ───────────────────────────────────────────────────────────

def auto_login() -> bool:
    """
    Full automated login with retries.
    Returns True on success, False on failure.
    """
    if os.getenv("FORCE_AUTO_LOGIN", "").strip().lower() not in {"1", "true", "yes"}:
        if _existing_session_active():
            return True

    r = _redis_client()
    lock_acquired = True
    if r is not None:
        try:
            lock_acquired = bool(r.set(_AUTO_LOGIN_IN_PROGRESS_KEY, str(time.time()), nx=True, ex=300))
            if not lock_acquired:
                log.warning("Another auto-login is already in progress; skipping duplicate attempt")
                return _existing_session_active()
        except Exception as e:
            log.warning("Auto-login in-progress lock skipped: %s", e)

    try:
        missing = _validate_config()
        if missing:
            msg = f"Missing config: {', '.join(missing)}"
            log.error(msg)
            _send_telegram(f"❌ <b>Auto-Login FAILED</b>\n{msg}")
            return False

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                log.info("═══ Login attempt %d/%d ═══", attempt, MAX_RETRIES)

                # Step 1-3: Get request_token
                request_token = _get_request_token()

                # Step 4: Exchange for access_token
                access_token = _exchange_token(request_token)

                # Step 5: Store everywhere
                _store_token(access_token)

                # Step 6: Verify it works
                if _verify_token(access_token):
                    _mark_auto_login_success()
                    log.info("✅ AUTO-LOGIN SUCCESSFUL (attempt %d)", attempt)
                    _send_telegram(
                        f"✅ <b>Auto-Login OK</b>\n"
                        f"User: {USER_ID}\n"
                        f"Attempt: {attempt}/{MAX_RETRIES}\n"
                        f"Token: {access_token[:6]}...{access_token[-4:]}"
                    )
                    return True
                else:
                    log.warning("Token verification failed, retrying...")

            except Exception as e:
                log.error("Attempt %d failed: %s", attempt, e)

            if attempt < MAX_RETRIES:
                log.info("Waiting %ds before retry...", RETRY_DELAY)
                time.sleep(RETRY_DELAY)
    finally:
        if r is not None and lock_acquired:
            try:
                r.delete(_AUTO_LOGIN_IN_PROGRESS_KEY)
            except Exception:
                pass

    # All retries exhausted
    log.error("❌ ALL %d LOGIN ATTEMPTS FAILED", MAX_RETRIES)
    _send_telegram(
        f"🚨 <b>Auto-Login FAILED</b>\n"
        f"User: {USER_ID}\n"
        f"All {MAX_RETRIES} attempts exhausted.\n"
        f"<b>⚠️ Manual login required!</b>\n"
        f"Run: python zerodha_login.py"
    )
    return False


if __name__ == "__main__":
    success = auto_login()
    sys.exit(0 if success else 1)
