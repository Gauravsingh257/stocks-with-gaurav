"""
dashboard/backend/user_store.py

Phase 3 — User-scoped data store.

Three tables in a small SQLite file (`dashboard_user_data.db`):

    user_preferences       (user_id PK, json)
    user_trade_journal     (id PK, user_id, symbol, direction, entry, exit,
                            stop, target, qty, pnl, rr, status, opened_at,
                            closed_at, notes, source, raw_json)
    -- performance is computed live from user_trade_journal, no table needed.

`user_id` is a free-form string. Frontend currently uses "default" until
real auth is wired in. The schema and routes are forward-compatible with
the existing `auth_router` (cookie session) — when present, we'll read the
user from there, otherwise fall back to "default".

All functions are best-effort and return safe empty values on failure so
the API layer never raises.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger("dashboard.user_store")

_DB_PATH = os.environ.get(
    "DASHBOARD_USER_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dashboard_user_data.db"),
)
_DB_PATH = os.path.abspath(_DB_PATH)

_lock = threading.Lock()
_initialized = False


# ─────────────────────────────────────────────────────────────────────────
# Connection / schema
# ─────────────────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    cn = sqlite3.connect(_DB_PATH, timeout=10, isolation_level=None)
    cn.row_factory = sqlite3.Row
    try:
        cn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    try:
        yield cn
    finally:
        try:
            cn.close()
        except Exception:
            pass


def _init() -> None:
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        with _conn() as cn:
            cn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry REAL,
                    stop REAL,
                    target REAL,
                    exit_price REAL,
                    qty REAL,
                    pnl REAL,
                    rr REAL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    setup TEXT,
                    confidence TEXT,
                    opened_at INTEGER NOT NULL,
                    closed_at INTEGER,
                    notes TEXT,
                    source TEXT,
                    raw_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_journal_user
                    ON user_trade_journal (user_id, opened_at DESC);
                CREATE INDEX IF NOT EXISTS idx_journal_status
                    ON user_trade_journal (user_id, status);
                """
            )
        _initialized = True


# ─────────────────────────────────────────────────────────────────────────
# Preferences
# ─────────────────────────────────────────────────────────────────────────

DEFAULT_PREFERENCES: Dict[str, Any] = {
    "risk_preference": "BALANCED",        # CONSERVATIVE / BALANCED / AGGRESSIVE
    "capital": 100_000,                   # ₹
    "risk_per_trade_pct": 1.0,            # % of capital per trade
    "min_rr": 1.5,
    "min_probability": 0,
    "preferred_setups": [],               # ["A", "B"] etc — ranking bias
    "setups_strict": False,               # if True, exclude non-preferred setups
    "direction": "BOTH",                  # LONG / SHORT / BOTH
    "alerts": {
        "approaching": True,
        "triggered": True,
        "target_hit": True,
        "stop_hit": True,
        "telegram": False,
    },
}


def get_preferences(user_id: str = "default") -> Dict[str, Any]:
    _init()
    try:
        with _conn() as cn:
            row = cn.execute(
                "SELECT payload_json FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return dict(DEFAULT_PREFERENCES)
        prefs = json.loads(row["payload_json"])
        merged = dict(DEFAULT_PREFERENCES)
        merged.update(prefs or {})
        return merged
    except Exception as exc:
        log.debug("get_preferences failed: %s", exc)
        return dict(DEFAULT_PREFERENCES)


def set_preferences(user_id: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
    _init()
    cleaned = _validate_prefs(prefs)
    try:
        with _conn() as cn:
            cn.execute(
                """INSERT INTO user_preferences (user_id, payload_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       payload_json=excluded.payload_json,
                       updated_at=excluded.updated_at""",
                (user_id, json.dumps(cleaned), int(time.time())),
            )
    except Exception as exc:
        log.warning("set_preferences failed: %s", exc)
    return get_preferences(user_id)


def _validate_prefs(prefs: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(DEFAULT_PREFERENCES)
    if not isinstance(prefs, dict):
        return out
    risk = str(prefs.get("risk_preference", out["risk_preference"])).upper()
    if risk in ("CONSERVATIVE", "BALANCED", "AGGRESSIVE"):
        out["risk_preference"] = risk
    cap = prefs.get("capital")
    if isinstance(cap, (int, float)) and cap > 0:
        out["capital"] = float(cap)
    rpt = prefs.get("risk_per_trade_pct")
    if isinstance(rpt, (int, float)) and 0 < rpt <= 10:
        out["risk_per_trade_pct"] = float(rpt)
    for key in ("min_rr", "min_probability"):
        v = prefs.get(key)
        if isinstance(v, (int, float)) and v >= 0:
            out[key] = float(v)
    setups = prefs.get("preferred_setups")
    if isinstance(setups, list):
        out["preferred_setups"] = [str(s).upper() for s in setups if str(s).upper() in {"A", "B", "C", "D"}]
    out["setups_strict"] = bool(prefs.get("setups_strict", False))
    direction = str(prefs.get("direction", out["direction"])).upper()
    if direction in ("LONG", "SHORT", "BOTH"):
        out["direction"] = direction
    alerts = prefs.get("alerts")
    if isinstance(alerts, dict):
        merged_alerts = dict(out["alerts"])
        for k, v in alerts.items():
            if k in merged_alerts:
                merged_alerts[k] = bool(v)
        out["alerts"] = merged_alerts
    return out


# ─────────────────────────────────────────────────────────────────────────
# Trade journal
# ─────────────────────────────────────────────────────────────────────────

def list_journal(user_id: str = "default", limit: int = 200, status: Optional[str] = None) -> List[Dict[str, Any]]:
    _init()
    try:
        with _conn() as cn:
            if status:
                rows = cn.execute(
                    """SELECT * FROM user_trade_journal
                       WHERE user_id = ? AND status = ?
                       ORDER BY opened_at DESC LIMIT ?""",
                    (user_id, status.upper(), int(limit)),
                ).fetchall()
            else:
                rows = cn.execute(
                    """SELECT * FROM user_trade_journal
                       WHERE user_id = ?
                       ORDER BY opened_at DESC LIMIT ?""",
                    (user_id, int(limit)),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        log.debug("list_journal failed: %s", exc)
        return []


def add_journal_entry(user_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    _init()
    if not isinstance(payload, dict):
        return None
    symbol = (payload.get("symbol") or "").upper().strip()
    if not symbol:
        return None
    direction = (payload.get("direction") or "LONG").upper()
    direction = "LONG" if direction in ("LONG", "BUY") else "SHORT"
    entry = _to_float(payload.get("entry"))
    stop = _to_float(payload.get("stop") or payload.get("sl"))
    target = _to_float(payload.get("target"))
    exit_price = _to_float(payload.get("exit") or payload.get("exit_price"))
    qty = _to_float(payload.get("qty"))
    rr = _to_float(payload.get("rr"))
    pnl = _to_float(payload.get("pnl"))
    if pnl is None and entry is not None and exit_price is not None and qty is not None:
        pnl = (exit_price - entry) * qty if direction == "LONG" else (entry - exit_price) * qty
    if rr is None and entry is not None and stop is not None and exit_price is not None:
        risk = abs(entry - stop)
        if risk > 0:
            move = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
            rr = round(move / risk, 2)
    status = (payload.get("status") or ("CLOSED" if exit_price is not None else "OPEN")).upper()
    opened_at = int(payload.get("opened_at") or time.time())
    closed_at = int(payload.get("closed_at")) if payload.get("closed_at") else (int(time.time()) if status == "CLOSED" else None)
    setup = (payload.get("setup") or "").upper() or None
    confidence = payload.get("confidence")
    notes = payload.get("notes")
    source = payload.get("source") or "manual"
    raw_json = json.dumps(payload, default=str) if payload else None

    try:
        with _conn() as cn:
            cur = cn.execute(
                """INSERT INTO user_trade_journal
                   (user_id, symbol, direction, entry, stop, target, exit_price, qty,
                    pnl, rr, status, setup, confidence, opened_at, closed_at, notes, source, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, symbol, direction, entry, stop, target, exit_price, qty,
                    pnl, rr, status, setup, confidence, opened_at, closed_at, notes, source, raw_json,
                ),
            )
            new_id = cur.lastrowid
            row = cn.execute("SELECT * FROM user_trade_journal WHERE id = ?", (new_id,)).fetchone()
        return _row_to_dict(row) if row else None
    except Exception as exc:
        log.warning("add_journal_entry failed: %s", exc)
        return None


def update_journal_entry(user_id: str, entry_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    _init()
    allowed = ("exit_price", "exit", "pnl", "rr", "status", "notes", "closed_at", "stop", "target")
    sets: List[str] = []
    args: List[Any] = []
    for key in allowed:
        if key not in payload:
            continue
        column = "exit_price" if key == "exit" else key
        v = payload[key]
        if column in ("exit_price", "pnl", "rr", "stop", "target"):
            v = _to_float(v)
        if column == "status" and isinstance(v, str):
            v = v.upper()
        if column == "closed_at" and isinstance(v, str) and v.isdigit():
            v = int(v)
        sets.append(f"{column} = ?")
        args.append(v)
    if not sets:
        return _get_journal(user_id, entry_id)
    args.extend([user_id, int(entry_id)])
    try:
        with _conn() as cn:
            cn.execute(
                f"UPDATE user_trade_journal SET {', '.join(sets)} WHERE user_id = ? AND id = ?",
                args,
            )
        return _get_journal(user_id, entry_id)
    except Exception as exc:
        log.warning("update_journal_entry failed: %s", exc)
        return None


def delete_journal_entry(user_id: str, entry_id: int) -> bool:
    _init()
    try:
        with _conn() as cn:
            cur = cn.execute(
                "DELETE FROM user_trade_journal WHERE user_id = ? AND id = ?",
                (user_id, int(entry_id)),
            )
            return cur.rowcount > 0
    except Exception as exc:
        log.warning("delete_journal_entry failed: %s", exc)
        return False


def _get_journal(user_id: str, entry_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as cn:
        row = cn.execute(
            "SELECT * FROM user_trade_journal WHERE user_id = ? AND id = ?",
            (user_id, int(entry_id)),
        ).fetchone()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: sqlite3.Row | None) -> Dict[str, Any]:
    if not row:
        return {}
    d = {k: row[k] for k in row.keys()}
    if isinstance(d.get("raw_json"), str):
        try:
            d["raw"] = json.loads(d["raw_json"])
        except (TypeError, ValueError):
            d["raw"] = None
    d.pop("raw_json", None)
    return d


# ─────────────────────────────────────────────────────────────────────────
# Performance engine
# ─────────────────────────────────────────────────────────────────────────

def compute_performance(user_id: str = "default") -> Dict[str, Any]:
    """Aggregate journal stats — never raises."""
    rows = list_journal(user_id=user_id, limit=10_000)
    closed = [r for r in rows if (r.get("status") or "").upper() == "CLOSED"]
    total = len(closed)
    if total == 0:
        return {
            "total_trades": 0,
            "open_trades": sum(1 for r in rows if (r.get("status") or "OPEN").upper() == "OPEN"),
            "win_rate": 0.0,
            "avg_rr": 0.0,
            "total_pnl": 0.0,
            "best_trade": None,
            "worst_trade": None,
            "by_setup": {},
            "best_setup": None,
            "worst_setup": None,
        }
    wins = [r for r in closed if (r.get("pnl") or 0) > 0]
    losses = [r for r in closed if (r.get("pnl") or 0) < 0]
    total_pnl = round(sum(r.get("pnl") or 0 for r in closed), 2)
    win_rate = round(len(wins) / total * 100.0, 1)
    rrs = [r.get("rr") for r in closed if isinstance(r.get("rr"), (int, float))]
    avg_rr = round(sum(rrs) / len(rrs), 2) if rrs else 0.0

    best = max(closed, key=lambda r: r.get("pnl") or 0)
    worst = min(closed, key=lambda r: r.get("pnl") or 0)

    by_setup: Dict[str, Dict[str, Any]] = {}
    for r in closed:
        s = (r.get("setup") or "—").upper()
        bucket = by_setup.setdefault(s, {"setup": s, "count": 0, "wins": 0, "pnl": 0.0, "avg_rr": 0.0})
        bucket["count"] += 1
        if (r.get("pnl") or 0) > 0:
            bucket["wins"] += 1
        bucket["pnl"] = round(bucket["pnl"] + (r.get("pnl") or 0), 2)
    for s, b in by_setup.items():
        b["win_rate"] = round(b["wins"] / b["count"] * 100.0, 1) if b["count"] else 0.0
        rr_for_setup = [r.get("rr") for r in closed if (r.get("setup") or "—").upper() == s and isinstance(r.get("rr"), (int, float))]
        b["avg_rr"] = round(sum(rr_for_setup) / len(rr_for_setup), 2) if rr_for_setup else 0.0

    setups_sorted = sorted(by_setup.values(), key=lambda x: (x["pnl"], x["win_rate"]), reverse=True)
    return {
        "total_trades": total,
        "open_trades": sum(1 for r in rows if (r.get("status") or "OPEN").upper() == "OPEN"),
        "win_rate": win_rate,
        "avg_rr": avg_rr,
        "total_pnl": total_pnl,
        "wins": len(wins),
        "losses": len(losses),
        "best_trade": _summarize_trade(best),
        "worst_trade": _summarize_trade(worst),
        "by_setup": list(by_setup.values()),
        "best_setup": setups_sorted[0]["setup"] if setups_sorted else None,
        "worst_setup": setups_sorted[-1]["setup"] if setups_sorted else None,
    }


def _summarize_trade(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": r.get("id"),
        "symbol": r.get("symbol"),
        "direction": r.get("direction"),
        "setup": r.get("setup"),
        "pnl": r.get("pnl"),
        "rr": r.get("rr"),
        "closed_at": r.get("closed_at"),
    }


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None
