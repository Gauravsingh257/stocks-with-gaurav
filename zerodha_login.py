"""
zerodha_login.py — Zerodha Kite Connect login helper.

Flow:
  1. Opens Kite login URL in browser.
  2. You log in on Zerodha.
  3. Zerodha redirects you to a URL like:
         https://127.0.0.1/?request_token=XXXXXX&action=login&status=success
  4. Paste that full URL (or just the raw token) here.
  5. Token is validated, exchanged for access_token, and saved to:
       - access_token.txt   (local engine use)
       - .env               (KITE_ACCESS_TOKEN line updated)
       - Redis              (if REDIS_URL is set — propagates to Railway dashboard)

Run:
    python zerodha_login.py
"""

import os
import re
import sys
import webbrowser
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zerodha_login")

# ── Load .env so KITE_API_KEY / KITE_API_SECRET / REDIS_URL are available ────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Credentials from env (never hardcode secrets) ────────────────────────────
API_KEY    = os.getenv("KITE_API_KEY", "").strip()
API_SECRET = os.getenv("KITE_API_SECRET", "").strip()

if not API_KEY or not API_SECRET:
    print("❌ KITE_API_KEY or KITE_API_SECRET not set in .env or environment.")
    print("   Add them to your .env file and re-run.")
    sys.exit(1)

# ── Token extraction ──────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"request_token=([A-Za-z0-9]+)")
_RAW_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{10,}$")

def extract_request_token(value: str) -> str | None:
    """Extract request_token from a full redirect URL or return raw token."""
    value = value.strip()
    if not value:
        return None
    m = _TOKEN_RE.search(value)
    if m:
        return m.group(1)
    if _RAW_TOKEN_RE.match(value):
        return value
    return None


def update_env_file(access_token: str) -> None:
    """Update KITE_ACCESS_TOKEN line in .env file in-place."""
    try:
        if not _env_path.exists():
            log.warning(".env not found — skipping .env update")
            return
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
        log.info(".env updated: KITE_ACCESS_TOKEN")
    except Exception as e:
        log.warning("Could not update .env: %s", e)


def store_in_redis(access_token: str) -> None:
    """Store access_token in Redis if REDIS_URL is set (propagates to Railway)."""
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        log.info("REDIS_URL not set — skipping Redis storage")
        return
    try:
        import redis as _redis
        r = _redis.from_url(redis_url, decode_responses=True)
        r.set("kite:access_token", access_token, ex=86400)
        log.info("✅ Token stored in Redis (key: kite:access_token, TTL: 24h)")
    except Exception as e:
        log.warning("Redis storage failed: %s", e)


# ── Main flow ─────────────────────────────────────────────────────────────────
def main():
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    print("\n" + "=" * 60)
    print("  ZERODHA KITE LOGIN")
    print("=" * 60)
    print(f"\n1. Opening Kite login page in browser...")
    print(f"   URL: {login_url}\n")
    webbrowser.open(login_url)

    print("2. Log in on Zerodha.")
    print("3. After login, Zerodha will redirect you to a URL like:")
    print("       https://127.0.0.1/?request_token=XXXXXX&action=login&status=success")
    print()

    for attempt in range(3):
        raw = input("4. Paste the full redirect URL (or just the request_token): ").strip()
        request_token = extract_request_token(raw)
        if request_token:
            break
        print(f"   ⚠️  Could not extract request_token from: {raw!r}")
        print("       Make sure you paste the full URL after login redirect.")
        if attempt == 2:
            print("❌ Failed 3 times. Exiting.")
            sys.exit(1)

    print(f"\n5. Extracted request_token: {request_token[:8]}...{request_token[-4:]}")
    print("   Exchanging for access_token...\n")

    try:
        data = kite.generate_session(request_token, api_secret=API_SECRET)
    except Exception as e:
        print(f"❌ Session generation failed: {e}")
        print("   The request_token may be expired (tokens are single-use, valid ~2 min).")
        print("   Re-run this script and paste the URL immediately after login.")
        sys.exit(1)

    access_token = data["access_token"]

    # ── Save everywhere ───────────────────────────────────────────────────────
    # 1. access_token.txt (engine reads this locally)
    Path("access_token.txt").write_text(access_token, encoding="utf-8")
    log.info("✅ Saved to access_token.txt")

    # 2. .env file (KITE_ACCESS_TOKEN line)
    update_env_file(access_token)

    # 3. Redis (propagates to Railway dashboard + engine)
    store_in_redis(access_token)

    # ── Final output ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  ✅  LOGIN SUCCESSFUL")
    print("=" * 60)
    print(f"  Access Token: {access_token[:12]}...{access_token[-6:]}")
    print()
    print("  Token saved to:")
    print("    → access_token.txt   (local engine)")
    print("    → .env               (KITE_ACCESS_TOKEN)")
    if os.getenv("REDIS_URL"):
        print("    → Redis              (Railway dashboard + engine)")
    print()
    print("  ⚠️  This token expires at midnight IST. Run this again tomorrow.")
    print("=" * 60 + "\n")

    # 4. Validate the session works
    try:
        kite.set_access_token(access_token)
        profile = kite.profile()
        print(f"  👤 Logged in as: {profile.get('user_name', 'Unknown')}")
        print(f"     Email: {profile.get('email', '')}")
        print(f"     Broker: {profile.get('broker', '')}")
    except Exception as e:
        print(f"  ⚠️  Could not validate session: {e}")


if __name__ == "__main__":
    main()
