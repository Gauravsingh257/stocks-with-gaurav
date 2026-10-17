"""
dashboard/backend/auth_throttle.py
==================================
Login brute-force throttling: per-ACCOUNT and per-IP, counting FAILED attempts.

Why both:
  * per-account — the real defence against guessing one user's password. It is
    keyed on the submitted email, so no amount of IP rotation or header
    spoofing gets around it.
  * per-IP — slows credential stuffing (one source trying many accounts).

Only failures count, so a legitimate user who signs in normally is never
affected. A successful login clears that ACCOUNT's counter only — never the
IP's, or an attacker could reset their IP budget by signing into an account
of their own between guesses.

The 429 is identical whether or not the email exists, so it can't be used to
enumerate accounts. Keys are SHA-256 hashed: no raw emails in Redis.

State lives in Redis (shared across replicas, survives restarts) with an
in-process fallback when Redis is unavailable. Each window is fixed from the
first failure via SET NX EX followed by INCR (INCR keeps the TTL), so a key
can never end up without an expiry and lock someone out permanently.

Client IP comes from rate_limit._client_ip — see the 2026-10-11 measurement
recorded there for why its leftmost-X-Forwarded-For choice is correct on
Railway's edge.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
import time

from fastapi import HTTPException

log = logging.getLogger("dashboard.auth_throttle")

ACCOUNT_MAX_FAILURES = int(os.getenv("LOGIN_ACCOUNT_MAX_FAILURES", "5"))
IP_MAX_FAILURES = int(os.getenv("LOGIN_IP_MAX_FAILURES", "20"))
WINDOW_SECONDS = int(os.getenv("LOGIN_THROTTLE_WINDOW_SECONDS", "900"))  # 15 min

_PREFIX = "auth:login_fail"
_local: dict[str, tuple[int, float]] = {}  # key -> (failures, window expiry epoch)
_lock = threading.Lock()


def _redis():
    try:
        from dashboard.backend.cache import _get_redis
        return _get_redis()
    except Exception:
        return None


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _account_key(email: str) -> str:
    return f"{_PREFIX}:acct:{_h((email or '').strip().lower())}"


def _ip_key(ip: str) -> str:
    return f"{_PREFIX}:ip:{_h(ip or 'unknown')}"


def _read(key: str) -> tuple[int, int]:
    """(failures, seconds until the window resets)."""
    r = _redis()
    if r is not None:
        try:
            n = int(r.get(key) or 0)
            ttl = int(r.ttl(key) or 0)
            return n, max(ttl, 0)
        except Exception as exc:
            log.warning("login throttle read via Redis failed (%s) — in-process", exc)
    with _lock:
        n, exp = _local.get(key, (0, 0.0))
        now = time.time()
        if exp <= now:
            return 0, 0
        return n, int(exp - now)


def _incr(key: str, ttl: int | None = None) -> None:
    window = WINDOW_SECONDS if ttl is None else ttl
    r = _redis()
    if r is not None:
        try:
            r.set(key, 0, ex=window, nx=True)
            r.incr(key)
            return
        except Exception as exc:
            log.warning("login throttle write via Redis failed (%s) — in-process", exc)
    with _lock:
        now = time.time()
        n, exp = _local.get(key, (0, 0.0))
        if exp <= now:
            n, exp = 0, now + window
        _local[key] = (n + 1, exp)


def _clear(key: str) -> None:
    r = _redis()
    if r is not None:
        with contextlib.suppress(Exception):
            r.delete(key)
    with _lock:
        _local.pop(key, None)


def check_login_allowed(email: str, ip: str | None) -> None:
    """Raise 429 if this account or this IP has too many recent failures.
    Call BEFORE touching the DB / bcrypt."""
    for key, limit in ((_account_key(email), ACCOUNT_MAX_FAILURES), (_ip_key(ip or ""), IP_MAX_FAILURES)):
        n, retry = _read(key)
        if n >= limit:
            retry = max(retry, 1)
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed sign-in attempts. Please try again in {max(1, -(-retry // 60))} minute(s).",
                headers={"Retry-After": str(retry)},
            )


def record_login_failure(email: str, ip: str | None) -> None:
    _incr(_account_key(email))
    _incr(_ip_key(ip or ""))


def record_login_success(email: str) -> None:
    _clear(_account_key(email))


# ── Registration ─────────────────────────────────────────────────────────────
# Sign-up had no limit of any kind, so one script could mint unlimited
# accounts. That is an abuse vector on its own, and it silently corrupts the
# user metrics PR #226 just made honest. This counts ATTEMPTS, not failures: a
# successful sign-up is exactly what an abuser wants, so it must spend budget.

SIGNUP_IP_MAX = int(os.getenv("SIGNUP_IP_MAX_PER_WINDOW", "5"))
SIGNUP_WINDOW_SECONDS = int(os.getenv("SIGNUP_THROTTLE_WINDOW_SECONDS", "3600"))  # 1 hour
_SIGNUP_PREFIX = "auth:signup_attempt"


def _signup_ip_key(ip: str) -> str:
    return f"{_SIGNUP_PREFIX}:ip:{_h(ip or 'unknown')}"


def check_signup_allowed(ip: str | None) -> None:
    """Raise 429 when this IP has made too many sign-up attempts recently.

    Per-IP only: there is no account to key on yet, and keying on the submitted
    email would let an attacker reset their budget just by varying it.
    """
    n, retry = _read(_signup_ip_key(ip or ""))
    if n >= SIGNUP_IP_MAX:
        retry = max(retry, 1)
        raise HTTPException(
            status_code=429,
            detail=f"Too many sign-up attempts. Please try again in {max(1, -(-retry // 60))} minute(s).",
            headers={"Retry-After": str(retry)},
        )


def record_signup_attempt(ip: str | None) -> None:
    _incr(_signup_ip_key(ip or ""), ttl=SIGNUP_WINDOW_SECONDS)


def _reset_for_tests() -> None:
    with _lock:
        _local.clear()
