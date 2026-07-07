"""
services/portfolio_reconcile.py
===============================
One-time reconciliation of the live portfolio for the arm-on-tap migration.

Before arm-on-tap, a promoted idea was inserted ACTIVE at its *planned* entry
and immediately accrued P&L — even if price never traded at that entry. This
module examines every ACTIVE position against real historical OHLC and:

  - reclassifies to PENDING only those whose planned entry was NEVER genuinely
    traded through (the phantom entries), wiping the fabricated P&L; and
  - preserves every legitimately-triggered position exactly as ACTIVE, only
    back-filling `entered_at` (the real tap date) so days-held is measured from
    the actual entry.

Fail-safe: if OHLC can't be fetched for a symbol, the position is LEFT ACTIVE
(never reclassified on uncertainty). Pure logic (`entry_traded_through`) is unit
testable; `reconcile_portfolio_entries(dry_run=...)` returns a full impact report
and only mutates the DB when dry_run=False.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

log = logging.getLogger("services.portfolio_reconcile")

_IST = timezone(timedelta(hours=5, minutes=30))


def entry_traded_through(entry: float, ohlc: list[dict]) -> tuple[bool, str | None]:
    """Did price genuinely trade at/through `entry` over the given daily bars?

    `ohlc` = chronologically-ordered dicts with 'low','high','date'. Uses a
    cumulative range so a GAP through the entry (no single bar contains it, but
    the market jumped past it) still counts as traded-through. Returns
    (triggered, first_cross_date_iso).
    """
    if entry <= 0 or not ohlc:
        return False, None
    cmin = float("inf")
    cmax = float("-inf")
    for bar in ohlc:
        try:
            lo = float(bar["low"]); hi = float(bar["high"])
        except (KeyError, TypeError, ValueError):
            continue
        cmin = min(cmin, lo)
        cmax = max(cmax, hi)
        if cmin <= entry <= cmax:
            return True, str(bar.get("date"))[:10]
    return False, None


def _fetch_daily_ohlc_yf(symbol: str, start_date: str) -> list[dict] | None:
    """Daily OHLC from `start_date` (inclusive) to now via yfinance. Returns None
    on failure so the caller can fail SAFE (leave the position untouched)."""
    try:
        import yfinance as yf

        sym = symbol.replace("NSE:", "").strip().upper()
        try:
            start = datetime.fromisoformat(str(start_date).replace(" ", "T")).date()
        except Exception:
            start = (datetime.now(_IST) - timedelta(days=30)).date()
        # A day of head-room on each side so the arm-day bar is definitely included.
        start_str = (start - timedelta(days=1)).isoformat()
        df = yf.Ticker(f"{sym}.NS").history(start=start_str, interval="1d")
        if df is None or df.empty:
            return None
        out: list[dict] = []
        for idx, r in df.iterrows():
            out.append({"date": str(idx)[:10], "low": float(r["Low"]), "high": float(r["High"])})
        return out
    except Exception as exc:
        log.debug("reconcile OHLC fetch failed %s: %s", symbol, exc)
        return None


def reconcile_portfolio_entries(
    dry_run: bool = True,
    fetch: Callable[[str, str], list[dict] | None] | None = None,
) -> dict:
    """Reconcile every ACTIVE position against historical OHLC.

    dry_run=True  → report only, no DB writes (safe to run anytime).
    dry_run=False → apply: reclassify phantom entries to PENDING; back-fill
                    entered_at on genuine ones.

    Returns a full impact report dict.
    """
    from dashboard.backend.db.portfolio import (
        get_portfolio, get_portfolio_counts,
        reclassify_active_to_pending, backfill_entered_at,
    )

    fetch = fetch or _fetch_daily_ohlc_yf
    before = get_portfolio_counts()
    active = [p for p in get_portfolio(include_closed=False) if p.get("status") == "ACTIVE"]

    reclassified: list[dict] = []
    preserved: list[dict] = []
    skipped: list[dict] = []
    phantom_pnl_sum = 0.0

    for p in active:
        sym = p["symbol"]
        entry = float(p["entry_price"])
        created = p.get("created_at") or ""
        ohlc = fetch(sym, created)
        if ohlc is None:
            # Can't verify → leave ACTIVE (never reclassify on uncertainty).
            skipped.append({"symbol": sym, "id": p["id"], "reason": "no_ohlc_data"})
            continue

        triggered, cross_date = entry_traded_through(entry, ohlc)
        if triggered:
            # Genuine entry — preserve. Back-fill the real tap date if missing.
            if not p.get("entered_at"):
                if not dry_run:
                    backfill_entered_at(p["id"], (cross_date or created))
            preserved.append({
                "symbol": sym, "id": p["id"], "entry": entry,
                "entered_at": p.get("entered_at") or cross_date,
                "pnl_pct": p.get("profit_loss_pct"),
            })
        else:
            # Phantom — entry never traded through. Reclassify to PENDING.
            phantom_pnl_sum += float(p.get("profit_loss_pct") or 0.0)
            arm_ref = float(p.get("current_price") or entry)
            if not dry_run:
                reclassify_active_to_pending(p["id"], arm_ref_price=arm_ref)
            reclassified.append({
                "symbol": sym, "id": p["id"], "entry": entry,
                "cmp": p.get("current_price"),
                "phantom_pnl_pct": p.get("profit_loss_pct"),
                "days_shown_held": p.get("days_held"),
                "reason": "entry_never_traded_through",
            })

    after = before if dry_run else get_portfolio_counts()
    return {
        "dry_run": dry_run,
        "generated_at": datetime.now(_IST).isoformat(),
        "before": {
            "swing_active": before["swing"], "longterm_active": before["longterm"],
            "swing_pending": before.get("swing_pending", 0), "longterm_pending": before.get("longterm_pending", 0),
        },
        "after": {
            "swing_active": after["swing"], "longterm_active": after["longterm"],
            "swing_pending": after.get("swing_pending", 0), "longterm_pending": after.get("longterm_pending", 0),
        },
        "examined_active": len(active),
        "reclassified_count": len(reclassified),
        "preserved_count": len(preserved),
        "skipped_count": len(skipped),
        "phantom_unrealized_pnl_pct_removed": round(phantom_pnl_sum, 2),
        "reclassified": reclassified,
        "preserved": preserved,
        "skipped": skipped,
    }
