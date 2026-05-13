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


def _norm_equity_sym(s: str) -> str:
    return str(s).replace("NSE:", "").strip().upper()


_EQUITY_LTP_HASH_KEY = "equity:ltp:latest"


def equity_ltp_from_snapshot(full_snap: Dict[str, Any], max_symbols: int = 64) -> Dict[str, float]:
    """
    Equity last prices for the unified market-LTP registry.
    Sources (merged in priority order):
      1. snapshot.equity_ltp — written directly by engine each cycle
      2. Redis ltp:{SYM} keys — for active trade symbols
      3. Redis equity:ltp:latest hash — engine scan-cycle LTP cache
    Keys are uppercase symbols (e.g. RELIANCE).
    """
    out: Dict[str, float] = {}

    # Source 1: snapshot.equity_ltp written directly by engine
    snap_eq = full_snap.get("equity_ltp")
    if isinstance(snap_eq, dict):
        for k, v in snap_eq.items():
            sym = _norm_equity_sym(str(k))
            if sym and isinstance(v, (int, float)) and v > 0:
                out[sym] = float(v)

    # Source 2: ltp:{SYM} Redis keys for active trade symbols
    seen: set[str] = set(out.keys())
    syms: list[str] = []
    for t in full_snap.get("active_trades") or []:
        if not isinstance(t, dict):
            continue
        raw = t.get("symbol")
        if not raw:
            continue
        sym = _norm_equity_sym(str(raw))
        if sym and sym not in seen:
            seen.add(sym)
            syms.append(sym)
        if len(syms) >= max_symbols:
            break
    if syms:
        try:
            from dashboard.backend.cache import get_ltp
            for sym in syms:
                if sym not in out:
                    p = get_ltp(sym)
                    if p is not None:
                        out[sym] = float(p)
                if len(out) >= max_symbols:
                    break
        except Exception:
            pass

    # Source 3: equity:ltp:latest hash (scan-cycle LTP written by engine for all scanned symbols)
    if len(out) < max_symbols:
        try:
            from dashboard.backend.cache import _get_redis
            r = _get_redis()
            if r is not None:
                raw_hash = r.hgetall(_EQUITY_LTP_HASH_KEY)
                if isinstance(raw_hash, dict):
                    for k, v in raw_hash.items():
                        sym = _norm_equity_sym(str(k))
                        if sym and sym not in out:
                            try:
                                price = float(v)
                                if price > 0:
                                    out[sym] = price
                            except (TypeError, ValueError):
                                pass
                        if len(out) >= max_symbols:
                            break
        except Exception:
            pass

    return out


def build_engine_digest(full_snap: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lightweight digest for WebSocket streaming — avoid re-sending full snapshot every tick.

    Clients merge into existing snapshot; full snapshot still sent periodically.
    """
    trades = full_snap.get("active_trades") or []
    idx = full_snap.get("index_ltp") if isinstance(full_snap.get("index_ltp"), dict) else {}
    cached_eq = full_snap.get("equity_ltp")
    if isinstance(cached_eq, dict) and cached_eq:
        eq = {
            str(k): float(v)
            for k, v in cached_eq.items()
            if isinstance(v, (int, float)) and str(k).strip()
        }
    else:
        eq = equity_ltp_from_snapshot(full_snap)
    base = {
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
    if eq:
        base["equity_ltp"] = eq
    return base
