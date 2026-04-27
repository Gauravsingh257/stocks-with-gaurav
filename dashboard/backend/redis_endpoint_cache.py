"""
dashboard/backend/redis_endpoint_cache.py

Canonical Redis snapshots per API surface (no parameterized key fragmentation).
Atomic discovery bundle + snapshot:global_version; aligned secondary endpoints;
non-empty write gate for discovery; consistency guard + bundle fallback; reliability metrics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Callable

log = logging.getLogger("dashboard.redis_endpoint_cache")

# --- Keys --------------------------------------------------------------------
KEY_GLOBAL_VERSION = "snapshot:global_version"
KEY_BUNDLE_CONSISTENT = "snapshot:bundle_consistent"
KEY_BUNDLE_LKG = "snapshot:last_known_good:bundle_consistent"

# Canonical endpoint names (single live key each: snapshot:{name})
CANONICAL_DISCOVERY = "discovery"
CANONICAL_WATCHLIST = "watchlist"
CANONICAL_FINAL = "final"

# Endpoints that participate in bundle alignment / mismatch detection
BUNDLE_ALIGNED_CANONICALS = frozenset(
    {CANONICAL_DISCOVERY, CANONICAL_WATCHLIST, CANONICAL_FINAL}
)

META_PREFIX = "snapshot:endpoint_meta:"
STATUS_PREFIX = "snapshot:endpoint_status:"
SIZE_PREFIX = "snapshot:endpoint_size:"

# Reliability observability (sidecar keys; does not change core snapshot key layout)
KEY_REL_ENGINE_OUTPUT_COUNT = "snapshot:reliability:discovery:engine_output_count"
KEY_REL_SNAPSHOT_WRITTEN_COUNT = "snapshot:reliability:discovery:snapshot_written_count"
KEY_REL_SKIP_REASON = "snapshot:reliability:discovery:snapshot_skipped_reason"
KEY_REL_SKIP_TS = "snapshot:reliability:discovery:snapshot_skipped_ts"

LIVE_TTL_SEC = int(os.getenv("ENDPOINT_SNAPSHOT_LIVE_TTL_SEC", "600"))
LKG_TTL_SEC = int(os.getenv("ENDPOINT_SNAPSHOT_LKG_TTL_SEC", "86400"))
REL_SKIP_REASON_TTL_SEC = int(os.getenv("SNAPSHOT_SKIP_REASON_TTL_SEC", "86400"))


def _get_redis():
    try:
        from dashboard.backend.cache import _get_redis as _gr
        return _gr()
    except Exception:
        return None


def canonical_live_key(name: str) -> str:
    return f"snapshot:{name}"


def canonical_lkg_key(name: str) -> str:
    return f"snapshot:last_known_good:{name}"


def slug_from_params(prefix: str, **params: Any) -> str:
    """Deprecated: stable slug from params. Prefer canonical keys (no params)."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    h = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"{prefix}:{h}"


def _read_global_version(r) -> int:
    try:
        raw = r.get(KEY_GLOBAL_VERSION)
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


def load_live_snapshot(canonical: str) -> dict | None:
    """Read live snapshot:{canonical} JSON (no LKG fallback)."""
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(canonical_live_key(canonical))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def serve_cached_endpoint(canonical: str) -> dict | None:
    """Prefer live Redis snapshot, else last_known_good for this canonical."""
    live = load_live_snapshot(canonical)
    if isinstance(live, dict) and live:
        return live
    return load_last_known_good(canonical)


def load_last_known_good(canonical: str) -> dict | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(canonical_lkg_key(canonical))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _load_bundle_live_or_lkg(r) -> dict | None:
    for key in (KEY_BUNDLE_CONSISTENT, KEY_BUNDLE_LKG):
        try:
            raw = r.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            continue
    return None


def _stamp_aligned_version(payload: dict) -> dict:
    """Align secondary endpoint payload to current global_version (no INCR)."""
    r = _get_redis()
    g = _read_global_version(r) if r else 0
    out = dict(payload)
    out["_snapshot_version"] = g
    out["_aligned_global_version"] = g
    return out


def store_endpoint_snapshot(canonical: str, payload: dict) -> None:
    """Persist validated payload under canonical snapshot:{name} + LKG + meta."""
    r = _get_redis()
    if r is None:
        return
    try:
        now = time.time()
        enriched = _stamp_aligned_version(payload)
        enriched["_endpoint_written_at"] = now
        enriched["_canonical"] = canonical
        raw = json.dumps(enriched, default=str)
        size_b = len(raw.encode("utf-8"))
        slug = canonical
        meta = {"canonical": canonical, "ts": now, "size_bytes": size_b, "ok": True}

        pipe = r.pipeline(transaction=True)
        pipe.setex(canonical_live_key(canonical), LIVE_TTL_SEC, raw)
        pipe.setex(canonical_lkg_key(canonical), LKG_TTL_SEC, raw)
        pipe.setex(f"{META_PREFIX}{canonical}", min(LIVE_TTL_SEC, 3600), json.dumps(meta, default=str))
        pipe.setex(f"{STATUS_PREFIX}{canonical}", min(LIVE_TTL_SEC, 3600), "success")
        pipe.setex(f"{SIZE_PREFIX}{canonical}", min(LIVE_TTL_SEC, 3600), str(size_b))
        pipe.execute()
    except Exception as e:
        log.debug("store_endpoint_snapshot failed %s: %s", canonical, e)


def commit_discovery_bundle(payload: dict) -> bool:
    """
    Atomic: INCR global_version; write discovery, watchlist, final slices + bundle + LKG.
    Call only after normalize_discovery_payload_for_storage + nonempty bucket check.
    """
    r = _get_redis()
    if r is None:
        return False
    try:
        now = time.time()
        v = int(r.incr(KEY_GLOBAL_VERSION))

        disc = dict(payload)
        disc["_snapshot_version"] = v
        disc["_aligned_global_version"] = v
        disc["_canonical"] = CANONICAL_DISCOVERY
        disc["_endpoint_written_at"] = now

        wt_list = payload.get("watchlist") if isinstance(payload.get("watchlist"), list) else []
        fn_list = payload.get("final_trades") if isinstance(payload.get("final_trades"), list) else []

        wl_payload = {
            "items": wt_list,
            "_snapshot_version": v,
            "_aligned_global_version": v,
            "_canonical": CANONICAL_WATCHLIST,
            "_endpoint_written_at": now,
        }
        fn_payload = {
            "items": fn_list,
            "_snapshot_version": v,
            "_aligned_global_version": v,
            "_canonical": CANONICAL_FINAL,
            "_endpoint_written_at": now,
        }

        bundle = {
            "version": v,
            "updated_at": now,
            "discovery": disc,
            "watchlist": wt_list,
            "final_trades": fn_list,
        }
        bundle_raw = json.dumps(bundle, default=str)
        disc_raw = json.dumps(disc, default=str)
        wl_raw = json.dumps(wl_payload, default=str)
        fn_raw = json.dumps(fn_payload, default=str)

        disc_size = len(disc_raw.encode("utf-8"))
        meta_d = {"canonical": CANONICAL_DISCOVERY, "ts": now, "size_bytes": disc_size, "ok": True}
        meta_w = {"canonical": CANONICAL_WATCHLIST, "ts": now, "size_bytes": len(wl_raw.encode("utf-8")), "ok": True}
        meta_f = {"canonical": CANONICAL_FINAL, "ts": now, "size_bytes": len(fn_raw.encode("utf-8")), "ok": True}

        pipe = r.pipeline(transaction=True)
        pipe.setex(canonical_live_key(CANONICAL_DISCOVERY), LIVE_TTL_SEC, disc_raw)
        pipe.setex(canonical_lkg_key(CANONICAL_DISCOVERY), LKG_TTL_SEC, disc_raw)
        pipe.setex(f"{META_PREFIX}{CANONICAL_DISCOVERY}", min(LIVE_TTL_SEC, 3600), json.dumps(meta_d, default=str))
        pipe.setex(f"{STATUS_PREFIX}{CANONICAL_DISCOVERY}", min(LIVE_TTL_SEC, 3600), "success")
        pipe.setex(f"{SIZE_PREFIX}{CANONICAL_DISCOVERY}", min(LIVE_TTL_SEC, 3600), str(disc_size))

        pipe.setex(canonical_live_key(CANONICAL_WATCHLIST), LIVE_TTL_SEC, wl_raw)
        pipe.setex(canonical_lkg_key(CANONICAL_WATCHLIST), LKG_TTL_SEC, wl_raw)
        pipe.setex(f"{META_PREFIX}{CANONICAL_WATCHLIST}", min(LIVE_TTL_SEC, 3600), json.dumps(meta_w, default=str))
        pipe.setex(f"{STATUS_PREFIX}{CANONICAL_WATCHLIST}", min(LIVE_TTL_SEC, 3600), "success")

        pipe.setex(canonical_live_key(CANONICAL_FINAL), LIVE_TTL_SEC, fn_raw)
        pipe.setex(canonical_lkg_key(CANONICAL_FINAL), LKG_TTL_SEC, fn_raw)
        pipe.setex(f"{META_PREFIX}{CANONICAL_FINAL}", min(LIVE_TTL_SEC, 3600), json.dumps(meta_f, default=str))
        pipe.setex(f"{STATUS_PREFIX}{CANONICAL_FINAL}", min(LIVE_TTL_SEC, 3600), "success")

        pipe.setex(KEY_BUNDLE_CONSISTENT, LIVE_TTL_SEC, bundle_raw)
        pipe.setex(KEY_BUNDLE_LKG, LKG_TTL_SEC, bundle_raw)
        pipe.execute()
        return True
    except Exception as e:
        log.warning("commit_discovery_bundle failed: %s", e)
        return False


def commit_watchlist_final_independent(watchlist: list[Any], final_trades: list[Any]) -> bool:
    """
    Update watchlist + final slices without rewriting full discovery payload.
    Bumps global_version once; merges discovery from current Redis into bundle.
    """
    r = _get_redis()
    if r is None:
        return False
    try:
        now = time.time()
        v = int(r.incr(KEY_GLOBAL_VERSION))

        wl_payload = {
            "items": watchlist,
            "_snapshot_version": v,
            "_aligned_global_version": v,
            "_canonical": CANONICAL_WATCHLIST,
            "_endpoint_written_at": now,
        }
        fn_payload = {
            "items": final_trades,
            "_snapshot_version": v,
            "_aligned_global_version": v,
            "_canonical": CANONICAL_FINAL,
            "_endpoint_written_at": now,
        }
        wl_raw = json.dumps(wl_payload, default=str)
        fn_raw = json.dumps(fn_payload, default=str)

        disc = {}
        try:
            dr = r.get(canonical_live_key(CANONICAL_DISCOVERY))
            if dr:
                disc = json.loads(dr)
        except Exception:
            disc = {}

        bundle = {
            "version": v,
            "updated_at": now,
            "discovery": disc if isinstance(disc, dict) else {},
            "watchlist": watchlist,
            "final_trades": final_trades,
        }
        bundle_raw = json.dumps(bundle, default=str)

        pipe = r.pipeline(transaction=True)
        pipe.setex(canonical_live_key(CANONICAL_WATCHLIST), LIVE_TTL_SEC, wl_raw)
        pipe.setex(canonical_lkg_key(CANONICAL_WATCHLIST), LKG_TTL_SEC, wl_raw)
        pipe.setex(f"{META_PREFIX}{CANONICAL_WATCHLIST}", min(LIVE_TTL_SEC, 3600), json.dumps(
            {"canonical": CANONICAL_WATCHLIST, "ts": now, "size_bytes": len(wl_raw.encode("utf-8")), "ok": True},
            default=str,
        ))
        pipe.setex(f"{STATUS_PREFIX}{CANONICAL_WATCHLIST}", min(LIVE_TTL_SEC, 3600), "success")

        pipe.setex(canonical_live_key(CANONICAL_FINAL), LIVE_TTL_SEC, fn_raw)
        pipe.setex(canonical_lkg_key(CANONICAL_FINAL), LKG_TTL_SEC, fn_raw)
        pipe.setex(f"{META_PREFIX}{CANONICAL_FINAL}", min(LIVE_TTL_SEC, 3600), json.dumps(
            {"canonical": CANONICAL_FINAL, "ts": now, "size_bytes": len(fn_raw.encode("utf-8")), "ok": True},
            default=str,
        ))
        pipe.setex(f"{STATUS_PREFIX}{CANONICAL_FINAL}", min(LIVE_TTL_SEC, 3600), "success")

        pipe.setex(KEY_BUNDLE_CONSISTENT, LIVE_TTL_SEC, bundle_raw)
        pipe.setex(KEY_BUNDLE_LKG, LKG_TTL_SEC, bundle_raw)
        pipe.execute()
        return True
    except Exception as e:
        log.warning("commit_watchlist_final_independent failed: %s", e)
        return False


def discovery_bucket_total_items(p: dict) -> int:
    """Stocks counted across watchlist + final_trades + discovery (engine-visible buckets)."""
    if not isinstance(p, dict):
        return 0
    wt = p.get("watchlist") if isinstance(p.get("watchlist"), list) else []
    fn = p.get("final_trades") if isinstance(p.get("final_trades"), list) else []
    disc = p.get("discovery") if isinstance(p.get("discovery"), list) else []
    return len(wt) + len(fn) + len(disc)


def normalize_discovery_payload_for_storage(p: dict) -> dict:
    """Ensure list keys exist so partial engine output can be stored safely."""
    out = dict(p)
    for k in ("watchlist", "final_trades", "discovery"):
        v = out.get(k)
        out[k] = list(v) if isinstance(v, list) else []
    ft = out["final_trades"]
    if not isinstance(out.get("items"), list):
        out["items"] = list(ft)
    else:
        out["items"] = list(out["items"])
    return out


def record_discovery_reliability_metrics(
    *,
    items_total: int = 0,
    written: bool = False,
    skip_reason: str | None = None,
) -> None:
    """Increment counters and store last skip reason (does not alter snapshot key schema)."""
    r = _get_redis()
    if r is None:
        return
    try:
        pipe = r.pipeline(transaction=True)
        if items_total > 0:
            pipe.incrby(KEY_REL_ENGINE_OUTPUT_COUNT, items_total)
        if written:
            pipe.incr(KEY_REL_SNAPSHOT_WRITTEN_COUNT)
        if skip_reason:
            ts = time.time()
            pipe.setex(KEY_REL_SKIP_REASON, REL_SKIP_REASON_TTL_SEC, skip_reason[:1024])
            pipe.setex(KEY_REL_SKIP_TS, REL_SKIP_REASON_TTL_SEC, str(ts))
        pipe.execute()
    except Exception as e:
        log.debug("record_discovery_reliability_metrics failed: %s", e)


def valid_discovery_redis_write(p: dict) -> bool:
    """Write snapshot only when engine produced ≥1 stock in wl/final/discovery; never on timeout/error."""
    if not isinstance(p, dict):
        return False
    if p.get("scan_id") == "timeout":
        return False
    if p.get("error"):
        return False
    return discovery_bucket_total_items(p) > 0


def _apply_bundle_guard(canonical: str, out: dict) -> dict:
    """If discovery trio versions disagree with global_version, serve bundle slice."""
    r = _get_redis()
    if r is None:
        out.setdefault("_global_version", 0)
        return out

    g = _read_global_version(r)
    out["_global_version"] = g

    if canonical not in BUNDLE_ALIGNED_CANONICALS:
        sv = int(out.get("_snapshot_version") or 0)
        aligned = (g == 0 and sv == 0) or (g > 0 and sv == g)
        out["snapshot_bundle_aligned"] = aligned
        out["snapshot_bundle_mismatch"] = not aligned and g > 0
        return out

    sv = int(out.get("_snapshot_version") or 0)
    aligned = (g == 0 and sv == 0) or (g > 0 and sv == g)
    out["snapshot_bundle_aligned"] = aligned
    out["snapshot_bundle_mismatch"] = not aligned and g > 0

    if aligned:
        return out

    bundle = _load_bundle_live_or_lkg(r)
    if not bundle:
        out["snapshot_stale_reason"] = out.get("snapshot_stale_reason") or "no_bundle_fallback"
        return out

    ver = int(bundle.get("version") or 0)
    out["_bundle_fallback_version"] = ver

    if canonical == CANONICAL_DISCOVERY:
        d = bundle.get("discovery")
        if isinstance(d, dict) and d:
            fb = dict(d)
            fb["snapshot_stale"] = True
            fb["snapshot_source"] = fb.get("snapshot_source") or "bundle_consistent_fallback"
            fb["snapshot_stale_reason"] = "endpoint_global_version_mismatch"
            fb["snapshot_bundle_mismatch"] = True
            fb["_global_version"] = g
            fb["_bundle_fallback_version"] = ver
            return fb

    if canonical == CANONICAL_WATCHLIST:
        wl = bundle.get("watchlist")
        if isinstance(wl, list):
            return {
                "items": wl,
                "_snapshot_version": ver,
                "_aligned_global_version": ver,
                "snapshot_stale": True,
                "snapshot_source": "bundle_consistent_fallback",
                "snapshot_stale_reason": "endpoint_global_version_mismatch",
                "snapshot_bundle_mismatch": True,
                "_global_version": g,
                "_bundle_fallback_version": ver,
            }

    if canonical == CANONICAL_FINAL:
        fn = bundle.get("final_trades")
        if isinstance(fn, list):
            return {
                "items": fn,
                "_snapshot_version": ver,
                "_aligned_global_version": ver,
                "snapshot_stale": True,
                "snapshot_source": "bundle_consistent_fallback",
                "snapshot_stale_reason": "endpoint_global_version_mismatch",
                "snapshot_bundle_mismatch": True,
                "_global_version": g,
                "_bundle_fallback_version": ver,
            }

    return out


def finalize_endpoint(
    canonical: str,
    payload: dict | None,
    is_valid: Callable[[dict], bool],
    *,
    discovery_atomic: bool = False,
) -> dict:
    """
    If payload is valid → store (discovery uses atomic trio when discovery_atomic=True).
    Else → prefer LKG (never replace good Redis with empty discovery output).
    Applies bundle consistency guard for discovery/watchlist/final.
    """
    if payload is None:
        payload = {}

    disc_mode = discovery_atomic and canonical == CANONICAL_DISCOVERY
    if disc_mode:
        payload = normalize_discovery_payload_for_storage(dict(payload))

    out = dict(payload)

    if isinstance(payload, dict) and is_valid(payload):
        if disc_mode:
            items_n = discovery_bucket_total_items(payload)
            if commit_discovery_bundle(payload):
                record_discovery_reliability_metrics(items_total=items_n, written=True)
                r2 = _get_redis()
                v = _read_global_version(r2) if r2 else 0
                out = dict(payload)
                out["_snapshot_version"] = v
                out["_aligned_global_version"] = v
                out["snapshot_stale"] = False
                out["snapshot_source"] = "live"
                out.pop("snapshot_stale_reason", None)
                return _apply_bundle_guard(canonical, out)
            log.warning("commit_discovery_bundle failed; refusing partial live write")
            record_discovery_reliability_metrics(skip_reason="discovery_commit_pipeline_failed")
            fb = load_last_known_good(canonical)
            if fb is not None:
                merged = dict(fb)
                merged["snapshot_stale"] = True
                merged["snapshot_source"] = "last_known_good"
                merged["snapshot_stale_reason"] = "discovery_atomic_commit_failed"
                merged["_requested_canonical"] = canonical
                return _apply_bundle_guard(canonical, merged)
            empty = dict(payload)
            empty["snapshot_stale"] = True
            empty["snapshot_source"] = "live_no_fallback"
            empty["snapshot_stale_reason"] = "discovery_atomic_commit_failed_no_lkg"
            empty["_requested_canonical"] = canonical
            return _apply_bundle_guard(canonical, empty)

        if canonical in ("swing", "longterm"):
            items_list = payload.get("items") if isinstance(payload.get("items"), list) else []
            if len(items_list) == 0:
                fb = load_last_known_good(canonical)
                if fb is not None:
                    merged = dict(fb)
                    merged["snapshot_stale"] = True
                    merged["snapshot_source"] = "last_known_good"
                    merged["snapshot_stale_reason"] = "empty_research_list_preserves_previous_snapshot"
                    merged["_requested_canonical"] = canonical
                    return _apply_bundle_guard(canonical, merged)

        store_endpoint_snapshot(canonical, payload)
        out = _stamp_aligned_version(dict(payload))
        out["snapshot_stale"] = False
        out["snapshot_source"] = "live"
        out.pop("snapshot_stale_reason", None)
        return _apply_bundle_guard(canonical, out)

    if disc_mode:
        sr = "empty_discovery_buckets_skip_write"
        if isinstance(payload, dict):
            if payload.get("scan_id") == "timeout":
                sr = "timeout_skip_write"
            elif payload.get("error"):
                sr = "error_skip_write"
        record_discovery_reliability_metrics(skip_reason=sr)

    fallback = load_last_known_good(canonical)
    if fallback is not None:
        fb = dict(fallback)
        fb["snapshot_stale"] = True
        fb["snapshot_source"] = "last_known_good"
        if disc_mode and isinstance(payload, dict) and discovery_bucket_total_items(payload) == 0:
            fb["snapshot_stale_reason"] = "empty_discovery_buckets_preserving_last_snapshot"
        else:
            fb["snapshot_stale_reason"] = "invalid_or_empty_live_payload"
        fb["_requested_canonical"] = canonical
        return _apply_bundle_guard(canonical, fb)

    out["snapshot_stale"] = True
    out["snapshot_source"] = "live_no_fallback"
    out["snapshot_stale_reason"] = "invalid_live_no_cached_fallback"
    out["_requested_canonical"] = canonical
    return _apply_bundle_guard(canonical, out)


# --- Validators --------------------------------------------------------------

def valid_discovery_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    if p.get("scan_id") == "timeout":
        return False
    if p.get("error"):
        return False
    for k in ("items", "final_trades", "watchlist", "discovery"):
        if k not in p:
            return False
    return True


def valid_discovery_user_visible(p: dict) -> bool:
    """Structural validity plus at least one actionable bucket (non-empty lists)."""
    if not valid_discovery_payload(p):
        return False
    wt = p.get("watchlist") if isinstance(p.get("watchlist"), list) else []
    fn = p.get("final_trades") if isinstance(p.get("final_trades"), list) else []
    disc = p.get("discovery") if isinstance(p.get("discovery"), list) else []
    items = p.get("items") if isinstance(p.get("items"), list) else []
    if len(wt) == 0 and len(fn) == 0 and len(disc) == 0 and len(items) == 0:
        return False
    return True


def valid_research_list_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    if not isinstance(p.get("items"), list):
        return False
    c = p.get("count", len(p.get("items") or []))
    return isinstance(c, (int, float))


def valid_running_trades_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    return isinstance(p.get("items"), list)


def valid_coverage_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    return "target_universe" in p and "latest" in p


def valid_layer_report_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    if p.get("available") is False:
        return False
    return True


def valid_performance_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    return "total_recommendations" in p


def valid_validation_payload(p: dict) -> bool:
    if not isinstance(p, dict):
        return False
    sid = p.get("scan_id")
    if not sid:
        return False
    return isinstance(p.get("items"), list)


def valid_oi_payload(p: dict) -> bool:
    if isinstance(p, dict) and p:
        return True
    return False


def valid_agents_status_payload(p: dict) -> bool:
    return isinstance(p, dict) and isinstance(p.get("agents"), list)


def valid_scan_status_payload(p: dict) -> bool:
    return isinstance(p, dict) and "horizons" in p and "in_flight" in p


def valid_portfolio_summary_payload(p: dict) -> bool:
    return isinstance(p, dict) and "swing" in p and "longterm" in p


def endpoint_debug_inventory() -> dict[str, Any]:
    """TTL/status, global_version, per-canonical versions, mismatch flags."""
    r = _get_redis()
    if r is None:
        return {"available": False}

    out: dict[str, Any] = {"available": True, "global_version": None, "bundle": {}, "endpoints": {}, "mismatch": {}}

    try:
        gv_raw = r.get(KEY_GLOBAL_VERSION)
        out["global_version"] = int(gv_raw) if gv_raw is not None else 0
    except Exception:
        out["global_version"] = None

    for label, rk in (
        ("bundle_consistent", KEY_BUNDLE_CONSISTENT),
        ("bundle_lkg", KEY_BUNDLE_LKG),
    ):
        try:
            ttl = r.ttl(rk)
            raw = r.get(rk)
            bd = json.loads(raw) if raw else None
            ver = int(bd.get("version")) if isinstance(bd, dict) and bd.get("version") is not None else None
            out["bundle"][label] = {
                "key": rk,
                "ttl_sec": ttl if ttl >= 0 else None,
                "exists": ttl != -2,
                "embedded_version": ver,
            }
        except Exception:
            out["bundle"][label] = {"error": True}

    # Known canonical endpoints (live keys snapshot:<name>)
    scan_names = [
        CANONICAL_DISCOVERY,
        CANONICAL_WATCHLIST,
        CANONICAL_FINAL,
        "swing",
        "longterm",
        "running_trades",
        "coverage",
        "layer_report",
        "performance",
        "validation",
        "scan_status",
        "portfolio_summary",
        "agents_status",
        "oi_intelligence",
    ]

    for name in scan_names:
        key = canonical_live_key(name)
        try:
            ttl = r.ttl(key)
            raw = r.get(key)
            parsed = json.loads(raw) if raw else None
            sv = None
            if isinstance(parsed, dict):
                sv = parsed.get("_snapshot_version")
                if sv is not None:
                    sv = int(sv)
            out["endpoints"][name] = {
                "live_key": key,
                "ttl_sec": ttl if ttl >= 0 else None,
                "exists": ttl != -2,
                "endpoint_version": sv,
                "size_bytes": r.get(f"{SIZE_PREFIX}{name}"),
                "status": r.get(f"{STATUS_PREFIX}{name}"),
            }
        except Exception:
            out["endpoints"][name] = {"error": True}

    gv = out.get("global_version")
    if isinstance(gv, int):
        mismatched: list[str] = []
        for name in BUNDLE_ALIGNED_CANONICALS:
            ep = out["endpoints"].get(name)
            if not isinstance(ep, dict):
                continue
            ev = ep.get("endpoint_version")
            if ev is not None and gv > 0 and ev != gv:
                mismatched.append(name)
        out["mismatch"] = {
            "bundle_aligned_canonicals_checked": list(BUNDLE_ALIGNED_CANONICALS),
            "mismatch_with_global_version": mismatched,
            "has_mismatch": len(mismatched) > 0,
        }

    try:
        eng = r.get(KEY_REL_ENGINE_OUTPUT_COUNT)
        wr = r.get(KEY_REL_SNAPSHOT_WRITTEN_COUNT)
        sk = r.get(KEY_REL_SKIP_REASON)
        sk_ts = r.get(KEY_REL_SKIP_TS)
        out["reliability_discovery"] = {
            "engine_output_count": int(eng) if eng is not None else 0,
            "snapshot_written_count": int(wr) if wr is not None else 0,
            "snapshot_skipped_reason": sk.decode() if isinstance(sk, bytes) else (sk or None),
            "snapshot_skipped_ts": sk_ts.decode() if isinstance(sk_ts, bytes) else sk_ts,
            "keys": {
                "engine_output_count": KEY_REL_ENGINE_OUTPUT_COUNT,
                "snapshot_written_count": KEY_REL_SNAPSHOT_WRITTEN_COUNT,
                "snapshot_skipped_reason": KEY_REL_SKIP_REASON,
            },
        }
    except Exception:
        out["reliability_discovery"] = {"error": True}

    return out
