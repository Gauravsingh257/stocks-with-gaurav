"""
Phase 6 — Trader Command Center: action-first aggregates from Redis snapshots + watchlist OS.

Snapshot-first; trust-first copy; no fabricated win rates.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import logging

logger = logging.getLogger("dashboard.command_center")

FREE_FEED_LIMIT = int(os.getenv("COMMAND_CENTER_FEED_LIMIT_FREE", "6"))
PREMIUM_FEED_LIMIT = int(os.getenv("COMMAND_CENTER_FEED_LIMIT_PREMIUM", "24"))


def _tier_limit(role: Optional[str]) -> int:
    r = (role or "FREE").upper()
    return PREMIUM_FEED_LIMIT if r in ("PREMIUM", "ADMIN") else FREE_FEED_LIMIT


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _action_label(item: Dict[str, Any]) -> str:
    dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    ua = dec.get("user_action") if isinstance(dec.get("user_action"), dict) else {}
    return str(ua.get("primary_action") or "Monitor")


def _load_watchlist_os(uid: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from dashboard.backend.cache import get as cache_get

    raw = cache_get(f"snapshot:watchlist_operating:{int(uid)}")
    if not isinstance(raw, dict):
        return [], {}
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    dp = raw.get("decision_portfolios") if isinstance(raw.get("decision_portfolios"), dict) else {}
    return [x for x in items if isinstance(x, dict)], dp


def build_daily_market_brief(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Deterministic digest from engine + discovery snapshot — no LLM. Optional user narrative from desk."""
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot

        es = get_engine_snapshot()
    except Exception:
        es = {}
    try:
        from dashboard.backend.redis_endpoint_cache import serve_cached_endpoint

        disc = serve_cached_endpoint("discovery") or {}
    except Exception:
        disc = {}

    regime = es.get("market_regime") or "unknown"
    items = disc.get("items") or disc.get("final_trades") or []
    top_syms: List[str] = []
    for it in items[:12]:
        if not isinstance(it, dict):
            continue
        s = str(it.get("symbol") or "").replace("NSE:", "").strip().upper()
        if s:
            top_syms.append(s)

    wl_items: List[Dict[str, Any]] = []
    if user_id is not None:
        wl_items, _ = _load_watchlist_os(int(user_id))

    try:
        from dashboard.backend.services.retention_engine_service import build_market_narrative_sections

        narrative_sections = build_market_narrative_sections(es, top_syms, wl_items)
    except Exception:
        narrative_sections = []

    chop = "CHOP" in str(regime).upper() or "RISK" in str(regime).upper()
    sections = [
        {
            "title": "Regime",
            "body": f"Desk reads aggregate regime as {regime} — use position sizing and patience accordingly.",
        },
        {
            "title": "Liquidity & volatility",
            "body": "Liquidity and volatility states vary by name — verify each symbol’s pillar context on the watchlist desk before acting.",
        },
        {
            "title": "Discovery focus (snapshot)",
            "body": "Names surfacing in discovery today include: "
            + (", ".join(top_syms[:8]) if top_syms else "— (no discovery snapshot)")
            + ".",
        },
        {
            "title": "Traps / chop",
            "body": "When regime is choppy or risk-off, false breaks become more frequent — wait for confirmation near triggers."
            if chop
            else "Maintain structure-first discipline; avoid chasing momentum without liquidity confirmation.",
        },
    ]

    return {
        "ok": True,
        "engine_version": "daily_brief_v1_5",
        "regime": regime,
        "top_discovery_symbols": top_syms[:15],
        "sections": sections,
        "narrative_sections": narrative_sections,
        "trust_note": "Brief is rules + snapshot-driven synthesis — not a prediction or recommendation.",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def build_command_center_payload(
    *,
    user_id: Optional[int],
    role: Optional[str],
) -> Dict[str, Any]:
    try:
        from dashboard.backend.state_bridge import get_engine_snapshot

        es = get_engine_snapshot()
    except Exception:
        es = {}
    try:
        from dashboard.backend.redis_endpoint_cache import serve_cached_endpoint

        discovery = serve_cached_endpoint("discovery") or {}
        final_snap = serve_cached_endpoint("final") or {}
    except Exception:
        discovery, final_snap = {}, {}

    limit = _tier_limit(role)
    regime = es.get("market_regime")
    signals_today = es.get("signals_today") or es.get("signals_count")

    disc_items = discovery.get("items") or discovery.get("final_trades") or []
    disc_cards: List[Dict[str, Any]] = []
    for it in disc_items[:40]:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").replace("NSE:", "").strip().upper()
        if not sym:
            continue
        score = _safe_float(it.get("confidence_score") or it.get("score"), 0)
        disc_cards.append({"symbol": sym, "confidence_score": score, "layer": "discovery"})

    disc_cards.sort(key=lambda x: -x["confidence_score"])
    best_opportunities = [
        {"symbol": x["symbol"], "note": "Discovery snapshot — verify structure before sizing.", "confidence_score": x["confidence_score"]}
        for x in disc_cards[: min(8, limit)]
    ]

    final_items = final_snap.get("items") or final_snap.get("final_trades") or []
    high_conviction: List[Dict[str, Any]] = []
    for it in final_items[:30]:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").replace("NSE:", "").strip().upper()
        if not sym:
            continue
        cf = _safe_float(it.get("confidence_score") or it.get("score"), 0)
        if cf >= 68:
            high_conviction.append(
                {
                    "symbol": sym,
                    "confidence_score": cf,
                    "note": "Final-review bucket — risk-map still required.",
                }
            )
    high_conviction.sort(key=lambda x: -x["confidence_score"])
    high_conviction = high_conviction[: min(10, limit)]

    wl_items: List[Dict[str, Any]] = []
    portfolios: Dict[str, Any] = {}
    feed_tail: List[Dict[str, Any]] = []
    if user_id is not None:
        wl_items, portfolios = _load_watchlist_os(int(user_id))
        try:
            from dashboard.backend.services.watchlist_intel_service import load_feed_only

            feed_tail = load_feed_only(int(user_id), 14)
        except Exception:
            feed_tail = []

    improvements: List[Dict[str, Any]] = []
    deteriorations: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []
    priority_lines: List[Dict[str, Any]] = []

    def add_line(headline: str, severity: str, symbol: Optional[str] = None, kind: str = "info") -> None:
        priority_lines.append({"headline": headline, "severity": severity, "symbol": symbol, "kind": kind})

    weakened_recent = 0
    for item in wl_items:
        sym = str(item.get("symbol") or "")
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        dv = dec.get("deterioration_validation") if isinstance(dec.get("deterioration_validation"), dict) else {}
        ru = dec.get("user_action") if isinstance(dec.get("user_action"), dict) else {}
        intel = item.get("intelligence") if isinstance(item.get("intelligence"), dict) else {}
        rd = _safe_float(item.get("readiness_delta_pct"), 0.0)
        dp = _safe_float(dec.get("deterioration_probability_pct"), 0.0)
        tier = str(dec.get("signal_tier") or "")
        prom = dec.get("promotion") if isinstance(dec.get("promotion"), dict) else {}
        if intel.get("setup_deteriorating"):
            weakened_recent += 1

        if rd >= 2.5:
            improvements.append(
                {
                    "symbol": sym,
                    "readiness_delta_pct": rd,
                    "action": _action_label(item),
                }
            )
        if dp >= 52 or dv.get("validated_overall") in ("likely_invalidation", "capital_protection", "avoid_entry"):
            deteriorations.append(
                {
                    "symbol": sym,
                    "deterioration_probability_pct": dp,
                    "validated_overall": dv.get("validated_overall"),
                    "action": _action_label(item),
                }
            )
        act = str(ru.get("primary_action") or "")
        if act in ("Capital protection mode", "Avoid now", "Momentum chasing risk"):
            alerts.append({"symbol": sym, "action": act, "severity": "high"})
        if prom.get("eligible_watchlist_to_final"):
            add_line(f"{sym}: promoted toward Final review posture — gates still apply", "medium", sym, "promotion")
        if tier == "institutional grade" and _safe_float(item.get("readiness_pct"), 0) >= 72:
            add_line(f"{sym}: nearing institutional-grade desk posture (modelled tier)", "low", sym, "tier")

    improvements.sort(key=lambda x: -abs(x.get("readiness_delta_pct") or 0))
    deteriorations.sort(key=lambda x: -_safe_float(x.get("deterioration_probability_pct"), 0))

    for it in deteriorations[:5]:
        add_line(
            f"{it['symbol']}: deterioration risk elevated — {it.get('validated_overall') or 'review structure'}",
            "high",
            it["symbol"],
            "deterioration",
        )
    for it in improvements[:5]:
        add_line(
            f"{it['symbol']}: readiness improving vs prior snapshot (~{it.get('readiness_delta_pct'):+.1f} pts)",
            "low",
            it["symbol"],
            "improvement",
        )

    if weakened_recent >= 3:
        add_line(f"{weakened_recent} setups showing weakening signals on your desk — review priorities.", "medium", None, "aggregate")

    hc_syms = [x["symbol"] for x in high_conviction[:3]]
    if hc_syms:
        add_line(f"High-conviction final-review names to study: {', '.join(hc_syms)}", "low", None, "opportunity")

    rot_note = None
    if portfolios:
        rot_note = "Virtual desks rotate by competition score — weakest slots yield when deterioration dominates."

    premium_gating = {
        "tier": (role or "FREE").upper(),
        "feed_limit": limit,
        "full_watchlist_intel": (role or "FREE").upper() in ("PREMIUM", "ADMIN"),
        "note": "FREE tier sees a shorter priority feed; PREMIUM unlocks longer feeds and full desk transitions.",
    }

    # Dedupe priority lines by headline (keep order)
    seen_h = set()
    deduped: List[Dict[str, Any]] = []
    for ln in priority_lines:
        if ln["headline"] in seen_h:
            continue
        seen_h.add(ln["headline"])
        deduped.append(ln)
    priority_lines = deduped[:limit]

    urgency_layer: Dict[str, Any] = {}
    revisit_psychology: Dict[str, Any] = {}
    session_intel: Dict[str, Any] = {}
    if user_id is not None:
        try:
            from dashboard.backend.services.retention_engine_service import (
                build_market_urgency_layer,
                build_revisit_psychology,
                build_session_intelligence_today,
            )

            urgency_layer = build_market_urgency_layer(wl_items, es if isinstance(es, dict) else {})
            revisit_psychology = build_revisit_psychology(int(user_id), wl_items)
            session_intel = build_session_intelligence_today(wl_items)
        except Exception:
            pass

    out: Dict[str, Any] = {
        "ok": True,
        "engine_version": "command_center_v1_5",
        "market_regime": regime,
        "signals_today": signals_today,
        "best_opportunities_now": best_opportunities[:8],
        "biggest_improvements": improvements[:8],
        "biggest_deteriorations": deteriorations[:8],
        "active_high_conviction": high_conviction[:8],
        "portfolio_rotation": {"desks": portfolios, "note": rot_note},
        "alerts_requiring_attention": alerts[:12],
        "what_matters_now": priority_lines,
        "watchlist_feed_preview": feed_tail[:12] if user_id else [],
        "personal_desk_symbols": len(wl_items),
        "premium": premium_gating,
        "trust_banner": "Action labels are desk rules + snapshots — not trade instructions.",
        "urgency_layer": urgency_layer,
        "revisit_psychology": revisit_psychology,
        "session_intelligence_today": session_intel,
    }
    return out
