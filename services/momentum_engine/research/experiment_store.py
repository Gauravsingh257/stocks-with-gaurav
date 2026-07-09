"""
services/momentum_engine/research/experiment_store.py
======================================================
Durable store for ExperimentRecords — the experiment platform.

Every simulated momentum signal is persisted with its features, its full set of
'why' explanations, and its realised outcome. Over time this becomes a labelled
dataset the engine is RE-FIT on (not tuned by intuition). Lives in the same
SQLite DB (Railway volume) as the rest of the app, in its own table so it never
touches trading state. Read/write only from research + backtest tooling.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from .models import ExperimentRecord

log = logging.getLogger("services.momentum_engine.research.experiment_store")
_IST = timezone(timedelta(hours=5, minutes=30))

_DDL = """
CREATE TABLE IF NOT EXISTS momentum_experiments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    config_id     TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    horizon       TEXT,
    scan_date     TEXT,
    regime        TEXT,
    sector        TEXT,
    rs_20d        REAL, atr_pct REAL, extension_atr REAL, trend_quality REAL,
    base_atr_pct  REAL, breakout_score REAL, volume_ratio REAL, quality_score REAL,
    entry_model   TEXT, stop_method TEXT, trail_method TEXT,
    why_qualified TEXT, why_ranked TEXT, why_entered TEXT, why_exited TEXT,
    entered       INTEGER, r_multiple REAL, hold_bars INTEGER,
    mfe_r REAL, mae_r REAL, outcome TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mexp_run    ON momentum_experiments(run_id);
CREATE INDEX IF NOT EXISTS idx_mexp_config ON momentum_experiments(config_id);
CREATE INDEX IF NOT EXISTS idx_mexp_symbol ON momentum_experiments(symbol);
CREATE INDEX IF NOT EXISTS idx_mexp_outcome ON momentum_experiments(outcome);
"""

_COLS = [
    "run_id", "config_id", "symbol", "horizon", "scan_date", "regime", "sector",
    "rs_20d", "atr_pct", "extension_atr", "trend_quality", "base_atr_pct",
    "breakout_score", "volume_ratio", "quality_score", "entry_model", "stop_method",
    "trail_method", "why_qualified", "why_ranked", "why_entered", "why_exited",
    "entered", "r_multiple", "hold_bars", "mfe_r", "mae_r", "outcome", "created_at",
]


def _conn():
    from dashboard.backend.db.schema import get_connection
    return get_connection()


def ensure() -> None:
    conn = _conn()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


def insert_records(records: list[ExperimentRecord]) -> int:
    if not records:
        return 0
    ensure()
    now = datetime.now(_IST).isoformat()
    rows = []
    for r in records:
        d = r.to_dict()
        d["created_at"] = d.get("created_at") or now
        for k in ("why_qualified", "why_ranked"):
            d[k] = json.dumps(d.get(k) or {}, default=str)
        d["entered"] = 1 if d.get("entered") else 0
        rows.append(tuple(d.get(c) for c in _COLS))
    conn = _conn()
    try:
        conn.executemany(
            f"INSERT INTO momentum_experiments ({', '.join(_COLS)}) "
            f"VALUES ({', '.join(['?'] * len(_COLS))})",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def summary() -> dict:
    ensure()
    conn = _conn()
    try:
        a = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT run_id), COUNT(DISTINCT config_id), "
            "COUNT(DISTINCT symbol), MIN(scan_date), MAX(scan_date), "
            "COALESCE(SUM(entered),0) FROM momentum_experiments"
        ).fetchone()
        outcomes = {r[0]: r[1] for r in conn.execute(
            "SELECT outcome, COUNT(*) FROM momentum_experiments GROUP BY outcome"
        ).fetchall()}
        return {
            "total_experiments": a[0], "runs": a[1], "configs": a[2], "symbols": a[3],
            "date_min": a[4], "date_max": a[5], "entered": a[6], "outcomes": outcomes,
        }
    finally:
        conn.close()


def query(config_id: str | None = None, run_id: str | None = None,
          outcome: str | None = None, limit: int = 5000) -> list[dict]:
    ensure()
    conn = _conn()
    try:
        cond, params = [], []
        for col, val in (("config_id", config_id), ("run_id", run_id), ("outcome", outcome)):
            if val is not None:
                cond.append(f"{col} = ?"); params.append(val)
        where = f"WHERE {' AND '.join(cond)}" if cond else ""
        rows = conn.execute(
            f"SELECT * FROM momentum_experiments {where} ORDER BY id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
