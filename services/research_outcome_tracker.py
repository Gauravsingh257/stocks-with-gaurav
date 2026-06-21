"""
services/research_outcome_tracker.py

Phase 2 - Recommendation lifecycle / outcome tracker.

Every ACTIVE SWING/LONGTERM recommendation is evaluated against live CMP:
    CMP <= stop_loss   -> STOP_HIT
    CMP >= max target  -> TARGET_HIT   (long)
(short geometry inverted automatically from entry/target/stop relationship).

On a terminal outcome we persist exit_price, exit_date, exit_reason, pnl_pct
and pnl_r onto the recommendation row (see db.close_recommendation), and - when
anything changed - rebuild the swing/longterm Redis snapshot so the website
reflects the new status immediately (Phase 6).

Intentionally simple: NO trailing stops, NO partial exits, NO advanced trade
management. One pass, one decision per recommendation.

Live price comes from the existing single source of truth, services.price_resolver
.resolve_cmp - no new market-data infrastructure is introduced.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta, timezone

log = logging.getLogger("services.research_outcome_tracker")

_IST = timezone(timedelta(hours=5, minutes=30))

# Public research market window (broader than the engine's 09:15–15:30 so the
# 09:00–16:00 IST requirement in the spec is honoured end to end).
MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(16, 0)


def is_research_market_hours(now: datetime | None = None) -> bool:
    """True Mon–Fri 09:00–16:00 IST."""
    now = now or datetime.now(_IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_IST)
    now = now.astimezone(_IST)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _evaluate_outcome(entry: float, stop: float, targets: list[float], cmp: float) -> tuple[str | None, float | None]:
    """Return (terminal_status, threshold_price) or (None, None) if still ACTIVE.

    threshold_price is the SL or target level that was breached - used purely for
    logging/reasoning; pnl is always computed from the actual CMP fill.
    """
    if not targets:
        return None, None
    max_t = max(targets)
    min_t = min(targets)
    # Direction inferred from geometry: long if the target sits above entry.
    is_long = max_t >= entry
    if is_long:
        if stop and cmp <= stop:
            return "STOP_HIT", stop
        if cmp >= max_t:
            return "TARGET_HIT", max_t
    else:
        if stop and cmp >= stop:
            return "STOP_HIT", stop
        if cmp <= min_t:
            return "TARGET_HIT", min_t
    return None, None


def _compute_pnl(entry: float, stop: float, exit_px: float, is_long: bool) -> tuple[float, float]:
    """Return (pnl_pct, pnl_r). pnl_r normalises by the original 1R risk."""
    if entry <= 0:
        return 0.0, 0.0
    direction = 1.0 if is_long else -1.0
    pnl_pct = round(direction * (exit_px - entry) / entry * 100.0, 2)
    risk = abs(entry - stop) if stop else 0.0
    pnl_r = round(direction * (exit_px - entry) / risk, 3) if risk > 0 else 0.0
    return pnl_pct, pnl_r


def run_outcome_tracker_tick(*, force: bool = False, rebuild_snapshot: bool = True) -> dict:
    """Evaluate every ACTIVE SWING/LONGTERM recommendation once.

    Parameters
    ----------
    force : run even outside market hours (used by the one-time repair migration).
    rebuild_snapshot : refresh the swing/longterm Redis snapshot if any row changed.
    """
    if not force and not is_research_market_hours():
        return {"status": "skipped_off_hours", "evaluated": 0, "closed": 0}

    from dashboard.backend.db import get_recommendations_for_tracking, close_recommendation
    from services.price_resolver import resolve_cmp

    recs = get_recommendations_for_tracking()
    if not recs:
        return {"status": "ok", "evaluated": 0, "closed": 0, "target_hit": 0, "stop_hit": 0}

    scan_cmp_map: dict[str, float] = {}
    for r in recs:
        sc = r.get("scan_cmp")
        if sc is not None:
            try:
                scan_cmp_map[r["symbol"]] = float(sc)
            except (TypeError, ValueError):
                pass

    # use_yf=True so the migration/off-hours run still resolves a real close;
    # the live tick prefers ws_cache/Kite first (handled inside resolve_cmp).
    cmp_resolved = resolve_cmp([r["symbol"] for r in recs], scan_cmp_map=scan_cmp_map)

    closed = target_hit = stop_hit = evaluated = 0
    changed_horizons: set[str] = set()
    details: list[dict] = []

    for r in recs:
        live = cmp_resolved.get(r["symbol"])
        if not live:
            continue
        cmp = float(live["price"])
        # A pure scan_snapshot fallback is NOT a live price - skip so we never
        # close a trade on its own stale entry-time snapshot.
        if live.get("source") == "scan_snapshot":
            continue
        evaluated += 1

        try:
            entry = float(r["entry_price"])
            stop = float(r["stop_loss"]) if r.get("stop_loss") is not None else 0.0
            targets = [float(t) for t in (r.get("targets") or []) if t is not None]
        except (TypeError, ValueError):
            continue
        if r.get("agent_type") == "LONGTERM" and r.get("long_term_target"):
            try:
                targets = targets or [float(r["long_term_target"])]
            except (TypeError, ValueError):
                pass

        status, threshold = _evaluate_outcome(entry, stop, targets, cmp)
        if status is None:
            continue

        is_long = (max(targets) if targets else entry) >= entry
        pnl_pct, pnl_r = _compute_pnl(entry, stop, cmp, is_long)
        reason = f"{status} @ {cmp:.2f} (level {threshold:.2f}, src {live.get('source')})"
        try:
            ok = close_recommendation(
                int(r["id"]), status=status, exit_price=cmp,
                exit_reason=reason, pnl_pct=pnl_pct, pnl_r=pnl_r,
            )
        except Exception as exc:
            log.warning("close_recommendation failed id=%s: %s", r.get("id"), exc)
            continue
        if ok:
            closed += 1
            changed_horizons.add(r.get("agent_type"))
            if status == "TARGET_HIT":
                target_hit += 1
            else:
                stop_hit += 1
            details.append({"symbol": r["symbol"], "status": status,
                            "exit": round(cmp, 2), "pnl_pct": pnl_pct, "pnl_r": pnl_r})

    if rebuild_snapshot and changed_horizons:
        _rebuild_research_snapshots(changed_horizons)

    log.info("[outcome-tracker] evaluated=%d closed=%d (target=%d stop=%d)",
             evaluated, closed, target_hit, stop_hit)
    return {
        "status": "ok",
        "evaluated": evaluated,
        "closed": closed,
        "target_hit": target_hit,
        "stop_hit": stop_hit,
        "details": details,
    }


def _rebuild_research_snapshots(horizons: set[str]) -> None:
    """Rebuild swing/longterm Redis snapshots from DB so the UI reflects new
    statuses immediately after the tracker closes any positions (Phase 6)."""
    try:
        from dashboard.backend.routes.research import _swing_payload, _longterm_payload
        from dashboard.backend.redis_endpoint_cache import (
            finalize_endpoint, valid_research_list_payload,
        )
    except Exception as exc:
        log.debug("snapshot rebuild imports unavailable: %s", exc)
        return
    if "SWING" in horizons:
        try:
            finalize_endpoint("swing", _swing_payload(100), valid_research_list_payload)
        except Exception:
            log.exception("swing snapshot rebuild failed")
    if "LONGTERM" in horizons:
        try:
            finalize_endpoint("longterm", _longterm_payload(100), valid_research_list_payload)
        except Exception:
            log.exception("longterm snapshot rebuild failed")
