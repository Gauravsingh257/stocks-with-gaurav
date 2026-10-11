"""
PR1 security hotfix — regression tests.

  S1  user-scoped terminal endpoints require a verified JWT; identity is never
      taken from the client (?user_id= is ignored); user A cannot touch user B.
  S2  ops/debug endpoints FAIL CLOSED when OPS_API_KEY is missing.
  S3  login is throttled per account (immune to X-Forwarded-For spoofing) and
      per IP; normal login still works and success clears the account counter.

Fully isolated: temp SQLite files for both DBs, Redis forced off (the local
.env REDIS_URL points at PRODUCTION — these tests must never reach it), and a
fixed test JWT secret.
"""
from __future__ import annotations

import os

os.environ["REDIS_URL"] = ""  # belt: nothing below may reach a real Redis

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.backend import auth_throttle, cache, user_store
from dashboard.backend.db import schema
from dashboard.backend.routes import auth as auth_routes
from dashboard.backend.routes import system as system_routes
from dashboard.backend.routes import terminal as terminal_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    # braces: Redis off everywhere it is resolved at call time
    monkeypatch.setattr(cache, "_get_redis", lambda: None)
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("JWT_SECRET", "test-secret-pr1-not-a-real-secret-0123456789")
    monkeypatch.setattr(auth_routes, "_jwt_secret_cache", None)
    monkeypatch.delenv("OPS_API_KEY", raising=False)
    monkeypatch.delenv("TERMINAL_API_KEY", raising=False)

    # isolated databases
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.setattr(user_store, "_DB_PATH", str(tmp_path / "user_data.db"))
    monkeypatch.setattr(user_store, "_initialized", False)
    auth_routes.init_auth_tables()

    # deterministic public data for the terminal's public endpoints
    monkeypatch.setattr(terminal_routes, "read_active_trades", lambda: [])
    monkeypatch.setattr(terminal_routes, "read_today_signals", lambda: [])

    auth_throttle._reset_for_tests()

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(terminal_routes.router)
    app.include_router(system_routes.router)
    with TestClient(app) as c:
        yield c
    auth_throttle._reset_for_tests()


def _register(c, email, password="correct-horse-1"):
    r = c.post("/api/auth/register", json={"email": email, "password": password, "name": email.split("@")[0]})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════ S1 ═══════════════════════════════════════

PRIVATE = [
    ("get", "/api/preferences", None),
    ("post", "/api/preferences", {"capital": 1}),
    ("get", "/api/journal", None),
    ("post", "/api/journal", {"symbol": "RELIANCE", "entry": 100}),
    ("patch", "/api/journal/1", {"notes": "x"}),
    ("delete", "/api/journal/1", None),
    ("get", "/api/performance", None),
    ("post", "/api/trades/RELIANCE/taken", {}),
]


@pytest.mark.parametrize("method,path,body", PRIVATE)
def test_private_terminal_endpoints_reject_unauthenticated(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 401, f"{method.upper()} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path,body", PRIVATE)
def test_client_user_id_cannot_substitute_for_a_token(client, method, path, body):
    """The old bypass: ?user_id=<victim> with no token. Must still be 401."""
    kwargs = {"json": body} if body is not None else {}
    sep = "&" if "?" in path else "?"
    r = getattr(client, method)(f"{path}{sep}user_id=1", **kwargs)
    assert r.status_code == 401


def test_invalid_token_is_rejected(client):
    r = client.get("/api/preferences", headers=_auth("not-a-real-jwt"))
    assert r.status_code == 401


def test_user_a_cannot_read_or_modify_user_b_data(client):
    a = _register(client, "alice@example.com")
    b = _register(client, "bob@example.com")
    ha, hb = _auth(a["token"]), _auth(b["token"])

    # A writes a journal entry and preferences
    entry = client.post("/api/journal", json={"symbol": "TCS", "entry": 3500, "notes": "alice-secret"}, headers=ha)
    assert entry.status_code == 200
    entry_id = entry.json()["id"]
    assert client.post("/api/preferences", json={"capital": 555_555}, headers=ha).json()["capital"] == 555_555

    # B sees none of it
    jb = client.get("/api/journal", headers=hb).json()
    assert jb["count"] == 0
    assert client.get("/api/preferences", headers=hb).json()["capital"] != 555_555
    assert client.get("/api/performance", headers=hb).json()["open_trades"] == 0

    # B cannot modify or delete A's entry, even knowing its id
    assert client.patch(f"/api/journal/{entry_id}", json={"notes": "pwned"}, headers=hb).status_code == 404
    assert client.delete(f"/api/journal/{entry_id}", headers=hb).status_code == 404

    # A's entry is intact
    ja = client.get("/api/journal", headers=ha).json()
    assert ja["count"] == 1 and ja["entries"][0]["notes"] == "alice-secret"


def test_query_user_id_is_ignored_when_authenticated(client):
    """An authenticated A passing ?user_id=<B> still reads/writes A's own data."""
    a = _register(client, "alice@example.com")
    b = _register(client, "bob@example.com")
    ha = _auth(a["token"])
    client.post(f"/api/journal?user_id={b['user']['id']}", json={"symbol": "INFY", "entry": 1500}, headers=ha)
    assert client.get("/api/journal", headers=_auth(b["token"])).json()["count"] == 0
    assert client.get("/api/journal", headers=ha).json()["count"] == 1


def test_mark_taken_is_scoped_to_the_token_user(client):
    a = _register(client, "alice@example.com")
    b = _register(client, "bob@example.com")
    r = client.post("/api/trades/HDFCBANK/taken", json={}, headers=_auth(a["token"]))
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/api/journal", headers=_auth(b["token"])).json()["count"] == 0


def test_legacy_planted_rows_are_not_inherited(client):
    """Rows anyone could write while the endpoints were open — the shared
    "default" and bare-id keys — must not surface for a real account or for
    anonymous callers."""
    user_store.set_preferences("default", {"capital": 111_111})
    a = _register(client, "alice@example.com")
    user_store.set_preferences(str(a["user"]["id"]), {"capital": 222_222})  # bare-id plant
    user_store.add_journal_entry(str(a["user"]["id"]), {"symbol": "PLANTED", "entry": 1})

    prefs = client.get("/api/preferences", headers=_auth(a["token"])).json()
    assert prefs["capital"] not in (111_111, 222_222)
    assert client.get("/api/journal", headers=_auth(a["token"])).json()["count"] == 0
    assert terminal_routes._prefs_for(None)["capital"] == user_store.DEFAULT_PREFERENCES["capital"]


def test_public_terminal_endpoints_still_work_anonymously(client):
    """Public, personalisable endpoints keep working logged-out (defaults)."""
    assert client.get("/api/summary").status_code == 200
    assert client.get("/api/trades").status_code == 200
    r = client.get("/api/pnl/daily")
    assert r.status_code == 200 and isinstance(r.json(), dict)


def test_daily_pnl_never_shows_another_users_journal_to_anonymous(client):
    a = _register(client, "alice@example.com")
    client.post("/api/journal", json={"symbol": "SBIN", "entry": 800, "exit": 820, "qty": 10, "status": "CLOSED"},
                headers=_auth(a["token"]))
    anon = client.get("/api/pnl/daily").json()
    assert all(t.get("symbol") != "SBIN" for t in anon.get("trades", []))


# ═══════════════════════════════ S2 ═══════════════════════════════════════

OPS_ENDPOINTS = [("get", "/api/system/debug/cache"), ("get", "/api/system/debug/platform"),
                 ("get", "/api/system/debug/signals"), ("get", "/api/system/debug/watchlist-sync")]


@pytest.mark.parametrize("method,path", OPS_ENDPOINTS)
def test_debug_endpoints_denied_when_ops_key_missing(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 403
    r = getattr(client, method)(path, headers={"X-Ops-Key": "anything"})
    assert r.status_code == 403  # a guessed key must not work either


def test_upgrade_denied_when_ops_key_missing_and_role_unchanged(client):
    a = _register(client, "alice@example.com")
    r = client.post("/api/auth/upgrade", headers=_auth(a["token"]))
    assert r.status_code == 403
    assert client.get("/api/auth/me", headers=_auth(a["token"])).json()["role"] == "FREE"


def test_ops_key_wrong_value_denied_and_right_value_allowed(client, monkeypatch):
    monkeypatch.setenv("OPS_API_KEY", "real-ops-key")
    a = _register(client, "alice@example.com")
    h = _auth(a["token"])
    assert client.post("/api/auth/upgrade", headers={**h, "X-Ops-Key": "wrong"}).status_code == 403
    assert client.post("/api/auth/upgrade", headers=h).status_code == 403
    assert client.post("/api/auth/upgrade", headers={**h, "X-Ops-Key": "real-ops-key"}).status_code == 200


def test_whitespace_only_ops_key_counts_as_missing(client, monkeypatch):
    monkeypatch.setenv("OPS_API_KEY", "   ")
    assert client.get("/api/system/debug/cache", headers={"X-Ops-Key": ""}).status_code == 403
    assert client.get("/api/system/debug/cache", headers={"X-Ops-Key": "   "}).status_code == 403


# ═══════════════════════════════ S3 ═══════════════════════════════════════

def _login(c, email, password, xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    return c.post("/api/auth/login", json={"email": email, "password": password}, headers=headers)


def test_normal_login_still_works(client):
    _register(client, "alice@example.com", "correct-horse-1")
    r = _login(client, "alice@example.com", "correct-horse-1")
    assert r.status_code == 200 and r.json()["token"]
    me = client.get("/api/auth/me", headers=_auth(r.json()["token"]))
    assert me.status_code == 200 and me.json()["email"] == "alice@example.com"


def test_spoofed_xff_cannot_bypass_account_throttle(client):
    """Each guess claims a different client IP. The per-account limit still
    trips, and then blocks even the CORRECT password."""
    _register(client, "victim@example.com", "correct-horse-1")
    for i in range(auth_throttle.ACCOUNT_MAX_FAILURES):
        r = _login(client, "victim@example.com", f"guess-{i}", xff=f"203.0.113.{i + 1}")
        assert r.status_code == 401
    r = _login(client, "victim@example.com", "correct-horse-1", xff="198.51.100.250")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_account_throttle_is_case_and_whitespace_insensitive(client):
    _register(client, "victim@example.com", "correct-horse-1")
    variants = ["VICTIM@example.com", " victim@example.com", "Victim@Example.com ", "victim@EXAMPLE.com", "victim@example.com"]
    for v in variants[: auth_throttle.ACCOUNT_MAX_FAILURES]:
        _login(client, v, "wrong")
    assert _login(client, "victim@example.com", "correct-horse-1").status_code == 429


def test_unknown_email_throttled_identically_no_enumeration(client):
    """Same 401 then same 429 whether or not the account exists."""
    _register(client, "real@example.com", "correct-horse-1")
    for email in ("real@example.com", "ghost@example.com"):
        codes = [_login(client, email, "wrong", xff="192.0.2.10").status_code
                 for _ in range(auth_throttle.ACCOUNT_MAX_FAILURES + 1)]
        assert codes[:-1] == [401] * auth_throttle.ACCOUNT_MAX_FAILURES
        assert codes[-1] == 429
    real = _login(client, "real@example.com", "wrong").json()["detail"]
    ghost = _login(client, "ghost@example.com", "wrong").json()["detail"]
    assert real == ghost


def test_per_ip_throttle_stops_credential_stuffing(client):
    """One source spraying many different accounts trips the per-IP limit."""
    for i in range(auth_throttle.IP_MAX_FAILURES):
        assert _login(client, f"user{i}@example.com", "wrong").status_code == 401
    assert _login(client, "someone-new@example.com", "wrong").status_code == 429


def test_success_clears_account_counter_but_not_ip_counter(client):
    _register(client, "alice@example.com", "correct-horse-1")
    for _ in range(auth_throttle.ACCOUNT_MAX_FAILURES - 1):
        assert _login(client, "alice@example.com", "typo").status_code == 401
    assert _login(client, "alice@example.com", "correct-horse-1").status_code == 200
    # account budget is fresh again after a genuine success
    for _ in range(auth_throttle.ACCOUNT_MAX_FAILURES - 1):
        assert _login(client, "alice@example.com", "typo").status_code == 401
    assert _login(client, "alice@example.com", "correct-horse-1").status_code == 200
    # …but the IP counter kept accumulating, so success can't be used to reset it
    n, _ = auth_throttle._read(auth_throttle._ip_key("testclient"))
    assert n == 2 * (auth_throttle.ACCOUNT_MAX_FAILURES - 1)


def test_one_users_failures_do_not_lock_out_another_user(client):
    _register(client, "alice@example.com", "correct-horse-1")
    _register(client, "bob@example.com", "correct-horse-2")
    for _ in range(auth_throttle.ACCOUNT_MAX_FAILURES):
        _login(client, "bob@example.com", "wrong", xff="192.0.2.20")
    assert _login(client, "bob@example.com", "correct-horse-2", xff="192.0.2.20").status_code == 429
    assert _login(client, "alice@example.com", "correct-horse-1", xff="192.0.2.30").status_code == 200


def test_throttle_redis_keys_never_contain_raw_email():
    key = auth_throttle._account_key("Someone@Example.com")
    assert "someone" not in key.lower() and "example" not in key.lower()
    assert key == auth_throttle._account_key(" someone@example.com ")
