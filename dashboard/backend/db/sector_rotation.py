"""
dashboard/backend/db/sector_rotation.py — storage for `sector_rotation_daily`.

One row per (date, timeframe, entity). Written only by
scripts/build_sector_rotation.py (out of band); read by nothing in the trading,
ranking, selection or portfolio path. The table is created lazily on first write
or read — never from a FastAPI startup handler.

History is point-in-time by construction: a normal run inserts only dates after
the latest one stored (plus the current, still-changing week), so a past row is
never recomputed with later membership. `source_mode` separates the one-off
backfill (computed with today's membership) from rows written live.
"""

from __future__ import annotations

import logging
from typing import Any

from .schema import get_connection

log = logging.getLogger("dashboard.db.sector_rotation")

COLUMNS = (
    "date", "timeframe", "entity", "kind", "as_of", "partial",
    "n_constituents", "n_core", "provider_share", "confidence",
    "level", "ret_bar", "ret_lookback", "rel_ret_lookback",
    "rs_ratio", "rs_momentum", "rs_accel", "rs_ratio_core", "quadrant",
    "rs_ratio_mkt", "rs_momentum_mkt", "quadrant_mkt",
    "pct_above_20", "pct_above_50", "pct_above_200",
    "new_highs_52w", "new_lows_52w", "n_52w_eligible",
    "vol_ratio", "up_turnover_pct", "dispersion", "is_filled",
    "calc_version", "source_mode",
)

DDL = """
CREATE TABLE IF NOT EXISTS sector_rotation_daily (
    date             TEXT NOT NULL,      -- trading date (D) or ISO-week Friday (W)
    timeframe        TEXT NOT NULL,      -- 'D' | 'W'
    entity           TEXT NOT NULL,      -- sector name, index name, or 'ALL EQUITIES'
    kind             TEXT NOT NULL,      -- 'sector' | 'index' | 'market'
    as_of            TEXT,               -- last trading date included
    partial          INTEGER DEFAULT 0,  -- W only: week still in progress
    n_constituents   INTEGER,
    n_core           INTEGER,
    provider_share   REAL,
    confidence       TEXT,               -- high | medium | low | insufficient | reference
    level            REAL,
    ret_bar          REAL,
    ret_lookback     REAL,
    rel_ret_lookback REAL,
    rs_ratio         REAL,
    rs_momentum      REAL,
    rs_accel         REAL,
    rs_ratio_core    REAL,
    quadrant         TEXT,               -- vs NIFTY 50
    rs_ratio_mkt     REAL,               -- vs equal-weight ALL EQUITIES (sectors only)
    rs_momentum_mkt  REAL,
    quadrant_mkt     TEXT,
    pct_above_20     REAL,
    pct_above_50     REAL,
    pct_above_200    REAL,
    new_highs_52w    INTEGER,
    new_lows_52w     INTEGER,
    n_52w_eligible   INTEGER,
    vol_ratio        REAL,
    up_turnover_pct  REAL,
    dispersion       REAL,
    is_filled        INTEGER,
    calc_version     TEXT NOT NULL,
    source_mode      TEXT NOT NULL,      -- 'backfill' | 'live'
    written_at       TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (date, timeframe, entity)
);
CREATE INDEX IF NOT EXISTS idx_sector_rotation_tf_date ON sector_rotation_daily(timeframe, date);
"""


def ensure_table(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(DDL)
        conn.commit()
    finally:
        if own:
            conn.close()


def latest_date(timeframe: str) -> str | None:
    conn = get_connection()
    try:
        ensure_table(conn)
        row = conn.execute(
            "SELECT MAX(date) d FROM sector_rotation_daily WHERE timeframe = ?", (timeframe,)
        ).fetchone()
        return row["d"] if row else None
    finally:
        conn.close()


def upsert_rows(rows: list[dict], *, source_mode: str, chunk: int = 1000) -> int:
    if not rows:
        return 0
    placeholders = ", ".join("?" * len(COLUMNS))
    updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c not in ("date", "timeframe", "entity"))
    sql = (
        f"INSERT INTO sector_rotation_daily ({', '.join(COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(date, timeframe, entity) DO UPDATE SET {updates}, written_at=datetime('now')"
    )

    def _val(r: dict, c: str):
        if c == "source_mode":
            return source_mode
        v = r.get(c)
        return int(v) if isinstance(v, bool) else v

    conn = get_connection()
    written = 0
    try:
        ensure_table(conn)
        for i in range(0, len(rows), chunk):
            batch = rows[i: i + chunk]
            conn.executemany(sql, [tuple(_val(r, c) for c in COLUMNS) for r in batch])
            conn.commit()
            written += len(batch)
    finally:
        conn.close()
    log.info("[sector_rotation] upserted %d rows (%s)", written, source_mode)
    return written


def read_rows(*, timeframe: str = "D", date: str | None = None, entity: str | None = None,
              limit: int = 5000) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        ensure_table(conn)
        where, params = ["timeframe = ?"], [timeframe]
        if date:
            where.append("date = ?")
            params.append(date)
        if entity:
            where.append("entity = ?")
            params.append(entity)
        rows = conn.execute(
            f"SELECT * FROM sector_rotation_daily WHERE {' AND '.join(where)} "
            f"ORDER BY date DESC, entity ASC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def summary() -> dict[str, Any]:
    conn = get_connection()
    try:
        ensure_table(conn)
        out = {}
        for r in conn.execute(
            "SELECT timeframe, source_mode, COUNT(*) n, COUNT(DISTINCT entity) e, "
            "MIN(date) first, MAX(date) last FROM sector_rotation_daily GROUP BY timeframe, source_mode"
        ):
            out[f"{r['timeframe']}:{r['source_mode']}"] = dict(r)
        return out
    finally:
        conn.close()
