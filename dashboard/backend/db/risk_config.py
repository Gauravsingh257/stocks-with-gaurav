"""
dashboard/backend/db/risk_config.py
===================================
Config version history for the risk engine. Every parameter change (stop cap,
risk budget, liquidity thresholds, flags …) is recorded with a timestamp, the
diff, and a reason — so the audit trail explains *why* the engine behaved
differently on a given day.

Two ways a version gets recorded:
  - AUTO: on each dashboard read we snapshot the live `risk_engine.cfg()` and, if
    it differs from the last stored version, append a new row (source=auto). This
    catches env-var changes made on the platform (which the app can't otherwise
    observe).
  - MANUAL: an operator can POST a change with a human reason (source=manual),
    e.g. "widened LT stop cap to 15% after Q3 review".

Read-only for the app's trading logic — this table never influences a decision.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from .schema import get_connection

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

_DDL = """
CREATE TABLE IF NOT EXISTS risk_config_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at  TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'auto',
    reason       TEXT,
    config_json  TEXT NOT NULL,
    changes_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_risk_cfg_hist_time ON risk_config_history(recorded_at DESC);
"""


def _ensure() -> None:
    conn = get_connection()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


def _latest_row() -> dict | None:
    _ensure()
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT * FROM risk_config_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _diff(old: dict | None, new: dict) -> dict:
    """{key: [old, new]} for changed/added keys."""
    old = old or {}
    out: dict[str, list] = {}
    for k, v in new.items():
        if old.get(k) != v:
            out[k] = [old.get(k), v]
    return out


def record_config_change(config: dict, *, reason: str | None = None,
                         source: str = "auto") -> dict:
    """Append a new version IFF the config differs from the last stored one
    (or a manual reason is supplied). Returns {recorded: bool, entry|latest}."""
    _ensure()
    latest = _latest_row()
    latest_cfg = json.loads(latest["config_json"]) if latest else None
    changes = _diff(latest_cfg, config)

    # Record when something changed, or when a human explicitly annotates.
    if not changes and not (source == "manual" and reason):
        return {"recorded": False, "latest": latest}

    now = datetime.now(_IST).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO risk_config_history (recorded_at, source, reason, config_json, changes_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, source, reason, json.dumps(config, default=str), json.dumps(changes, default=str)),
        )
        conn.commit()
        entry = {"id": cur.lastrowid, "recorded_at": now, "source": source,
                 "reason": reason, "config": config, "changes": changes}
        logger.info("[RiskConfig] recorded v%s (%s) %d change(s): %s",
                    cur.lastrowid, source, len(changes), reason or "")
        return {"recorded": True, "entry": entry}
    finally:
        conn.close()


def get_config_history(limit: int = 50) -> list[dict]:
    _ensure()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM risk_config_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["config"] = json.loads(d.pop("config_json"))
                d["changes"] = json.loads(d.pop("changes_json") or "{}")
            except Exception:
                d["config"], d["changes"] = {}, {}
            out.append(d)
        return out
    finally:
        conn.close()
