"""
services/portfolio_tracker.py

Dedicated price tracker for portfolio_positions.
Reuses the hybrid Kite+yfinance price fetcher from trade_tracker.
Runs as a daemon thread — updates every 2min (market) / 15min (off-hours).

Auto-resolves: TARGET_HIT when CMP >= target_2 (or target_1 if no target_2).
              STOP_HIT when CMP <= stop_loss.
On exit: journals the trade + sends Telegram alert.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime

log = logging.getLogger("services.portfolio_tracker")

_tracker_thread: threading.Thread | None = None

# ── PR-3 structure-break exit (flag-gated, default OFF — opt in after review) ──
_STRUCTURE_EXIT_ON = os.getenv("PORTFOLIO_STRUCTURE_EXIT", "0").strip().lower() in {"1", "true", "yes"}
_STRUCTURE_EXIT_MIN_DAYS = int(os.getenv("PORTFOLIO_STRUCTURE_EXIT_MIN_DAYS", "3"))
_STRUCTURE_EXIT_BUFFER = float(os.getenv("PORTFOLIO_STRUCTURE_EXIT_BUFFER", "0.02"))  # 2% below 200DMA
# per-day 200-DMA cache {symbol: (day, dma)}
_dma_cache: dict[str, tuple[str, float | None]] = {}


def _get_200dma(symbol: str) -> float | None:
    """Best-effort 200-day SMA from Kite daily bars, cached once per IST day.
    Returns None on any failure (caller then skips the structure check)."""
    today = date.today().isoformat()
    hit = _dma_cache.get(symbol)
    if hit and hit[0] == today:
        return hit[1]
    dma: float | None = None
    try:
        from datetime import timedelta
        from services.fvg_tap_engine import _get_fvg_tap_kite_client
        kite = _get_fvg_tap_kite_client()
        if kite is not None:
            d = kite.ltp(symbol)
            tok = int(list(d.values())[0]["instrument_token"]) if d else None
            if tok:
                to_dt = datetime.now()
                bars = kite.historical_data(tok, to_dt - timedelta(days=320), to_dt, "day")
                closes = [float(b["close"]) for b in bars if b.get("close")]
                if len(closes) >= 200:
                    dma = sum(closes[-200:]) / 200.0
    except Exception as exc:
        log.debug("200DMA fetch failed for %s: %s", symbol, exc)
    _dma_cache[symbol] = (today, dma)
    return dma


def _promote_final_ideas_on_tap() -> int:
    """Arm-on-tap: during market hours, promote any Final Trade Idea now trading
    in its entry zone (CMP-buy). The Final Trade Ideas feed IS the armed set —
    select_from_final_ideas re-checks live CMP each cycle, so a name that was
    "waiting" (above entry) earlier gets promoted the moment price pulls back
    into the ±band. Flag-gated + best-effort; never raises into the loop."""
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
            room = max(0, counts.get(f"{horizon.lower()}_max", 20) - counts.get(horizon.lower(), 0))
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
                    )
                    promoted += 1
                    log.info("[ArmOnTap] promoted %s into %s portfolio", idea["symbol"], horizon)
                except ValueError:
                    pass  # full / already held
        return promoted
    except Exception:
        log.exception("[ArmOnTap] tick failed")
        return 0


def _update_portfolio_prices() -> int:
    """Fetch live prices and update all ACTIVE portfolio positions. Returns count updated."""
    from dashboard.backend.db.portfolio import get_portfolio, update_position_price
    from services.trade_tracker import _fetch_cmp_batch, _is_market_hours

    positions = get_portfolio(include_closed=False)
    if not positions:
        return 0

    symbols = list({p["symbol"] for p in positions})
    prices = _fetch_cmp_batch(symbols)

    updated = 0
    today = date.today()

    for pos in positions:
        sym = pos["symbol"]
        cmp = prices.get(sym)
        if cmp is None:
            continue

        entry = float(pos["entry_price"])
        sl = float(pos["stop_loss"])
        t1 = float(pos["target_1"]) if pos.get("target_1") else None
        t2 = float(pos["target_2"]) if pos.get("target_2") else None
        max_target = t2 or t1 or entry * 1.20

        pl = round(cmp - entry, 2)
        pl_pct = round((cmp - entry) / entry * 100, 2) if entry else 0.0
        dd = round(min(pl, 0.0), 2)
        dd_pct = round(min(pl_pct, 0.0), 2)

        # Days held
        try:
            created = datetime.fromisoformat(pos["created_at"]).date()
            days_held = (today - created).days
        except Exception:
            days_held = int(pos.get("days_held", 0))

        # Track high/low since entry
        prev_high = float(pos.get("high_since_entry") or cmp)
        prev_low = float(pos.get("low_since_entry") or cmp)
        high_since = max(prev_high, cmp)
        low_since = min(prev_low, cmp)

        # Auto-resolve status
        old_status = pos.get("status", "ACTIVE")
        new_status = "ACTIVE"
        exit_reason = None

        if cmp >= max_target:
            new_status = "TARGET_HIT"
            exit_reason = "TARGET_HIT"
        elif cmp <= sl:
            new_status = "STOP_HIT"
            exit_reason = "STOP_HIT"
        elif _STRUCTURE_EXIT_ON and days_held >= _STRUCTURE_EXIT_MIN_DAYS and cmp < entry:
            # PR-3 structure-break cull (flag-gated, default OFF): exit a LOSING
            # hold whose trend has broken — CMP below the 200-DMA by a buffer —
            # rather than wait for the hard stop. Frees a slot from dead capital
            # (e.g. a name that drifts down for weeks without hitting SL). The
            # loss + min-held guards avoid whipsawing out of healthy positions.
            dma200 = _get_200dma(sym)
            if dma200 and cmp < dma200 * (1.0 - _STRUCTURE_EXIT_BUFFER):
                new_status = "CLOSED"
                exit_reason = "STRUCTURE_BREAK"

        update_position_price(
            pos["id"],
            current_price=cmp,
            profit_loss=pl,
            profit_loss_pct=pl_pct,
            drawdown=dd,
            drawdown_pct=dd_pct,
            high_since_entry=high_since,
            low_since_entry=low_since,
            days_held=days_held,
            status=new_status,
        )

        # On SL/Target hit: close and journal
        if new_status != "ACTIVE" and old_status == "ACTIVE":
            try:
                from services.portfolio_manager import close_portfolio_position
                close_portfolio_position(pos["id"], cmp, exit_reason)
                log.info("Portfolio auto-close: %s %s at %.2f (PL: %.2f%%)",
                         sym, exit_reason, cmp, pl_pct)
            except Exception:
                log.exception("Failed to auto-close %s", sym)

        updated += 1

    if updated:
        log.debug("Portfolio tracker: updated %d positions", updated)
    return updated


def _portfolio_tracker_loop() -> None:
    from services.trade_tracker import _current_interval
    log.info("Portfolio tracker started")
    while True:
        try:
            _update_portfolio_prices()
        except Exception:
            log.exception("Portfolio tracker loop error")
        try:
            _promote_final_ideas_on_tap()
        except Exception:
            log.exception("Portfolio tracker: arm-on-tap error")
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
