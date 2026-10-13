"""
dashboard/backend/db/analytics.py
=================================
First-party product-analytics event store for the Sprint-1 validation window.

A single `product_events` table in the same durable SQLite DB (dashboard.db /
Railway volume). Additive; never touches engine/user tables beyond reading. The
frontend `track()` posts events here (in addition to GA4/Clarity), which lets
the internal Product Health Dashboard compute exact KPIs + the activation funnel
in real time, independent of any third-party analytics being configured.

Self-initialises the schema on first use.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .schema import get_connection

logger = logging.getLogger("dashboard.db.analytics")
_IST = timezone(timedelta(hours=5, minutes=30))

_DDL = """
CREATE TABLE IF NOT EXISTS product_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,          -- ISO-8601 UTC
    day         TEXT NOT NULL,          -- YYYY-MM-DD (IST) for daily grouping/retention
    event       TEXT NOT NULL,
    anon_id     TEXT,                   -- stable per-browser id
    user_id     INTEGER,                -- set when authenticated
    session_id  TEXT,                   -- per-session id
    path        TEXT,
    device      TEXT,                   -- mobile | tablet | desktop
    props       TEXT                    -- JSON blob
);
CREATE INDEX IF NOT EXISTS idx_pe_event ON product_events(event);
CREATE INDEX IF NOT EXISTS idx_pe_day   ON product_events(day);
CREATE INDEX IF NOT EXISTS idx_pe_anon  ON product_events(anon_id);
CREATE INDEX IF NOT EXISTS idx_pe_sess  ON product_events(session_id);
"""

_ready = False


def ensure_tables() -> None:
    global _ready
    if _ready:
        return
    conn = get_connection()
    try:
        conn.executescript(_DDL)
        conn.commit()
        _ready = True
    finally:
        conn.close()


def _today_ist() -> str:
    return datetime.now(_IST).date().isoformat()


def _window_start(days: int) -> str:
    d = max(1, int(days))
    start = datetime.now(_IST).date() - timedelta(days=d - 1)
    return start.isoformat()


def insert_event(
    *,
    event: str,
    anon_id: Optional[str] = None,
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    path: Optional[str] = None,
    device: Optional[str] = None,
    props: Optional[Dict[str, Any]] = None,
) -> None:
    """Store one event. Best-effort — never raises to the caller."""
    ensure_tables()
    ev = (event or "").strip()[:80]
    if not ev:
        return
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO product_events (ts, day, event, anon_id, user_id, session_id, path, device, props)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                _today_ist(),
                ev,
                (anon_id or None),
                (int(user_id) if user_id is not None else None),
                (session_id or None),
                (str(path)[:200] if path else None),
                (str(device)[:16] if device else None),
                (json.dumps(props)[:2000] if props else None),
            ),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("insert_event failed: %s", exc)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# READ PATH — metric truth (PR2)
# ═══════════════════════════════════════════════════════════════════════════
# Until 2026-10-13 every user-facing KPI counted `anon_id`, a per-browser
# localStorage value. That is a count of BROWSERS, not people: one person
# averaged 2.25 browser ids (max 5), 162 of 228 ids only ever produced
# page-view noise, and anonymous visitors were counted as "users". The table
# always carried a trustworthy `user_id` (attached server-side from the
# verified JWT, never client-supplied) — no KPI read it.
#
# The rules below exist so that cannot happen again:
#   * ACCOUNTS, USERS, BROWSERS, TAB SESSIONS, EVENTS and ENGAGEMENT are six
#     different entities and never share a number.
#   * Anything derived from `anon_id` is reported as "browsers (approx.)".
#   * A metric we cannot support is `None` plus a reason — never 0, and never
#     an estimate dressed as a measurement.
# Raw events are untouched: this module's write path is unchanged and nothing
# here deletes, rewrites or backfills a row.

# Pure telemetry — never evidence that a human did something.
_TELEMETRY_EVENTS = ("session_end",)

# Passive page impressions. `page_view` fires on EVERY route change alongside
# the named feature event, so counting it as engagement double-counts;
# landing/login/signup views are marketing pages a visitor can hit by accident.
# Deliberately NOT passive: stock_page_viewed (a chosen piece of content).
_PASSIVE_EVENTS = _TELEMETRY_EVENTS + (
    "page_view", "landing_viewed", "login_page_viewed", "signup_page_viewed",
)

# The day first-party events began. Unique-user history cannot exist before it
# and is never fabricated.
_EVENTS_EPOCH_FALLBACK = "2026-07-12"


def _sql_not_in(values: tuple) -> str:
    return "(" + ",".join("?" * len(values)) + ")"


def _internal_user_ids(conn) -> list[int]:
    """User ids treated as internal (our own traffic), from an EXPLICIT list.

    Deliberately NOT "role = ADMIN" and NOT the auth email allowlist: admin is
    an authorisation fact, and an admin may also be a genuine customer whose
    usage belongs in the numbers. Configure with PRODUCT_HEALTH_INTERNAL_EMAILS
    (comma-separated); set it empty to treat everyone as external.
    """
    raw = os.getenv("PRODUCT_HEALTH_INTERNAL_EMAILS", "hellogaurav2577@gmail.com")
    emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
    if not emails:
        return []
    try:
        rows = conn.execute(
            "SELECT id FROM users WHERE lower(email) IN " + _sql_not_in(tuple(emails)), emails
        ).fetchall()
        return [int(r["id"]) for r in rows]
    except Exception as exc:  # users table missing in a bare test DB
        logger.debug("internal-user lookup failed: %s", exc)
        return []


def _exclude_internal_sql(internal_ids: list[int], col: str = "user_id") -> tuple[str, list]:
    """SQL fragment + params excluding internal accounts. Anonymous rows have a
    NULL user_id and are never removed by this."""
    if not internal_ids:
        return "", []
    return f" AND ({col} IS NULL OR {col} NOT IN {_sql_not_in(tuple(internal_ids))})", list(internal_ids)


def _events_epoch(conn) -> str:
    row = conn.execute("SELECT MIN(day) AS d FROM product_events").fetchone()
    return (row["d"] if row and row["d"] else _EVENTS_EPOCH_FALLBACK)


def _accounts_epoch(conn) -> str | None:
    try:
        row = conn.execute("SELECT MIN(created_at) AS d FROM users").fetchone()
        return row["d"] if row and row["d"] else None
    except Exception:
        return None


def _active_users(conn, start: str, internal_ids: list[int], engaged: bool = False) -> int:
    """DISTINCT authenticated users in the window.

    `engaged=False` (DAU/WAU/MAU): any qualifying event, excluding pure
    telemetry. `engaged=True`: excludes passive page impressions too, so it
    counts people who actually used a feature.
    """
    excl = _PASSIVE_EVENTS if engaged else _TELEMETRY_EVENTS
    sql = ("SELECT COUNT(DISTINCT user_id) AS c FROM product_events"
           " WHERE day >= ? AND user_id IS NOT NULL AND event NOT IN " + _sql_not_in(excl))
    params: list = [start, *excl]
    frag, p = _exclude_internal_sql(internal_ids)
    sql += frag
    params += p
    return int(conn.execute(sql, params).fetchone()["c"] or 0)


def _anonymous_browsers(conn, start: str) -> int:
    """Distinct browsers in the window that never authenticated. APPROXIMATE —
    a browser is not a person (new device/profile/cleared storage = new id)."""
    return int(conn.execute(
        "SELECT COUNT(*) AS c FROM (SELECT anon_id FROM product_events"
        " WHERE day >= ? AND anon_id IS NOT NULL GROUP BY anon_id HAVING MAX(user_id) IS NULL)",
        (start,),
    ).fetchone()["c"] or 0)


def _count(conn, event: str, start: str, internal_ids: Optional[list[int]] = None) -> int:
    """Raw EVENT count (not users, not browsers)."""
    sql = "SELECT COUNT(*) AS c FROM product_events WHERE event = ? AND day >= ?"
    params: list = [event, start]
    frag, p = _exclude_internal_sql(internal_ids or [])
    sql += frag
    params += p
    row = conn.execute(sql, params).fetchone()
    return int(row["c"] if row else 0)


def _distinct_users_for_event(conn, event: str, start: str, internal_ids: list[int]) -> int:
    sql = ("SELECT COUNT(DISTINCT user_id) AS c FROM product_events"
           " WHERE event = ? AND day >= ? AND user_id IS NOT NULL")
    params: list = [event, start]
    frag, p = _exclude_internal_sql(internal_ids)
    sql += frag
    params += p
    return int(conn.execute(sql, params).fetchone()["c"] or 0)


def _distinct_browsers_for_event(conn, event: str, start: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(DISTINCT anon_id) AS c FROM product_events"
        " WHERE event = ? AND day >= ? AND anon_id IS NOT NULL",
        (event, start),
    ).fetchone()["c"] or 0)


def _account_counts(conn) -> dict[str, Any]:
    """Ground truth from the users table — the only real 'how many people'."""
    out: dict[str, Any] = {"registered_total": 0, "by_role": {}, "new_today": 0,
                           "new_7d": 0, "new_30d": 0}
    try:
        out["registered_total"] = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] or 0)
        out["by_role"] = {str(r["role"]).upper(): int(r["c"]) for r in
                          conn.execute("SELECT role, COUNT(*) AS c FROM users GROUP BY role").fetchall()}
        for key, expr in (("new_today", "date(created_at) = date('now')"),
                          ("new_7d", "created_at >= datetime('now','-7 days')"),
                          ("new_30d", "created_at >= datetime('now','-30 days')")):
            out[key] = int(conn.execute(
                f"SELECT COUNT(*) AS c FROM users WHERE {expr}").fetchone()["c"] or 0)
    except Exception as exc:
        logger.debug("account counts unavailable: %s", exc)
    return out


def metric_basis(days: int, conn, internal_ids: list[int]) -> dict[str, Any]:
    """Provenance shipped with every response so a number can always be traced
    to the entity it counts and the period it can honestly cover."""
    epoch = _events_epoch(conn)
    return {
        "window_days": int(days),
        "since": _window_start(days),
        "unique_user_data_from": epoch,
        "accounts_data_from": _accounts_epoch(conn),
        "internal_accounts_excluded": len(internal_ids),
        "entities": {
            "accounts": "rows in `users` — registered people",
            "users": "DISTINCT product_events.user_id — signed-in people (server-attributed from the JWT)",
            "browsers": "DISTINCT anon_id — per-browser id, APPROXIMATE, not people",
            "tab_sessions": "DISTINCT session_id — one BROWSER TAB, not a login session",
            "events": "raw event rows",
        },
        "correction_note": (
            f"Before 2026-10-13 the 'users' figures on this page counted browser ids "
            f"(anon_id), which mixed anonymous visitors and multiple devices per person. "
            f"They now count signed-in accounts. The resulting drop is a MEASUREMENT "
            f"CORRECTION, not lost users. Unique-user analytics begin {epoch}; earlier "
            f"periods have no per-user history and none has been invented."
        ),
    }


def health_kpis(days: int = 7, include_internal: bool = False) -> dict[str, Any]:
    """Product-Health KPIs, one entity per number."""
    ensure_tables()
    start = _window_start(days)
    conn = get_connection()
    try:
        internal_ids: list[int] = [] if include_internal else _internal_user_ids(conn)
        excl_frag, excl_params = _exclude_internal_sql(internal_ids)

        accounts = _account_counts(conn)
        dau = _active_users(conn, _window_start(1), internal_ids)
        wau = _active_users(conn, _window_start(7), internal_ids)
        mau = _active_users(conn, _window_start(30), internal_ids)

        returning_users = int(conn.execute(
            "SELECT COUNT(DISTINCT pe.user_id) AS c FROM product_events pe"
            " WHERE pe.day >= ? AND pe.user_id IS NOT NULL"
            " AND EXISTS (SELECT 1 FROM product_events p2 WHERE p2.user_id = pe.user_id AND p2.day < ?)"
            + excl_frag.replace("user_id", "pe.user_id"),
            [start, start, *excl_params],
        ).fetchone()["c"] or 0)

        tab_sessions = int(conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS c FROM product_events"
            " WHERE day >= ? AND session_id IS NOT NULL", (start,)).fetchone()["c"] or 0)
        total_events = int(conn.execute(
            "SELECT COUNT(*) AS c FROM product_events WHERE day >= ?", (start,)).fetchone()["c"] or 0)

        # tab-session shape (explicitly a TAB metric, not a login session)
        sess = conn.execute(
            "SELECT session_id,"
            " (julianday(MAX(ts)) - julianday(MIN(ts))) * 86400.0 AS dur,"
            " SUM(CASE WHEN event = 'page_view' THEN 1 ELSE 0 END) AS pv"
            " FROM product_events WHERE session_id IS NOT NULL AND day >= ? GROUP BY session_id",
            (start,),
        ).fetchall()
        durations = [float(r["dur"] or 0) for r in sess]
        pvs = [int(r["pv"] or 0) for r in sess]

        # Why tabs ended — an EVENT count kept as a churn signal.
        session_endings: dict[str, int] = {}
        try:
            for r in conn.execute(
                "SELECT COALESCE(json_extract(props, '$.reason'), 'unknown') AS reason, COUNT(*) AS c"
                " FROM product_events WHERE event = 'session_end' AND day >= ? GROUP BY reason",
                (start,),
            ).fetchall():
                session_endings[str(r["reason"])] = int(r["c"])
        except Exception:  # JSON1 unavailable — non-fatal
            session_endings = {}

        # Retention on real users. With a handful of accounts a percentage is
        # noise, so it is withheld below a floor rather than shown as 0%/100%.
        today = _today_ist()
        d1 = conn.execute(
            "WITH firsts AS ("
            "  SELECT user_id, MIN(day) AS fd FROM product_events WHERE user_id IS NOT NULL"
            + (" AND user_id NOT IN " + _sql_not_in(tuple(internal_ids)) if internal_ids else "")
            + "  GROUP BY user_id"
            ") SELECT COUNT(*) AS cohort,"
            " SUM(CASE WHEN EXISTS ("
            "     SELECT 1 FROM product_events e WHERE e.user_id = firsts.user_id AND e.day = date(firsts.fd, '+1 day')"
            " ) THEN 1 ELSE 0 END) AS retained"
            " FROM firsts WHERE date(fd, '+1 day') <= ?",
            [*internal_ids, today],
        ).fetchone()
        cohort = int(d1["cohort"] or 0)
        retained = int(d1["retained"] or 0)
        min_cohort = int(os.getenv("PRODUCT_HEALTH_MIN_COHORT", "5"))
        d1_pct = round(100.0 * retained / cohort, 1) if cohort >= min_cohort else None

        return {
            **metric_basis(days, conn, internal_ids),
            "include_internal": bool(include_internal),

            # ── ACCOUNTS (people who registered) ──────────────────────────
            "accounts": accounts,

            # ── AUTHENTICATED USERS (distinct signed-in people) ───────────
            "users": {
                "dau": dau, "wau": wau, "mau": mau,
                "engaged_dau": _active_users(conn, _window_start(1), internal_ids, engaged=True),
                "engaged_wau": _active_users(conn, _window_start(7), internal_ids, engaged=True),
                "engaged_mau": _active_users(conn, _window_start(30), internal_ids, engaged=True),
                "active_in_window": _active_users(conn, start, internal_ids),
                "returning": returning_users,
                "ever_authenticated": int(conn.execute(
                    "SELECT COUNT(DISTINCT user_id) AS c FROM product_events WHERE user_id IS NOT NULL"
                ).fetchone()["c"] or 0),
            },

            # ── ANONYMOUS TRAFFIC (browsers — approximate, NOT users) ─────
            "browsers": {
                "anonymous_in_window": _anonymous_browsers(conn, start),
                "distinct_all_time": int(conn.execute(
                    "SELECT COUNT(DISTINCT anon_id) AS c FROM product_events WHERE anon_id IS NOT NULL"
                ).fetchone()["c"] or 0),
                "note": "Browser ids, not people: a new device, profile or cleared storage creates another id.",
            },

            # ── VOLUME ────────────────────────────────────────────────────
            "tab_sessions": tab_sessions,
            "events": total_events,

            # ── ENGAGEMENT (event counts — never user counts) ─────────────
            "engagement_events": {
                "command_center_views": _count(conn, "command_center_viewed", start, internal_ids),
                "nba_clicks": _count(conn, "nba_clicked", start, internal_ids),
                "watchlist_adds": _count(conn, "watchlist_stock_added", start, internal_ids),
                "watchlist_opens": _count(conn, "watchlist_opened", start, internal_ids),
                "research_searches": _count(conn, "global_search", start, internal_ids),
                "ai_research_usage": _count(conn, "ai_research_used", start, internal_ids),
                "telegram_link_clicks": _count(conn, "telegram_link_clicked", start, internal_ids),
            },
            "nba_ctr_pct": (round(100.0 * _count(conn, "nba_clicked", start, internal_ids)
                                  / _count(conn, "command_center_viewed", start, internal_ids), 1)
                            if _count(conn, "command_center_viewed", start, internal_ids) else 0.0),
            "avg_tab_session_seconds": round(sum(durations) / len(durations), 1) if durations else 0.0,
            "pages_per_tab_session": round(sum(pvs) / len(pvs), 2) if pvs else 0.0,
            "session_endings": session_endings,

            # ── RETENTION (users, withheld while the cohort is too small) ──
            "day1_retention_pct": d1_pct,
            "day1_cohort": cohort,
            "day1_unavailable_reason": (
                None if d1_pct is not None else
                f"cohort of {cohort} signed-in users is below the {min_cohort} needed for a meaningful rate"
            ),
            "day7_retention_pct": None,
            "day7_unavailable_reason": "needs ≥7 days of per-user history after a cohort forms",

            # ── AUTH SIGNALS (client-reported — see note) ─────────────────
            "auth_signals": {
                "login_events_client_reported": _count(conn, "login", start, internal_ids),
                "signup_events_client_reported": _count(conn, "signup", start, internal_ids),
                "new_accounts_db_truth": accounts.get("new_7d") if days == 7 else None,
                "failed_logins": None,
                "note": ("Login/signup here are browser-sent events: ad-blockers suppress them and "
                         "anyone can post them. Account creation above comes from the database. "
                         "Failed logins, OTP and provider outcomes are not recorded server-side yet."),
            },

            # ── NOT MEASURABLE YET (never invent a number) ────────────────
            "verified_users": None,
            "verified_users_unavailable_reason": "email/phone verification does not exist yet",
        }
    finally:
        conn.close()


def activation_funnel(days: int = 7, include_internal: bool = False) -> dict[str, Any]:
    """Two-phase funnel. The entity CHANGES at signup — anonymous browsers
    before it, identified accounts after — so each stage states what it counts
    instead of silently mixing the two."""
    ensure_tables()
    start = _window_start(days)
    conn = get_connection()
    try:
        internal_ids: list[int] = [] if include_internal else _internal_user_ids(conn)
        browsers = int(conn.execute(
            "SELECT COUNT(DISTINCT anon_id) AS c FROM product_events WHERE day >= ? AND anon_id IS NOT NULL",
            (start,),
        ).fetchone()["c"] or 0)

        pre = [
            {"stage": "Visitors", "count": browsers, "entity": "browsers"},
            {"stage": "Signed up", "count": _distinct_browsers_for_event(conn, "signup", start), "entity": "browsers"},
        ]
        post = [
            {"stage": "Signed in", "count": _distinct_users_for_event(conn, "login", start, internal_ids), "entity": "users"},
            {"stage": "Opened Command Center", "count": _distinct_users_for_event(conn, "command_center_viewed", start, internal_ids), "entity": "users"},
            {"stage": "Clicked an action", "count": _distinct_users_for_event(conn, "nba_clicked", start, internal_ids), "entity": "users"},
            {"stage": "Opened Research", "count": _distinct_users_for_event(conn, "research_opened", start, internal_ids), "entity": "users"},
            {"stage": "Added to Watchlist", "count": _distinct_users_for_event(conn, "watchlist_stock_added", start, internal_ids), "entity": "users"},
        ]
        for group, base in ((pre, browsers), (post, post[0]["count"])):
            for i, s in enumerate(group):
                prev = group[i - 1]["count"] if i > 0 else s["count"]
                s["conversion_pct"] = round(100.0 * s["count"] / prev, 1) if prev else 0.0
                s["of_base_pct"] = round(100.0 * s["count"] / base, 1) if base else 0.0

        return {
            **metric_basis(days, conn, internal_ids),
            "include_internal": bool(include_internal),
            "anonymous_phase": pre,
            "authenticated_phase": post,
            "note": ("Loose funnel: a stage counts anyone who performed that event in the window, "
                     "not a strict sequence. Browser and user stages are NOT comparable — a browser "
                     "is not a person — so no single conversion rate spans the two phases."),
        }
    finally:
        conn.close()


# ── Feature adoption ─────────────────────────────────────────────────────────
# (display name, event name) — one row per major module.
_FEATURES: list[tuple[str, str]] = [
    ("Command Center", "command_center_viewed"),
    ("Next Best Action", "nba_clicked"),
    ("Terminal", "terminal_opened"),
    ("Research", "research_opened"),
    ("Screeners", "screeners_opened"),
    ("Watchlist", "watchlist_opened"),
    ("Watchlist Add", "watchlist_stock_added"),
    ("Portfolio", "portfolio_viewed"),
    ("OI Intelligence", "oi_intelligence_opened"),
    ("Market Intel", "market_intel_opened"),
    ("Track Record", "track_record_viewed"),
    ("Global Search", "global_search"),
]


def feature_adoption(days: int = 7, include_internal: bool = False) -> dict[str, Any]:
    """Per-module usage. `unique_users` counts signed-in people; `browsers`
    counts anonymous traffic separately; `uses` is an event count. Adoption %
    is users ÷ active users — both sides the same entity."""
    ensure_tables()
    start = _window_start(days)
    conn = get_connection()
    try:
        internal_ids: list[int] = [] if include_internal else _internal_user_ids(conn)
        active_users = _active_users(conn, start, internal_ids)

        features: List[dict[str, Any]] = []
        for name, ev in _FEATURES:
            users = _distinct_users_for_event(conn, ev, start, internal_ids)
            features.append({
                "feature": name,
                "event": ev,
                "unique_users": users,
                "browsers": _distinct_browsers_for_event(conn, ev, start),
                "uses": _count(conn, ev, start, internal_ids),
                "adoption_pct": round(100.0 * users / active_users, 1) if active_users else None,
            })

        features.sort(key=lambda x: (-(x["adoption_pct"] or 0), -x["unique_users"], -x["uses"]))
        return {
            **metric_basis(days, conn, internal_ids),
            "include_internal": bool(include_internal),
            "active_users": active_users,
            "features": features,
            "note": ("adoption_pct is signed-in users of the feature ÷ active signed-in users. "
                     "`browsers` is anonymous traffic and is NOT part of that ratio."),
        }
    finally:
        conn.close()


def business_health() -> dict[str, Any]:
    """Account composition only.

    MRR/ARPU used to be derived here as premium_accounts × a price env var.
    There is no billing system and no paid plan, so those were invented
    numbers on a dashboard meant to be trusted; they are gone rather than
    shown as ₹0. Role counts stay because they are real rows.
    """
    conn = get_connection()
    try:
        accounts = _account_counts(conn)
        roles = accounts.get("by_role", {})
        return {
            "registered_users": accounts.get("registered_total", 0),
            "accounts_by_role": roles,
            "new_accounts_today": accounts.get("new_today", 0),
            "new_accounts_7d": accounts.get("new_7d", 0),
            "new_accounts_30d": accounts.get("new_30d", 0),
            "accounts_data_from": _accounts_epoch(conn),
            "billing": {
                "status": "not_implemented",
                "note": ("No billing system and no paid plans exist, so revenue cannot be "
                         "measured. MRR, ARPU, LTV, CAC, churn, renewal and refund rates are "
                         "deliberately absent rather than estimated."),
            },
            "role_note": ("`role` is an authorisation field, not a subscription. PREMIUM is unused "
                          "while no paid plan exists, and ADMIN access is granted by an email "
                          "allowlist, so this is not a count of administrators."),
        }
    finally:
        conn.close()
