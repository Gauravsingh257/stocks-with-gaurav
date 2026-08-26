"""
dashboard/backend/db/universe.py — read/write for the researchable stock universe.

One row per NSE symbol: company, sector, and the headline ratios. Written weekly
by scripts/refresh_stock_universe.py; read by /api/research/universe.

Nothing in the trading path imports this — it is a research surface only.
"""

from __future__ import annotations

import logging
from typing import Any

from .schema import get_connection

log = logging.getLogger("dashboard.db.universe")

COLUMNS = (
    "symbol", "company_name", "sector", "sector_source", "instrument",
    "price", "market_cap_cr", "turnover_cr", "pe", "pb", "roe_pct",
    "debt_to_equity", "revenue_growth_pct", "net_margin_pct", "roe_source", "promoter_pct",
    "pct_from_52w_high", "ret_1y_pct",
)


def upsert_universe(rows: list[dict], chunk: int = 500) -> int:
    """Replace/refresh universe rows. Idempotent on symbol."""
    if not rows:
        return 0
    placeholders = ", ".join("?" * len(COLUMNS))
    updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "symbol")
    sql = (
        f"INSERT INTO stock_universe ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {updates}, refreshed_at=datetime('now')"
    )
    written = 0
    conn = get_connection()
    try:
        for i in range(0, len(rows), chunk):
            batch = rows[i: i + chunk]
            conn.executemany(sql, [tuple(r.get(c) for c in COLUMNS) for r in batch])
            conn.commit()
            written += len(batch)
    finally:
        conn.close()
    log.info("[universe] upserted %d rows", written)
    return written


def get_universe(
    *,
    search: str | None = None,
    sector: str | None = None,
    equity_only: bool = True,
    limit: int = 5000,
) -> dict[str, Any]:
    """The universe with optional search/sector filters, plus facet counts."""
    conn = get_connection()
    try:
        where, params = [], []
        if equity_only:
            where.append("instrument = 'EQUITY'")
        if sector:
            where.append("sector = ?")
            params.append(sector)
        if search:
            where.append("(UPPER(symbol) LIKE ? OR UPPER(COALESCE(company_name,'')) LIKE ?)")
            term = f"%{search.strip().upper()}%"
            params += [term, term]
        clause = ("WHERE " + " AND ".join(where)) if where else ""

        rows = conn.execute(
            f"SELECT * FROM stock_universe {clause} "
            f"ORDER BY COALESCE(turnover_cr, 0) DESC, symbol ASC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()

        sectors = conn.execute(
            "SELECT sector, COUNT(*) n FROM stock_universe WHERE instrument='EQUITY' "
            "GROUP BY sector ORDER BY n DESC"
        ).fetchall()
        meta = conn.execute(
            "SELECT COUNT(*) total, MAX(refreshed_at) refreshed_at, "
            "SUM(CASE WHEN instrument='EQUITY' THEN 1 ELSE 0 END) equities, "
            "SUM(CASE WHEN pe IS NOT NULL THEN 1 ELSE 0 END) with_pe "
            "FROM stock_universe"
        ).fetchone()
    except Exception as exc:
        log.warning("[universe] read failed: %s", exc)
        return {"available": False, "error": str(exc), "items": [], "sectors": []}
    finally:
        conn.close()

    return {
        "available": bool(meta and meta["total"]),
        "items": [dict(r) for r in rows],
        "count": len(rows),
        "total": int(meta["total"] or 0) if meta else 0,
        "equities": int(meta["equities"] or 0) if meta else 0,
        "with_fundamentals": int(meta["with_pe"] or 0) if meta else 0,
        "refreshed_at": meta["refreshed_at"] if meta else None,
        "sectors": [{"sector": r["sector"], "count": r["n"]} for r in sectors],
    }


def get_symbol(symbol: str) -> dict[str, Any] | None:
    """Exact-match single-symbol lookup for the public /stock/<symbol> SEO page.

    Deliberately separate from get_universe(): that one does a LIKE '%term%'
    scan plus sector/meta aggregates, so "TCS" would also match "TCSLTD" and
    every page render would pay for facet counts it never shows. This is a
    single indexed row read — cheap enough to sit on a server-render path.

    Returns None for an unknown symbol so the route can emit a real 404 rather
    than an empty page (a soft-404 is worse for indexing than no page at all).
    """
    clean = (symbol or "").strip().upper().replace("NSE:", "").replace(".NS", "")
    if not clean:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM stock_universe WHERE UPPER(symbol) = ? LIMIT 1", (clean,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.warning("[universe] symbol read failed for %s: %s", clean, exc)
        return None
    finally:
        conn.close()


def list_symbols_for_sitemap(limit: int = 5000) -> dict[str, Any]:
    """Minimal {symbol, company_name, sector} rows for sitemap generation.

    The sitemap needs three columns for ~2.4k rows; get_universe() returns all
    18. Keeping this thin keeps the sitemap build off the slow path.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, company_name, sector, refreshed_at FROM stock_universe "
            "WHERE instrument = 'EQUITY' "
            "ORDER BY COALESCE(turnover_cr, 0) DESC, symbol ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return {
            "available": True,
            "items": [dict(r) for r in rows],
            "count": len(rows),
        }
    except Exception as exc:
        log.warning("[universe] sitemap list failed: %s", exc)
        return {"available": False, "items": [], "count": 0, "error": str(exc)}
    finally:
        conn.close()
