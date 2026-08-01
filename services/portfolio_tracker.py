"""
services/portfolio_tracker.py

Thin runner for the shared PositionTrackingService (PR-A refactor). The
per-position tracking logic (CMP → P&L → SL/Target/Structure exit → journal)
now lives in services/position_tracking_service.py and runs against pluggable
stores (services/position_stores.py), so it can be reused for the per-user
book without duplication. This module keeps:
  - the daemon loop + cadence (2min market / 15min off-hours)
  - arm-on-tap promotion of Final Trade Ideas (system portfolio)
Behavior is unchanged from before the refactor for the system portfolio.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("services.portfolio_tracker")

_tracker_thread: threading.Thread | None = None

# One shared engine instance; PR-A registers only the system store. The
# per-user store plugs into this same list in PR-B (no engine change).
from services.position_stores import PortfolioPositionStore, UserPositionStore  # noqa: E402
from services.position_tracking_service import PositionTrackingService  # noqa: E402

# One shared engine, two stores: the system book + the per-user book. Same
# tracking logic for both (UserPositionStore is flag-gated via
# USER_POSITION_TRACKING and maps its own status vocabulary).
_service = PositionTrackingService([PortfolioPositionStore(), UserPositionStore()])


def _update_portfolio_prices() -> int:
    """Back-compat shim — drives the shared engine. Kept so existing callers
    (routes/portfolio.py /refresh-prices) work unchanged."""
    return _service.tick()


# ── Arm-on-tap trigger / expiry config ───────────────────────────────────────
# A PENDING (armed) idea becomes ACTIVE only when its planned entry is genuinely
# traded through. It expires (never enters) if it times out or the setup runs
# away, so the slot is freed for a real candidate.
_PENDING_MAX_DAYS = int(os.getenv("PORTFOLIO_PENDING_MAX_DAYS", "7"))        # arm validity window (calendar days)
_PENDING_RUNAWAY_PCT = float(os.getenv("PORTFOLIO_PENDING_RUNAWAY_PCT", "15.0"))  # setup ran away → expire
# Maximum distance CMP may sit BEYOND the planned entry (in the profitable
# direction) and still count as a genuine tap. Beyond this the move has already
# happened: filling at the planned entry would book a gain that was never
# earned, since the position is recorded at a price that is no longer available.
# NAZARA filled at ₹290.95 while trading at ₹314.85 (+8.2% beyond) and instantly
# re-hit target — a phantom +8.63%. Past this threshold the arm is retired.
_PENDING_MAX_SLIP_PCT = float(os.getenv("PORTFOLIO_PENDING_MAX_SLIP_PCT", "2.0"))

# Lifecycle-ledger resync cadence, in tracker cycles.
_LC_EVERY_N_CYCLES = max(int(os.getenv("LIFECYCLE_SYNC_EVERY_N_CYCLES", "5")), 1)
_lc_cycle = 0


class _SkipLifecycle(Exception):
    """Control-flow marker: this cycle is not a ledger-sync cycle."""


def _arm_and_expire_pending() -> int:
    """Advance every armed (PENDING) system-portfolio idea:

      - TRIGGER → ACTIVE when the planned entry is genuinely traded through
        (pullback: CMP falls to/through entry; breakout: CMP rises to/through it).
        Direction is decided by arm_ref_price (the CMP captured at arm time).
      - EXPIRE (retire, never a trade) when it times out (> _PENDING_MAX_DAYS) or
        runs so far from entry a fill is implausible.

    Runs every tracker cycle. Best-effort; never raises into the loop."""
    from datetime import datetime, timezone, timedelta
    try:
        from dashboard.backend.db.portfolio import (
            get_pending_positions, activate_pending_position, expire_pending_position,
        )
        pend = get_pending_positions()
        if not pend:
            return 0
        from services.trade_tracker import _fetch_cmp_batch
        from services.portfolio_manager import send_portfolio_triggered_alert

        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist)
        prices = _fetch_cmp_batch(list({p["symbol"] for p in pend})) or {}
        triggered = expired = 0

        for p in pend:
            pid = int(p["id"])
            entry = float(p["entry_price"])
            arm_ref = float(p.get("arm_ref_price") or p.get("current_price") or entry)
            cmp = prices.get(p["symbol"])

            # Expiry by age (independent of a live price).
            try:
                created = str(p.get("created_at") or "").replace(" ", "T")
                cdt = datetime.fromisoformat(created)
                if cdt.tzinfo is None:
                    cdt = cdt.replace(tzinfo=ist)
                age_days = (now - cdt).days
            except Exception:
                age_days = 0
            if age_days > _PENDING_MAX_DAYS:
                if expire_pending_position(pid, "EXPIRED_TIMEOUT"):
                    expired += 1
                continue

            if cmp is None or entry <= 0:
                continue

            is_pullback = arm_ref >= entry
            # Genuine tap: price traded through the planned entry in the right dir.
            tapped = (cmp <= entry) if is_pullback else (cmp >= entry)

            # STALE-FILL GUARD. A "tap" only counts while price is still AT the
            # level. If CMP has already run past the entry in the profitable
            # direction by more than _PENDING_MAX_SLIP_PCT, the move is over —
            # filling at the planned entry would fabricate the gap as profit the
            # instant the row is created, and can immediately register as a
            # target hit. Retire the arm instead of manufacturing a fill.
            if tapped and _PENDING_MAX_SLIP_PCT > 0:
                slip_pct = ((entry - cmp) / entry * 100.0) if is_pullback \
                    else ((cmp - entry) / entry * 100.0)
                if slip_pct > _PENDING_MAX_SLIP_PCT:
                    log.warning(
                        "[ArmOnTap] %s stale fill refused — CMP %.2f is %.2f%% beyond entry %.2f "
                        "(max %.2f%%); expiring instead of booking an unearned gap",
                        p["symbol"], cmp, slip_pct, entry, _PENDING_MAX_SLIP_PCT,
                    )
                    if expire_pending_position(pid, "EXPIRED_STALE_FILL"):
                        expired += 1
                    continue

            if tapped:
                # Fill at the planned entry (the level just traded through) so P&L
                # starts at zero — no fabricated gain. Strategy entry unchanged.
                if activate_pending_position(pid, trigger_price=entry, entered_at=now.isoformat()):
                    triggered += 1
                    try:
                        send_portfolio_triggered_alert(
                            p["symbol"], p["horizon"], entry, cmp,
                            float(p["stop_loss"]), p.get("target_1"),
                        )
                    except Exception:
                        pass
                continue

            # Runaway: moved the WRONG way past a plausible fill → retire.
            runaway = (
                cmp > entry * (1 + _PENDING_RUNAWAY_PCT / 100.0) if is_pullback
                else cmp < entry * (1 - _PENDING_RUNAWAY_PCT / 100.0)
            )
            if runaway:
                if expire_pending_position(pid, "EXPIRED_RUNAWAY"):
                    expired += 1

        if triggered or expired:
            log.info("[ArmOnTap] pending: %d triggered→active, %d expired", triggered, expired)
        return triggered
    except Exception:
        log.exception("[ArmOnTap] pending processing failed")
        return 0


def _promote_final_ideas_on_tap() -> int:
    """Arm Final Trade Ideas into the SYSTEM portfolio as PENDING (awaiting
    entry). The actual fill happens later in _arm_and_expire_pending when the
    planned entry is genuinely traded through — this function only ARMS, it never
    creates a live position. Flag-gated + best-effort; never raises into the loop.

    A "slot" is held by an ACTIVE or PENDING position, so room is computed against
    committed slots (`*_used`) — the book never commits past MAX."""
    if os.getenv("PORTFOLIO_SOURCE_FINAL_IDEAS", "1").strip().lower() not in {"1", "true", "yes"}:
        return 0
    try:
        from services.trade_tracker import _is_market_hours
        if not _is_market_hours():
            return 0
        from services.idea_selector import select_from_final_ideas
        from services.portfolio_manager import promote_to_portfolio
        from dashboard.backend.db.portfolio import get_portfolio_counts

        promoted = 0
        for horizon in ("SWING", "LONGTERM"):
            counts = get_portfolio_counts()
            used = counts.get(f"{horizon.lower()}_used", counts.get(horizon.lower(), 0))
            room = max(0, counts.get(f"{horizon.lower()}_max", 20) - used)
            if room <= 0:
                continue
            for idea in select_from_final_ideas(horizon, max_picks=room):
                try:
                    promote_to_portfolio(
                        symbol=idea["symbol"], horizon=idea["horizon"],
                        entry_price=idea["entry_price"], stop_loss=idea["stop_loss"],
                        target_1=idea.get("target_1"), target_2=idea.get("target_2"),
                        confidence_score=idea.get("confidence_score", 0),
                        reasoning=idea.get("reasoning", ""),
                        recommendation_id=idea.get("recommendation_id"),
                        current_price=idea.get("scan_cmp"),
                        pending=True,   # ARM only — never an instant live fill
                    )
                    promoted += 1
                    log.info("[ArmOnTap] armed %s into %s portfolio (awaiting entry)", idea["symbol"], horizon)
                except ValueError:
                    pass  # full / already committed
        return promoted
    except Exception:
        log.exception("[ArmOnTap] arm tick failed")
        return 0


def _sync_engine_trades() -> int:
    """Mirror the engine's live running_trades into the system portfolio EVERY
    cycle (not just at web boot) and fire an instant Telegram entry alert for
    each newly-seeded position — so an engine trade taken intraday shows on the
    site + notifies within one tracker cycle instead of at the next restart.

    Flag-gated PORTFOLIO_LIVE_SYNC_ENABLED (default on). Best-effort; the boot
    seed stays silent, so only trades taken AFTER boot ever alert here. Alerts
    are inherently deduped: the seed skips symbols already committed, so a given
    entry is seeded — and alerted — exactly once."""
    if os.getenv("PORTFOLIO_LIVE_SYNC_ENABLED", "1").strip().lower() not in {"1", "true", "yes"}:
        return 0
    try:
        from dashboard.backend.db.portfolio import seed_portfolio_from_recommendations
        from services.portfolio_manager import send_portfolio_triggered_alert

        new_rows = seed_portfolio_from_recommendations()
        for r in new_rows:
            try:
                send_portfolio_triggered_alert(
                    r["symbol"], r["horizon"], float(r["entry_price"]),
                    float(r.get("current_price") or r["entry_price"]),
                    float(r["stop_loss"]), r.get("target_1"),
                )
            except Exception:
                log.warning("[LiveSync] entry alert failed for %s (best-effort)", r.get("symbol"))
        if new_rows:
            log.info("[LiveSync] mirrored %d engine trade(s) into portfolio + alerted", len(new_rows))
        return len(new_rows)
    except Exception:
        log.exception("[LiveSync] engine-trade sync failed")
        return 0


def _portfolio_tracker_loop() -> None:
    from services.trade_tracker import _current_interval
    log.info("Portfolio tracker started")
    while True:
        try:
            _update_portfolio_prices()
        except Exception:
            log.exception("Portfolio tracker loop error")
        try:
            # 1) advance armed ideas: trigger those whose entry just traded
            #    through → ACTIVE; retire timed-out / run-away ones.
            _arm_and_expire_pending()
        except Exception:
            log.exception("Portfolio tracker: pending trigger error")
        try:
            # 2) arm new Final Trade Ideas into freed/empty slots (as PENDING).
            _promote_final_ideas_on_tap()
        except Exception:
            log.exception("Portfolio tracker: arm-on-tap error")
        try:
            # 3) mirror engine live trades into the portfolio in real-time and
            #    fire an instant Telegram entry alert for each new one.
            _sync_engine_trades()
        except Exception:
            log.exception("Portfolio tracker: engine-trade live-sync error")
        try:
            # Watchlist entry-trigger monitor (flag-gated WATCHLIST_MONITOR_ENABLED;
            # inert until enabled). Same loop, no separate scheduler/engine.
            from services.entry_trigger_service import EntryTriggerService
            res = EntryTriggerService().tick()
            if res.get("status") == "ok" and (res.get("triggered") or res.get("promoted")):
                log.info("[WatchlistTrigger] %s", res)
        except Exception:
            log.exception("Portfolio tracker: watchlist-trigger error")
        try:
            # 4) Re-sync the canonical lifecycle ledger that Track Record reads.
            #
            # Driven from this loop rather than wired into every call site: the
            # backfill upserts on deterministic UUIDs, so it is idempotent and
            # picks up state changes from EVERY source (swing, long-term,
            # momentum, research) in one pass. That keeps Track Record current
            # without each module having to remember to write, which is exactly
            # the kind of "one writer forgot" gap that let the books and the
            # published record drift apart in the first place.
            # Throttled: the ledger sweep touches every row and takes a write
            # lock on a database the web service reads on every request. Running
            # it on each tick added contention for no benefit — a few minutes of
            # staleness on a history view costs nothing.
            global _lc_cycle
            _lc_cycle += 1
            if _lc_cycle % _LC_EVERY_N_CYCLES != 0:
                raise _SkipLifecycle()
            from dashboard.backend.db.trade_lifecycle_migrate import backfill as _lc_backfill
            _lc_backfill()
            # Roll up analytics once per cycle so the trend endpoints read a
            # stored row instead of recomputing the whole history per request.
            # Attach new positions to the research idea that produced them, so
            # cross-engine conversion stays answerable as the book turns over.
            from dashboard.backend.db.lifecycle_chain import link_chains as _lc_link
            _lc_link()
            from dashboard.backend.db.lifecycle_analytics import snapshot_stats as _lc_snap
            _lc_snap(periods=("DAILY",), portfolios=("ALL", "SWING", "LONGTERM", "MOMENTUM"))
            # Attach entry/exit charts to a few rows per cycle. Deliberately
            # small: each row costs a broker call, and the ledger is complete
            # without charts — they enrich the detail page, they don't gate it.
            from dashboard.backend.db.lifecycle_capture import capture_missing_charts as _lc_charts
            _lc_charts(limit=int(os.getenv("LIFECYCLE_CHART_CAPTURE_PER_CYCLE", "5")))
        except _SkipLifecycle:
            pass
        except Exception:
            log.exception("Portfolio tracker: lifecycle ledger sync error")
        interval = _current_interval()
        time.sleep(interval)


def start_portfolio_tracker() -> None:
    """Start the background portfolio price tracker. Call once from main.py startup."""
    global _tracker_thread
    if _tracker_thread is not None and _tracker_thread.is_alive():
        return
    _tracker_thread = threading.Thread(
        target=_portfolio_tracker_loop, daemon=True, name="portfolio-tracker"
    )
    _tracker_thread.start()
    log.info("Portfolio tracker thread launched")
