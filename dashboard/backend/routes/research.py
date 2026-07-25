"""
dashboard/backend/routes/research.py
Research Center APIs for swing ideas, long-term ideas, and running trades.
"""

import logging
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from dashboard.backend.db import get_connection, get_ranking_runs, get_stock_recommendations, list_running_trades
from services.universe_manager import load_nse_universe
from services.price_resolver import resolve_cmp
from dashboard.backend.redis_endpoint_cache import (
    commit_watchlist_final_independent,
    finalize_endpoint,
    load_last_known_good,
    serve_cached_endpoint,
    serve_cached_research_list,
    valid_coverage_payload,
    valid_discovery_redis_write,
    valid_layer_report_payload,
    valid_performance_payload,
    valid_research_list_payload,
    valid_running_trades_payload,
    valid_scan_status_payload,
    valid_validation_payload,
)
from dashboard.backend.snapshot_consistency import validate_research_list_snapshot

try:
    from dashboard.backend.routes.auth import get_optional_user
except ImportError:
    def get_optional_user(authorization: str | None = None) -> dict | None:
        return None

router = APIRouter(tags=["research"])
log = logging.getLogger("dashboard.research")


# ── Telegram alert helper ─────────────────────────────────────────────────────

# MEDIUM-tier Telegram batching (in-process; multi-instance may duplicate occasionally)
_MEDIUM_TG_BUFFER: list[str] = []
_MEDIUM_BATCH_START: float = 0.0
_MEDIUM_TG_LOCK = threading.Lock()
_MEDIUM_TG_INTERVAL_SEC = float(os.getenv("TELEGRAM_MEDIUM_BATCH_SEC", "1800"))


def _telegram_flush_medium_buffer(force: bool = False) -> None:
    """Send buffered MEDIUM_SETUP lines once per TELEGRAM_MEDIUM_BATCH_SEC window."""
    global _MEDIUM_BATCH_START
    now = time.time()
    with _MEDIUM_TG_LOCK:
        if not _MEDIUM_TG_BUFFER:
            return
        if not force:
            start = _MEDIUM_BATCH_START or now
            if now - start < _MEDIUM_TG_INTERVAL_SEC:
                return
        body = "\n".join(_MEDIUM_TG_BUFFER[:40])
        _MEDIUM_TG_BUFFER.clear()
        _MEDIUM_BATCH_START = 0.0
    _telegram_notify(f"<b>📎 Medium setups (batch)</b>\n{body}\n<i>Research Center</i>")


def _telegram_discovery_tiers(payload: dict) -> None:
    """Send STRONG setups immediately; queue MEDIUM for periodic batch."""
    lines_strong: list[str] = []
    lines_med: list[str] = []
    for bucket in ("final_trades", "watchlist", "discovery"):
        for c in payload.get(bucket) or []:
            if not isinstance(c, dict):
                continue
            tier = str(c.get("signal_tier") or "").upper()
            sym = str(c.get("symbol", "?")).replace("NSE:", "")
            conf = float(c.get("confidence_score") or 0)
            line = f"  {sym} — {tier} — conf {conf:.0f}%"
            if tier == "STRONG_SETUP":
                lines_strong.append(line)
            elif tier == "MEDIUM_SETUP":
                lines_med.append(line)
    if lines_strong:
        _telegram_notify("<b>🔥 Strong setups</b>\n" + "\n".join(lines_strong[:20]))
    if lines_med:
        global _MEDIUM_BATCH_START
        with _MEDIUM_TG_LOCK:
            _MEDIUM_TG_BUFFER.extend(lines_med)
            if _MEDIUM_BATCH_START == 0.0:
                _MEDIUM_BATCH_START = time.time()
        _telegram_flush_medium_buffer(force=False)


def _telegram_notify(message: str) -> bool:
    """Send a Telegram message using the configured bot. Non-blocking, best-effort."""
    try:
        import requests as _req
        from config.settings import get_settings
        settings = get_settings()
        token = settings.telegram_bot_token
        chat_id = settings.telegram_chat_id
        if not token or not chat_id:
            log.debug("Telegram not configured — skipping notification")
            return False
        resp = _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.ok
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)
        return False

# ── Sector cache (lightweight, populated on demand from yfinance) ─────────
_sector_cache: dict[str, str | None] = {}
_sector_cache_lock = threading.Lock()


def _resolve_sector(symbol: str) -> str | None:
    """Return sector string for an NSE symbol, cached in memory."""
    clean = symbol.replace("NSE:", "")
    with _sector_cache_lock:
        if clean in _sector_cache:
            return _sector_cache[clean]
    try:
        import yfinance as yf  # noqa: PLC0415
        info = yf.Ticker(f"{clean}.NS").info or {}
        sector = info.get("sector") or info.get("industry")
    except Exception:
        sector = None
    with _sector_cache_lock:
        _sector_cache[clean] = sector
    return sector


def _get_sector(row: dict) -> str | None:
    """Return sector from DB row, or resolve via yfinance if missing."""
    s = row.get("sector")
    if s:
        return s
    symbol = row.get("symbol", "")
    return _resolve_sector(symbol)


def _confidence_tier(confidence: float) -> str:
    """Map confidence_score (0.0–1.0) to quality tier label."""
    if confidence >= 0.85:
        return "Elite"
    if confidence >= 0.75:
        return "Strong"
    if confidence >= 0.65:
        return "Good"
    if confidence >= 0.55:
        return "Watchlist"
    return "Below"


# Track in-flight auto-scans to avoid duplicate triggers
_auto_scan_lock = threading.Lock()
_auto_scan_running: set[str] = set()

# Observable scan diagnostics — surfaced via GET /api/research/scan-status
# Redis-backed so state survives restarts and is shared across instances
from dashboard.backend import cache as _cache

def _scan_history_get(horizon: str) -> dict:
    return _cache.get(f"research:scan_history:{horizon}") or {}

def _scan_history_set(horizon: str, value: dict) -> None:
    _cache.set(f"research:scan_history:{horizon}", value, ttl_seconds=86400)

def _scan_history_all() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for h in ("SWING", "LONGTERM", "swing", "longterm"):
        v = _cache.get(f"research:scan_history:{h}")
        if v:
            result[h] = v
    return result

def _decision_cache_get(top_k: int, min_turnover: float, src: str) -> tuple[float, dict] | None:
    key = f"research:decision:{top_k}:{min_turnover}:{src}"
    cached = _cache.get(key)
    if cached and isinstance(cached, dict):
        return (cached.get("ts", 0.0), cached.get("payload", {}))
    return None

def _decision_cache_set(top_k: int, min_turnover: float, src: str, payload: dict) -> None:
    key = f"research:decision:{top_k}:{min_turnover}:{src}"
    _cache.set(key, {"ts": time.time(), "payload": payload}, ttl_seconds=DECISION_CACHE_SECONDS)

STALE_THRESHOLD_HOURS = 12  # auto-trigger scan if last run older than this
# Dynamic inventory ceiling — kept in sync with the agents
# (agents/swing_alpha_agent.py, agents/longterm_investment_agent.py).
# This is a sanity cap, not a hard slot count — should rarely bind.
RESEARCH_MAX_INVENTORY = int(os.getenv("RESEARCH_MAX_INVENTORY", "50"))
# Back-compat alias: legacy responses still expose the field as "max".
MAX_LONGTERM_SLOTS = RESEARCH_MAX_INVENTORY
FREE_TIER_LIMIT = 3  # free users see this many ideas per horizon
DECISION_CACHE_SECONDS = int(os.getenv("RESEARCH_DECISION_CACHE_SECONDS", "600"))

# GET /swing /longterm: Redis snapshot + LKG only (no DB assembly / resolve_cmp on request).
RESEARCH_SNAPSHOT_ONLY = os.getenv("RESEARCH_SNAPSHOT_ONLY", "true").lower() in ("1", "true", "yes")

RESEARCH_LEADS_DDL = """
CREATE TABLE IF NOT EXISTS research_email_leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);
"""


def init_research_leads_table() -> None:
    try:
        conn = get_connection()
        conn.executescript(RESEARCH_LEADS_DDL)
        conn.commit()
    except Exception as exc:
        log.warning("research_email_leads init: %s", exc)


def _utc_iso(s: str | None) -> str | None:
    """
    Normalize a SQLite datetime('now') string to an ISO-8601 UTC string with
    explicit "Z" suffix so JavaScript's Date constructor parses it correctly.
    SQLite's datetime('now') returns naive UTC like '2026-04-23 03:00:00';
    without a timezone marker, the browser would mis-interpret it as local time.
    """
    if not s:
        return None
    value = str(s).strip()
    if not value:
        return None
    if value.endswith("Z") or "+" in value[10:]:
        return value
    return value.replace(" ", "T") + "Z"


def _serve_reachability(cmp, entry) -> tuple[float | None, str]:
    """Reachability bands for the *served* decision feed.

    Mirrors the frozen bands in services.validation_engine._entry_reachability
    so the frontend's "hide ran-past-entry" filter works on the cached
    signals_log payload too. The persisted row schema does not carry these
    fields, so without this the field is absent and every unreachable idea
    (price already >15% past the planned entry) leaks into the default view.

      gap = (cmp - entry) / entry * 100
      gap < -5%        -> pre_breakout (entry above CMP; awaiting breakout)
      |gap| <= 5%      -> actionable   (trading at/near the entry)
      5% < gap <= 15%  -> waiting      (above entry; may pull back)
      gap > 15%        -> unreachable  (price ran away; hidden by default)
    """
    try:
        c = float(cmp)
        e = float(entry)
    except (TypeError, ValueError):
        return None, "unknown"
    if not c or e <= 0:
        return None, "unknown"
    gap = (c - e) / e * 100.0
    if gap < -5.0:
        band = "pre_breakout"
    elif gap <= 5.0:
        band = "actionable"
    elif gap <= 15.0:
        band = "waiting"
    else:
        band = "unreachable"
    return round(gap, 2), band


def _signals_log_row_to_decision_card(row: dict) -> dict:
    """Convert a persisted validation row into the public decision-feed shape."""
    confidence = float(row.get("confidence") or 0)
    final_selected = bool(row.get("final_selected"))
    layer1 = bool(row.get("layer1_pass"))
    layer2 = bool(row.get("layer2_pass"))
    layer3 = bool(row.get("layer3_pass"))
    section = "final" if final_selected else "watchlist" if layer1 and layer2 else "discovery"
    target = row.get("target")
    entry = row.get("entry")
    stop = row.get("stop_loss")
    risk_reward = None
    try:
        if entry is not None and stop is not None and target is not None:
            risk = abs(float(entry) - float(stop))
            reward = abs(float(target) - float(entry))
            risk_reward = round(reward / risk, 2) if risk > 0 else None
    except Exception:
        risk_reward = None
    entry_distance_pct, reachability = _serve_reachability(row.get("cmp"), entry)
    # PR3 — READY / WATCH / IN_MOTION / MISSED entry-timing state (reusable).
    try:
        from services.entry_state import classify_entry_state
        _es = classify_entry_state(row.get("cmp"), entry, stop,
                                   [target] if target is not None else [])
    except Exception:
        _es = {"state": None, "actionable": None}
    _exc = row.get("exceptionalism") or {}
    if isinstance(_exc, str):
        try:
            import json as _json_exc
            _exc = _json_exc.loads(_exc)
        except Exception:
            _exc = {}
    # EP3 — per-recommendation "why surfaced" decision trace (additive, best-effort).
    _trace = None
    try:
        from services.decision_trace import build_decision_trace
        _trace = build_decision_trace(
            symbol=row.get("symbol"),
            exceptionalism=_exc if isinstance(_exc, dict) else None,
            entry_state=_es.get("state"),
            confidence=confidence,
            setup=row.get("setup"),
        )
    except Exception:
        _trace = None
    return {
        "decision_trace": _trace,
        "id": row.get("id"),
        "symbol": row.get("symbol"),
        "setup": "SMC_VALIDATION",
        "entry_state": _es.get("state"),
        "entry_actionable": _es.get("actionable"),
        "exceptionalism": _exc.get("exceptionalism"),
        "exceptionalism_threshold": _exc.get("threshold"),
        "exceptionalism_qualifies": _exc.get("qualifies"),
        "exceptionalism_reason": _exc.get("reason"),
        "section": section,
        "entry_price": entry,
        "stop_loss": stop,
        "target_1": target,
        "target_2": target,
        "targets": [target] if target is not None else [],
        "risk_reward": risk_reward,
        "entry_distance_pct": entry_distance_pct,
        "reachability": reachability,
        "confidence_score": confidence,
        "scan_cmp": row.get("cmp"),
        "entry_type": "validation_scan",
        "expected_holding_period": "Swing",
        "layer1_pass": layer1,
        "layer2_pass": layer2,
        "layer3_pass": layer3,
        "final_selected": final_selected,
        "near_setup": section == "watchlist",
        "rejection_reason": row.get("rejection_reason") or [],
        "layer_details": row.get("layer_details") or {},
        "reasoning": "Latest persisted SMC validation scan.",
        "reasoning_summary": "Latest persisted SMC validation scan.",
        "technical_signals": {},
        "action_tag": "final_review" if section == "final" else section,
    }


def _latest_logged_decision_payload(top_k: int, min_turnover_cr: float, src: str) -> dict | None:
    """Return a fast public decision payload from the latest persisted scan, if available."""
    try:
        from dashboard.backend.db import get_latest_signals_scan_report

        report = get_latest_signals_scan_report(horizon="SWING", limit=max(top_k * 8, 80))
    except Exception as exc:
        log.warning("latest logged decision feed unavailable: %s", exc)
        return None
    if not report.get("available"):
        return None

    sample = report.get("sample") or []
    cards = [_signals_log_row_to_decision_card(row) for row in sample if row.get("symbol")]
    final = [card for card in cards if card.get("section") == "final"][:top_k]
    watchlist = [card for card in cards if card.get("section") == "watchlist"][:top_k]
    discovery = [card for card in cards if card.get("section") == "discovery"][:top_k]
    return {
        "data_source": src,
        "universe_size": (report.get("coverage") or {}).get("total_universe") or report.get("funnel", {}).get("total") or len(cards),
        "scanned": (report.get("coverage") or {}).get("scanned") or report.get("funnel", {}).get("total") or len(cards),
        "returned": len(final),
        "watchlist_returned": len(watchlist),
        "discovery_returned": len(discovery),
        "fallback_returned": len(discovery),
        "generated_at": report.get("created_at") or datetime.now().isoformat(),
        "scan_id": report.get("scan_id") or "latest-signals-log",
        "coverage": report.get("coverage") or {},
        "funnel": report.get("funnel") or {},
        "items": final,
        "final_trades": final,
        "watchlist": watchlist,
        "discovery": discovery,
        "fallback_items": discovery,
        "cache_hit": True,
        "cache_source": "signals_log",
        "cache_ttl_sec": DECISION_CACHE_SECONDS,
        "min_turnover_cr": min_turnover_cr,
    }
def _extract_fundamentals(row: dict) -> dict:
    """Pull structured fundamental metrics from fundamental_factors or parse from
    fundamental_signals text as a fallback for older DB records.

    Returns a flat dict of nullable floats that the frontend can render as columns
    (PE, ROE, ROCE, revenue growth, D/E, market cap, promoter %).
    """
    import re as _re

    ff = row.get("fundamental_factors") or {}
    if isinstance(ff, str):
        try:
            import json as _json
            ff = _json.loads(ff)
        except Exception:
            ff = {}

    def _f(key: str) -> float | None:
        v = ff.get(key)
        if v is None:
            return None
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    result = {
        "pe_ratio": _f("raw_pe"),
        "roe_pct": _f("raw_roe_pct"),
        "roce_pct": _f("raw_roce_pct"),
        "revenue_growth_pct": _f("raw_revenue_growth_pct"),
        "debt_equity": _f("raw_debt_equity"),
        "market_cap_cr": _f("raw_market_cap_cr"),
        "promoter_pct": _f("raw_promoter_pct"),
    }

    # Fallback: parse values from fundamental_signals text (for older DB records)
    if all(v is None for v in result.values()):
        fs = row.get("fundamental_signals") or {}
        if isinstance(fs, str):
            try:
                import json as _json2
                fs = _json2.loads(fs)
            except Exception:
                fs = {}
        all_text = " ".join(str(v) for v in fs.values())

        if result["pe_ratio"] is None:
            m = _re.search(r"PE[:\s]+([0-9.]+)x", all_text)
            if m:
                result["pe_ratio"] = round(float(m.group(1)), 1)

        if result["debt_equity"] is None:
            m = _re.search(r"D/E[:\s]+([0-9.]+)x", all_text)
            if m:
                result["debt_equity"] = round(float(m.group(1)), 2)

        if result["promoter_pct"] is None:
            m = _re.search(r"(?:Insider|Promoter)[^:]*:\s*([0-9.]+)%", all_text)
            if m:
                result["promoter_pct"] = round(float(m.group(1)), 1)

        if result["revenue_growth_pct"] is None:
            m = _re.search(r"Revenue:\s*([+-]?[0-9.]+)%", all_text)
            if m:
                result["revenue_growth_pct"] = round(float(m.group(1)), 1)

        if result["market_cap_cr"] is None:
            m = _re.search(r"MCap[:\s]+([0-9,.]+)\s*Cr", all_text)
            if m:
                result["market_cap_cr"] = round(float(m.group(1).replace(",", "")), 1)

    return result


def _is_data_stale(horizon: str) -> bool:
    """Return True if the latest ranking run for *horizon* is older than STALE_THRESHOLD_HOURS."""
    runs = get_ranking_runs(horizon=horizon, limit=1)
    if not runs:
        return True
    last_run_time = runs[0].get("run_time")
    if not last_run_time:
        return True
    try:
        # run_time is stored as ISO string from SQLite datetime('now')
        last_dt = datetime.fromisoformat(last_run_time.replace("Z", "+00:00"))
        # Compare in UTC (SQLite datetime('now') is UTC)
        return datetime.utcnow() - last_dt.replace(tzinfo=None) > timedelta(hours=STALE_THRESHOLD_HOURS)
    except (ValueError, TypeError):
        return True


def _maybe_auto_scan(horizon: str) -> None:
    """If data for *horizon* is stale and no scan is already running, trigger one in the background."""
    agent_map = {"SWING": "SwingTradeAlphaAgent", "LONGTERM": "LongTermInvestmentAgent"}
    agent_name = agent_map.get(horizon)
    if not agent_name:
        return

    with _auto_scan_lock:
        if horizon in _auto_scan_running:
            return  # already in-flight
        if not _is_data_stale(horizon):
            return
        _auto_scan_running.add(horizon)

    started_at = datetime.utcnow().isoformat() + "Z"

    def _job() -> None:
        try:
            from agents.runner import run_agent_now
            out = run_agent_now(agent_name)
            if isinstance(out, dict) and out.get("error"):
                log.error("[auto-scan %s] error: %s", horizon, out["error"])
                _scan_history_set(horizon, {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "error": str(out["error"]),
                    "agent": agent_name,
                    "trigger": "auto",
                })
            else:
                summary = out.get("summary", str(out)) if isinstance(out, dict) else str(out)
                log.info("[auto-scan %s] finished: %s", horizon, summary)
                _scan_history_set(horizon, {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "summary": summary,
                    "agent": agent_name,
                    "trigger": "auto",
                })
        except Exception as exc:
            log.exception("[auto-scan %s] failed", horizon)
            _scan_history_set(horizon, {
                "status": "exception",
                "started_at": started_at,
                "finished_at": datetime.utcnow().isoformat() + "Z",
                "error": f"{exc.__class__.__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
                "agent": agent_name,
                "trigger": "auto",
            })
        finally:
            with _auto_scan_lock:
                _auto_scan_running.discard(horizon)

    threading.Thread(target=_job, daemon=True, name=f"auto_scan_{horizon}").start()
    log.info("[auto-scan %s] triggered — data is stale (>%dh)", horizon, STALE_THRESHOLD_HOURS)


def _sm_alert_active() -> bool:
    """True only when EQUITY_STATE_MACHINE=alert. Gates the swing-page
    merge so it is BYTE-IDENTICAL to today in any other mode."""
    return os.getenv("EQUITY_STATE_MACHINE", "").strip().lower() == "alert"


def _swing_payload(limit: int) -> dict:
    rows = get_stock_recommendations("SWING", limit=limit)

    # G2-6 Rung B: in =alert mode, surface the ISOLATED SWING_SM
    # planned-execution signals on the SAME swing page. They run through
    # the IDENTICAL transform below (no shape drift) and are tagged so
    # the UI/users see they are validation-phase, not auto-traded. The
    # legacy SWING slot machine is untouched (separate agent_type); when
    # not in alert mode this block is skipped ⟹ byte-identical.
    if _sm_alert_active():
        try:
            sm_rows = get_stock_recommendations("SWING_SM", limit=limit) or []
            for _sr in sm_rows:
                _sr["_sm_engine"] = True
            rows = list(rows) + sm_rows
        except Exception:
            pass
    runs = get_ranking_runs(horizon="SWING", limit=1)
    last_scan = runs[0]["run_time"] if runs else None

    # ── Resolve live CMP for all symbols in one batch (Phase 1: single source
    # of truth). Falls back to scan_cmp from DB when no live source is
    # available so the API never returns null. ──
    scan_cmp_map: dict[str, float] = {}
    for r in rows:
        sc = r.get("scan_cmp")
        if sc is not None:
            try:
                scan_cmp_map[r["symbol"]] = float(sc)
            except (TypeError, ValueError):
                pass
    cmp_resolved = resolve_cmp([r["symbol"] for r in rows], scan_cmp_map=scan_cmp_map)

    items: list[dict] = []
    for row in rows:
        targets = row.get("targets", [])
        target_1 = float(targets[0]) if len(targets) > 0 else None
        target_2 = float(targets[1]) if len(targets) > 1 else None
        entry = float(row["entry_price"])
        stop = float(row["stop_loss"]) if row.get("stop_loss") is not None else entry * 0.95
        risk = abs(entry - stop)
        reward = abs((target_2 or target_1 or entry) - entry)
        rr = reward / max(risk, 0.01)
        entry_type = row.get("entry_type", "MARKET")

        # ── Live CMP via resolver (overrides stale scan_cmp). cmp_source +
        # cmp_age_sec exposed so the frontend can render a trust badge. ──
        sym = row["symbol"]
        live = cmp_resolved.get(sym)
        if live:
            scan_cmp = float(live["price"])
            cmp_source = live["source"]
            cmp_age_sec = int(live["age_sec"])
        else:
            scan_cmp = float(row["scan_cmp"]) if row.get("scan_cmp") else None
            cmp_source = "scan_snapshot" if scan_cmp is not None else "unknown"
            cmp_age_sec = None

        # ── Entry gap: how far CMP is from entry (for LONG: positive = CMP above entry) ──
        entry_gap_pct = None
        if scan_cmp and entry > 0:
            entry_gap_pct = round((scan_cmp - entry) / entry * 100, 1)

        # ── Action tag logic ──
        # Determine LONG vs SHORT from target vs entry geometry
        is_long = (target_2 or target_1 or entry) >= entry
        favorable_move_R = 0.0
        if scan_cmp and risk > 0:
            favorable_move_R = ((scan_cmp - entry) / risk) if is_long else ((entry - scan_cmp) / risk)

        action_tag = "EXECUTE_NOW"  # default for MARKET entries
        if entry_type == "LIMIT":
            if scan_cmp and entry > 0:
                # entry_gap_pct: positive = CMP above entry, negative = CMP below entry.
                # For LONG LIMIT BUY: "EXECUTE_NOW" only when price has actually visited
                # the limit zone (CMP at-or-below entry, with 0.25% tick slack).
                gap = entry_gap_pct or 0.0
                total_move = reward
                progress = ((scan_cmp - entry) / total_move) if (total_move > 0 and is_long) else 0.0
                if is_long:
                    if progress > 0.30:
                        action_tag = "MISSED"
                    elif gap <= 0.25:  # CMP at/below entry (or within 0.25% tick noise)
                        action_tag = "EXECUTE_NOW"
                    else:
                        action_tag = "WAIT_FOR_RETEST"
                else:
                    # SHORT LIMIT SELL: invert directional check
                    if abs(progress) > 0.30 and scan_cmp < entry:
                        action_tag = "MISSED"
                    elif gap >= -0.25:
                        action_tag = "EXECUTE_NOW"
                    else:
                        action_tag = "WAIT_FOR_RETEST"
            else:
                action_tag = "WAIT_FOR_RETEST"
        # Phase 3.4: IN_MOTION demotion if price already >0.5R in our favor
        # (applies regardless of MARKET/LIMIT, but never overrides MISSED)
        if action_tag != "MISSED" and favorable_move_R > 0.5:
            action_tag = "IN_MOTION"

        conf = float(row.get("confidence_score", 0))
        items.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "setup": row.get("setup") or "SWING",
                "sector": _get_sector(row),
                "target_source": row.get("target_source"),
                "entry_price": entry,
                "stop_loss": stop,
                "target_1": target_1,
                "target_2": target_2,
                "risk_reward": round(rr, 2),
                "confidence_score": conf,
                "quality_tier": _confidence_tier(conf),
                "expected_holding_period": row.get("expected_holding_period") or "1-8 weeks",
                "technical_signals": row.get("technical_signals", {}),
                "fundamental_signals": row.get("fundamental_signals", {}),
                "sentiment_signals": row.get("sentiment_signals", {}),
                "technical_factors": row.get("technical_factors", {}),
                "fundamental_factors": row.get("fundamental_factors", {}),
                "sentiment_factors": row.get("sentiment_factors", {}),
                "reasoning_summary": row.get("reasoning", ""),
                "signal_first_detected_at": _utc_iso(row.get("signal_first_detected_at") or row.get("created_at")),
                "signals_updated_at": _utc_iso(row.get("signals_updated_at") or row.get("signal_first_detected_at") or row.get("created_at")),
                "created_at": _utc_iso(row.get("created_at")),
                "data_authenticity": row.get("data_authenticity", "unknown"),
                "status": row.get("status", "ACTIVE"),
                "entry_type": entry_type,
                "scan_cmp": scan_cmp,
                "cmp_source": cmp_source,
                "cmp_age_sec": cmp_age_sec,
                "entry_gap_pct": entry_gap_pct,
                "action_tag": action_tag,
                "smc_evidence": row.get("smc_evidence"),
                **_extract_fundamentals(row),
                # Tag ONLY state-machine items; legacy items unchanged.
                **({"engine": "planned_state_machine",
                    "validation_phase": True,
                    "auto_traded": False} if row.get("_sm_engine") else {}),
            }
        )
    return {"items": items, "count": len(items), "last_scan_time": _utc_iso(last_scan)}


def _longterm_payload(limit: int) -> dict:
    rows = get_stock_recommendations("LONGTERM", limit=limit)
    runs = get_ranking_runs(horizon="LONGTERM", limit=1)
    last_scan = runs[0]["run_time"] if runs else None

    # ── Same single-source-of-truth resolver as swing payload. ──
    scan_cmp_map: dict[str, float] = {}
    for r in rows:
        sc = r.get("scan_cmp")
        if sc is not None:
            try:
                scan_cmp_map[r["symbol"]] = float(sc)
            except (TypeError, ValueError):
                pass
    cmp_resolved = resolve_cmp([r["symbol"] for r in rows], scan_cmp_map=scan_cmp_map)

    items: list[dict] = []
    for row in rows:
        targets = row.get("targets", [])
        entry = float(row["entry_price"])
        stop = float(row["stop_loss"]) if row.get("stop_loss") is not None else entry * 0.90
        risk = abs(entry - stop)
        long_target = row.get("long_term_target") or (targets[0] if targets else entry)
        reward = abs(float(long_target) - entry) if long_target else 0
        rr = reward / max(risk, 0.01)
        entry_type = row.get("entry_type", "MARKET")

        sym = row["symbol"]
        live = cmp_resolved.get(sym)
        if live:
            scan_cmp = float(live["price"])
            cmp_source = live["source"]
            cmp_age_sec = int(live["age_sec"])
        else:
            scan_cmp = float(row["scan_cmp"]) if row.get("scan_cmp") else None
            cmp_source = "scan_snapshot" if scan_cmp is not None else "unknown"
            cmp_age_sec = None

        # ── Entry gap: how far CMP is from entry ──
        entry_gap_pct = None
        if scan_cmp and entry > 0:
            entry_gap_pct = round((scan_cmp - entry) / entry * 100, 1)

        # ── Action tag logic ──
        favorable_move_R = 0.0
        if scan_cmp and risk > 0:
            favorable_move_R = (scan_cmp - entry) / risk  # longterm is LONG-only

        action_tag = "EXECUTE_NOW"
        if entry_type == "LIMIT":
            if scan_cmp and entry > 0:
                # LONGTERM is LONG-only. EXECUTE_NOW only when CMP has actually pulled
                # back into the limit zone (at-or-below entry, with 0.25% tick slack).
                gap = entry_gap_pct or 0.0
                total_move = reward
                progress = ((scan_cmp - entry) / total_move) if total_move > 0 else 0.0
                if progress > 0.30:
                    action_tag = "MISSED"
                elif gap <= 0.25:
                    action_tag = "EXECUTE_NOW"
                else:
                    action_tag = "WAIT_FOR_RETEST"
            else:
                action_tag = "WAIT_FOR_RETEST"
        # Phase 3.4: IN_MOTION demotion if price already >0.5R favorable
        if action_tag != "MISSED" and favorable_move_R > 0.5:
            action_tag = "IN_MOTION"

        conf_lt = float(row.get("confidence_score", 0))
        items.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "setup": row.get("setup") or "LONGTERM",
                "sector": _get_sector(row),
                "target_source": row.get("target_source"),
                "long_term_thesis": row.get("reasoning", ""),
                "fair_value_estimate": row.get("fair_value_estimate"),
                "entry_price": entry,
                "entry_zone": row.get("entry_zone") or [],
                "stop_loss": stop,
                "long_term_target": float(long_target) if long_target else None,
                "risk_reward": round(rr, 2),
                "risk_factors": row.get("risk_factors") or [],
                "time_horizon": row.get("expected_holding_period") or "6-24 months",
                "confidence_score": conf_lt,
                "quality_tier": _confidence_tier(conf_lt),
                "technical_signals": row.get("technical_signals", {}),
                "fundamental_signals": row.get("fundamental_signals", {}),
                "sentiment_signals": row.get("sentiment_signals", {}),
                "fundamental_factors": row.get("fundamental_factors", {}),
                "technical_factors": row.get("technical_factors", {}),
                "sentiment_factors": row.get("sentiment_factors", {}),
                "reasoning_summary": row.get("reasoning", ""),
                "signal_first_detected_at": _utc_iso(row.get("signal_first_detected_at") or row.get("created_at")),
                "signals_updated_at": _utc_iso(row.get("signals_updated_at") or row.get("signal_first_detected_at") or row.get("created_at")),
                "created_at": _utc_iso(row.get("created_at")),
                "data_authenticity": row.get("data_authenticity", "unknown"),
                "status": row.get("status", "ACTIVE"),
                "entry_type": entry_type,
                "scan_cmp": scan_cmp,
                "cmp_source": cmp_source,
                "cmp_age_sec": cmp_age_sec,
                "entry_gap_pct": entry_gap_pct,
                "action_tag": action_tag,
                "smc_evidence": row.get("smc_evidence"),
                **_extract_fundamentals(row),
            }
        )
    # Inventory status — dynamic, capped only by the sanity ceiling. UI uses
    # this for messaging like "X ideas active". slots_full kept for backwards
    # compat with older clients; it only goes True at the sanity ceiling.
    occupied = len(rows)
    slot_status = {
        "occupied": occupied,
        "max": RESEARCH_MAX_INVENTORY,
        "slots_full": occupied >= RESEARCH_MAX_INVENTORY,
    }
    return {
        "items": items,
        "count": len(items),
        "last_scan_time": _utc_iso(last_scan),
        "slot_status": slot_status,
    }


def _f(v, default: float = 0.0) -> float:
    """Coerce to float; return default on None / unparseable. Used to harden running_trades rows."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _running_trades_payload(limit: int) -> dict:
    rows = list_running_trades(limit=limit, active_only=True)

    # ── Refresh CMP via the central resolver. Time-bounded: if it raises or
    # blocks on Kite API issues, we fall through to stored current_price so
    # the endpoint never 500s due to upstream broker problems. ──
    stored_cmp_map: dict[str, float] = {}
    for r in rows:
        try:
            v = r["current_price"]
            if v is not None:
                stored_cmp_map[r["symbol"]] = float(v)
        except (TypeError, ValueError, KeyError):
            pass
    try:
        cmp_resolved = resolve_cmp([r["symbol"] for r in rows], scan_cmp_map=stored_cmp_map)
    except Exception as exc:
        log.warning("running_trades: resolve_cmp failed (%s) — using stored prices", exc)
        cmp_resolved = {}

    items: list[dict] = []
    for row in rows:
        try:
            targets_raw = row.get("targets") or []
            targets = [_f(t) for t in targets_raw if t is not None]
            entry = _f(row.get("entry_price"))
            if entry <= 0:
                # Row corrupt — skip rather than 500 the whole endpoint.
                continue
            sym = row["symbol"]
            live = cmp_resolved.get(sym)
            if live:
                current = _f(live.get("price"), entry)
                cmp_source = live.get("source") or "live"
                cmp_age_sec = int(live.get("age_sec") or 0)
            else:
                current = _f(row.get("current_price"), entry)
                cmp_source = "db_snapshot"
                cmp_age_sec = None
            stop = _f(row.get("stop_loss"), entry * 0.95)
            max_target = max(targets) if targets else entry
            range_size = max(max_target - entry, 0.01)
            progress = max(0.0, min(1.0, (current - entry) / range_size))
            if current <= stop * 1.01:
                color = "red"
            elif progress >= 0.75:
                color = "green"
            else:
                color = "yellow"
            items.append(
                {
                    "id": row.get("id"),
                    "symbol": sym,
                    "entry_price": entry,
                    "current_price": current,
                    "cmp_source": cmp_source,
                    "cmp_age_sec": cmp_age_sec,
                    "stop_loss": stop,
                    "targets": targets,
                    "profit_loss": round(current - entry, 2),
                    "profit_loss_pct": round((current - entry) / entry * 100, 2),
                    "drawdown": _f(row.get("drawdown")),
                    "drawdown_pct": _f(row.get("drawdown_pct")),
                    "high_since_entry": row.get("high_since_entry"),
                    "low_since_entry": row.get("low_since_entry"),
                    "days_held": int(_f(row.get("days_held"))),
                    "distance_to_target": row.get("distance_to_target"),
                    "distance_to_stop_loss": row.get("distance_to_stop_loss"),
                    "status": row.get("status", "RUNNING"),
                    "progress": round(progress, 4),
                    "progress_color": color,
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        except Exception as exc:
            log.warning("running_trades: row skipped sym=%s err=%s", row.get("symbol"), exc)
            continue
    return {"items": items, "count": len(items)}


def _notify_new_picks(horizon: str) -> None:
    """Send Telegram alert summarizing current active picks after a scan completes."""
    try:
        rows = get_stock_recommendations(horizon, limit=10)
        if not rows:
            return
        lines = [f"<b>{'Swing' if horizon == 'SWING' else 'Long-Term'} Scan Complete</b>"]
        lines.append(f"<i>{len(rows)} active idea(s):</i>\n")
        for r in rows[:5]:
            sym = r.get("symbol", "?").replace("NSE:", "")
            entry = float(r.get("entry_price", 0))
            sl = float(r.get("stop_loss", 0)) if r.get("stop_loss") else 0
            conf = float(r.get("confidence_score", 0))
            action = r.get("entry_type", "MARKET")
            lines.append(f"  {sym} — Entry ₹{entry:.0f} | SL ₹{sl:.0f} | Conf {conf:.0f}% [{action}]")
        if len(rows) > 5:
            lines.append(f"  ... +{len(rows) - 5} more")
        lines.append("\nhttps://www.stockswithgaurav.com/research")
        _telegram_notify("\n".join(lines))
    except Exception as exc:
        log.warning("Telegram new-pick notification failed: %s", exc)


def _finalize_research_snapshot(canonical: str, merged: dict) -> dict:
    """Apply structural validation; fall back to LKG when live payload is corrupt."""
    ok, issues = validate_research_list_snapshot(merged)
    if ok:
        return merged
    fb = load_last_known_good(canonical)
    if fb is not None:
        out = dict(fb)
        out["snapshot_stale"] = True
        out["snapshot_source"] = "last_known_good"
        out["snapshot_stale_reason"] = "snapshot_structure_invalid:" + ",".join(issues)
        return out
    return merged


def _overlay_live_cmp(snapshot: dict) -> dict:
    """Phase 1: overlay live CMP onto a cached research snapshot during market hours.

    Preserves the snapshot architecture entirely — we only refresh the per-item
    price fields (scan_cmp / cmp_source / cmp_age_sec / entry_gap_pct) using the
    existing single source of truth, services.price_resolver.resolve_cmp.

    Outside research market hours (Mon–Fri 09:00–16:00 IST) the snapshot is
    returned untouched, so users see the latest available close. On the request
    path we skip yfinance (use_yf=False) to keep latency low — ws_cache + Kite
    only; symbols the resolver can't price keep their existing snapshot value.
    """
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        return snapshot
    try:
        from services.research_outcome_tracker import is_research_market_hours
        if not is_research_market_hours():
            return snapshot
    except Exception:
        return snapshot

    symbols = [it.get("symbol") for it in items if isinstance(it, dict) and it.get("symbol")]
    if not symbols:
        return snapshot

    scan_cmp_map: dict[str, float] = {}
    for it in items:
        sc = it.get("scan_cmp") if isinstance(it, dict) else None
        if sc is not None:
            try:
                scan_cmp_map[it["symbol"]] = float(sc)
            except (TypeError, ValueError):
                pass

    try:
        resolved = resolve_cmp(symbols, scan_cmp_map=scan_cmp_map, use_yf=False)
    except Exception as exc:
        log.warning("live CMP overlay failed (%s) — serving snapshot as-is", exc)
        return snapshot
    if not resolved:
        return snapshot

    out = dict(snapshot)
    new_items: list[dict] = []
    overlaid = 0
    for it in items:
        if not isinstance(it, dict):
            new_items.append(it)
            continue
        live = resolved.get(it.get("symbol"))
        # Only overlay a genuinely live price — never replace with a scan_snapshot.
        if live and live.get("source") != "scan_snapshot":
            ni = dict(it)
            price = float(live["price"])
            ni["scan_cmp"] = price
            ni["cmp_source"] = live.get("source")
            ni["cmp_age_sec"] = int(live.get("age_sec") or 0)
            try:
                entry = float(ni.get("entry_price") or 0)
                if entry > 0:
                    ni["entry_gap_pct"] = round((price - entry) / entry * 100, 1)
            except (TypeError, ValueError):
                pass
            new_items.append(ni)
            overlaid += 1
        else:
            new_items.append(it)
    out["items"] = new_items
    out["cmp_overlay"] = {"applied": True, "overlaid": overlaid, "total": len(new_items)}
    return out


def _gate_research_items(result: dict, is_premium: bool) -> dict:
    """Apply free-tier item cap without building a live DB payload."""
    if is_premium:
        return result
    items = list(result.get("items") or [])
    if len(items) <= FREE_TIER_LIMIT:
        return result
    out = dict(result)
    total_avail = len(items)
    out["items"] = items[:FREE_TIER_LIMIT]
    out["gated"] = True
    out["total_available"] = total_avail
    out["count"] = FREE_TIER_LIMIT
    return out


@router.get("/api/research/swing")
@router.get("/research/swing")
def get_swing_research(limit: int = Query(10, ge=1, le=100), user: dict | None = Depends(get_optional_user)):
    # Hardening (trust > features): a stale/corrupt swing cache or a throw in
    # finalize/gate must degrade to an empty 200, never a 500. Mirrors the
    # _safe_json_response guard added for running-trades in Phase F.
    try:
        if not RESEARCH_SNAPSHOT_ONLY:
            _maybe_auto_scan("SWING")
        is_premium = bool(user and user.get("role") in ("PREMIUM", "ADMIN"))

        if RESEARCH_SNAPSHOT_ONLY:
            snap = serve_cached_research_list("swing")
            if snap is not None:
                served = _overlay_live_cmp(_finalize_research_snapshot("swing", dict(snap)))
                return _safe_json_response(_gate_research_items(served, is_premium))
            merged = finalize_endpoint("swing", {}, valid_research_list_payload)
            return _safe_json_response(_gate_research_items(dict(merged), is_premium))

        full = _swing_payload(limit)
        snap = serve_cached_endpoint("swing")
        if snap is not None:
            result = dict(snap)
        else:
            result = dict(full)
        if not is_premium and len(result.get("items") or []) > FREE_TIER_LIMIT:
            result = dict(result)
            result["items"] = list(result.get("items", []))[:FREE_TIER_LIMIT]
            result["gated"] = True
            result["total_available"] = full.get("count", len(full.get("items", [])))
            result["count"] = FREE_TIER_LIMIT
        if snap is not None:
            return _safe_json_response(result)
        return _safe_json_response(finalize_endpoint("swing", full, valid_research_list_payload))
    except Exception:
        log.exception("get_swing_research failed; serving safe empty payload")
        return _safe_json_response({
            "items": [], "count": 0, "last_scan_time": None,
            "snapshot_stale": True, "error": "transient_serve_error",
        })


@router.get("/api/search-stock/suggestions")
@router.get("/search-stock/suggestions")
def get_stock_search_suggestions(
    q: str = Query("", min_length=0, max_length=32),
    limit: int = Query(10, ge=1, le=25),
):
    """Return NSE symbol suggestions for the global Research search box."""
    try:
        from services.stock_search_analysis import stock_suggestions
        return {"items": stock_suggestions(q, limit=limit)}
    except Exception as exc:
        log.exception("stock suggestions failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/search-stock")
@router.get("/search-stock")
def search_stock(symbol: str = Query(..., min_length=1, max_length=32)):
    """Analyze a single NSE symbol on demand using existing SMC + fundamentals logic."""
    try:
        from services.stock_search_analysis import analyze_stock
        return analyze_stock(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("stock analysis failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/research/longterm")
@router.get("/research/longterm")
def get_longterm_research(limit: int = Query(10, ge=1, le=100), user: dict | None = Depends(get_optional_user)):
    # Same trust-first hardening as get_swing_research (defensive symmetry).
    try:
        if not RESEARCH_SNAPSHOT_ONLY:
            _maybe_auto_scan("LONGTERM")
        is_premium = bool(user and user.get("role") in ("PREMIUM", "ADMIN"))

        if RESEARCH_SNAPSHOT_ONLY:
            snap = serve_cached_research_list("longterm")
            if snap is not None:
                served = _overlay_live_cmp(_finalize_research_snapshot("longterm", dict(snap)))
                return _safe_json_response(_gate_research_items(served, is_premium))
            merged = finalize_endpoint("longterm", {}, valid_research_list_payload)
            return _safe_json_response(_gate_research_items(dict(merged), is_premium))

        full = _longterm_payload(limit)
        snap = serve_cached_endpoint("longterm")
        if snap is not None:
            result = dict(snap)
        else:
            result = dict(full)
        if not is_premium and len(result.get("items") or []) > FREE_TIER_LIMIT:
            result = dict(result)
            result["items"] = list(result.get("items", []))[:FREE_TIER_LIMIT]
            result["gated"] = True
            result["total_available"] = full.get("count", len(full.get("items", [])))
            result["count"] = FREE_TIER_LIMIT
        if snap is not None:
            return _safe_json_response(result)
        return _safe_json_response(finalize_endpoint("longterm", full, valid_research_list_payload))
    except Exception:
        log.exception("get_longterm_research failed; serving safe empty payload")
        return _safe_json_response({
            "items": [], "count": 0, "last_scan_time": None,
            "snapshot_stale": True, "error": "transient_serve_error",
        })


@router.get("/api/research/live-signals")
@router.get("/research/live-signals")
def get_live_signals(limit: int = Query(40, ge=1, le=200)):
    """
    Surface today's Telegram-delivered signals so the website never diverges
    from what users see in Telegram.

    Reads Redis `signals:today:YYYY-MM-DD` list (RPUSHed by the engine on every
    Telegram send via utils.telegram_signal_log.push_signal_to_redis).
    """
    try:
        from dashboard.backend.terminal_events import read_today_signals
        items = read_today_signals() or []
        # newest first, then bounded
        items = list(reversed(items))[:limit]
        return {
            "items": items,
            "count": len(items),
            "source": "signals:today",
            "note": "Mirror of Telegram signal stream. Same truth, same time.",
        }
    except Exception:
        log.exception("live-signals endpoint failed; returning empty payload")
        return {"items": [], "count": 0, "error": "transient_payload_error"}


import math as _math


def _strip_non_finite(obj):
    """Recursively replace NaN/Inf floats with None so json.dumps(allow_nan=False) accepts the payload."""
    if isinstance(obj, float):
        if _math.isnan(obj) or _math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _strip_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_non_finite(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_strip_non_finite(v) for v in obj)
    return obj


def _to_jsonable(v):
    """Coerce ANY value to a JSON-safe primitive. Last line of defense before serialization."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        if _math.isnan(v) or _math.isinf(v):
            return None
        return v
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _to_jsonable(val) for k, val in v.items()}
    # bytes, datetime, Decimal, sqlite3.Row, custom objects → string
    try:
        return str(v)
    except Exception:
        return None


def _safe_json_response(content: dict, fallback: dict | None = None) -> JSONResponse:
    """Coerce content to pure JSON primitives, then serialize. Never raises."""
    try:
        clean = _to_jsonable(content)
        return JSONResponse(content=clean)
    except Exception:
        log.exception("response serialization failed; returning fallback")
        return JSONResponse(content=fallback or {"items": [], "count": 0, "error": "serialization_failed"})


@router.get("/api/research/running-trades")
@router.get("/research/running-trades")
def get_running_trades(limit: int = Query(40, ge=1, le=200)):
    """
    Resilient: never raises. Builds payload, optionally enriches with Redis LKG
    finalize, bypasses on failure, then serializes via jsonable_encoder to catch
    any non-JSON-safe values (datetimes, Decimals, NaN) before they reach the
    HTTP layer.
    """
    try:
        payload = _running_trades_payload(limit)
    except Exception:
        log.exception("running-trades payload_build failed")
        return _safe_json_response({"items": [], "count": 0, "error": "payload_build_failed"})

    if not isinstance(payload, dict):
        return _safe_json_response({"items": [], "count": 0, "error": "invalid_payload_shape"})

    try:
        finalized = finalize_endpoint(
            "running_trades",
            payload,
            valid_running_trades_payload,
        )
        if isinstance(finalized, dict):
            return _safe_json_response(finalized, fallback=payload)
    except Exception:
        log.exception("running-trades finalize_endpoint failed; returning raw payload")
    return _safe_json_response(payload)


@router.get("/api/market/state")
@router.get("/market/state")
def get_market_state():
    """Regime-governor exposure block for the Research feed + Command Center.

    Returns the current market state (STRONG_BULL … BEAR), suggested exposure /
    cash %, the graduated policy (max ideas, min confidence/RR), and leading
    sectors. Informational even when the governor is disabled (it does not
    change what the feed serves — enforcement is separate and flag-gated).
    Never raises: degrades to a safe UNKNOWN payload.
    """
    try:
        from services.regime_governor import exposure_state
        return _safe_json_response(exposure_state())
    except Exception:
        log.exception("market/state endpoint failed; serving safe fallback")
        return _safe_json_response({
            "governor_enabled": False,
            "market_state": "UNKNOWN",
            "exposure_pct": 100,
            "cash_pct": 0,
            "exposure_label": "🟢 Normal",
            "advisory": "Market state unavailable.",
        })


@router.get("/api/research/coverage")
@router.get("/research/coverage")
def get_research_coverage(target_universe: int = Query(2200, ge=100, le=5000)):
    universe = load_nse_universe(target_size=target_universe)
    swing_latest = get_ranking_runs(horizon="SWING", limit=1)
    long_latest = get_ranking_runs(horizon="LONGTERM", limit=1)

    def _shape(row: dict | None) -> dict | None:
        if not row:
            return None
        scanned = int(row.get("universe_scanned", 0))
        requested = int(row.get("universe_requested", target_universe))
        coverage_pct = round((scanned / requested) * 100, 2) if requested > 0 else 0.0
        return {
            "run_time": _utc_iso(row.get("run_time")),
            "universe_requested": requested,
            "universe_scanned": scanned,
            "quality_passed": int(row.get("quality_passed", 0)),
            "ranked_candidates": int(row.get("ranked_candidates", 0)),
            "selected_count": int(row.get("selected_count", 0)),
            "coverage_pct": coverage_pct,
        }

    payload = {
        "target_universe": target_universe,
        "available_universe": universe.total_size or universe.actual_size,
        "returned_universe": universe.actual_size,
        "sources": universe.sources,
        "cache_path": universe.cache_path,
        "cache_date": universe.cache_date,
        "source_errors": universe.source_errors,
        "latest": {
            "SWING": _shape(swing_latest[0] if swing_latest else None),
            "LONGTERM": _shape(long_latest[0] if long_latest else None),
        },
    }
    return finalize_endpoint("coverage", payload, valid_coverage_payload)


@router.get("/api/research/ranking-runs")
@router.get("/research/ranking-runs")
def get_research_ranking_runs(
    horizon: str | None = Query(None, description="SWING or LONGTERM"),
    limit: int = Query(20, ge=1, le=200),
):
    h = horizon.upper() if horizon else None
    if h and h not in {"SWING", "LONGTERM"}:
        raise HTTPException(status_code=400, detail="horizon must be SWING or LONGTERM")
    return {"items": get_ranking_runs(horizon=h, limit=limit)}


def _run_scan(agent_name: str, label: str) -> dict:
    """Run a research scan agent synchronously. Returns dict with ok/status/result or error."""
    try:
        from agents.runner import run_agent_now
        result = run_agent_now(agent_name)
    except Exception as exc:
        log.exception("Research run_agent_now failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if "error" in result:
        return {
            "ok": False,
            "scan": label,
            "agent": agent_name,
            "status": "error",
            "message": result["error"],
            "summary": str(result.get("error", "")),
            "result": result,
        }

    return {
        "ok": True,
        "scan": label,
        "agent": agent_name,
        "status": result.get("status", "OK"),
        "summary": result.get("summary", ""),
        "message": result.get("summary", ""),
        "result": result,
    }


def _start_research_scan_background(agent_name: str, label: str) -> dict:
    """
    Run ranking agent in a daemon thread so the HTTP request returns immediately.
    Full scans can take minutes (OHLC for finalist pool); avoids gateway timeouts on Railway/Vercel.
    Results/errors are tracked in ``_scan_history`` for observability via ``/api/research/scan-status``.
    """
    from agents.runner import run_agent_now

    horizon = label.upper()
    started_at = datetime.utcnow().isoformat() + "Z"

    _scan_history_set(horizon, {
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "error": None,
        "agent": agent_name,
        "trigger": "manual",
    })

    def _job() -> None:
        try:
            out = run_agent_now(agent_name)
            if isinstance(out, dict) and out.get("error"):
                log.error("[%s] background scan error: %s", agent_name, out["error"])
                _scan_history_set(horizon, {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "error": str(out["error"]),
                    "agent": agent_name,
                    "trigger": "manual",
                })
            else:
                summary = out.get("summary", str(out)) if isinstance(out, dict) else str(out)
                log.info("[%s] background scan finished: %s", agent_name, summary)
                # Refresh Redis-cached payload from DB so /api/research/{swing,longterm}
                # returns the new picks immediately. Without this, scan saved to DB but
                # the GET path serves stale/empty cache until next snapshot writer cycle.
                try:
                    if horizon == "SWING":
                        full_payload = _swing_payload(100)
                    elif horizon == "LONGTERM":
                        full_payload = _longterm_payload(100)
                    else:
                        full_payload = None
                    if isinstance(full_payload, dict) and full_payload.get("items"):
                        finalize_endpoint(horizon.lower(), full_payload, valid_research_list_payload)
                        log.info("[%s] redis snapshot refreshed (%d items)", horizon, len(full_payload["items"]))
                except Exception:
                    log.exception("[%s] redis snapshot refresh after scan failed", horizon)
                _scan_history_set(horizon, {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "summary": summary,
                    "agent": agent_name,
                    "trigger": "manual",
                })
                _notify_new_picks(horizon)
        except Exception as exc:
            log.exception("[%s] background scan failed", agent_name)
            _scan_history_set(horizon, {
                "status": "exception",
                "started_at": started_at,
                "finished_at": datetime.utcnow().isoformat() + "Z",
                "error": f"{exc.__class__.__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
                "agent": agent_name,
                "trigger": "manual",
            })

    threading.Thread(target=_job, daemon=True, name=f"research_{label}").start()
    return {
        "ok": True,
        "scan": label,
        "agent": agent_name,
        "status": "accepted",
        "summary": "Scan started in the background. Refresh in 1–3 minutes for results.",
        "message": "Scan started in the background. Refresh in 1–3 minutes for results.",
        "result": {},
    }


@router.post("/api/research/run/swing")
@router.post("/research/run/swing")
def run_swing_scan():
    """Trigger swing scan. Returns immediately; work runs in a background thread."""
    try:
        return _start_research_scan_background("SwingTradeAlphaAgent", "swing")
    except Exception as e:
        log.exception("run_swing_scan failed: %s", e)
        return {
            "ok": False,
            "scan": "swing",
            "agent": "SwingTradeAlphaAgent",
            "status": "error",
            "message": str(e),
            "summary": str(e),
            "result": {},
        }


@router.post("/api/research/run/longterm")
@router.post("/research/run/longterm")
def run_longterm_scan(force: bool = Query(False, description="Bypass slot-full check and scan anyway")):
    """Trigger long-term scan. Returns immediately; work runs in a background thread.

    When force=true, the agent ignores the "all slots occupied" guard and runs a
    full scan, useful when you want fresh ideas even if existing recommendations
    are still active.
    """
    try:
        if force:
            os.environ["LONGTERM_FORCE_SCAN"] = "1"
        return _start_research_scan_background("LongTermInvestmentAgent", "longterm")
    except Exception as e:
        log.exception("run_longterm_scan failed: %s", e)
        return {
            "ok": False,
            "scan": "longterm",
            "agent": "LongTermInvestmentAgent",
            "status": "error",
            "message": str(e),
            "summary": str(e),
            "result": {},
        }


@router.get("/api/research/track-record/ledger")
@router.get("/research/track-record/ledger")
def get_track_record_ledger(
    horizon: str = Query("all", pattern="^(swing|longterm|all)$"),
    limit: int = Query(200, ge=1, le=500),
):
    """Survivorship-free track record from the immutable ledger (PR3).

    Unlike /track-record (which reads mutable, recyclable recommendation rows),
    this reads `research_track_record` where every published idea — including
    EXPIRED / never-triggered ones — is permanently recorded. The win rate is
    over ALL resolved recommendations, so it is not inflated by dropping the
    ideas that quietly timed out. Never raises.
    """
    try:
        from dashboard.backend.db import track_record
        h = None if horizon == "all" else horizon
        return _safe_json_response({
            "stats": track_record.stats(h),
            "items": track_record.rows(h, limit=limit),
            "source": "immutable_ledger",
        })
    except Exception:
        log.exception("track-record ledger endpoint failed; serving safe empty payload")
        return _safe_json_response({"stats": {"available": False}, "items": [], "source": "immutable_ledger"})


@router.get("/api/research/track-record")
@router.get("/research/track-record")
def get_track_record(
    horizon: str = Query("all", pattern="^(swing|longterm|all)$"),
    limit: int = Query(100, ge=1, le=500),
):
    """Return historical track record of all recommendations with outcomes."""
    conn = get_connection()
    try:
        params: list = []
        where_parts = []
        if horizon.upper() in ("SWING", "LONGTERM"):
            where_parts.append("sr.agent_type = ?")
            params.append(horizon.upper())

        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        params.append(limit)

        rows = conn.execute(
            f"""
            SELECT sr.id, sr.symbol, sr.agent_type, sr.setup, sr.status,
                   sr.entry_price, sr.stop_loss, sr.targets, sr.confidence_score,
                   sr.scan_cmp, sr.exit_price, sr.exit_date, sr.exit_reason,
                   sr.created_at, sr.signals_updated_at,
                   rt.current_price, rt.profit_loss_pct, rt.days_held,
                   rt.high_since_entry, rt.low_since_entry, rt.status AS trade_status
            FROM stock_recommendations sr
            LEFT JOIN running_trades rt ON rt.recommendation_id = sr.id
                AND rt.id = (SELECT MAX(rt2.id) FROM running_trades rt2 WHERE rt2.recommendation_id = sr.id)
            {where_clause}
            ORDER BY datetime(sr.created_at) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        import json as _json

        picks = []
        total_resolved = 0
        total_target_hit = 0
        total_stop_hit = 0
        pnl_vals = []

        for row in rows:
            row = dict(row)
            status = row.get("trade_status") or row.get("status") or "ACTIVE"
            entry = float(row["entry_price"])
            current = float(row["current_price"]) if row.get("current_price") else None
            exit_p = float(row["exit_price"]) if row.get("exit_price") else None
            pnl_pct = float(row["profit_loss_pct"]) if row.get("profit_loss_pct") else None

            if pnl_pct is None and current and entry > 0:
                pnl_pct = round((current - entry) / entry * 100, 2)
            if pnl_pct is None and exit_p and entry > 0:
                pnl_pct = round((exit_p - entry) / entry * 100, 2)

            targets_raw = row.get("targets", "[]")
            try:
                targets = _json.loads(targets_raw) if isinstance(targets_raw, str) else (targets_raw or [])
            except Exception:
                targets = []

            if status in ("TARGET_HIT", "STOP_HIT", "CLOSED"):
                total_resolved += 1
                if pnl_pct is not None:
                    pnl_vals.append(pnl_pct)
                if status == "TARGET_HIT":
                    total_target_hit += 1
                elif status == "STOP_HIT":
                    total_stop_hit += 1

            picks.append({
                "id": row["id"],
                "symbol": row["symbol"],
                "agent_type": row["agent_type"],
                "setup": row.get("setup"),
                "status": status,
                "entry_price": entry,
                "stop_loss": float(row["stop_loss"]) if row.get("stop_loss") else None,
                "targets": targets,
                "confidence_score": float(row.get("confidence_score", 0)),
                "current_price": current,
                "exit_price": exit_p,
                "exit_date": row.get("exit_date"),
                "exit_reason": row.get("exit_reason"),
                "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                "days_held": int(row["days_held"]) if row.get("days_held") else None,
                "high_since_entry": float(row["high_since_entry"]) if row.get("high_since_entry") else None,
                "low_since_entry": float(row["low_since_entry"]) if row.get("low_since_entry") else None,
                "created_at": _utc_iso(row.get("created_at")),
                "signals_updated_at": _utc_iso(row.get("signals_updated_at")),
            })

        hit_rate = round(total_target_hit / total_resolved * 100, 1) if total_resolved > 0 else 0
        avg_pnl = round(sum(pnl_vals) / len(pnl_vals), 2) if pnl_vals else 0
        best_pnl = round(max(pnl_vals), 2) if pnl_vals else 0
        worst_pnl = round(min(pnl_vals), 2) if pnl_vals else 0

        return {
            "picks": picks,
            "total": len(picks),
            "summary": {
                "total_picks": len(picks),
                "resolved": total_resolved,
                "target_hit": total_target_hit,
                "stop_hit": total_stop_hit,
                "hit_rate_pct": hit_rate,
                "avg_pnl_pct": avg_pnl,
                "best_pnl_pct": best_pnl,
                "worst_pnl_pct": worst_pnl,
            },
        }
    finally:
        conn.close()


@router.get("/api/research/scan-status")
@router.get("/research/scan-status")
def get_scan_status():
    """Return the current state of research scans (in-flight + latest result per horizon).

    This endpoint makes auto-scan failures observable. If a scan keeps failing silently
    the frontend (or an operator) can inspect the error and traceback here.
    """
    with _auto_scan_lock:
        in_flight = sorted(_auto_scan_running)
    all_history = _scan_history_all()
    payload = {
        "in_flight": in_flight,
        "horizons": {
            h: {k: v for k, v in info.items() if k != "traceback"}
            for h, info in all_history.items()
        },
        "debug": {
            h: info.get("traceback")
            for h, info in all_history.items()
            if info.get("traceback")
        },
    }
    return finalize_endpoint("scan_status", payload, valid_scan_status_payload)


@router.get("/api/research/discovery")
@router.get("/research/discovery")
async def get_discovery(
    top_k: int = Query(20, ge=1, le=100),
    min_turnover_cr: float = Query(1.0, ge=0.0),
    source: str = Query(None),
    refresh: bool = Query(False),
):
    """Strict validation feed across Discovery → Quality → SMC.

    The backend assigns each stock to exactly one section: final, watchlist, or
    discovery. The frontend renders these buckets directly so symbols do not
    appear in multiple decision stages.
    """
    from services.validation_engine import run_validation_scan

    src = source or os.getenv("RESEARCH_DATA_SOURCE", "yfinance")
    _allow_on_demand_scan = os.getenv("DISCOVERY_ALLOW_ON_DEMAND_SCAN", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    cached = _decision_cache_get(top_k, float(min_turnover_cr), src)
    if cached:
        ts, cached_payload = cached
        age = time.time() - ts
        if age < DECISION_CACHE_SECONDS:
            payload = dict(cached_payload)
            payload["cache_hit"] = True
            payload["cache_ttl_sec"] = max(0, int(DECISION_CACHE_SECONDS - age))
            return finalize_endpoint(
                "discovery",
                payload,
                valid_discovery_redis_write,
                discovery_atomic=True,
            )

    rsnap = serve_cached_endpoint("discovery")
    if rsnap:
        out = dict(rsnap)
        out.setdefault("snapshot_source", "redis")
        out.setdefault("cache_hit", False)
        return out

    if not refresh:
        logged_payload = _latest_logged_decision_payload(top_k, min_turnover_cr, src)
        if logged_payload:
            _decision_cache_set(top_k, float(min_turnover_cr), src, logged_payload)
            return finalize_endpoint(
                "discovery",
                logged_payload,
                valid_discovery_redis_write,
                discovery_atomic=True,
            )

    if not (_allow_on_demand_scan and refresh):
        stale_payload = {
            "data_source": src,
            "universe_size": 0,
            "scanned": 0,
            "returned": 0,
            "watchlist_returned": 0,
            "discovery_returned": 0,
            "fallback_returned": 0,
            "generated_at": datetime.now().isoformat(),
            "scan_id": "snapshot_only",
            "coverage": {},
            "funnel": {},
            "items": [],
            "final_trades": [],
            "watchlist": [],
            "discovery": [],
            "fallback_items": [],
            "cache_hit": False,
            "cache_ttl_sec": 0,
            "snapshot_only_no_engine_scan": True,
            "hint": "Discovery is snapshot-first. Populate via engine/scheduler or set DISCOVERY_ALLOW_ON_DEMAND_SCAN=true with refresh=true for admin rescans.",
        }
        return finalize_endpoint(
            "discovery",
            stale_payload,
            valid_discovery_redis_write,
            discovery_atomic=True,
        )

    try:
        _telegram_flush_medium_buffer(force=False)
    except Exception:
        pass

    import asyncio
    _scan_timeout = int(os.getenv("RESEARCH_DISCOVERY_TIMEOUT", "25"))
    try:
        result = await asyncio.wait_for(
            run_validation_scan(
                "SWING",
                top_k=top_k,
                target_universe=int(os.getenv("RESEARCH_DISCOVERY_UNIVERSE", "2200")),
                min_turnover_cr=min_turnover_cr,
                source=src,
            ),
            timeout=_scan_timeout,
        )
    except asyncio.TimeoutError:
        log.warning("validation discovery scan timed out after %ds", _scan_timeout)
        bad = {
            "data_source": src, "universe_size": 0, "scanned": 0,
            "returned": 0, "watchlist_returned": 0, "discovery_returned": 0,
            "fallback_returned": 0, "generated_at": datetime.now().isoformat(),
            "scan_id": "timeout", "coverage": {}, "funnel": {},
            "items": [], "final_trades": [], "watchlist": [], "discovery": [],
            "fallback_items": [], "cache_hit": False, "cache_ttl_sec": 60,
            "error": f"Scan timed out after {_scan_timeout}s — try again or run a manual scan first.",
        }
        return finalize_endpoint(
            "discovery",
            bad,
            valid_discovery_redis_write,
            discovery_atomic=True,
        )
    except Exception as exc:
        log.exception("validation discovery scan failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"validation_discovery_failed: {exc}") from exc

    cards = [record.to_trade_card() for record in result.selected]
    watchlist = [record.to_trade_card() for record in result.watchlist]
    discovery = [record.to_trade_card() for record in result.discovery]
    payload = {
        "data_source": src,
        "universe_size": result.universe.total_size or result.universe.actual_size,
        "scanned": result.coverage.scanned,
        "returned": len(cards),
        "watchlist_returned": len(watchlist),
        "discovery_returned": len(discovery),
        "fallback_returned": len(discovery),
        "generated_at": datetime.now().isoformat(),
        "scan_id": result.scan_id,
        "coverage": result.coverage.to_dict(),
        "funnel": result.funnel.to_dict(),
        "items": cards,
        "final_trades": cards,
        "watchlist": watchlist,
        "discovery": discovery,
        "fallback_items": discovery,
        "cache_hit": False,
        "cache_ttl_sec": DECISION_CACHE_SECONDS,
        "scan_diagnostics": getattr(result, "diagnostics", {}),
    }
    try:
        _telegram_discovery_tiers(payload)
    except Exception as exc:
        log.debug("telegram discovery tiers skipped: %s", exc)
    _decision_cache_set(top_k, float(min_turnover_cr), src, payload)
    return finalize_endpoint(
        "discovery",
        payload,
        valid_discovery_redis_write,
        discovery_atomic=True,
    )


@router.get("/api/research/validation")
@router.get("/research/validation")
async def run_layer_validation(
    horizon: str = Query("SWING", pattern="^(SWING|LONGTERM)$"),
    top_k: int = Query(10, ge=1, le=50),
    target_universe: int = Query(2200, ge=10, le=5000),
    min_turnover_cr: float = Query(1.0, ge=0.0),
    source: str | None = Query(None),
):
    """Run full 3-layer validation and log every stock to signals_log."""
    from services.validation_engine import run_validation_scan

    import asyncio
    src = source or os.getenv("RESEARCH_DATA_SOURCE", "yfinance")
    _scan_timeout = int(os.getenv("RESEARCH_VALIDATION_TIMEOUT", "90"))
    try:
        result = await asyncio.wait_for(
            run_validation_scan(
                horizon.upper(),
                top_k=top_k,
                target_universe=target_universe,
                min_turnover_cr=min_turnover_cr,
                source=src,
                log_scan=True,
            ),
            timeout=_scan_timeout,
        )
    except asyncio.TimeoutError:
        log.warning("validation scan timed out after %ds", _scan_timeout)
        bad = {
            "scan_id": "",
            "horizon": horizon.upper(),
            "coverage": {},
            "funnel": {},
            "logged_rows": 0,
            "items": [],
            "final_trades": [],
            "watchlist": [],
            "discovery": [],
            "fallback_items": [],
            "records_sample": [],
            "error": f"Validation scan timed out after {_scan_timeout}s",
        }
        return finalize_endpoint("validation", bad, valid_validation_payload)
    payload = {
        "scan_id": result.scan_id,
        "horizon": result.horizon,
        "coverage": result.coverage.to_dict(),
        "funnel": result.funnel.to_dict(),
        "logged_rows": result.logged_rows,
        "items": [record.to_trade_card() for record in result.selected],
        "final_trades": [record.to_trade_card() for record in result.selected],
        "watchlist": [record.to_trade_card() for record in result.watchlist],
        "discovery": [record.to_trade_card() for record in result.discovery],
        "fallback_items": [record.to_trade_card() for record in result.discovery],
        "records_sample": [record.to_dict() for record in result.records[:100]],
        "scan_diagnostics": getattr(result, "diagnostics", {}),
    }
    try:
        commit_watchlist_final_independent(
            list(payload.get("watchlist") or []),
            list(payload.get("final_trades") or []),
        )
    except Exception as exc:
        log.debug("watchlist/final independent commit failed: %s", exc)
    return finalize_endpoint("validation", payload, valid_validation_payload)


@router.get("/api/research/layer-report")
@router.get("/research/layer-report")
def get_layer_report(
    horizon: str | None = Query(None, pattern="^(SWING|LONGTERM)$"),
    limit: int = Query(100, ge=1, le=500),
):
    """Return latest logged validation scan with layer funnel and rejection reasons."""
    from dashboard.backend.db import get_latest_signals_scan_report

    rep = get_latest_signals_scan_report(horizon=horizon, limit=limit)
    return finalize_endpoint("layer_report", rep, valid_layer_report_payload)


@router.get("/api/research/lifecycle-events")
@router.get("/research/lifecycle-events")
def get_lifecycle_events(
    limit: int = Query(100, ge=1, le=1000),
    symbol: str | None = Query(None),
    state: str | None = Query(None),
):
    """PHASE G2-3 (read-only): inspect the canonical lifecycle ledger.
    Append-only shadow stream — nothing reads it for behaviour yet; it
    accumulates the real FILTERED→ENTRY_ACTIVE→CLOSED transitions so later
    phases have a real data foundation."""
    try:
        from dashboard.backend.lifecycle_ledger import recent_events, summary

        # Foundational observability: prove the ledger TABLE actually
        # exists (the G2-3 incident was a silently-missing table; readers
        # fail-safe to empty, so "0 events" alone could not distinguish
        # "no writes yet" from "table never created").
        core_missing: list[str] = []
        try:
            from dashboard.backend.db.schema import _verify_core_tables
            core_missing = _verify_core_tables()
        except Exception:
            core_missing = ["<verify_failed>"]

        return {
            "summary": summary(),
            "events": recent_events(limit, symbol, state),
            "db_health": {
                "lifecycle_events_table_present": "lifecycle_events" not in core_missing,
                "equity_sm_state_table_present": "equity_sm_state" not in core_missing,
                "core_tables_missing": core_missing,
            },
            "_note": "Read-only shadow. No behaviour is driven by this table in G2-3.",
        }
    except Exception as exc:
        log.exception("lifecycle-events failed: %s", exc)
        return _safe_json_response({"error": "lifecycle_unavailable", "detail": str(exc)})


@router.get("/api/research/regime-sector-shadow")
@router.get("/research/regime-sector-shadow")
def get_regime_sector_shadow(horizon: str = Query("SWING", pattern="^(SWING|LONGTERM)$")):
    """PHASE G2-2 (read-only): inspect the latest regime+sector SHADOW —
    what the canonical mandatory pre-gate WOULD have done to the most recent
    scan's picks. Pure observability; nothing was filtered."""
    try:
        from datetime import date as _date

        from dashboard.backend.cache import get as cache_get
        from services.market_regime import get_regime_summary
        from services.sector_strength import compute_sector_strength

        shadow = cache_get(f"shadow:regime_sector:{horizon.upper()}:{_date.today().isoformat()}")
        return {
            "horizon": horizon.upper(),
            "shadow_available": shadow is not None,
            "shadow": shadow,
            "regime_now": get_regime_summary(),
            "sector_strength_now": compute_sector_strength(),
            "_note": "Read-only. No recommendations were filtered by regime/sector yet (G2-2 is shadow only).",
        }
    except Exception as exc:
        log.exception("regime-sector-shadow failed: %s", exc)
        return _safe_json_response({"error": "shadow_unavailable", "detail": str(exc)})


@router.get("/api/research/sm-shadow-scorecard")
@router.get("/research/sm-shadow-scorecard")
def get_sm_shadow_scorecard(
    hold_days: int = Query(15, ge=1, le=120),
    source: str = Query("yfinance"),
):
    """PHASE G2-6 Rung A (read-only): score the accumulated
    `g2_6_sm_shadow` ONLINE-engine would-FIRE stream against the corrected
    G2-6 gate using realised forward outcomes from later OHLC. This is the
    LIVE confirmation Rung A must pass before Rung B. Nothing user-facing;
    no recommendation/position/alert is driven by this."""
    try:
        from services.equity_state_machine import compute_shadow_scorecard

        return compute_shadow_scorecard(hold_days=hold_days, source=source)
    except Exception as exc:
        log.exception("sm-shadow-scorecard failed: %s", exc)
        return _safe_json_response(
            {"error": "sm_shadow_unavailable", "detail": str(exc)})


@router.get("/api/research/state-machine-signals")
@router.get("/research/state-machine-signals")
def get_state_machine_signals(limit: int = Query(50, ge=1, le=200)):
    """PHASE G2-6 Rung B (read-only): the planned-execution state-machine
    signals published in `=alert` mode, as an ISOLATED `SWING_SM`
    stream (separate agent_type — never mixed with or counted against the
    legacy SWING slot machine). These are PLANNED setups in validation
    phase: no position is opened, nothing is auto-traded. Empty until
    `EQUITY_STATE_MACHINE=alert` and a recent fire occurs."""
    try:
        rows = get_stock_recommendations("SWING_SM", limit=limit) or []
        return {
            "engine": "planned_state_machine",
            "validation_phase": True,
            "auto_traded": False,
            "count": len(rows),
            "signals": rows,
            "_note": (
                "Backtest-proven (5/5 windows) planned-execution signals, "
                "LIVE validation in progress. Planned LIMIT entries — wait "
                "for the zone; NOT auto-executed. Isolated from legacy "
                "swing recommendations."
            ),
        }
    except Exception as exc:
        log.exception("state-machine-signals failed: %s", exc)
        return _safe_json_response(
            {"error": "sm_signals_unavailable", "detail": str(exc),
             "signals": [], "count": 0})


@router.get("/api/research/anchor-shadow-status")
@router.get("/research/anchor-shadow-status")
def anchor_shadow_status():
    """Anchor10 shadow-programme status (read-only).

    Surfaces the validation soak of ENTRY_ANCHOR_MAX_GAP_PCT=10: session count,
    current criteria status, and PASS/FAIL for C1–C5 (see
    docs/PHASE2_ENABLEMENT_CRITERIA.md). The recorder job appends one session
    per trading day to the shadow:anchor10:sessions Redis key.
    STRUCTURAL_TARGET_CAP remains disabled — not evaluated here."""
    try:
        import os as _os
        import redis as _r

        from services.anchor_shadow import evaluate_criteria, load_sessions

        url = _os.getenv("REDIS_URL", "").strip()
        cli = _r.from_url(url, decode_responses=True, socket_timeout=3) if url else None
        return _safe_json_response(evaluate_criteria(load_sessions(cli)))
    except Exception as exc:
        log.exception("anchor-shadow-status failed: %s", exc)
        return _safe_json_response(
            {"error": "anchor_shadow_status_unavailable", "detail": str(exc),
             "session_count": 0, "overall": "UNKNOWN"})


@router.get("/api/research/fvg-tap-signals")
@router.get("/research/fvg-tap-signals")
def get_fvg_tap_signals(limit: int = Query(50, ge=1, le=200)):
    """FVG-Tap (read-only): the index-5m FVG-Tap signals published in
    `FVG_TAP_MODE=alert`, as an ISOLATED `FVG_TAP` stream (separate
    agent_type — never mixed with Setup-A, SWING, or SWING_SM). Backtest
    cleared the pre-committed index-domain gate (NIFTY 5m PF 2.07,
    BANKNIFTY 5m PF 2.33, criteria locked before results). VALIDATION
    phase: forward-validating on live bars; NOT auto-traded, no position.
    Empty until FVG_TAP_MODE=alert and a fresh confirmation occurs."""
    try:
        rows = get_stock_recommendations("FVG_TAP", limit=limit) or []
        return {
            "engine": "fvg_tap",
            "validation_phase": True,
            "auto_traded": False,
            "count": len(rows),
            "signals": rows,
            "_note": (
                "FVG-Tap index-5m signals (NIFTY/BANKNIFTY). Backtest "
                "cleared the locked index-domain gate; LIVE forward-"
                "validation in progress. MARKET-on-confirmation entries — "
                "NOT auto-executed. Isolated from all other engines."
            ),
        }
    except Exception as exc:
        log.exception("fvg-tap-signals failed: %s", exc)
        return _safe_json_response(
            {"error": "fvg_tap_signals_unavailable", "detail": str(exc),
             "signals": [], "count": 0})


@router.get("/api/research/quality-universe")
@router.get("/research/quality-universe")
async def get_quality_universe_endpoint(
    limit: int = Query(120, ge=10, le=2500, description="symbols to score (bounded for interactive use)"),
    min_score: float = Query(0.0, ge=0.0, le=100.0, description="filter qualified list to score >= this"),
    refresh: bool = Query(False, description="bypass 24h Redis cache and rebuild"),
    source: str = Query("yfinance"),
):
    """PHASE F2 — Quality Universe Engine inspection (read-only, no look-ahead,
    no production mutation). Scores the live NSE universe on real OHLCV
    tradability factors. `limit` bounds the symbol count so the endpoint
    returns within the HTTP timeout; the full daily build is intended to run
    as a background job that populates the 24h Redis cache."""
    from services.universe_manager import load_nse_universe
    from services.universe_quality import build_quality_universe

    try:
        universe = load_nse_universe(limit)
        result = await build_quality_universe(universe.symbols[:limit], source=source)
        if min_score > 0:
            result["qualified"] = [
                q for q in result.get("qualified", []) if q.get("score", 0) >= min_score
            ]
            result["qualified_count"] = len(result["qualified"])
        result["_served_from"] = "fresh_build"
        result["_note"] = (
            "Bounded interactive run. Delivery% and bid/ask spread are "
            "intentionally excluded — no real data source in repo; not synthesized."
        )
        return result
    except Exception as exc:
        log.exception("quality-universe failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"quality_universe_failed: {exc}")


@router.get("/api/research/backtest")
@router.get("/research/backtest")
async def run_research_backtest(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    horizon: str = Query("SWING", pattern="^(SWING|LONGTERM)$"),
    top_n: int = Query(5, ge=1, le=20),
    target_universe: int = Query(300, ge=10, le=5000),
    hold_days: int = Query(20, ge=1, le=250),
    scan_step_days: int = Query(5, ge=1, le=30),
    source: str = Query("yfinance"),
    transaction_cost_pct: float = Query(0.10, ge=0.0, le=5.0),
    slippage_pct: float = Query(0.05, ge=0.0, le=5.0),
    universe_quality_filter: bool = Query(False, description="F3: pre-filter universe via F2 Quality Engine (point-in-time, no look-ahead)"),
    quality_min_tier: str = Query("Good", pattern="^(Good|Strong|Elite)$"),
    risk_per_trade_pct: float = Query(1.0, ge=0.1, le=5.0, description="F-Risk: % of equity risked if SL hit"),
    max_position_pct: float = Query(20.0, ge=2.0, le=100.0, description="F-Risk: hard cap on single position weight"),
    max_concurrent_positions: int = Query(8, ge=1, le=50, description="F-Risk: concurrent open positions cap"),
    max_per_sector: int = Query(3, ge=1, le=20, description="F-Risk: sector concentration cap"),
    gated_only: bool = Query(False, description="F-XRAY: disable ungated _scored_smc_levels fallback — measure ONLY the strict gated scorer"),
    engine_mode: str = Query("validation", pattern="^(validation|state_machine|state_machine_online)$", description="G2-5: state_machine = batch planned-execution oracle. G2-6: state_machine_online = ONLINE-correct live-cadence engine (the honest set to grade vs the G2-6 gate). All shadow/research, no live behaviour change."),
):
    """Run an OHLC-sliced historical backtest of the 3-layer strategy.

    F3: pass universe_quality_filter=true to run the SAME backtest on the
    F2-filtered universe — the filtered-vs-unfiltered delta is the proof of
    whether selection quality produces edge.

    G2-5: pass engine_mode=state_machine to run the F2-filtered equity universe
    through the planned-execution state-machine simulator (the canonical Engine A
    logic, bar-clocked) instead of the validation/instant-entry path. Read-only
    research — does not affect live recommendations.
    """
    from services.backtest_engine import (
        PortfolioConfig,
        run_backtest,
        run_state_machine_backtest,
    )

    try:
        if engine_mode in ("state_machine", "state_machine_online"):
            return await run_state_machine_backtest(
                start_date=start_date,
                end_date=end_date,
                target_universe=target_universe,
                hold_days=hold_days,
                source=source,
                transaction_cost_pct=transaction_cost_pct,
                slippage_pct=slippage_pct,
                quality_min_tier=quality_min_tier,
                portfolio_cfg=PortfolioConfig(
                    risk_per_trade_pct=risk_per_trade_pct,
                    max_position_pct=max_position_pct,
                    max_concurrent_positions=max_concurrent_positions,
                    max_per_sector=max_per_sector,
                ),
                online=(engine_mode == "state_machine_online"),
            )
        return await run_backtest(
            start_date=start_date,
            end_date=end_date,
            horizon=horizon.upper(),
            top_n=top_n,
            target_universe=target_universe,
            hold_days=hold_days,
            scan_step_days=scan_step_days,
            source=source,
            log_scans=False,
            transaction_cost_pct=transaction_cost_pct,
            slippage_pct=slippage_pct,
            universe_quality_filter=universe_quality_filter,
            quality_min_tier=quality_min_tier,
            portfolio_cfg=PortfolioConfig(
                risk_per_trade_pct=risk_per_trade_pct,
                max_position_pct=max_position_pct,
                max_concurrent_positions=max_concurrent_positions,
                max_per_sector=max_per_sector,
            ),
            gated_only=gated_only,
        )
    except Exception as exc:
        log.exception("research backtest failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"backtest_failed: {exc}")


@router.get("/api/research/scan-debug")
@router.get("/research/scan-debug")
def get_scan_debug(horizon: str = Query("SWING", pattern="^(SWING|LONGTERM)$")):
    """Surface rejection counts + per-symbol reasons from the most recent ranking run.

    Used by the dashboard's `?debug=1` panel so operators can see exactly why a
    given scan returned 0 (or fewer than expected) ideas. Reads the latest
    `RankingResult` cached by the agents runner.
    """
    try:
        from agents.runner import get_last_ranking_result  # type: ignore
        result = get_last_ranking_result(horizon)
    except Exception:
        result = None

    if result is None:
        try:
            from dashboard.backend.db import get_latest_signals_scan_report
            logged = get_latest_signals_scan_report(horizon=horizon, limit=50)
        except Exception:
            logged = {"available": False}
        return {
            "horizon": horizon,
            "available": bool(logged.get("available")),
            "message": "No cached in-memory ranking result; returning latest signals_log report when available.",
            "signals_log": logged,
            "scan_history": _scan_history_get(horizon.lower()),
        }

    rejections = getattr(result, "rejections", None) or []
    reasons_by_stage: dict[str, dict[str, int]] = {}
    for r in rejections:
        stage = getattr(r, "stage", "unknown")
        reason = getattr(r, "reason", "unknown")
        reasons_by_stage.setdefault(stage, {})
        reasons_by_stage[stage][reason] = reasons_by_stage[stage].get(reason, 0) + 1

    return {
        "horizon": horizon,
        "available": True,
        "scanned": getattr(result, "scanned", 0),
        "quality_passed": getattr(result, "quality_passed", 0),
        "ranked_candidates": getattr(result, "ranked_candidates", 0),
        "ideas_returned": len(getattr(result, "ideas", []) or []),
        "fallback_used": getattr(result, "fallback_used", False),
        "rejection_counts": {stage: sum(reasons.values()) for stage, reasons in reasons_by_stage.items()},
        "rejections_by_reason": reasons_by_stage,
        "rejections_sample": [
            {"symbol": getattr(r, "symbol", ""), "stage": getattr(r, "stage", ""), "reason": getattr(r, "reason", "")}
            for r in rejections[:50]
        ],
        "scan_history": _scan_history_get(horizon.lower()),
    }


@router.post("/api/research/tracker/refresh")
@router.post("/research/tracker/refresh")
def tracker_refresh():
    """Immediately seed any un-tracked recommendations and update all running trade prices."""
    try:
        from services.trade_tracker import refresh_now
        result = refresh_now()
        return {"ok": True, "seeded": result["seeded"], "updated": result["updated"], "purged": result.get("purged", 0)}
    except Exception as e:
        log.exception("tracker_refresh failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/research/running-trades/history")
@router.get("/research/running-trades/history")
def get_running_trades_history(limit: int = Query(100, ge=1, le=500)):
    """Return all running trades including closed ones (TARGET_HIT, STOP_HIT)."""
    try:
        from dashboard.backend.db import list_running_trades
        rows = list_running_trades(limit=limit, active_only=False)
    except Exception:
        log.exception("running-trades/history list_running_trades failed")
        return {"items": [], "count": 0, "error": "db_query_failed"}

    items = []
    for row in rows or []:
        try:
            entry = _f(row.get("entry_price"))
            if entry <= 0:
                continue
            targets_raw = row.get("targets") or []
            targets = [_f(t) for t in targets_raw if t is not None]
            items.append({
                "id": row.get("id"),
                "symbol": row.get("symbol"),
                "entry_price": entry,
                "current_price": _f(row.get("current_price"), entry),
                "stop_loss": _f(row.get("stop_loss"), entry * 0.95),
                "targets": targets,
                "profit_loss": _f(row.get("profit_loss")),
                "profit_loss_pct": _f(row.get("profit_loss_pct")),
                "drawdown_pct": _f(row.get("drawdown_pct")),
                "high_since_entry": row.get("high_since_entry"),
                "low_since_entry": row.get("low_since_entry"),
                "days_held": int(_f(row.get("days_held"))),
                "status": row.get("status", "RUNNING"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            })
        except Exception as exc:
            log.warning("running-trades/history row skip sym=%s err=%s", row.get("symbol"), exc)
            continue
    return _safe_json_response({"items": items, "count": len(items)})


@router.get("/api/research/performance")
@router.get("/research/performance")
def get_research_performance():
    """Aggregate performance stats from all tracked recommendations."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN status = 'TARGET_HIT' THEN 1 ELSE 0 END) AS target_hit,
                SUM(CASE WHEN status = 'STOP_HIT' THEN 1 ELSE 0 END) AS stop_hit,
                SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed,
                AVG(CASE WHEN status IN ('TARGET_HIT','STOP_HIT','CLOSED') THEN profit_loss_pct END) AS avg_pnl_pct,
                AVG(CASE WHEN status = 'RUNNING' THEN profit_loss_pct END) AS avg_open_pnl_pct,
                SUM(CASE WHEN status IN ('TARGET_HIT','STOP_HIT','CLOSED') THEN profit_loss_pct ELSE 0 END) AS total_pnl_pct,
                MAX(profit_loss_pct) AS best_pnl_pct,
                MIN(CASE WHEN status IN ('TARGET_HIT','STOP_HIT','CLOSED') THEN profit_loss_pct END) AS worst_pnl_pct,
                AVG(CASE WHEN status IN ('TARGET_HIT','STOP_HIT','CLOSED') THEN days_held END) AS avg_days_held
            FROM running_trades
            """
        ).fetchone()

        total = rows["total"] or 0
        target_hit = rows["target_hit"] or 0
        stop_hit = rows["stop_hit"] or 0
        closed_count = rows["closed"] or 0
        resolved = target_hit + stop_hit + closed_count

        hit_rate = round((target_hit / resolved) * 100, 1) if resolved > 0 else 0

        best = conn.execute(
            "SELECT symbol, profit_loss_pct FROM running_trades WHERE status IN ('TARGET_HIT','STOP_HIT','CLOSED') ORDER BY profit_loss_pct DESC LIMIT 1"
        ).fetchone()
        worst = conn.execute(
            "SELECT symbol, profit_loss_pct FROM running_trades WHERE status IN ('TARGET_HIT','STOP_HIT','CLOSED') ORDER BY profit_loss_pct ASC LIMIT 1"
        ).fetchone()

        scan_rows = conn.execute(
            "SELECT COUNT(*) AS cnt, horizon FROM ranking_runs GROUP BY horizon"
        ).fetchall()
        scan_counts = {r["horizon"]: r["cnt"] for r in scan_rows}

        payload = {
            "total_recommendations": total,
            "active": rows["active"] or 0,
            "target_hit": target_hit,
            "stop_hit": stop_hit,
            "closed": closed_count,
            "resolved": resolved,
            "hit_rate_pct": hit_rate,
            "avg_closed_pnl_pct": round(float(rows["avg_pnl_pct"] or 0), 2),
            "avg_open_pnl_pct": round(float(rows["avg_open_pnl_pct"] or 0), 2),
            "total_pnl_pct": round(float(rows["total_pnl_pct"] or 0), 2),
            "best_trade": {"symbol": best["symbol"], "pnl_pct": round(best["profit_loss_pct"], 2)} if best else None,
            "worst_trade": {"symbol": worst["symbol"], "pnl_pct": round(worst["profit_loss_pct"], 2)} if worst else None,
            "avg_days_held": round(float(rows["avg_days_held"] or 0), 1),
            "swing_scans": scan_counts.get("SWING", 0),
            "longterm_scans": scan_counts.get("LONGTERM", 0),
        }
        return finalize_endpoint("performance", payload, valid_performance_payload)
    finally:
        conn.close()


# ── Phase 4C: research outcomes (TARGET_HIT / STOP_HIT / EXPIRED rollup) ──────

@router.get("/api/research/outcomes")
@router.get("/research/outcomes")
def get_research_outcomes(
    horizon: str = Query("swing", pattern="^(swing|longterm|all)$"),
    days: int = Query(30, ge=1, le=365),
):
    """Aggregate stock_recommendations outcomes over the last ``days`` days.

    Returns per-status counts, hit-rate, average R, profit-factor, and a
    per-setup breakdown. Drives the "Research Hit Rate" analytics card.
    """
    horizon_filter = horizon.upper()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        params: list = [cutoff]
        sql_where = "WHERE COALESCE(exit_date, created_at) >= ?"
        if horizon_filter in ("SWING", "LONGTERM"):
            sql_where += " AND agent_type = ?"
            params.append(horizon_filter)

        rows = conn.execute(
            f"""
            SELECT id, symbol, agent_type, setup, status, pnl_r, exit_date,
                   created_at, entry_price, stop_loss
              FROM stock_recommendations
              {sql_where}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    by_status = {"ACTIVE": 0, "TARGET_HIT": 0, "STOP_HIT": 0, "EXPIRED": 0, "ARCHIVED": 0}
    pnl_vals: list[float] = []
    wins_r = 0.0
    losses_r = 0.0
    for r in rows:
        st = (r["status"] or "ACTIVE").upper()
        by_status[st] = by_status.get(st, 0) + 1
        if st in ("TARGET_HIT", "STOP_HIT", "EXPIRED"):
            try:
                pr = float(r["pnl_r"]) if r["pnl_r"] is not None else 0.0
            except (TypeError, ValueError):
                pr = 0.0
            pnl_vals.append(pr)
            if pr > 0:
                wins_r += pr
            elif pr < 0:
                losses_r += abs(pr)

    resolved = by_status["TARGET_HIT"] + by_status["STOP_HIT"] + by_status["EXPIRED"]
    decisive = by_status["TARGET_HIT"] + by_status["STOP_HIT"]
    hit_rate = round(by_status["TARGET_HIT"] / decisive * 100, 2) if decisive else 0.0
    avg_pnl_r = round(sum(pnl_vals) / len(pnl_vals), 3) if pnl_vals else 0.0
    profit_factor = round(wins_r / losses_r, 2) if losses_r > 0 else (wins_r if wins_r > 0 else 0.0)

    # Per-setup breakdown (decisive trades only)
    by_setup: dict[str, dict] = {}
    for r in rows:
        st = (r["status"] or "").upper()
        if st not in ("TARGET_HIT", "STOP_HIT"):
            continue
        setup = (r["setup"] or "UNKNOWN").upper()
        bucket = by_setup.setdefault(setup, {"setup": setup, "wins": 0, "losses": 0})
        if st == "TARGET_HIT":
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
    setup_rows = []
    for s in by_setup.values():
        n = s["wins"] + s["losses"]
        s["total"] = n
        s["hit_rate_pct"] = round(s["wins"] / n * 100, 2) if n else 0.0
        setup_rows.append(s)
    setup_rows.sort(key=lambda x: x["total"], reverse=True)

    return {
        "horizon": horizon_filter,
        "window_days": days,
        "total": total,
        "active": by_status["ACTIVE"],
        "target_hit": by_status["TARGET_HIT"],
        "stop_hit": by_status["STOP_HIT"],
        "expired": by_status["EXPIRED"],
        "resolved": resolved,
        "hit_rate_pct": hit_rate,
        "avg_pnl_r": avg_pnl_r,
        "profit_factor": profit_factor,
        "by_setup": setup_rows,
    }


# ── Research chart data endpoint ──────────────────────────────────────────────

_chart_cache: dict[str, tuple[float, dict]] = {}
_CHART_CACHE_TTL = 300  # 5 minutes

@router.get("/api/research/chart-data/{symbol}")
@router.get("/research/chart-data/{symbol}")
def get_research_chart_data(symbol: str, horizon: str = Query("SWING")):
    """
    Return daily OHLC candles + SMC zones + trade levels for a research stock.
    Used by the interactive chart overlay page.
    """
    import time as _t
    symbol = symbol.upper().replace("NSE:", "")
    horizon = horizon.upper()

    cache_key = f"{symbol}:{horizon}"
    cached = _chart_cache.get(cache_key)
    if cached and (_t.time() - cached[0]) < _CHART_CACHE_TTL:
        return cached[1]

    # 1. Fetch recommendation from DB (best-effort — a missing/malformed reco
    #    must NOT crash the chart; the candles are the point). A row with a
    #    NULL symbol previously raised AttributeError here and 500'd EVERY chart
    #    request (unhandled 500s also drop CORS headers, so the browser only
    #    saw "Failed to fetch").
    reco = None
    try:
        for r in get_stock_recommendations(horizon, limit=50):
            rsym = r.get("symbol")
            if not isinstance(rsym, str) or not rsym:
                continue
            if rsym.upper().replace("NSE:", "") == symbol:
                reco = r
                break
    except Exception as exc:
        log.warning("chart-data reco lookup failed for %s: %s", symbol, exc)
        reco = None

    # 2. Fetch daily OHLC via yfinance
    candles = _fetch_yfinance_ohlc(symbol, days=180)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No OHLC data for {symbol}")

    # 3. Detect SMC zones
    zones = _detect_smc_zones(candles)

    # 4. Build trade levels from recommendation (best-effort — a malformed
    #    field must degrade to "no levels", never crash the chart).
    levels = []
    if reco:
        try:
            entry = float(reco["entry_price"])
            sl = float(reco["stop_loss"]) if reco.get("stop_loss") else None
            targets = reco.get("targets", []) or []
            if not isinstance(targets, list):
                targets = []
            scan_cmp = float(reco["scan_cmp"]) if reco.get("scan_cmp") else None
            entry_type = reco.get("entry_type", "MARKET")

            levels.append({"type": "entry", "price": entry, "label": f"Entry ₹{entry:.2f}", "color": "#2962ff",
                            "style": "solid", "entry_type": entry_type})
            if sl:
                levels.append({"type": "sl", "price": sl, "label": f"SL ₹{sl:.2f}", "color": "#ff4757", "style": "dashed"})
            for i, t in enumerate(targets):
                try:
                    tv = float(t)
                except (TypeError, ValueError):
                    continue
                levels.append({"type": "target", "price": tv, "label": f"T{i+1} ₹{tv:.2f}", "color": "#00e096", "style": "dashed"})
            if scan_cmp:
                levels.append({"type": "cmp", "price": scan_cmp, "label": f"CMP ₹{scan_cmp:.2f}", "color": "#f0c060", "style": "dotted"})

            # Entry zone for longterm
            entry_zone = reco.get("entry_zone")
            if entry_zone and isinstance(entry_zone, list) and len(entry_zone) == 2:
                zones.append({
                    "top": float(entry_zone[1]),
                    "bottom": float(entry_zone[0]),
                    "zone_type": "ENTRY_ZONE",
                    "color": "rgba(41, 98, 255, 0.12)",
                    "border_color": "rgba(41, 98, 255, 0.4)",
                    "label": "Entry Zone",
                })
        except Exception as exc:
            log.warning("chart-data level build failed for %s: %s", symbol, exc)
            levels = []

    # 5. Build response
    response = {
        "symbol": symbol,
        "horizon": horizon,
        "candles": candles,
        "zones": zones,
        "levels": levels,
        "setup": reco.get("setup", "") if reco else "",
        "confidence": float(reco["confidence_score"]) if reco else 0,
        "reasoning": reco.get("reasoning", "") if reco else "",
    }
    _chart_cache[cache_key] = (_t.time(), response)
    return response


def _fetch_yfinance_ohlc(symbol: str, days: int = 180) -> list[dict]:
    """Fetch daily OHLC from yfinance, return lightweight-charts format."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        df = ticker.history(period=f"{days}d")
        if df.empty:
            return []
        import math
        candles = []
        for idx, row in df.iterrows():
            o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
            # Skip bars with NaN OHLC (e.g. a halted day or an incomplete bar);
            # a single bad bar must not nuke the whole chart.
            if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (o, h, l, c)):
                continue
            vol = row["Volume"]
            vol = 0 if (vol is None or (isinstance(vol, float) and math.isnan(vol))) else int(vol)
            day = idx.strftime("%Y-%m-%d")
            candles.append({
                # "time" → lightweight-charts (frontend); "date" → SMC detectors
                # (services.research_levels.daily_candles_to_weekly needs "date").
                "time": day,
                "date": day,
                "open": round(float(o), 2),
                "high": round(float(h), 2),
                "low": round(float(l), 2),
                "close": round(float(c), 2),
                "volume": vol,
            })
        return candles
    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", symbol, e)
        return []


def _detect_smc_zones(candles: list[dict]) -> list[dict]:
    """Run SMC zone detection (OB, FVG, structure) on daily candles."""
    try:
        from engine.swing import detect_daily_ob, detect_daily_fvg, detect_daily_structure, detect_weekly_trend
        from services.research_levels import daily_candles_to_weekly

        zones = []

        # Daily OB
        ob_long = detect_daily_ob(candles, "LONG")
        if ob_long:
            zones.append({
                "top": ob_long[1], "bottom": ob_long[0],
                "zone_type": "OB", "color": "rgba(0, 209, 140, 0.10)",
                "border_color": "rgba(0, 209, 140, 0.5)", "label": "Order Block (Demand)",
            })

        ob_short = detect_daily_ob(candles, "SHORT")
        if ob_short:
            zones.append({
                "top": ob_short[1], "bottom": ob_short[0],
                "zone_type": "OB", "color": "rgba(255, 71, 87, 0.10)",
                "border_color": "rgba(255, 71, 87, 0.5)", "label": "Order Block (Supply)",
            })

        # Daily FVG
        fvg_long = detect_daily_fvg(candles, "LONG")
        if fvg_long:
            zones.append({
                "top": fvg_long[1], "bottom": fvg_long[0],
                "zone_type": "FVG", "color": "rgba(91, 156, 246, 0.10)",
                "border_color": "rgba(91, 156, 246, 0.5)", "label": "FVG (Bullish)",
            })

        fvg_short = detect_daily_fvg(candles, "SHORT")
        if fvg_short:
            zones.append({
                "top": fvg_short[1], "bottom": fvg_short[0],
                "zone_type": "FVG", "color": "rgba(255, 152, 0, 0.10)",
                "border_color": "rgba(255, 152, 0, 0.5)", "label": "FVG (Bearish)",
            })

        # Daily structure
        ds, ds_info = detect_daily_structure(candles)
        if ds_info and ds_info.get("level"):
            zones.append({
                "top": ds_info["level"], "bottom": ds_info["level"],
                "zone_type": "STRUCTURE", "color": "rgba(240, 192, 96, 0.15)",
                "border_color": "#f0c060", "label": f"Structure ({ds})",
            })

        # Weekly zones (for longterm)
        weekly = daily_candles_to_weekly(candles)
        if len(weekly) >= 10:
            wt = detect_weekly_trend(weekly)
            if wt in ("BULLISH", "STRONG_BULL"):
                w_ob = detect_daily_ob(weekly, "LONG")
                if w_ob:
                    zones.append({
                        "top": w_ob[1], "bottom": w_ob[0],
                        "zone_type": "WEEKLY_OB", "color": "rgba(0, 209, 140, 0.06)",
                        "border_color": "rgba(0, 209, 140, 0.3)", "label": "Weekly OB (Demand)",
                    })
                w_fvg = detect_daily_fvg(weekly, "LONG")
                if w_fvg:
                    zones.append({
                        "top": w_fvg[1], "bottom": w_fvg[0],
                        "zone_type": "WEEKLY_FVG", "color": "rgba(91, 156, 246, 0.06)",
                        "border_color": "rgba(91, 156, 246, 0.3)", "label": "Weekly FVG (Bullish)",
                    })

        return zones
    except Exception as e:
        log.warning("SMC zone detection failed: %s", e)
        return []


@router.post("/api/research/lead")
@router.post("/research/lead")
def post_research_email_lead(payload: dict = Body(...)):
    """Store email for future daily-ideas / digest notifications (monetization capture)."""
    init_research_leads_table()
    raw = str((payload or {}).get("email", "")).strip().lower()
    if len(raw) < 5 or "@" not in raw or "." not in raw.split("@", 1)[-1] or len(raw) > 120:
        raise HTTPException(status_code=400, detail="Invalid email")
    try:
        conn = get_connection()
        conn.execute("INSERT OR IGNORE INTO research_email_leads (email) VALUES (?)", (raw,))
        conn.commit()
    except Exception as exc:
        log.warning("research lead insert failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save") from exc
    return {"ok": True}
