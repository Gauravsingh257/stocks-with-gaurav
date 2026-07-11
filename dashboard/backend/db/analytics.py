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
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

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


def _count(conn, event: str, start: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM product_events WHERE event = ? AND day >= ?", (event, start)
    ).fetchone()
    return int(row["c"] if row else 0)


def _distinct_anon(conn, event: str, start: str) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT anon_id) AS c FROM product_events WHERE event = ? AND day >= ? AND anon_id IS NOT NULL",
        (event, start),
    ).fetchone()
    return int(row["c"] if row else 0)


def health_kpis(days: int = 7) -> Dict[str, Any]:
    """Compute the 16 Product-Health KPIs over a rolling window (IST days)."""
    ensure_tables()
    start = _window_start(days)
    conn = get_connection()
    try:
        total_users = int(conn.execute(
            "SELECT COUNT(DISTINCT anon_id) AS c FROM product_events WHERE anon_id IS NOT NULL"
        ).fetchone()["c"] or 0)
        active_users = int(conn.execute(
            "SELECT COUNT(DISTINCT anon_id) AS c FROM product_events WHERE day >= ? AND anon_id IS NOT NULL",
            (start,),
        ).fetchone()["c"] or 0)
        new_signups = _count(conn, "signup", start)
        returning_users = int(conn.execute(
            "SELECT COUNT(DISTINCT pe.anon_id) AS c FROM product_events pe WHERE pe.day >= ? AND pe.anon_id IS NOT NULL"
            " AND EXISTS (SELECT 1 FROM product_events p2 WHERE p2.anon_id = pe.anon_id AND p2.day < ?)",
            (start, start),
        ).fetchone()["c"] or 0)

        cc_views = _count(conn, "command_center_viewed", start)
        nba_clicks = _count(conn, "nba_clicked", start)
        nba_ctr = round(100.0 * nba_clicks / cc_views, 1) if cc_views else 0.0
        wl_adds = _count(conn, "watchlist_stock_added", start)
        wl_opens = _count(conn, "watchlist_opened", start)
        research_searches = _count(conn, "global_search", start)
        ai_research = _count(conn, "ai_research_used", start)
        telegram_clicks = _count(conn, "telegram_link_clicked", start)

        # session duration + pages/session
        sess = conn.execute(
            "SELECT session_id,"
            " (julianday(MAX(ts)) - julianday(MIN(ts))) * 86400.0 AS dur,"
            " SUM(CASE WHEN event = 'page_view' THEN 1 ELSE 0 END) AS pv"
            " FROM product_events WHERE session_id IS NOT NULL AND day >= ? GROUP BY session_id",
            (start,),
        ).fetchall()
        durations = [float(r["dur"] or 0) for r in sess]
        pvs = [int(r["pv"] or 0) for r in sess]
        avg_session_s = round(sum(durations) / len(durations), 1) if durations else 0.0
        pages_per_session = round(sum(pvs) / len(pvs), 2) if pvs else 0.0

        # Day-1 retention over all cohorts that had a chance to return
        today = _today_ist()
        d1 = conn.execute(
            "WITH firsts AS ("
            "  SELECT anon_id, MIN(day) AS fd FROM product_events WHERE anon_id IS NOT NULL GROUP BY anon_id"
            ") SELECT COUNT(*) AS cohort,"
            " SUM(CASE WHEN EXISTS ("
            "     SELECT 1 FROM product_events e WHERE e.anon_id = firsts.anon_id AND e.day = date(firsts.fd, '+1 day')"
            " ) THEN 1 ELSE 0 END) AS retained"
            " FROM firsts WHERE date(fd, '+1 day') <= ?",
            (today,),
        ).fetchone()
        cohort = int(d1["cohort"] or 0)
        retained = int(d1["retained"] or 0)
        d1_ret = round(100.0 * retained / cohort, 1) if cohort else None

        return {
            "window_days": int(days),
            "since": start,
            "total_users": total_users,
            "active_users": active_users,
            "new_signups": new_signups,
            "returning_users": returning_users,
            "command_center_views": cc_views,
            "nba_clicks": nba_clicks,
            "nba_ctr_pct": nba_ctr,
            "watchlist_adds": wl_adds,
            "watchlist_opens": wl_opens,
            "research_searches": research_searches,
            "ai_research_usage": ai_research,
            "avg_session_seconds": avg_session_s,
            "pages_per_session": pages_per_session,
            "telegram_link_clicks": telegram_clicks,
            "day1_retention_pct": d1_ret,
            "day1_cohort": cohort,
            "day7_retention_pct": None,  # needs ≥7 days of data
        }
    finally:
        conn.close()


def activation_funnel(days: int = 7) -> Dict[str, Any]:
    """Activation funnel — distinct users reaching each stage, with stage-to-stage
    conversion %. Loose (non-sequential) funnel: a user counts toward a stage if
    they performed that event at all in the window."""
    ensure_tables()
    start = _window_start(days)
    conn = get_connection()
    try:
        visitors = int(conn.execute(
            "SELECT COUNT(DISTINCT anon_id) AS c FROM product_events WHERE day >= ? AND anon_id IS NOT NULL",
            (start,),
        ).fetchone()["c"] or 0)
        returned_next_day = int(conn.execute(
            "SELECT COUNT(*) AS c FROM ("
            "  SELECT anon_id FROM product_events WHERE day >= ? AND anon_id IS NOT NULL"
            "  GROUP BY anon_id HAVING COUNT(DISTINCT day) >= 2)",
            (start,),
        ).fetchone()["c"] or 0)

        stages = [
            {"stage": "Visitor", "count": visitors},
            {"stage": "Signup", "count": _distinct_anon(conn, "signup", start)},
            {"stage": "Login", "count": _distinct_anon(conn, "login", start)},
            {"stage": "Command Center", "count": _distinct_anon(conn, "command_center_viewed", start)},
            {"stage": "NBA Clicked", "count": _distinct_anon(conn, "nba_clicked", start)},
            {"stage": "Research Opened", "count": _distinct_anon(conn, "research_opened", start)},
            {"stage": "Watchlist Add", "count": _distinct_anon(conn, "watchlist_stock_added", start)},
            {"stage": "Returned Next Day", "count": returned_next_day},
        ]
        # stage-to-stage conversion vs previous stage
        for i, s in enumerate(stages):
            prev = stages[i - 1]["count"] if i > 0 else s["count"]
            s["conversion_pct"] = round(100.0 * s["count"] / prev, 1) if prev else 0.0
            s["overall_pct"] = round(100.0 * s["count"] / visitors, 1) if visitors else 0.0
        return {"window_days": int(days), "since": start, "stages": stages,
                "note": "Loose funnel: users are counted at a stage if they performed that event in the window (not strictly sequential)."}
    finally:
        conn.close()
