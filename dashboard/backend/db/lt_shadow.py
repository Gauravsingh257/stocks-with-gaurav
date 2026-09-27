"""
dashboard/backend/db/lt_shadow.py — storage for the Long-Term SHADOW selector (Phase 4A).

`lt_shadow_runs`  one row per logged LONGTERM validation scan: selector version,
                  frozen config, deploy SHA, universe / eligible / budget counts.
`lt_shadow_picks` the shadow's top-N ranked stocks for that scan plus EVERY control
                  pick (with its shadow rank, or the eligibility `exclusion` when
                  the shadow could not rank it), with the exact inputs, component
                  percentiles, levels and whether the control chose the stock.

`scan_id` is the signals_log scan_id, so the control for any shadow run is the
signals_log rows with that scan_id and final_selected = 1, and forward outcomes
join on (symbol, date) to `forward_returns` exactly as the control's do.

Written only by services.lt_shadow_selector; read by nothing in ranking,
selection, the served feed, the portfolio or the engine. Tables are created
lazily on first write — never from a FastAPI startup handler.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .schema import get_connection

DDL = """
CREATE TABLE IF NOT EXISTS lt_shadow_runs (
    scan_id          TEXT PRIMARY KEY,   -- = signals_log.scan_id of the control scan
    date             TEXT NOT NULL,
    selector_version TEXT NOT NULL,
    config           TEXT NOT NULL,      -- frozen JSON: weights, eligibility, budget rule
    git_sha          TEXT,               -- deploy that produced the run
    universe         INTEGER NOT NULL,
    eligible         INTEGER NOT NULL,
    budget           INTEGER NOT NULL,   -- = control final_selected count
    control_count    INTEGER NOT NULL,
    overlap          INTEGER NOT NULL,   -- shadow-selected ∩ control-selected
    exclusions       TEXT NOT NULL,      -- JSON {reason: count}
    coverage         TEXT,               -- JSON fundamentals coverage of the common-support pool
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lt_shadow_runs_date ON lt_shadow_runs(date);
CREATE TABLE IF NOT EXISTS lt_shadow_picks (
    scan_id          TEXT NOT NULL,
    date             TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    rank             INTEGER,            -- NULL only for a control pick the shadow could not rank
    score            REAL,
    selected         INTEGER NOT NULL,   -- rank <= budget
    control_selected INTEGER NOT NULL,
    cmp              REAL,
    entry            REAL,
    stop_loss        REAL,
    targets          TEXT,
    setup            TEXT,
    components       TEXT NOT NULL,      -- JSON percentiles actually scored
    exclusion        TEXT,               -- shadow eligibility failure (control picks only)
    inputs           TEXT NOT NULL,      -- JSON raw inputs incl. legacy vs corrected D/E
    PRIMARY KEY (scan_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_lt_shadow_picks_date ON lt_shadow_picks(date, selected);
"""

PICK_COLUMNS = ("scan_id", "date", "symbol", "rank", "score", "selected", "control_selected", "cmp",
                "entry", "stop_loss", "targets", "setup", "components", "exclusion", "inputs")


def ensure_tables(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(DDL)
    finally:
        if own:
            conn.close()


def write_run(run: Any, config: dict) -> int:
    """Persist one shadow run atomically. Re-running a scan_id replaces it."""
    from services.lt_shadow_selector import pick_row

    rows = [pick_row(run, p) for p in run.picks]
    conn = get_connection()
    try:
        ensure_tables(conn)
        with conn:
            conn.execute("DELETE FROM lt_shadow_picks WHERE scan_id = ?", (run.scan_id,))
            conn.execute(
                "INSERT OR REPLACE INTO lt_shadow_runs (scan_id, date, selector_version, config, git_sha, "
                "universe, eligible, budget, control_count, overlap, exclusions, coverage) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run.scan_id, run.date, config["selector_version"], json.dumps(config, sort_keys=True),
                 os.getenv("RAILWAY_GIT_COMMIT_SHA"), run.universe, run.eligible, run.budget,
                 run.control_count, run.overlap, json.dumps(run.exclusions, sort_keys=True),
                 json.dumps(run.coverage, sort_keys=True)),
            )
            conn.executemany(
                f"INSERT INTO lt_shadow_picks ({','.join(PICK_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(PICK_COLUMNS))})",
                [tuple(r[c] for c in PICK_COLUMNS) for r in rows],
            )
        return len(rows)
    finally:
        conn.close()
