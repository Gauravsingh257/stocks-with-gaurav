"""
PR2 — Product Health metric truth.

Every assertion reconciles a dashboard number against the raw rows that
produced it. The defect being guarded against: user-facing KPIs counted
`anon_id` (a per-browser id), so anonymous visitors and one person's several
devices were all reported as "users".

The six entities must never share a number:
    accounts · users · browsers · tab sessions · events · engagement
"""
from __future__ import annotations

import os
import tempfile

os.environ["REDIS_URL"] = ""
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pr2-tests-"))

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

from dashboard.backend.db import analytics as store  # noqa: E402
from dashboard.backend.db import schema  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
TODAY = datetime.now(IST).date().isoformat()


def _day(offset: int) -> str:
    return (datetime.now(IST).date() - timedelta(days=offset)).isoformat()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated DB with the real schema; no internal accounts unless a test adds one."""
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "dashboard.db")
    monkeypatch.setattr(store, "_ready", False)
    monkeypatch.setenv("PRODUCT_HEALTH_INTERNAL_EMAILS", "")
    monkeypatch.delenv("PRODUCT_HEALTH_MIN_COHORT", raising=False)
    schema.init_db()
    from dashboard.backend.routes import auth as auth_routes
    auth_routes.init_auth_tables()
    store.ensure_tables()
    yield


def _add_user(email: str, role: str = "FREE") -> int:
    conn = schema.get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, name, role) VALUES (?,?,?,?)",
            (email.lower(), "x", email.split("@")[0], role),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _event(event: str, *, user_id=None, anon_id=None, session_id=None, day=None):
    """Insert one event, optionally backdated (insert_event always stamps today)."""
    store.insert_event(event=event, user_id=user_id, anon_id=anon_id, session_id=session_id)
    if day and day != TODAY:
        conn = schema.get_connection()
        try:
            conn.execute(
                "UPDATE product_events SET day = ?, ts = ? WHERE id = (SELECT MAX(id) FROM product_events)",
                (day, f"{day}T06:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()


def _raw(sql: str, params=()):
    conn = schema.get_connection()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# ═══════════ the headline scenario ═══════════════════════════════════════════

def test_one_person_many_logins_devices_sessions_is_one_user(db):
    """1 signup · 5 logins · 5 sessions · 50 events · 3 devices  →  ONE user.

    This is the exact shape that produced the inflated numbers: the same
    person on three browsers used to count as three "users"."""
    uid = _add_user("solo@example.com")
    for d in range(3):                       # three devices = three anon ids
        for s in range(2):                   # sessions per device
            _event("login", user_id=uid, anon_id=f"browser-{d}", session_id=f"sess-{d}-{s}")
    # top the logins up to exactly 5 and pad to 50 events total
    sessions = {f"sess-{d}-{s}" for d in range(3) for s in range(2)}
    assert len(sessions) == 6
    for i in range(44):
        _event("command_center_viewed", user_id=uid,
               anon_id=f"browser-{i % 3}", session_id=f"sess-{i % 3}-{i % 2}")

    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 1, "three devices are still one person"
    assert k["users"]["active_in_window"] == 1
    assert k["accounts"]["registered_total"] == 1
    assert k["browsers"]["distinct_all_time"] == 3      # browsers reported separately
    assert k["tab_sessions"] == 6                        # sessions are their own number
    assert k["events"] == 50
    # and each reconciles with the raw table
    assert k["users"]["dau"] == _raw(
        "SELECT COUNT(DISTINCT user_id) FROM product_events WHERE user_id IS NOT NULL")
    assert k["events"] == _raw("SELECT COUNT(*) FROM product_events")
    assert k["tab_sessions"] == _raw(
        "SELECT COUNT(DISTINCT session_id) FROM product_events WHERE session_id IS NOT NULL")


def test_two_distinct_people_are_two_users(db):
    a, b = _add_user("a@example.com"), _add_user("b@example.com")
    for uid in (a, b):
        _event("command_center_viewed", user_id=uid, anon_id=f"br-{uid}", session_id=f"s-{uid}")
    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 2
    assert k["accounts"]["registered_total"] == 2


# ═══════════ entity separation ═══════════════════════════════════════════════

def test_anonymous_visitors_are_browsers_not_users(db):
    """The core defect: anonymous traffic must never raise the user count."""
    uid = _add_user("real@example.com")
    _event("command_center_viewed", user_id=uid, anon_id="known-browser", session_id="s1")
    for i in range(20):                               # 20 anonymous browsers
        _event("page_view", anon_id=f"anon-{i}", session_id=f"as-{i}")

    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 1, "anonymous browsers must not count as users"
    assert k["browsers"]["anonymous_in_window"] == 20
    assert k["browsers"]["distinct_all_time"] == 21
    assert k["accounts"]["registered_total"] == 1
    assert k["events"] == 21
    # the old (wrong) basis would have said 21 users
    assert k["users"]["dau"] != _raw(
        "SELECT COUNT(DISTINCT anon_id) FROM product_events WHERE anon_id IS NOT NULL")


def test_no_metric_silently_mixes_entities(db):
    uid = _add_user("x@example.com")
    _event("command_center_viewed", user_id=uid, anon_id="b1", session_id="s1")
    _event("page_view", anon_id="b2", session_id="s2")
    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 1 and k["browsers"]["anonymous_in_window"] == 1
    assert k["tab_sessions"] == 2 and k["events"] == 2
    assert "accounts" in k and "users" in k and "browsers" in k
    assert "anon" not in str(k["users"]).lower(), "user block must not expose browser ids"


# ═══════════ engaged vs active ═══════════════════════════════════════════════

def test_engaged_excludes_passive_page_views_active_does_not(db):
    passive_only = _add_user("passive@example.com")
    engaged = _add_user("engaged@example.com")
    _event("page_view", user_id=passive_only, anon_id="b1", session_id="s1")
    _event("landing_viewed", user_id=passive_only, anon_id="b1", session_id="s1")
    _event("watchlist_stock_added", user_id=engaged, anon_id="b2", session_id="s2")

    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 2, "any qualifying event counts as active"
    assert k["users"]["engaged_dau"] == 1, "only feature use counts as engaged"


def test_session_end_telemetry_never_makes_a_user_active(db):
    uid = _add_user("ghost@example.com")
    _event("session_end", user_id=uid, anon_id="b1", session_id="s1")
    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 0
    assert k["users"]["engaged_dau"] == 0
    assert k["events"] == 1, "the raw event is still stored — nothing is discarded"


# ═══════════ internal exclusion ══════════════════════════════════════════════

def test_internal_accounts_excluded_by_default_and_toggleable(db, monkeypatch):
    internal = _add_user("owner@swg.internal")
    customer = _add_user("customer@example.com")
    _event("command_center_viewed", user_id=internal, anon_id="bi", session_id="si")
    _event("command_center_viewed", user_id=customer, anon_id="bc", session_id="sc")
    monkeypatch.setenv("PRODUCT_HEALTH_INTERNAL_EMAILS", "owner@swg.internal")

    assert store.health_kpis(days=7)["users"]["dau"] == 1          # default: excluded
    assert store.health_kpis(days=7, include_internal=True)["users"]["dau"] == 2
    assert store.health_kpis(days=7)["internal_accounts_excluded"] == 1


def test_admin_role_alone_does_not_make_someone_internal(db, monkeypatch):
    """An admin may also be a genuine customer: only the explicit list excludes."""
    admin_customer = _add_user("admin-but-real@example.com", role="ADMIN")
    _event("command_center_viewed", user_id=admin_customer, anon_id="b", session_id="s")
    monkeypatch.setenv("PRODUCT_HEALTH_INTERNAL_EMAILS", "someone-else@swg.internal")
    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 1, "ADMIN role must not imply internal"
    assert k["internal_accounts_excluded"] == 0


def test_excluding_internal_never_removes_anonymous_traffic(db, monkeypatch):
    internal = _add_user("owner@swg.internal")
    _event("command_center_viewed", user_id=internal, anon_id="bi", session_id="si")
    _event("page_view", anon_id="anon-1", session_id="s1")
    monkeypatch.setenv("PRODUCT_HEALTH_INTERNAL_EMAILS", "owner@swg.internal")
    k = store.health_kpis(days=7)
    assert k["users"]["dau"] == 0
    assert k["browsers"]["anonymous_in_window"] == 1


# ═══════════ honesty: no invented numbers ════════════════════════════════════

def test_unsupported_metrics_are_null_with_a_reason_not_zero(db):
    k = store.health_kpis(days=7)
    assert k["verified_users"] is None and k["verified_users_unavailable_reason"]
    assert k["day7_retention_pct"] is None and k["day7_unavailable_reason"]
    assert k["auth_signals"]["failed_logins"] is None


def test_retention_withheld_while_the_cohort_is_too_small(db, monkeypatch):
    monkeypatch.setenv("PRODUCT_HEALTH_MIN_COHORT", "5")
    uid = _add_user("one@example.com")
    _event("command_center_viewed", user_id=uid, anon_id="b", session_id="s", day=_day(3))
    _event("command_center_viewed", user_id=uid, anon_id="b", session_id="s", day=_day(2))
    k = store.health_kpis(days=30)
    assert k["day1_retention_pct"] is None, "a 1-person cohort cannot yield a rate"
    assert "below the 5" in k["day1_unavailable_reason"]


def test_business_health_has_no_invented_revenue(db, monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_PRICE_INR", "1200")
    _add_user("p@example.com", role="PREMIUM")
    b = store.business_health()
    for banned in ("mrr_estimate_inr", "arpu_estimate_inr", "mrr", "arpu", "ltv_inr", "cac_inr"):
        assert banned not in b, f"{banned} must not be reported without a billing system"
    assert b["billing"]["status"] == "not_implemented"
    assert b["registered_users"] == 1


def test_every_response_states_its_basis_and_data_boundary(db):
    uid = _add_user("u@example.com")
    _event("command_center_viewed", user_id=uid, anon_id="b", session_id="s")
    for payload in (store.health_kpis(days=7), store.activation_funnel(days=7),
                    store.feature_adoption(days=7)):
        assert payload["unique_user_data_from"]
        assert "MEASUREMENT" in payload["correction_note"]
        assert payload["entities"]["browsers"].startswith("DISTINCT anon_id")


# ═══════════ funnel & adoption ═══════════════════════════════════════════════

def test_funnel_separates_browser_and_user_phases(db):
    uid = _add_user("f@example.com")
    for i in range(5):
        _event("page_view", anon_id=f"anon-{i}", session_id=f"s{i}")
    _event("login", user_id=uid, anon_id="known", session_id="sk")
    _event("command_center_viewed", user_id=uid, anon_id="known", session_id="sk")

    f = store.activation_funnel(days=7)
    assert {s["entity"] for s in f["anonymous_phase"]} == {"browsers"}
    assert {s["entity"] for s in f["authenticated_phase"]} == {"users"}
    assert f["anonymous_phase"][0]["count"] == 6      # 5 anon + 1 known browser
    assert f["authenticated_phase"][0]["count"] == 1  # one signed-in person


def test_adoption_ratio_uses_one_entity_on_both_sides(db):
    a, b = _add_user("a@example.com"), _add_user("b@example.com")
    _event("watchlist_opened", user_id=a, anon_id="ba", session_id="sa")
    _event("command_center_viewed", user_id=b, anon_id="bb", session_id="sb")
    _event("watchlist_opened", anon_id="anon-x", session_id="sx")   # anonymous use

    ad = store.feature_adoption(days=7)
    wl = next(f for f in ad["features"] if f["event"] == "watchlist_opened")
    assert ad["active_users"] == 2
    assert wl["unique_users"] == 1 and wl["browsers"] == 2 and wl["uses"] == 2
    assert wl["adoption_pct"] == 50.0, "users ÷ active users, anonymous excluded from the ratio"


def test_adoption_pct_is_null_not_zero_when_there_are_no_active_users(db):
    _event("watchlist_opened", anon_id="anon-only", session_id="s")
    ad = store.feature_adoption(days=7)
    assert ad["active_users"] == 0
    assert all(f["adoption_pct"] is None for f in ad["features"])


# ═══════════ raw data preserved ══════════════════════════════════════════════

def test_read_path_never_mutates_the_raw_events(db):
    uid = _add_user("keep@example.com")
    for _ in range(10):
        _event("page_view", user_id=uid, anon_id="b", session_id="s")
    before = _raw("SELECT COUNT(*) FROM product_events")
    checksum_before = _raw("SELECT COALESCE(SUM(id),0) FROM product_events")
    store.health_kpis(days=7)
    store.activation_funnel(days=7)
    store.feature_adoption(days=7)
    store.business_health()
    assert _raw("SELECT COUNT(*) FROM product_events") == before
    assert _raw("SELECT COALESCE(SUM(id),0) FROM product_events") == checksum_before
