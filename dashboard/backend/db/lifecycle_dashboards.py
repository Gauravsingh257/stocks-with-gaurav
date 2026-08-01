"""
dashboard/backend/db/lifecycle_dashboards.py
============================================
Aggregations that back the visual analytics: monthly performance, engine and
engine-version comparison, the conversion funnel, exit-reason attribution, and
the enrichment behind the trade-detail page.

Everything reads the lifecycle ledger. Returns and win rates cover CLOSED,
genuinely-executed positions; ideas that never filled are counted where the
question is about ideas (the funnel) and excluded where it is about money.
"""

from __future__ import annotations

import json
import logging

from .schema import get_connection
from .trade_lifecycle import init_lifecycle_db, CLOSED_STATUSES, OPEN_STATUSES

logger = logging.getLogger(__name__)

_LIVE = "COALESCE(record_state,'ACTIVE') = 'ACTIVE' AND is_duplicate = 0"
_CL = ",".join("?" * len(CLOSED_STATUSES))


def _book_clause(portfolio: str | None):
    if portfolio and portfolio.upper() != "ALL":
        return " AND (portfolio = ? OR source = ?)", [portfolio.upper(), portfolio.upper()]
    return "", []


def monthly_performance(portfolio: str | None = None, months: int = 24) -> dict:
    """Return, win rate and trade count per calendar month."""
    init_lifecycle_db()
    bc, bp = _book_clause(portfolio)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT year, month,
                   COUNT(*)                                   AS trades,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                   ROUND(SUM(pnl_pct), 2)                     AS sum_pnl,
                   ROUND(AVG(pnl_pct), 2)                     AS avg_pnl,
                   SUM(CASE WHEN status = 'TARGET_HIT' THEN 1 ELSE 0 END) AS targets,
                   SUM(CASE WHEN status = 'STOP_HIT'  THEN 1 ELSE 0 END) AS stops
            FROM trade_lifecycle
            WHERE {_LIVE} AND status IN ({_CL}) AND year IS NOT NULL{bc}
            GROUP BY year, month ORDER BY year DESC, month DESC LIMIT ?
            """,
            list(CLOSED_STATUSES) + bp + [int(months)],
        ).fetchall()
        out = []
        for r in reversed([dict(x) for x in rows]):
            t = r["trades"] or 0
            out.append({
                "period": f"{r['year']}-{int(r['month']):02d}",
                "year": r["year"], "month": r["month"], "trades": t,
                "wins": r["wins"] or 0,
                "win_rate_pct": round((r["wins"] or 0) / t * 100, 1) if t else 0.0,
                "sum_pnl_pct": r["sum_pnl"] or 0.0,
                "avg_pnl_pct": r["avg_pnl"] or 0.0,
                "target_hits": r["targets"] or 0, "stop_hits": r["stops"] or 0,
            })
        # Cumulative curve, so the chart can show the path rather than only bars.
        run = 0.0
        for p in out:
            run = round(run + p["sum_pnl_pct"], 2)
            p["cumulative_pnl_pct"] = run
        return {"points": out, "portfolio": (portfolio or "ALL").upper()}
    finally:
        conn.close()


def engine_comparison(by_version: bool = False) -> dict:
    """Compare books — or engine VERSIONS — on the same closed population."""
    init_lifecycle_db()
    key = "engine_version" if by_version else "COALESCE(portfolio, source)"
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT {key} AS k,
                   COUNT(*)                                     AS closed,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                   ROUND(AVG(pnl_pct), 2)                       AS avg_pnl,
                   ROUND(SUM(pnl_pct), 2)                       AS sum_pnl,
                   ROUND(AVG(rr_realized), 2)                   AS avg_rr,
                   ROUND(AVG(holding_days), 1)                  AS avg_days,
                   SUM(CASE WHEN status = 'TARGET_HIT' THEN 1 ELSE 0 END) AS targets,
                   SUM(CASE WHEN status = 'STOP_HIT'  THEN 1 ELSE 0 END) AS stops
            FROM trade_lifecycle
            WHERE {_LIVE} AND status IN ({_CL}) AND {key} IS NOT NULL
            GROUP BY k ORDER BY closed DESC
            """,
            list(CLOSED_STATUSES),
        ).fetchall()
        out = []
        for r in [dict(x) for x in rows]:
            c = r["closed"] or 0
            gw = gl = 0.0
            out.append({
                "key": r["k"], "closed_trades": c, "wins": r["wins"] or 0,
                "win_rate_pct": round((r["wins"] or 0) / c * 100, 1) if c else 0.0,
                "avg_pnl_pct": r["avg_pnl"] or 0.0, "sum_pnl_pct": r["sum_pnl"] or 0.0,
                "avg_rr": r["avg_rr"], "avg_holding_days": r["avg_days"],
                "target_hits": r["targets"] or 0, "stop_hits": r["stops"] or 0,
                "target_hit_rate_pct": round((r["targets"] or 0) / c * 100, 1) if c else 0.0,
            })
        return {"dimension": "engine_version" if by_version else "book", "rows": out}
    finally:
        conn.close()


def conversion_funnel(portfolio: str | None = None) -> dict:
    """Idea → armed → entered → closed → target, as ordered stages.

    Reported as counts AND as a rate against the stage above, because the drop
    between two specific stages is the actionable number — a single overall
    percentage hides where ideas are actually being lost.
    """
    init_lifecycle_db()
    bc, bp = _book_clause(portfolio)
    conn = get_connection()
    try:
        def n(where, params=()):
            return conn.execute(
                f"SELECT COUNT(*) c FROM trade_lifecycle WHERE {_LIVE}{bc} AND {where}",
                bp + list(params),
            ).fetchone()["c"] or 0

        ideas = n("stage = 'IDEA'")
        armed = n("status = 'AWAITING_ENTRY'")
        entered = n("stage = 'POSITION' AND executed = 1")
        op = ",".join("?" * len(OPEN_STATUSES))
        still_open = n(f"status IN ({op})", OPEN_STATUSES)
        closed = n(f"status IN ({_CL})", CLOSED_STATUSES)
        targets = n("status = 'TARGET_HIT'")
        stops = n("status = 'STOP_HIT'")
        expired = n("status = 'EXPIRED'")
        never = n("status = 'NEVER_EXECUTED'")

        def rate(a, b):
            return round(a / b * 100, 1) if b else 0.0

        return {
            "portfolio": (portfolio or "ALL").upper(),
            "stages": [
                {"stage": "Ideas generated", "count": ideas, "of_previous_pct": 100.0},
                {"stage": "Entries taken", "count": entered, "of_previous_pct": rate(entered, ideas)},
                {"stage": "Positions closed", "count": closed, "of_previous_pct": rate(closed, entered)},
                {"stage": "Targets reached", "count": targets, "of_previous_pct": rate(targets, closed)},
            ],
            "still_open": still_open,
            "awaiting_entry": armed,
            "expired": expired,
            "never_executed": never,
            "stopped_out": stops,
            "leakage": {
                "ideas_that_never_traded": max(ideas - entered, 0),
                "entered_but_stopped": stops,
            },
        }
    finally:
        conn.close()


def exit_attribution(portfolio: str | None = None) -> dict:
    """Which exit rule produced which outcome — where the money is actually made
    and lost."""
    init_lifecycle_db()
    bc, bp = _book_clause(portfolio)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT status,
                   COUNT(*)                                     AS n,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                   ROUND(SUM(pnl_pct), 2)                       AS sum_pnl,
                   ROUND(AVG(pnl_pct), 2)                       AS avg_pnl,
                   ROUND(AVG(mfe_pct), 2)                       AS avg_mfe,
                   ROUND(AVG(holding_days), 1)                  AS avg_days
            FROM trade_lifecycle
            WHERE {_LIVE} AND status IN ({_CL}){bc}
            GROUP BY status ORDER BY sum_pnl ASC
            """,
            list(CLOSED_STATUSES) + bp,
        ).fetchall()
        out = []
        for r in [dict(x) for x in rows]:
            n_ = r["n"] or 0
            giveback = None
            if r["avg_mfe"] is not None and r["avg_pnl"] is not None:
                giveback = round(r["avg_mfe"] - r["avg_pnl"], 2)
            out.append({**r, "n": n_,
                        "win_rate_pct": round((r["wins"] or 0) / n_ * 100, 1) if n_ else 0.0,
                        "avg_giveback_pct": giveback})
        return {"rows": out, "portfolio": (portfolio or "ALL").upper()}
    finally:
        conn.close()


def trade_detail(lifecycle_id: str) -> dict:
    """Everything the trade-detail page needs, in one call.

    Combines the ledger row, its append-only event history, the chain it belongs
    to, any Telegram alerts recorded for the symbol, a reconstructed price path,
    and a derived post-trade analysis.
    """
    from .trade_lifecycle_query import timeline
    from .lifecycle_chain import chain

    base = timeline(lifecycle_id)
    if not base.get("found"):
        return {"found": False}
    t = base["trade"]

    def _j(v):
        if not v:
            return None
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None

    detail = {
        "found": True,
        "trade": t,
        "events": base.get("events", []),
        "recommendation": _j(t.get("recommendation_json")),
        "context": _j(t.get("context_json")),
        "chart_entry": _j(t.get("chart_entry_json")),
        "chart_exit": _j(t.get("chart_exit_json")),
        "chain": chain(t.get("chain_id") or lifecycle_id),
        "alerts": _alerts_for(t),
        "price_path": _price_path(t),
        "analysis": post_trade_analysis(t),
    }
    return detail


def _alerts_for(t: dict) -> list[dict]:
    """Telegram alerts recorded for this symbol around the trade's life."""
    conn = get_connection()
    try:
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lifecycle_alerts'"
        ).fetchone()
        if not has:
            return []
        rows = conn.execute(
            "SELECT kind, message, sent_at FROM lifecycle_alerts "
            "WHERE symbol = ? ORDER BY datetime(sent_at) ASC LIMIT 50",
            (t.get("symbol"),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _price_path(t: dict) -> dict:
    """The excursion path we can state honestly from stored values.

    Entry, worst point, best point and exit — not a synthetic candle series.
    Fabricating intermediate bars would present invented data as history.
    """
    entry = t.get("entry_price")
    if not entry:
        return {"available": False}
    pts = [{"label": "Entry", "price": entry, "pct": 0.0, "at": t.get("entry_fill_at")}]
    if t.get("low_since_entry"):
        pts.append({"label": "Worst (MAE)", "price": t["low_since_entry"],
                    "pct": t.get("mae_pct")})
    if t.get("high_since_entry"):
        pts.append({"label": "Best (MFE)", "price": t["high_since_entry"],
                    "pct": t.get("mfe_pct")})
    if t.get("exit_price"):
        pts.append({"label": "Exit", "price": t["exit_price"],
                    "pct": t.get("pnl_pct"), "at": t.get("exit_at")})
    return {"available": True, "points": pts,
            "stop_loss": t.get("stop_loss"), "target_1": t.get("target_1"),
            "target_2": t.get("target_2")}


def post_trade_analysis(t: dict) -> dict:
    """Derived post-trade read — stated from the record, not generated prose.

    Every line is a fact the ledger supports. Nothing here speculates about why
    price moved; it reports how the trade was managed and what that cost or
    earned, which is the part we can actually stand behind.
    """
    notes: list[str] = []
    verdict = "—"
    pnl = t.get("pnl_pct")
    mfe = t.get("mfe_pct")
    mae = t.get("mae_pct")
    status = t.get("status")
    entry, sl = t.get("entry_price"), t.get("stop_loss")
    t1 = t.get("target_1")

    if not t.get("executed"):
        return {"verdict": "Never executed",
                "notes": ["This idea was published but never traded, so it has no P&L. "
                          "It counts as a signal, not as a result."],
                "giveback_pct": None}

    giveback = round(mfe - pnl, 2) if (mfe is not None and pnl is not None) else None

    if status == "TARGET_HIT":
        verdict = "Target reached"
        notes.append(f"Closed at target for {pnl:+.2f}%.")
    elif status == "STOP_HIT":
        verdict = "Stopped out"
        if mfe is not None and t1 and entry and mfe >= (t1 - entry) / entry * 100:
            notes.append(f"Reached the first target ({mfe:+.2f}% at best) before "
                         f"reversing into the stop — the move was there and was given back.")
        elif mfe is not None and mfe >= 3:
            notes.append(f"Ran to {mfe:+.2f}% before reversing into the stop.")
        else:
            notes.append("Went against the position from entry with little favourable excursion.")
    elif status == "TIME_EXIT":
        verdict = "Closed by time rule"
        notes.append(f"Held {t.get('holding_days') or '?'} days without resolving; "
                     f"closed at {pnl:+.2f}% by the stale-position rule.")
        if mfe is not None and mfe >= 5:
            notes.append(f"Peaked at {mfe:+.2f}% during the hold — most of that was not banked.")
    elif status == "FORCED_EXIT":
        verdict = "Risk/structure exit"
        notes.append(f"Closed at {pnl:+.2f}% because the structural condition for the "
                     f"setup broke, not because a price level was hit.")
    else:
        verdict = "Closed"
        if pnl is not None:
            notes.append(f"Closed at {pnl:+.2f}%.")

    if giveback is not None and giveback >= 5:
        notes.append(f"Give-back was {giveback:.2f}% from the best point — the single "
                     f"largest controllable cost on this trade.")
    if mae is not None and mae <= -3 and (pnl or 0) > 0:
        notes.append(f"Drew down {mae:.2f}% before working — the position needed room.")
    if entry and sl and entry > sl:
        risk = (entry - sl) / entry * 100
        if pnl is not None and risk:
            notes.append(f"Risked {risk:.2f}% to make {pnl:+.2f}% ({pnl / risk:+.2f}R).")

    return {"verdict": verdict, "notes": notes, "giveback_pct": giveback,
            "basis": "derived from the recorded lifecycle; no external inference"}
