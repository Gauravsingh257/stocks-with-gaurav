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

# ── Stale / dead-money time-stop (flag-gated, default ON) ────────────────────
# Cull a position that has been held a long time but is still stuck near its
# entry (small loss to small gain) — it's occupying a slot without progressing,
# so we free it for a better candidate. Only fires within a tight band around
# entry: a real loser (below the lower band) is left to STOP/STRUCTURE, and a
# winner (above the upper band) is left to run to target. Gated per-store like
# the structure cull, so user-chosen positions are never auto-sold.
_STALE_EXIT_ON = os.getenv("PORTFOLIO_STALE_EXIT", "1").strip().lower() in {"1", "true", "yes"}
_STALE_EXIT_MIN_DAYS = int(os.getenv("PORTFOLIO_STALE_EXIT_MIN_DAYS", "20"))
_STALE_EXIT_UPPER_PCT = float(os.getenv("PORTFOLIO_STALE_EXIT_UPPER_PCT", "3.0"))    # <= +3% P&L = no real progress
_STALE_EXIT_LOWER_PCT = float(os.getenv("PORTFOLIO_STALE_EXIT_LOWER_PCT", "-5.0"))   # >= -5% P&L = still near entry

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

        # Structure-break cull is a per-store policy (system book honors
        # PORTFOLIO_STRUCTURE_EXIT; user book defaults OFF so user-chosen
        # trades aren't auto-sold on a soft 200-DMA break).
        structure_exit_on = bool(getattr(store, "structure_exit_enabled", lambda: _STRUCTURE_EXIT_ON)())
        # Stale cull reuses the store's auto-cull permission (so the user book,
        # which disables structure culls, is also protected from stale culls)
        # AND its own feature flag.
        stale_exit_on = _STALE_EXIT_ON and structure_exit_on

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
            else:
                # Structure-break cull: underwater + long-held + below 200-DMA.
                # (Its inner 200-DMA test may NOT fire, so it must not consume the
                # exit chain — the stale check below still gets a chance.)
                if structure_exit_on and days_held >= _STRUCTURE_EXIT_MIN_DAYS and cmp < entry:
                    dma200 = _get_200dma(pos.symbol)
                    if dma200 and cmp < dma200 * (1.0 - _STRUCTURE_EXIT_BUFFER):
                        new_status = "CLOSED"
                        exit_reason = "STRUCTURE_BREAK"
                # Stale / dead-money cull: long-held but hovering near entry.
                # Evaluated even when the structure branch did NOT exit (e.g.
                # underwater but still above the 200-DMA) — that gap is exactly
                # the non-mover we want to free the slot from.
                if (
                    exit_reason is None
                    and stale_exit_on
                    and days_held >= _STALE_EXIT_MIN_DAYS
                    and _STALE_EXIT_LOWER_PCT <= pl_pct <= _STALE_EXIT_UPPER_PCT
                ):
                    new_status = "CLOSED"
                    exit_reason = "STALE_EXIT"

            is_exit = (new_status != "ACTIVE" and old_status == "ACTIVE")
            # On an exit, keep the row ACTIVE for the metric write so close()
            # (which requires an ACTIVE row to build the journal entry) can find
            # it; close() then performs the terminal flip + journal. Previously
            # the status was pre-flipped here, so close() found no ACTIVE row and
            # silently skipped journaling — closed trades never reached the book.
            write_status = store.map_status("ACTIVE" if is_exit else new_status)

            # Per-position isolation: a single row's DB error (e.g. a UNIQUE
            # clash) must NEVER abort the tick and freeze every position after
            # it. Log and skip just this one; the rest still get live prices.
            try:
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
                    status=write_status,
                )
            except Exception:
                log.exception("[%s] update_metrics failed id=%s sym=%s — skipping row",
                              getattr(store, "name", "?"), pos.id, pos.symbol)
                continue

            # On exit: close + journal (close() does the terminal flip + records
            # the immutable journal entry). Isolated so it can't abort the tick.
            if is_exit:
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
