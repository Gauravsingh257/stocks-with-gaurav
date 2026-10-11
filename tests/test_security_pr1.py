"""
PR1 security hotfix — regression tests.

  S1  user-scoped terminal endpoints require a verified JWT; identity is never
      taken from the client (?user_id= is ignored); user A cannot touch user B.
  S2  ops/debug endpoints FAIL CLOSED when OPS_API_KEY is missing.
  S3  login is throttled per account (immune to X-Forwarded-For spoofing) and
      per IP; normal login still works and success clears the account counter.

Requests go through the REAL dashboard.backend.main:app, so the production
router registration order (and therefore route shadowing) is exercised — an
isolated router would have hidden that /api/journal is served by the public
engine journal, not by the terminal.

Fully isolated: temp SQLite files for both DBs, Redis forced off (the local
.env REDIS_URL points at PRODUCTION — these tests must never reach it), and a
fixed test JWT secret. The app's lifespan is deliberately NOT run (no
TestClient context manager), so no scheduler, websocket or engine thread
starts during tests.
"""
from __future__ import annotations

import os
import tempfile

# Must precede any dashboard import: db/schema.py resolves DB_PATH from
# DATA_DIR at import time, and we must never touch the real dashboard.db.
os.environ["REDIS_URL"] = ""  # belt: nothing below may reach a real Redis
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pr1-tests-"))

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import auth_throttle, cache, rate_limit, user_store
from dashboard.backend.db import schema
from dashboard.backend.routes import auth as auth_routes
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

    # isolated databases (read at call time, so patching the module attr works)
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.setattr(user_store, "_DB_PATH", str(tmp_path / "user_data.db"))
    monkeypatch.setattr(user_store, "_initialized", False)
    # Full production schema (pure idempotent DDL — no CSV/network), so the
    # public journal handler has its `trades` table and we test the real thing.
    schema.init_db()
    auth_routes.init_auth_tables()

    # deterministic public data for the terminal's public endpoints
    monkeypatch.setattr(terminal_routes, "read_active_trades", lambda: [])
    monkeypatch.setattr(terminal_routes, "read_today_signals", lambda: [])

    auth_throttle._reset_for_tests()
    # the per-IP middleware counter is a module global shared by every test in
    # this process; without this, ~300 cumulative requests would 429 spuriously
    rate_limit._counters.clear()

    from dashboard.backend.main import app  # the production app, all routers

    # No `with`: skip lifespan so startup/shutdown side effects never run.
    yield TestClient(app)

    auth_throttle._reset_for_tests()
    rate_limit._counters.clear()


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
    ("get", "/api/terminal/journal", None),
    ("post", "/api/terminal/journal", {"symbol": "RELIANCE", "entry": 100}),
    ("patch", "/api/terminal/journal/1", {"notes": "x"}),
    ("delete", "/api/terminal/journal/1", None),
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
    entry = client.post("/api/terminal/journal", json={"symbol": "TCS", "entry": 3500, "notes": "alice-secret"}, headers=ha)
    assert entry.status_code == 200
    entry_id = entry.json()["id"]
    assert client.post("/api/preferences", json={"capital": 555_555}, headers=ha).json()["capital"] == 555_555

    # B sees none of it
    jb = client.get("/api/terminal/journal", headers=hb).json()
    assert jb["count"] == 0
    assert client.get("/api/preferences", headers=hb).json()["capital"] != 555_555
    assert client.get("/api/performance", headers=hb).json()["open_trades"] == 0

    # B cannot modify or delete A's entry, even knowing its id
    assert client.patch(f"/api/terminal/journal/{entry_id}", json={"notes": "pwned"}, headers=hb).status_code == 404
    assert client.delete(f"/api/terminal/journal/{entry_id}", headers=hb).status_code == 404

    # A's entry is intact
    ja = client.get("/api/terminal/journal", headers=ha).json()
    assert ja["count"] == 1 and ja["entries"][0]["notes"] == "alice-secret"


def test_query_user_id_is_ignored_when_authenticated(client):
    """An authenticated A passing ?user_id=<B> still reads/writes A's own data."""
    a = _register(client, "alice@example.com")
    b = _register(client, "bob@example.com")
    ha = _auth(a["token"])
    client.post(f"/api/terminal/journal?user_id={b['user']['id']}", json={"symbol": "INFY", "entry": 1500}, headers=ha)
    assert client.get("/api/terminal/journal", headers=_auth(b["token"])).json()["count"] == 0
    assert client.get("/api/terminal/journal", headers=ha).json()["count"] == 1


def test_mark_taken_is_scoped_to_the_token_user(client):
    a = _register(client, "alice@example.com")
    b = _register(client, "bob@example.com")
    r = client.post("/api/trades/HDFCBANK/taken", json={}, headers=_auth(a["token"]))
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/api/terminal/journal", headers=_auth(b["token"])).json()["count"] == 0


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
    assert client.get("/api/terminal/journal", headers=_auth(a["token"])).json()["count"] == 0
    assert terminal_routes._prefs_for(None)["capital"] == user_store.DEFAULT_PREFERENCES["capital"]


def test_public_terminal_endpoints_still_work_anonymously(client):
    """Public, personalisable endpoints keep working logged-out (defaults)."""
    assert client.get("/api/summary").status_code == 200
    assert client.get("/api/trades").status_code == 200
    r = client.get("/api/pnl/daily")
    assert r.status_code == 200 and isinstance(r.json(), dict)


def test_daily_pnl_never_shows_another_users_journal_to_anonymous(client):
    a = _register(client, "alice@example.com")
    client.post("/api/terminal/journal", json={"symbol": "SBIN", "entry": 800, "exit": 820, "qty": 10, "status": "CLOSED"},
                headers=_auth(a["token"]))
    anon = client.get("/api/pnl/daily").json()
    assert all(t.get("symbol") != "SBIN" for t in anon.get("trades", []))


# ═════════════════ Route resolution (production order) ════════════════════
# /api/journal is the PUBLIC engine trade journal; the personal one lives at
# /api/terminal/journal. These assert the real main:app wiring, because the
# bug being guarded against is invisible to an isolated-router test.

def _routes_for(client, path: str, method: str):
    """Routes on the app THIS CLIENT serves.

    Deliberately not `from dashboard.backend.main import app`: in a full-suite
    run that re-import did not yield the same route set as the app under test
    (0 matches in CI while requests to those very paths succeeded). Inspecting
    client.app removes the ambiguity and checks the object actually serving.
    """
    return [r for r in client.app.routes
            if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or ())]


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/journal"), ("POST", "/api/journal"),
    ("GET", "/api/terminal/journal"), ("POST", "/api/terminal/journal"),
    ("PATCH", "/api/terminal/journal/{entry_id}"), ("DELETE", "/api/terminal/journal/{entry_id}"),
])
def test_no_duplicate_route_registrations(client, method, path):
    """No path+method may be registered twice: the second is unreachable."""
    matches = _routes_for(client, path, method)
    assert len(matches) <= 1, (
        f"{method} {path} registered {len(matches)}x: "
        + ", ".join(f"{r.endpoint.__module__}.{r.endpoint.__name__}" for r in matches)
    )


def test_public_journal_is_served_by_the_public_handler(client):
    """GET /api/journal must remain the public engine journal, not the
    personal one — and must NOT start demanding a token."""
    matches = _routes_for(client, "/api/journal", "GET")
    assert len(matches) == 1, f"expected exactly one handler, got {len(matches)}"
    assert matches[0].endpoint.__module__.endswith("routes.journal")
    assert client.get("/api/journal?limit=1").status_code == 200  # still public


def test_personal_journal_is_served_by_the_terminal_handler(client):
    for method, path in [("GET", "/api/terminal/journal"), ("POST", "/api/terminal/journal"),
                         ("PATCH", "/api/terminal/journal/{entry_id}"),
                         ("DELETE", "/api/terminal/journal/{entry_id}")]:
        matches = _routes_for(client, path, method)
        assert len(matches) == 1, f"{method} {path}: got {len(matches)}"
        assert matches[0].endpoint.__module__.endswith("routes.terminal")


def test_personal_journal_round_trip_through_the_real_app(client):
    """The secured GET is actually REACHABLE now (it was shadowed before)."""
    a = _register(client, "alice@example.com")
    ha = _auth(a["token"])
    created = client.post("/api/terminal/journal", json={"symbol": "WIPRO", "entry": 400, "notes": "mine"}, headers=ha)
    assert created.status_code == 200
    got = client.get("/api/terminal/journal", headers=ha).json()
    assert got["count"] == 1 and got["entries"][0]["notes"] == "mine"
    # the public journal is a different dataset and never contains it
    pub = client.get("/api/journal?limit=200")
    assert pub.status_code == 200
    assert "mine" not in pub.text


def test_public_journal_write_endpoints_are_unaffected(client):
    """POST /api/journal/trade and /api/journal/sync belong to the public
    router and must not have been captured by the terminal's auth."""
    for path in ("/api/journal/trade", "/api/journal/sync"):
        matches = _routes_for(client, path, "POST")
        assert len(matches) == 1 and matches[0].endpoint.__module__.endswith("routes.journal")
        # reachable without a bearer token (not 401/403 from our new guards)
        assert client.post(path, json={}).status_code not in (401, 403)


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
