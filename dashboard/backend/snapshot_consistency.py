"""
Snapshot consistency checks for Redis-backed API payloads.

Lightweight structural validation — no business-rule scoring here.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

# Minimum keys expected on enriched engine snapshots from state_bridge
_ENGINE_CORE_KEYS = frozenset(
    {"active_trades", "engine_live", "engine_running", "market_regime", "stale"}
)


def validate_engine_snapshot_structure(snap: Any) -> Tuple[bool, List[str]]:
    """
    Return (ok, issues). Does not reject unknown keys; flags missing/invalid types.
    """
    issues: List[str] = []
    if not isinstance(snap, dict):
        return False, ["not_a_dict"]
    if not isinstance(snap.get("active_trades"), list):
        issues.append("active_trades_not_list")
    for k in ("engine_live", "engine_running", "stale"):
        if k in snap and not isinstance(snap[k], (bool, type(None))):
            issues.append(f"{k}_not_bool")
    if "market_regime" in snap and snap["market_regime"] is not None:
        if not isinstance(snap["market_regime"], str):
            issues.append("market_regime_not_str")
    missing_core = [k for k in _ENGINE_CORE_KEYS if k not in snap]
    if missing_core:
        issues.append(f"missing_keys:{','.join(sorted(missing_core))}")
    ok = len(issues) == 0
    return ok, issues


def estimate_json_bytes(obj: Any) -> int:
    """UTF-8 byte length of canonical JSON (for observability only)."""
    try:
        return len(json.dumps(obj, default=str).encode("utf-8"))
    except Exception:
        return 0


def validate_research_list_snapshot(p: Any) -> Tuple[bool, List[str]]:
    """
    Structural validation for Redis-backed swing/longterm payloads (GET serve path).

    Does not enforce business rules; rejects obvious corrupt/partial writes.
    """
    issues: List[str] = []
    if not isinstance(p, dict):
        return False, ["not_a_dict"]
    if "items" not in p:
        issues.append("missing_items")
    elif not isinstance(p.get("items"), list):
        issues.append("items_not_list")
    c = p.get("count")
    if c is not None and not isinstance(c, (int, float)):
        issues.append("count_not_numeric")
    ok = len(issues) == 0
    return ok, issues


def validate_watchlist_operating_payload(p: Any) -> Tuple[bool, List[str]]:
    """
    Structural validation for Redis / JSON watchlist operating snapshots.
    Does not enforce business rules.
    """
    issues: List[str] = []
    if not isinstance(p, dict):
        return False, ["not_a_dict"]
    if "items" in p and not isinstance(p.get("items"), list):
        issues.append("items_not_list")
    if "feed" in p and not isinstance(p.get("feed"), list):
        issues.append("feed_not_list")
    ok = len(issues) == 0
    return ok, issues


def read_global_snapshot_version() -> int:
    """Unified global state version (snapshot:global_state_version + legacy mirror)."""
    try:
        from dashboard.backend.global_state_version import read_global_state_version

        return read_global_state_version()
    except Exception:
        return 0


def build_engine_digest(full_snap: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight digest for WebSocket streaming — avoid re-sending full snapshot every tick.

    Clients merge into existing snapshot; full snapshot still sent periodically.
    """
    trades = full_snap.get("active_trades") or []
    idx = full_snap.get("index_ltp") if isinstance(full_snap.get("index_ltp"), dict) else {}
    return {
        "digest": True,
        "snapshot_time": full_snap.get("snapshot_time"),
        "stale": full_snap.get("stale"),
        "stale_reason": full_snap.get("stale_reason"),
        "data_source": full_snap.get("data_source"),
        "engine_live": full_snap.get("engine_live"),
        "engine_running": full_snap.get("engine_running"),
        "engine_heartbeat_age_sec": full_snap.get("engine_heartbeat_age_sec"),
        "engine_last_cycle_age_sec": full_snap.get("engine_last_cycle_age_sec"),
        "market_regime": full_snap.get("market_regime"),
        "signals_today": full_snap.get("signals_today"),
        "active_trade_count": full_snap.get("active_trade_count"),
        "daily_pnl_r": full_snap.get("daily_pnl_r"),
        "circuit_breaker_active": full_snap.get("circuit_breaker_active"),
        "index_ltp": idx,
        "active_symbols_sample": [t.get("symbol") for t in trades[:12] if isinstance(t, dict)],
        "_estimate_full_bytes": estimate_json_bytes(full_snap),
    }
