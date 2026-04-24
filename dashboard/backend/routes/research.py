"""
dashboard/backend/routes/research.py
Research Center APIs for swing ideas, long-term ideas, and running trades.
"""

import logging
import os
import threading
import traceback
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query

from dashboard.backend.db import get_connection, get_ranking_runs, get_stock_recommendations, list_running_trades
from services.universe_manager import load_nse_universe
from services.price_resolver import resolve_cmp

router = APIRouter(tags=["research"])
log = logging.getLogger("dashboard.research")

# Track in-flight auto-scans to avoid duplicate triggers
_auto_scan_lock = threading.Lock()
_auto_scan_running: set[str] = set()

# Observable scan diagnostics — surfaced via GET /api/research/scan-status
_scan_history: dict[str, dict] = {}  # horizon -> latest scan result info

STALE_THRESHOLD_HOURS = 12  # auto-trigger scan if last run older than this
MAX_LONGTERM_SLOTS = int(os.getenv("MAX_LONGTERM_SLOTS", "10"))


def _utc_iso(s: str | None) -> str | None:
    """
    Normalize a SQLite datetime('now') string to an ISO-8601 UTC string with
    explicit "Z" suffix so JavaScript's Date constructor parses it correctly.
    SQLite's datetime('now') returns naive UTC like '2026-04-23 03:00:00';
    without a timezone marker, the browser would mis-interpret it as local time.
    """
    if not s:
        return s
    try:
        s = str(s).strip()
        if not s:
            return None
        # already has timezone info
        if s.endswith("Z") or "+" in s[10:] or s.endswith("+00:00"):
            return s
        # SQLite uses space separator; normalize to ISO 'T'
        s = s.replace(" ", "T", 1)
        return s + "Z"
    except Exception:
        return s


def _extract_fundamentals(row: dict) -> dict:
    """Pull structured fundamental metrics from the fundamental_factors JSON blob.

    Returns a flat dict of nullable floats that the frontend can render as columns
    (PE, ROE, ROCE, revenue growth, D/E, market cap, promoter %).
    """
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

    return {
        "pe_ratio": _f("raw_pe"),
        "roe_pct": _f("raw_roe_pct"),
        "roce_pct": _f("raw_roce_pct"),
        "revenue_growth_pct": _f("raw_revenue_growth_pct"),
        "debt_equity": _f("raw_debt_equity"),
        "market_cap_cr": _f("raw_market_cap_cr"),
        "promoter_pct": _f("raw_promoter_pct"),
    }


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
                _scan_history[horizon] = {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "error": str(out["error"]),
                    "agent": agent_name,
                    "trigger": "auto",
                }
            else:
                summary = out.get("summary", str(out)) if isinstance(out, dict) else str(out)
                log.info("[auto-scan %s] finished: %s", horizon, summary)
                _scan_history[horizon] = {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "summary": summary,
                    "agent": agent_name,
                    "trigger": "auto",
                }
        except Exception as exc:
            log.exception("[auto-scan %s] failed", horizon)
            _scan_history[horizon] = {
                "status": "exception",
                "started_at": started_at,
                "finished_at": datetime.utcnow().isoformat() + "Z",
                "error": f"{exc.__class__.__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
                "agent": agent_name,
                "trigger": "auto",
            }
        finally:
            with _auto_scan_lock:
                _auto_scan_running.discard(horizon)

    threading.Thread(target=_job, daemon=True, name=f"auto_scan_{horizon}").start()
    log.info("[auto-scan %s] triggered — data is stale (>%dh)", horizon, STALE_THRESHOLD_HOURS)


def _swing_payload(limit: int) -> dict:
    rows = get_stock_recommendations("SWING", limit=limit)
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

        items.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "setup": row.get("setup") or "SWING",
                "sector": row.get("sector"),
                "target_source": row.get("target_source"),
                "entry_price": entry,
                "stop_loss": stop,
                "target_1": target_1,
                "target_2": target_2,
                "risk_reward": round(rr, 2),
                "confidence_score": float(row.get("confidence_score", 0)),
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

        items.append(
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "setup": row.get("setup") or "LONGTERM",
                "sector": row.get("sector"),
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
                "confidence_score": float(row.get("confidence_score", 0)),
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
    # Slot status — surfaces why long-term scan may not have produced new ideas.
    occupied = len(rows)
    slot_status = {
        "occupied": occupied,
        "max": MAX_LONGTERM_SLOTS,
        "slots_full": occupied >= MAX_LONGTERM_SLOTS,
    }
    return {
        "items": items,
        "count": len(items),
        "last_scan_time": _utc_iso(last_scan),
        "slot_status": slot_status,
    }


def _running_trades_payload(limit: int) -> dict:
    rows = list_running_trades(limit=limit, active_only=True)

    # ── Refresh CMP via the central resolver so research + running trades
    # never disagree on price for the same symbol. Falls back to the stored
    # current_price (last daemon update) if no live source is available. ──
    stored_cmp_map: dict[str, float] = {}
    for r in rows:
        try:
            stored_cmp_map[r["symbol"]] = float(r["current_price"])
        except (TypeError, ValueError, KeyError):
            pass
    cmp_resolved = resolve_cmp([r["symbol"] for r in rows], scan_cmp_map=stored_cmp_map)

    items: list[dict] = []
    for row in rows:
        targets = [float(t) for t in row.get("targets", [])]
        entry = float(row["entry_price"])
        sym = row["symbol"]
        live = cmp_resolved.get(sym)
        if live:
            current = float(live["price"])
            cmp_source = live["source"]
            cmp_age_sec = int(live["age_sec"])
        else:
            current = float(row["current_price"])
            cmp_source = "db_snapshot"
            cmp_age_sec = None
        stop = float(row["stop_loss"])
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
                "id": row["id"],
                "symbol": row["symbol"],
                "entry_price": entry,
                "current_price": current,
                "cmp_source": cmp_source,
                "cmp_age_sec": cmp_age_sec,
                "stop_loss": stop,
                "targets": targets,
                "profit_loss": round(current - entry, 2),
                "profit_loss_pct": round((current - entry) / entry * 100, 2) if entry else 0.0,
                "drawdown": float(row.get("drawdown", 0)),
                "drawdown_pct": float(row.get("drawdown_pct", 0)),
                "high_since_entry": row.get("high_since_entry"),
                "low_since_entry": row.get("low_since_entry"),
                "days_held": int(row.get("days_held", 0)),
                "distance_to_target": row.get("distance_to_target"),
                "distance_to_stop_loss": row.get("distance_to_stop_loss"),
                "status": row.get("status", "RUNNING"),
                "progress": round(progress, 4),
                "progress_color": color,
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    return {"items": items, "count": len(items)}


@router.get("/api/research/swing")
@router.get("/research/swing")
def get_swing_research(limit: int = Query(10, ge=1, le=100)):
    _maybe_auto_scan("SWING")
    return _swing_payload(limit)


@router.get("/api/research/longterm")
@router.get("/research/longterm")
def get_longterm_research(limit: int = Query(10, ge=1, le=100)):
    _maybe_auto_scan("LONGTERM")
    return _longterm_payload(limit)


@router.get("/api/research/running-trades")
@router.get("/research/running-trades")
def get_running_trades(limit: int = Query(40, ge=1, le=200)):
    return _running_trades_payload(limit)


@router.get("/api/research/coverage")
@router.get("/research/coverage")
def get_research_coverage(target_universe: int = Query(1800, ge=100, le=5000)):
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

    return {
        "target_universe": target_universe,
        "available_universe": universe.actual_size,
        "sources": universe.sources,
        "latest": {
            "SWING": _shape(swing_latest[0] if swing_latest else None),
            "LONGTERM": _shape(long_latest[0] if long_latest else None),
        },
    }


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

    _scan_history[horizon] = {
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "error": None,
        "agent": agent_name,
        "trigger": "manual",
    }

    def _job() -> None:
        try:
            out = run_agent_now(agent_name)
            if isinstance(out, dict) and out.get("error"):
                log.error("[%s] background scan error: %s", agent_name, out["error"])
                _scan_history[horizon] = {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "error": str(out["error"]),
                    "agent": agent_name,
                    "trigger": "manual",
                }
            else:
                summary = out.get("summary", str(out)) if isinstance(out, dict) else str(out)
                log.info("[%s] background scan finished: %s", agent_name, summary)
                _scan_history[horizon] = {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat() + "Z",
                    "summary": summary,
                    "agent": agent_name,
                    "trigger": "manual",
                }
        except Exception as exc:
            log.exception("[%s] background scan failed", agent_name)
            _scan_history[horizon] = {
                "status": "exception",
                "started_at": started_at,
                "finished_at": datetime.utcnow().isoformat() + "Z",
                "error": f"{exc.__class__.__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
                "agent": agent_name,
                "trigger": "manual",
            }

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


@router.get("/api/research/scan-status")
@router.get("/research/scan-status")
def get_scan_status():
    """Return the current state of research scans (in-flight + latest result per horizon).

    This endpoint makes auto-scan failures observable. If a scan keeps failing silently
    the frontend (or an operator) can inspect the error and traceback here.
    """
    with _auto_scan_lock:
        in_flight = sorted(_auto_scan_running)
    return {
        "in_flight": in_flight,
        "horizons": {
            h: {k: v for k, v in info.items() if k != "traceback"}
            for h, info in _scan_history.items()
        },
        "debug": {
            h: info.get("traceback")
            for h, info in _scan_history.items()
            if info.get("traceback")
        },
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
    from dashboard.backend.db import list_running_trades
    rows = list_running_trades(limit=limit, active_only=False)
    items = []
    for row in rows:
        targets = [float(t) for t in row.get("targets", [])]
        items.append({
            "id": row["id"],
            "symbol": row["symbol"],
            "entry_price": float(row["entry_price"]),
            "current_price": float(row["current_price"]),
            "stop_loss": float(row["stop_loss"]),
            "targets": targets,
            "profit_loss": float(row.get("profit_loss", 0)),
            "profit_loss_pct": float(row.get("profit_loss_pct", 0)),
            "drawdown_pct": float(row.get("drawdown_pct", 0)),
            "high_since_entry": row.get("high_since_entry"),
            "low_since_entry": row.get("low_since_entry"),
            "days_held": int(row.get("days_held", 0)),
            "status": row.get("status", "RUNNING"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        })
    return {"items": items, "count": len(items)}


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

        return {
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

    # 1. Fetch recommendation from DB
    reco = None
    rows = get_stock_recommendations(horizon, limit=50)
    for r in rows:
        if r["symbol"].upper().replace("NSE:", "") == symbol:
            reco = r
            break

    # 2. Fetch daily OHLC via yfinance
    candles = _fetch_yfinance_ohlc(symbol, days=180)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No OHLC data for {symbol}")

    # 3. Detect SMC zones
    zones = _detect_smc_zones(candles)

    # 4. Build trade levels from recommendation
    levels = []
    if reco:
        entry = float(reco["entry_price"])
        sl = float(reco["stop_loss"]) if reco.get("stop_loss") else None
        targets = reco.get("targets", [])
        scan_cmp = float(reco["scan_cmp"]) if reco.get("scan_cmp") else None
        entry_type = reco.get("entry_type", "MARKET")
        setup = reco.get("setup", "")

        levels.append({"type": "entry", "price": entry, "label": f"Entry ₹{entry:.2f}", "color": "#2962ff",
                        "style": "solid", "entry_type": entry_type})
        if sl:
            levels.append({"type": "sl", "price": sl, "label": f"SL ₹{sl:.2f}", "color": "#ff4757", "style": "dashed"})
        for i, t in enumerate(targets):
            tv = float(t)
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
        candles = []
        for idx, row in df.iterrows():
            candles.append({
                "time": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
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
