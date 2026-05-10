"""
Phase 6.5 — Retention, evolution timelines, urgency layer, revisit psychology (Redis-first).

Trust-first: probabilistic language, threshold-based signals — no fabricated urgency.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dashboard.retention_engine")

PRODUCT_V1 = "product:v1:"
EVO_LIST_PREFIX = PRODUCT_V1 + "evo:"
VISIT_DIGEST_KEY = PRODUCT_V1 + "visit_digest:"
LAST_VISIT_TS_KEY = PRODUCT_V1 + "last_visit_ts:"
METRICS_TIMELINE_DAY = PRODUCT_V1 + "metrics:timeline_events:"
METRICS_URGENCY_DAY = PRODUCT_V1 + "metrics:urgency_events:"
METRICS_REVISIT_DAY = PRODUCT_V1 + "metrics:revisit_triggers:"
METRICS_EVO_SYMBOLS_DAY = PRODUCT_V1 + "metrics:evo_symbols:"
ALERT_HOUR_COUNT = PRODUCT_V1 + "alert_hour:"
INTEL_INTERACTION_DAY = PRODUCT_V1 + "metrics:intel_events:"
MAX_TIMELINE_PER_SYMBOL = int(os.getenv("RETENTION_TIMELINE_CAP", "80"))
MAX_ALERTS_PER_HOUR = int(os.getenv("RETENTION_IN_APP_ALERTS_PER_HOUR", "12"))


def _today() -> str:
    return date.today().isoformat()


def _norm_symbol(sym: str) -> str:
    return str(sym).replace("NSE:", "").strip().upper()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _intel_hash_lazy(item: Dict[str, Any]) -> str:
    """Lazy import avoids circular dependency with watchlist_intel_service."""
    from dashboard.backend.services.watchlist_intel_service import _intel_hash

    return _intel_hash(item)


def push_evolution_timeline_events(user_id: int, feed_events: List[Dict[str, Any]]) -> None:
    """Persist per-symbol evolution lines from watchlist feed diff (bounded list + metrics)."""
    if not feed_events:
        return
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        uid = int(user_id)
        day = _today()
        for ev in feed_events:
            sym = _norm_symbol(str(ev.get("symbol") or ""))
            if not sym:
                continue
            payload = {
                "ts": ev.get("ts") or datetime.now(timezone.utc).isoformat(),
                "headline": str(ev.get("headline") or "")[:400],
                "kind": str(ev.get("type") or "setup_shift"),
                "setup_status": ev.get("setup_status"),
                "lifecycle_stage": ev.get("lifecycle_stage"),
            }
            key = f"{EVO_LIST_PREFIX}{uid}:{sym}"
            r.lpush(key, json.dumps(payload, default=str))
            r.ltrim(key, 0, max(20, MAX_TIMELINE_PER_SYMBOL) - 1)
            r.expire(key, 14 * 86400)
            mk = f"{METRICS_TIMELINE_DAY}{day}"
            r.incr(mk)
            r.expire(mk, 5 * 86400)
            r.sadd(f"{METRICS_EVO_SYMBOLS_DAY}{day}", sym)
            r.expire(f"{METRICS_EVO_SYMBOLS_DAY}{day}", 5 * 86400)
            hl = str(payload.get("headline") or "").lower()
            if any(
                k in hl
                for k in (
                    "weakening",
                    "deterioration",
                    "invalidated",
                    "promoted",
                    "readiness",
                    "executable",
                    "final review",
                    "execution quality",
                )
            ):
                uk = f"{METRICS_URGENCY_DAY}{day}"
                r.incr(uk)
                r.expire(uk, 5 * 86400)
    except Exception as exc:
        logger.debug("evolution timeline push skipped: %s", exc)


def maybe_enqueue_high_signal_alerts(user_id: int, feed_events: List[Dict[str, Any]]) -> None:
    """Cap-aware in-app alerts for material desk transitions only."""
    if not feed_events:
        return
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        uid = int(user_id)
        hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        cap_key = f"{ALERT_HOUR_COUNT}{uid}:{hour_bucket}"
        current = int(r.get(cap_key) or 0)
        if current >= MAX_ALERTS_PER_HOUR:
            return
        from dashboard.backend.routes.user_product import enqueue_in_app_alert

        for ev in feed_events:
            if current >= MAX_ALERTS_PER_HOUR:
                break
            hl = str(ev.get("headline") or "").lower()
            sym = str(ev.get("symbol") or "") or None
            kind = None
            if "invalidated" in hl or "deterioration risk accelerating" in hl:
                kind = "deterioration"
            elif "promoted" in hl or "final review" in hl:
                kind = "promotion"
            elif "readiness improved" in hl and "→" in hl:
                kind = "readiness_acceleration"
            elif "execution quality band" in hl:
                kind = "execution_quality"
            elif "weakening" in hl or "softening" in hl:
                kind = "momentum_shift"
            else:
                continue
            enqueue_in_app_alert(uid, kind or "desk_shift", str(ev.get("headline") or "")[:240], sym)
            current += 1
            r.incr(cap_key)
            r.expire(cap_key, 7200)
    except Exception as exc:
        logger.debug("enqueue alerts skipped: %s", exc)


def get_evolution_timeline(user_id: int, symbol: str, limit: int = 40) -> List[Dict[str, Any]]:
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return []
        sym = _norm_symbol(symbol)
        cap = max(5, min(limit, 80))
        raw = r.lrange(f"{EVO_LIST_PREFIX}{int(user_id)}:{sym}", 0, cap - 1)
        out: List[Dict[str, Any]] = []
        for row in raw or []:
            try:
                out.append(json.loads(row.decode() if isinstance(row, bytes) else row))
            except Exception:
                continue
        return out
    except Exception:
        return []


def save_visit_digest(user_id: int, wl_items: List[Dict[str, Any]]) -> None:
    """Snapshot fingerprints for revisit comparison (called from visit-mark)."""
    try:
        from dashboard.backend.cache import set as cache_set

        digest: Dict[str, Any] = {}
        for item in wl_items:
            sym = str(item.get("symbol") or "")
            if not sym:
                continue
            digest[sym] = {
                "h": _intel_hash_lazy(item),
                "readiness_pct": item.get("readiness_pct"),
                "setup_status": item.get("setup_status"),
            }
        cache_set(f"{VISIT_DIGEST_KEY}{int(user_id)}", digest, ttl_seconds=14 * 86400)
        cache_set(f"{LAST_VISIT_TS_KEY}{int(user_id)}", time.time(), ttl_seconds=14 * 86400)
    except Exception as exc:
        logger.debug("visit digest save skipped: %s", exc)


def _load_visit_digest(user_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    try:
        from dashboard.backend.cache import get as cache_get

        d = cache_get(f"{VISIT_DIGEST_KEY}{int(user_id)}")
        ts_raw = cache_get(f"{LAST_VISIT_TS_KEY}{int(user_id)}")
        ts = float(ts_raw) if ts_raw is not None else None
        return (d if isinstance(d, dict) else None, ts)
    except Exception:
        return None, None


def build_revisit_psychology(user_id: int, wl_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Diff vs last visit-mark — meaningful change thresholds only."""
    prev, last_ts = _load_visit_digest(user_id)
    lines: List[str] = []
    if not prev:
        return {
            "has_prior_visit": False,
            "lines": [],
            "significant_changes": 0,
            "last_visit_ts": None,
            "trust_note": "Mark a visit from the desk after reviews to unlock revisit deltas.",
        }

    changed = 0
    improved = 0
    weakened = 0
    new_final_posture = 0

    for item in wl_items:
        sym = str(item.get("symbol") or "")
        if not sym:
            continue
        p = prev.get(sym) if isinstance(prev.get(sym), dict) else None
        if not p:
            continue
        cur_h = _intel_hash_lazy(item)
        if p.get("h") == cur_h:
            continue
        changed += 1
        try:
            old_rp = float(p.get("readiness_pct") or 0)
            new_rp = float(item.get("readiness_pct") or 0)
            if new_rp - old_rp >= 3:
                improved += 1
            elif old_rp - new_rp >= 3:
                weakened += 1
        except (TypeError, ValueError):
            pass
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        prom = dec.get("promotion") if isinstance(dec.get("promotion"), dict) else {}
        if prom.get("eligible_watchlist_to_final"):
            new_final_posture += 1

    if changed >= 4:
        lines.append(f"{changed} setups on your desk shifted materially since your last marked visit.")
    elif changed >= 1:
        lines.append(f"{changed} watchlist setup(s) changed significantly since your last visit.")

    if improved >= 2:
        lines.append(f"{improved} names show improved readiness vs your last snapshot.")
    elif improved == 1:
        lines.append("1 watchlist name improved readiness materially.")

    if weakened >= 1:
        lines.append(f"{weakened} name(s) softened or stressed vs prior check-in.")

    if new_final_posture >= 1:
        lines.append(f"{new_final_posture} name(s) moved toward final-review posture — verify gates before acting.")

    return {
        "has_prior_visit": True,
        "lines": lines[:8],
        "significant_changes": changed,
        "last_visit_ts": datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat() if last_ts else None,
        "trust_note": "Revisit lines compare desk fingerprints — not trade recommendations.",
    }


def _symbol_urgency_row(item: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Returns (label, detail, severity).
    Labels are descriptive, not manipulative.
    """
    sym = str(item.get("symbol") or "")
    dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    intel = item.get("intelligence") if isinstance(item.get("intelligence"), dict) else {}
    rp = _safe_float(item.get("readiness_pct"), 0.0)
    rd = _safe_float(item.get("readiness_delta_pct"), 0.0)
    dp = _safe_float(dec.get("deterioration_probability_pct"), 0.0)
    dv = dec.get("deterioration_validation") if isinstance(dec.get("deterioration_validation"), dict) else {}
    rev = dec.get("readiness_evolution") if isinstance(dec.get("readiness_evolution"), dict) else {}
    prom = dec.get("promotion") if isinstance(dec.get("promotion"), dict) else {}
    regime = rev.get("evolution_regime")

    if dv.get("validated_overall") in ("likely_invalidation", "capital_protection") or dp >= 62:
        return ("rapidly_weakening", f"{sym}: deterioration risk elevated — review plan validity.", "high")
    if intel.get("setup_deteriorating") and dp >= 48:
        return ("urgent_review", f"{sym}: weakening structure vs desk rules — prioritize review.", "medium")
    if prom.get("eligible_watchlist_to_final") and rp >= 68:
        return ("opportunity_forming", f"{sym}: advancing toward final-review posture — liquidity still required.", "low")
    if regime == "explosive expansion" or (rd >= 4 and rp >= 65):
        return ("heating_up", f"{sym}: readiness trajectory strengthening — confirm triggers.", "medium")
    if regime == "accelerating" and rp >= 58:
        return ("institutional_activity_increasing", f"{sym}: quality stack improving — watch execution bandwidth.", "low")
    if rd <= -3:
        return ("cooling", f"{sym}: readiness slipped vs prior snapshot — patience.", "low")
    return ("monitor", f"{sym}: no urgent desk transition — maintain protocol.", "low")


def attach_desk_os_fields(enriched: List[Dict[str, Any]]) -> None:
    """Personal desk dependency fields — mutates items in place."""
    for idx, item in enumerate(enriched):
        sym = str(item.get("symbol") or "")
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        intel = item.get("intelligence") if isinstance(item.get("intelligence"), dict) else {}
        eq = dec.get("execution_quality") if isinstance(dec.get("execution_quality"), dict) else {}
        rev = dec.get("readiness_evolution") if isinstance(dec.get("readiness_evolution"), dict) else {}
        rp = _safe_float(item.get("readiness_pct"), 0.0)
        dp = _safe_float(dec.get("deterioration_probability_pct"), 0.0)
        label, detail, sev = _symbol_urgency_row(item)

        det_trend = "controlled"
        if dp >= 55:
            det_trend = "elevated"
        elif dp >= 38:
            det_trend = "rising"
        mom = 0.0
        ps = intel.get("pillar_scores") if isinstance(intel.get("pillar_scores"), dict) else {}
        try:
            mom = float(ps.get("momentum") or 0)
        except (TypeError, ValueError):
            pass

        rec = item.get("recommendation") if isinstance(item.get("recommendation"), dict) else {}
        window = "wide"
        if rp >= 72 and item.get("setup_status") in ("READY", "NEAR_ENTRY"):
            window = "tightening"
        elif rp < 42:
            window = "extended"

        item["desk_os"] = {
            "evolving_rank": idx + 1,
            "urgency_state": label,
            "urgency_detail": detail,
            "urgency_severity": sev,
            "capital_priority_score": round(min(100, rp * 0.55 + max(0, 100 - dp) * 0.45), 1),
            "deterioration_risk_trend": det_trend,
            "confidence_evolution": rev.get("evolution_regime"),
            "execution_timing_quality": eq.get("band"),
            "momentum_pillar": round(mom, 1),
            "nearest_actionable_trigger": rec.get("nearest_trigger"),
            "expected_opportunity_window": window,
        }


def build_market_urgency_layer(
    wl_items: List[Dict[str, Any]],
    engine_snap: Dict[str, Any],
) -> Dict[str, Any]:
    """Cross-section urgency — ranked for command center (not fake scarcity)."""
    rows: List[Dict[str, Any]] = []
    for item in wl_items:
        label, detail, sev = _symbol_urgency_row(item)
        if label == "monitor":
            continue
        rows.append(
            {
                "symbol": item.get("symbol"),
                "label": label,
                "detail": detail,
                "severity": sev,
            }
        )
    rows.sort(
        key=lambda x: (0 if x["severity"] == "high" else 1 if x["severity"] == "medium" else 2, x["symbol"] or "")
    )
    near_ready = sum(
        1
        for x in wl_items
        if str(x.get("setup_status") or "") in ("READY", "NEAR_ENTRY") and _safe_float(x.get("readiness_pct"), 0) >= 62
    )
    weakening = sum(
        1 for x in wl_items if isinstance(x.get("intelligence"), dict) and x["intelligence"].get("setup_deteriorating")
    )
    return {
        "rows": rows[:12],
        "market_regime": engine_snap.get("market_regime"),
        "near_ready_count": near_ready,
        "weakening_count": weakening,
        "trust_note": "Urgency = desk rule thresholds on your symbols — not a timer or guarantee.",
    }


def build_session_intelligence_today(wl_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Today's desk interpretation from latest snapshot (session-aligned)."""
    if not wl_items:
        return {
            "strongest_readiness_gains": [],
            "largest_deterioration_stress": [],
            "highest_momentum_names": [],
            "trust_note": "Add symbols to your watchlist desk for session storytelling.",
        }

    gains = sorted(
        [x for x in wl_items if _safe_float(x.get("readiness_delta_pct"), 0) >= 0.5],
        key=lambda x: -_safe_float(x.get("readiness_delta_pct"), 0),
    )[:6]
    stress = sorted(
        wl_items,
        key=lambda x: -_safe_float((x.get("decision") or {}).get("deterioration_probability_pct"), 0),
    )[:6]
    def _mom(x: Dict[str, Any]) -> float:
        intel = x.get("intelligence")
        if not isinstance(intel, dict):
            return 0.0
        ps = intel.get("pillar_scores")
        if not isinstance(ps, dict):
            return 0.0
        return _safe_float(ps.get("momentum"), 0.0)

    mom_sorted = sorted(wl_items, key=lambda x: -_mom(x))[:6]

    def _sym_list(xs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for z in xs:
            s = str(z.get("symbol") or "")
            if not s:
                continue
            out.append({"symbol": s, "readiness_pct": z.get("readiness_pct"), "setup_status": z.get("setup_status")})
        return out

    return {
        "strongest_readiness_gains": _sym_list(gains),
        "largest_deterioration_stress": _sym_list(stress),
        "highest_momentum_names": _sym_list(mom_sorted),
        "trust_note": "Rankings are relative within your saved desk — compare vs your own risk rules.",
    }


def build_market_narrative_sections(
    engine_snap: Dict[str, Any],
    discovery_syms: List[str],
    wl_items: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Rule-based market story beats for brief + command center."""
    regime = str(engine_snap.get("market_regime") or "unknown")
    sections: List[Dict[str, str]] = []

    chop = "CHOP" in regime.upper() or "RISK" in regime.upper()
    sections.append(
        {
            "title": "Regime framing",
            "body": f"Aggregate regime reads as {regime}. {'Patience and confirmation matter more when chop/risk dominates.' if chop else 'Trend-friendly regimes still require per-name liquidity checks.'}",
        }
    )

    if discovery_syms:
        sections.append(
            {
                "title": "Discovery rotation (snapshot)",
                "body": f"Scanner emphasis includes {', '.join(discovery_syms[:6])} — validate HTF structure before sizing.",
            }
        )

    wl_weak = [x["symbol"] for x in wl_items if isinstance(x.get("intelligence"), dict) and x["intelligence"].get("setup_deteriorating")]
    if wl_weak:
        sections.append(
            {
                "title": "Your desk — softening contexts",
                "body": f"Watchlist names showing weakening signals: {', '.join(wl_weak[:5])}. Treat as risk review, not exit orders.",
            }
        )

    near = [x["symbol"] for x in wl_items if str(x.get("setup_status") or "") in ("READY", "NEAR_ENTRY") and _safe_float(x.get("readiness_pct"), 0) >= 65]
    if near:
        sections.append(
            {
                "title": "Your desk — approaching actionable windows",
                "body": f"Setups nearing readiness discipline: {', '.join(near[:6])}. Executable only after your liquidity + execution checks.",
            }
        )

    sections.append(
        {
            "title": "Liquidity & traps",
            "body": "Liquidity quality varies by symbol — avoid chasing spikes without OB/FVG context aligned to your plan.",
        }
    )
    return sections


def read_retention_engine_metrics() -> Dict[str, Any]:
    """Observability for GET /api/system/debug/platform."""
    out: Dict[str, Any] = {"date": _today()}
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            out["redis_available"] = False
            return out
        out["redis_available"] = True
        day = _today()
        out["timeline_events_today"] = int(r.get(f"{METRICS_TIMELINE_DAY}{day}") or 0)
        out["urgency_events_today"] = int(r.get(f"{METRICS_URGENCY_DAY}{day}") or 0)
        out["revisit_triggers_today"] = int(r.get(f"{METRICS_REVISIT_DAY}{day}") or 0)
        try:
            out["intel_interactions_today"] = int(r.get(f"{INTEL_INTERACTION_DAY}{day}") or 0)
        except Exception:
            out["intel_interactions_today"] = None
        try:
            out["active_evolution_symbols"] = int(r.scard(f"{METRICS_EVO_SYMBOLS_DAY}{day}"))
        except Exception:
            out["active_evolution_symbols"] = None

        pulses = int(r.get(f"product:v1:session_day:{day}:pulses") or 0)
        queue_samples = 0
        cur = 0
        for _ in range(40):
            cur, batch = r.scan(cur, match="product:v1:in_app_queue:*", count=30)
            queue_samples += len(batch)
            if cur == 0:
                break
        out["in_app_queue_keys_approx"] = queue_samples
        out["alert_engagement_rate_approx"] = round(queue_samples / max(pulses, 1), 4) if pulses else None

        intel_events = 0
        cur = 0
        for _ in range(50):
            cur, batch = r.scan(cur, match="product:v1:intel:*", count=40)
            intel_events += len(batch)
            if cur == 0:
                break
        out["avg_watchlist_interactions_proxy"] = round(intel_events / max(1, pulses), 4) if pulses else None

        premium_users = None
        total_users = None
        try:
            from dashboard.backend.db import get_connection

            conn = get_connection()
            try:
                premium_users = conn.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE UPPER(COALESCE(role,'FREE')) IN ('PREMIUM','ADMIN')"
                ).fetchone()["c"]
                total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            finally:
                conn.close()
        except Exception:
            pass
        if premium_users is not None and total_users not in (None, 0):
            out["premium_readiness_score"] = round(float(premium_users) / float(total_users), 4)
        else:
            out["premium_readiness_score"] = None

        out["notes"] = {
            "revisit_triggers": "increments when revisit psychology returns non-empty lines",
            "alert_engagement_rate": "queue key count / session pulses — proxy only",
        }
    except Exception as e:
        out["error"] = str(e)
    return out


def touch_intel_event_metric() -> None:
    """Increment coarse interaction counter for watchlist/session actions."""
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        day = _today()
        k = f"{INTEL_INTERACTION_DAY}{day}"
        r.incr(k)
        r.expire(k, 5 * 86400)
    except Exception:
        pass
