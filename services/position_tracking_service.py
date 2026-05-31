"""
services/position_tracking_service.py
=====================================
The single, store-agnostic position tracking engine. Runs the SAME logic
(live CMP → P&L → SL/Target/Structure exit → metrics persist → close+journal)
against any number of PositionStore adapters.

PR-A: extracted verbatim from portfolio_tracker._update_portfolio_prices with
ZERO business-logic changes — the system portfolio is tracked identically,
just routed through PortfolioPositionStore. Additional stores (per-user
user_positions) plug in later with no engine changes.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

from services.position_stores import PositionStore, TrackedPosition

log = logging.getLogger("services.position_tracking")

# ── Structure-break exit (flag-gated, default OFF) — moved verbatim from
#    portfolio_tracker; behavior unchanged. ───────────────────────────────
_STRUCTURE_EXIT_ON = os.getenv("PORTFOLIO_STRUCTURE_EXIT", "0").strip().lower() in {"1", "true", "yes"}
_STRUCTURE_EXIT_MIN_DAYS = int(os.getenv("PORTFOLIO_STRUCTURE_EXIT_MIN_DAYS", "3"))
_STRUCTURE_EXIT_BUFFER = float(os.getenv("PORTFOLIO_STRUCTURE_EXIT_BUFFER", "0.02"))
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


class PositionTrackingService:
    """One tracking engine, many stores. tick() updates every enabled store."""

    def __init__(self, stores: list[PositionStore]):
        self._stores = stores

    def tick(self) -> int:
        total = 0
        for store in self._stores:
            try:
                if not store.enabled():
                    continue
                total += self._tick_store(store)
            except Exception:
                log.exception("PositionTracking: store '%s' tick failed", getattr(store, "name", "?"))
        return total

    def _tick_store(self, store: PositionStore) -> int:
        from services.trade_tracker import _fetch_cmp_batch

        positions: list[TrackedPosition] = store.list_active()
        if not positions:
            return 0

        symbols = list({p.symbol for p in positions})
        prices = _fetch_cmp_batch(symbols)

        updated = 0
        today = date.today()

        for pos in positions:
            cmp = prices.get(pos.symbol)
            if cmp is None:
                continue

            entry = pos.entry
            sl = pos.stop_loss
            t1 = pos.target_1
            t2 = pos.target_2
            max_target = t2 or t1 or entry * 1.20

            pl = round(cmp - entry, 2)
            pl_pct = round((cmp - entry) / entry * 100, 2) if entry else 0.0
            dd = round(min(pl, 0.0), 2)
            dd_pct = round(min(pl_pct, 0.0), 2)

            # Days held
            try:
                created = datetime.fromisoformat(pos.created_at).date()
                days_held = (today - created).days
            except Exception:
                days_held = pos.days_held

            # Track high/low since entry
            prev_high = pos.high_since_entry if pos.high_since_entry is not None else cmp
            prev_low = pos.low_since_entry if pos.low_since_entry is not None else cmp
            high_since = max(prev_high, cmp)
            low_since = min(prev_low, cmp)

            # Auto-resolve status
            old_status = pos.status or "ACTIVE"
            new_status = "ACTIVE"
            exit_reason = None

            if cmp >= max_target:
                new_status = "TARGET_HIT"
                exit_reason = "TARGET_HIT"
            elif cmp <= sl:
                new_status = "STOP_HIT"
                exit_reason = "STOP_HIT"
            elif _STRUCTURE_EXIT_ON and days_held >= _STRUCTURE_EXIT_MIN_DAYS and cmp < entry:
                dma200 = _get_200dma(pos.symbol)
                if dma200 and cmp < dma200 * (1.0 - _STRUCTURE_EXIT_BUFFER):
                    new_status = "CLOSED"
                    exit_reason = "STRUCTURE_BREAK"

            store.update_metrics(
                pos.id,
                current_price=cmp,
                profit_loss=pl,
                profit_loss_pct=pl_pct,
                drawdown=dd,
                drawdown_pct=dd_pct,
                high_since_entry=high_since,
                low_since_entry=low_since,
                days_held=days_held,
                status=store.map_status(new_status),
            )

            # On exit: close + journal
            if new_status != "ACTIVE" and old_status == "ACTIVE":
                try:
                    store.close(pos.id, cmp, exit_reason)
                    log.info("[%s] auto-close: %s %s at %.2f (PL: %.2f%%)",
                             store.name, pos.symbol, exit_reason, cmp, pl_pct)
                except Exception:
                    log.exception("[%s] failed to auto-close %s", store.name, pos.symbol)

            updated += 1

        if updated:
            log.debug("PositionTracking[%s]: updated %d positions", store.name, updated)
        return updated
