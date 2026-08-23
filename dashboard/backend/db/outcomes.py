"""
dashboard/backend/db/outcomes.py — PHASE 0 evidence-base storage.

Two isolated tables, both purely additive and read-only with respect to every
existing engine:

    forward_returns         what each scanned (symbol, date) did next
    fundamentals_quarterly  real quarterly financial history

Nothing in the trading path imports this module. It exists so calibration can
be done on measured outcomes instead of assumptions, which is the whole point of
Phase 0. Writes come from the out-of-band backfill scripts, never from a request
handler and never from a FastAPI startup hook.

Batched with an explicit chunk size because `dashboard.db` lives on a volume that
has already hit 84% once: a single 185k-row transaction would balloon the WAL,
whereas committed chunks let SQLite checkpoint as it goes.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from .schema import get_connection

log = logging.getLogger("dashboard.db.outcomes")

DEFAULT_CHUNK = 2000

_FORWARD_COLUMNS: tuple[str, ...] = (
    "symbol", "date", "base_close", "bars_available", "days_to_target",
    "fwd_5d_pct", "mfe_5d_pct", "mae_5d_pct", "bench_fwd_5d_pct", "excess_5d_pct",
    "fwd_10d_pct", "mfe_10d_pct", "mae_10d_pct", "bench_fwd_10d_pct", "excess_10d_pct",
    "fwd_20d_pct", "mfe_20d_pct", "mae_20d_pct", "bench_fwd_20d_pct", "excess_20d_pct",
    "fwd_60d_pct", "mfe_60d_pct", "mae_60d_pct", "bench_fwd_60d_pct", "excess_60d_pct",
)

_FUNDAMENTAL_COLUMNS: tuple[str, ...] = (
    "symbol", "period_end", "revenue", "ebitda", "ebit", "net_income",
    "gross_profit", "total_debt", "total_equity",
    "ebitda_margin_pct", "ebit_margin_pct", "net_margin_pct", "roce_pct", "source",
)


def _chunks(rows: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _upsert(table: str, columns: tuple[str, ...], rows: list[dict],
            chunk: int = DEFAULT_CHUNK, stamp_column: str = "computed_at") -> int:
    """Chunked INSERT..ON CONFLICT DO UPDATE. Returns rows written."""
    if not rows:
        return 0
    placeholders = ", ".join("?" * len(columns))
    key = columns[0], columns[1]
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in key)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key[0]}, {key[1]}) DO UPDATE SET {updates}, "
        f"{stamp_column}=datetime('now')"
    )
    written = 0
    conn = get_connection()
    try:
        for batch in _chunks(rows, chunk):
            payload = [tuple(row.get(c) for c in columns) for row in batch]
            conn.executemany(sql, payload)
            conn.commit()
            written += len(payload)
    finally:
        conn.close()
    return written


def upsert_forward_returns(rows: list[dict], chunk: int = DEFAULT_CHUNK) -> int:
    """Persist outcome labels. Idempotent — re-running the backfill refreshes
    rows whose windows have since elapsed (a NULL +60d becomes a real number
    once 60 trading days have passed)."""
    written = _upsert("forward_returns", _FORWARD_COLUMNS, rows, chunk, "computed_at")
    log.info("[Phase0] forward_returns upserted: %d rows", written)
    return written


def upsert_fundamentals_quarterly(rows: list[dict], chunk: int = DEFAULT_CHUNK) -> int:
    written = _upsert("fundamentals_quarterly", _FUNDAMENTAL_COLUMNS, rows, chunk, "fetched_at")
    log.info("[Phase0] fundamentals_quarterly upserted: %d rows", written)
    return written


def distinct_scan_keys(limit: int | None = None) -> list[tuple[str, str]]:
    """Every distinct (symbol, date) in signals_log — the set that needs labels.

    This is the ~185k-pair collapse of the ~903k-row corpus described in the
    schema comment. Ordered newest-first so a partial run still labels the most
    useful rows.
    """
    conn = get_connection()
    try:
        sql = (
            "SELECT DISTINCT symbol, date FROM signals_log "
            "WHERE symbol IS NOT NULL AND date IS NOT NULL ORDER BY date DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [(str(r["symbol"]), str(r["date"])) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def forward_return_stats() -> dict:
    """Coverage of the labelled dataset, per horizon. The number that says which
    horizons a calibration may legitimately use."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)                                        AS total,
                   MIN(date)                                       AS min_date,
                   MAX(date)                                       AS max_date,
                   SUM(CASE WHEN fwd_5d_pct  IS NOT NULL THEN 1 ELSE 0 END) AS have_5d,
                   SUM(CASE WHEN fwd_10d_pct IS NOT NULL THEN 1 ELSE 0 END) AS have_10d,
                   SUM(CASE WHEN fwd_20d_pct IS NOT NULL THEN 1 ELSE 0 END) AS have_20d,
                   SUM(CASE WHEN fwd_60d_pct IS NOT NULL THEN 1 ELSE 0 END) AS have_60d,
                   SUM(CASE WHEN days_to_target IS NOT NULL THEN 1 ELSE 0 END) AS reached_target
            FROM forward_returns
            """
        ).fetchone()
    except Exception as exc:
        log.warning("forward_return_stats failed: %s", exc)
        return {"available": False, "error": str(exc)}
    finally:
        conn.close()

    total = int(row["total"] or 0)
    if not total:
        return {"available": False, "total": 0}

    def pct(value: Any) -> float:
        return round(int(value or 0) / total * 100, 2)

    return {
        "available": True,
        "total": total,
        "date_range": [row["min_date"], row["max_date"]],
        "by_horizon": {
            "5d": {"labelled": int(row["have_5d"] or 0), "coverage_pct": pct(row["have_5d"])},
            "10d": {"labelled": int(row["have_10d"] or 0), "coverage_pct": pct(row["have_10d"])},
            "20d": {"labelled": int(row["have_20d"] or 0), "coverage_pct": pct(row["have_20d"])},
            "60d": {"labelled": int(row["have_60d"] or 0), "coverage_pct": pct(row["have_60d"])},
        },
        "target_touch": {
            "reached": int(row["reached_target"] or 0),
            "reached_pct": pct(row["reached_target"]),
        },
    }


def fundamentals_coverage() -> dict:
    """How many symbols have real quarterly history, and how deep it goes."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols, "
            "MIN(period_end) AS oldest, MAX(period_end) AS newest "
            "FROM fundamentals_quarterly"
        ).fetchone()
        depth = conn.execute(
            "SELECT AVG(n) AS avg_quarters FROM ("
            "  SELECT COUNT(*) AS n FROM fundamentals_quarterly GROUP BY symbol)"
        ).fetchone()
    except Exception as exc:
        log.warning("fundamentals_coverage failed: %s", exc)
        return {"available": False, "error": str(exc)}
    finally:
        conn.close()
    rows = int(row["rows"] or 0)
    return {
        "available": rows > 0,
        "rows": rows,
        "symbols": int(row["symbols"] or 0),
        "period_range": [row["oldest"], row["newest"]],
        "avg_quarters_per_symbol": round(float(depth["avg_quarters"] or 0), 2),
        "not_collected": {
            "cash_flow": "yfinance quarterly_cashflow returns empty for NSE symbols",
            "promoter_pct": "nseindia.com/api requires a cookie handshake; 403 from datacenter IPs",
            "fii_dii_pct": "same source as promoter_pct",
            "pledge": "same source as promoter_pct",
        },
    }
