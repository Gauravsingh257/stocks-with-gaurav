"""
A3 — registration abuse protection.

PR #223 hardened sign-IN. Sign-UP still had no limit of any kind: one script
could mint unlimited accounts, which is an abuse vector on its own and
silently corrupts the user metrics PR #226 made honest. It also accepted any
string as an email, so unreachable addresses could register (production holds
one such row).

Deliberately NOT fixed here, and asserted as such so the gap stays visible:
account enumeration via the 409. Hiding it means reporting a sign-up that did
not happen; closing it properly needs email verification.

Requests go through the real dashboard.backend.main:app.
"""
from __future__ import annotations

import os
import tempfile

os.environ["REDIS_URL"] = ""
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pr4-tests-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend import auth_throttle, cache, rate_limit  # noqa: E402
from dashboard.backend.db import schema  # noqa: E402
from dashboard.backend.routes import auth as auth_routes  # noqa: E402

GOOD_PW = "correct-horse-8"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_get_redis", lambda: None)
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("JWT_SECRET", "test-secret-pr4-not-a-real-secret-0123456789")
    monkeypatch.setattr(auth_routes, "_jwt_secret_cache", None)
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "dashboard.db")
    schema.init_db()
    auth_routes.init_auth_tables()
    auth_throttle._reset_for_tests()
    rate_limit._counters.clear()

    from dashboard.backend.main import app
    yield TestClient(app)

    auth_throttle._reset_for_tests()
    rate_limit._counters.clear()


def _register(c, email, password=GOOD_PW, xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    return c.post("/api/auth/register",
                  json={"email": email, "password": password, "name": "T"}, headers=headers)


# ── the abuse vector this closes ─────────────────────────────────────────────

def test_signup_is_capped_per_ip(client):
    for i in range(auth_throttle.SIGNUP_IP_MAX):
        assert _register(client, f"ok{i}@example.com").status_code == 200
    r = _register(client, "one-too-many@example.com")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_successful_signups_spend_the_budget(client):
    """A limit that only counts failures would not stop mass account creation."""
    for i in range(auth_throttle.SIGNUP_IP_MAX):
        assert _register(client, f"real{i}@example.com").status_code == 200
    assert _register(client, f"real{auth_throttle.SIGNUP_IP_MAX}@example.com").status_code == 429


def test_spoofed_xff_cannot_mint_unlimited_accounts(client, monkeypatch):
    """Railway's edge rebuilds X-Forwarded-For, so the leftmost entry is the
    real client and a spoof does not open a fresh bucket in production. This
    pins the behaviour the limiter relies on."""
    monkeypatch.setattr(rate_limit, "_client_ip", lambda request: "198.51.100.7")
    for i in range(auth_throttle.SIGNUP_IP_MAX):
        assert _register(client, f"s{i}@example.com", xff=f"203.0.113.{i}").status_code == 200
    assert _register(client, "blocked@example.com", xff="203.0.113.250").status_code == 429


def test_a_different_ip_is_not_punished(client, monkeypatch):
    # a holder, not an iterator: _client_ip is also called by the rate-limit
    # middleware on every request, so an iterator drains unpredictably.
    current = {"ip": "203.0.113.1"}
    monkeypatch.setattr(rate_limit, "_client_ip", lambda request: current["ip"])
    for i in range(auth_throttle.SIGNUP_IP_MAX):
        assert _register(client, f"a{i}@example.com").status_code == 200
    assert _register(client, "exhausted@example.com").status_code == 429
    current["ip"] = "203.0.113.99"
    assert _register(client, "elsewhere@example.com").status_code == 200


# ── input validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["bad", "a@b", "a b@c.com", "a@@b.com", "@b.com", "a@.com", ""])
def test_unreachable_addresses_are_rejected(client, bad):
    r = _register(client, bad)
    assert r.status_code == 400, f"{bad!r} was accepted"


@pytest.mark.parametrize("good", ["a@b.com", "first.last@sub.example.co.in", "x+tag@example.org"])
def test_real_addresses_are_accepted(client, good):
    assert _register(client, good).status_code == 200


def test_email_is_normalised_and_cannot_duplicate_by_case(client):
    created = _register(client, "Mixed.Case@Example.COM")
    assert created.status_code == 200
    assert created.json()["user"]["email"] == "mixed.case@example.com", "stored lower-cased"
    dup = _register(client, "mixed.case@example.com")
    assert dup.status_code == 409, "case variants must resolve to one account"


def test_short_passwords_are_rejected_with_the_configured_minimum(client):
    r = _register(client, "pw@example.com", password="a" * (auth_routes.MIN_PASSWORD_LENGTH - 1))
    assert r.status_code == 400
    assert str(auth_routes.MIN_PASSWORD_LENGTH) in r.json()["detail"]
    assert _register(client, "pw@example.com", password="a" * auth_routes.MIN_PASSWORD_LENGTH).status_code == 200


# ── what is NOT fixed (kept visible on purpose) ──────────────────────────────

def test_enumeration_via_409_is_still_open_and_documented(client):
    """Known gap: a 409 proves the address exists. Closing it needs email
    verification. Asserted so the limitation cannot be quietly forgotten."""
    assert _register(client, "taken@example.com").status_code == 200
    assert _register(client, "taken@example.com").status_code == 409
    assert _register(client, "never-seen@example.com").status_code == 200


# ── no regression to sign-in ─────────────────────────────────────────────────

def test_login_still_works_after_registration(client):
    assert _register(client, "alice@example.com").status_code == 200
    r = client.post("/api/auth/login", json={"email": "alice@example.com", "password": GOOD_PW})
    assert r.status_code == 200 and r.json()["token"]


def test_signup_throttle_does_not_consume_the_login_budget(client):
    """The two limits are independent keys; exhausting sign-ups must not lock
    anyone out of signing in."""
    assert _register(client, "bob@example.com").status_code == 200
    for i in range(auth_throttle.SIGNUP_IP_MAX + 2):
        _register(client, f"flood{i}@example.com")
    r = client.post("/api/auth/login", json={"email": "bob@example.com", "password": GOOD_PW})
    assert r.status_code == 200, "sign-in was collateral damage of the sign-up limit"
