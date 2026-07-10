"""
services/pil/accounting.py
==========================
The Portfolio Intelligence Layer's **book-capital accounting layer**.

The Swing/LT engines track P&L in % / per-share terms only — no ₹ notional. To
produce PMS-grade ₹ metrics (Portfolio Value, Invested, Cash, Turnover, CAGR,
drawdown ...) PIL reconstructs a *virtual ledger* per book from the existing
position/journal rows, WITHOUT touching any engine:

  * Each book starts from a configurable initial capital (services/pil/config).
  * Events are replayed chronologically. On a new entry a position is allocated
    an equal share of the *currently available cash* across free slots
    (alloc = cash / free_slots); cash is debited; qty = alloc / entry_price.
    Momentum positions reuse their real `position_size` when present.
  * On exit the proceeds (qty * exit_price) return to cash and realized P&L is
    booked. Open positions are marked to `current_price`.

This is pure accounting/reporting — deterministic given the rows, and it never
feeds back into a trading decision. Reads happen exclusively through the engine
DB getters. A future switch to risk-based sizing only changes the `alloc` rule.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from services.pil import BOOK_LABELS
from services.pil import config as pil_config
from services.pil import reference_data as ref

logger = logging.getLogger("pil.accounting")
_IST = timezone(timedelta(hours=5, minutes=30))

_BIG_LIMIT = 100_000  # pull the full journal, not the UI's default page


# ── time helpers ─────────────────────────────────────────────────────────────

def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    s = str(ts).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _date_str(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def _today() -> datetime:
    return datetime.now(_IST)


# ── engine data adapters (read-only) ─────────────────────────────────────────

def _load_book(book: str) -> tuple[list[dict], list[dict], int, float]:
    """Return (open_positions, closed_trades, max_slots, initial_capital).

    Reads exclusively through the engines' existing DB getters.
    """
    book = book.upper()
    initial_capital = pil_config.book_capital(book)

    if book in ("SWING", "LONGTERM"):
        from dashboard.backend.db.portfolio import (
            get_portfolio, get_journal, MAX_SWING_POSITIONS, MAX_LONGTERM_POSITIONS,
        )
        opens = get_portfolio(book, include_closed=False, include_pending=False)
        closed = get_journal(book, limit=_BIG_LIMIT)
        max_slots = MAX_SWING_POSITIONS if book == "SWING" else MAX_LONGTERM_POSITIONS
        return opens, closed, max_slots, initial_capital

    if book == "MOMENTUM":
        from dashboard.backend.db.momentum_portfolio import (
            get_portfolio, get_journal, MAX_MOMENTUM_POSITIONS,
        )
        opens = [p for p in get_portfolio(include_pending=False, include_closed=False)
                 if p.get("status") == "ACTIVE"]
        closed = get_journal(limit=_BIG_LIMIT)
        return opens, closed, MAX_MOMENTUM_POSITIONS, initial_capital

    raise ValueError(f"unknown book {book!r}")


def _lot_from_open(p: dict) -> dict:
    entry = float(p.get("entry_price") or 0)
    cur = float(p.get("current_price") or entry)
    open_dt = _parse(p.get("entered_at")) or _parse(p.get("created_at"))
    return {
        "symbol": p.get("symbol", ""),
        "entry_price": entry,
        "current_price": cur,
        "exit_price": None,
        "open_dt": open_dt,
        "close_dt": None,
        "position_size": float(p["position_size"]) if p.get("position_size") else None,
        "days_held": int(p.get("days_held") or 0),
        "sector": ref.get_sector(p.get("symbol", ""), p.get("sector")),
        "open": True,
    }


def _lot_from_closed(t: dict) -> dict:
    entry = float(t.get("entry_price") or 0)
    exit_p = float(t.get("exit_price") or entry)
    open_dt = _parse(t.get("created_at"))
    close_dt = _parse(t.get("closed_at"))
    return {
        "symbol": t.get("symbol", ""),
        "entry_price": entry,
        "current_price": exit_p,
        "exit_price": exit_p,
        "open_dt": open_dt,
        "close_dt": close_dt,
        "position_size": float(t["position_size"]) if t.get("position_size") else None,
        "days_held": int(t.get("days_held") or 0),
        "exit_reason": t.get("exit_reason", ""),
        "sector": ref.get_sector(t.get("symbol", ""), t.get("sector")),
        "open": False,
    }


# ── the reconstruction ───────────────────────────────────────────────────────

def reconstruct(book: str) -> dict:
    """Replay a book's lots into a virtual ledger. See module docstring."""
    opens_raw, closed_raw, max_slots, initial_capital = _load_book(book)
    lots = [_lot_from_open(p) for p in opens_raw] + [_lot_from_closed(t) for t in closed_raw]
    max_slots = max(int(max_slots or 1), 1)

    # Event timeline: (sort_key, kind, lot). CLOSE before OPEN on ties so freed
    # capital can be recycled the same day. Lots with no open time sort first
    # (they are historical opens we still want to account for).
    _far_future = datetime.max.replace(tzinfo=None)
    events: list[tuple] = []
    for i, lot in enumerate(lots):
        od = lot["open_dt"]
        events.append(((od.replace(tzinfo=None) if od else datetime.min, 1, i), "OPEN", lot))
        if not lot["open"] and lot["close_dt"]:
            cd = lot["close_dt"].replace(tzinfo=None)
            events.append(((cd, 0, i), "CLOSE", lot))
    events.sort(key=lambda e: e[0])

    cash = initial_capital
    realized = 0.0
    open_state: dict[int, dict] = {}          # lot_idx -> {alloc, qty}
    curve: list[dict] = []                     # (date, value) at each event

    def _mark_cost_value() -> float:
        return cash + sum(s["alloc"] for s in open_state.values())

    for (key, kind, lot) in events:
        idx = key[2]
        if kind == "OPEN":
            free_slots = max(max_slots - len(open_state), 1)
            if lot["position_size"] and lot["position_size"] > 0:
                alloc = min(lot["position_size"], cash)
            else:
                alloc = cash / free_slots
            alloc = max(min(alloc, cash), 0.0)
            qty = (alloc / lot["entry_price"]) if lot["entry_price"] > 0 else 0.0
            cash -= alloc
            open_state[idx] = {"alloc": alloc, "qty": qty, "lot": lot}
        else:  # CLOSE
            st = open_state.pop(idx, None)
            if st is None:
                continue
            proceeds = st["qty"] * lot["exit_price"]
            cash += proceeds
            realized += proceeds - st["alloc"]
            lot["_alloc"] = st["alloc"]
            lot["_qty"] = st["qty"]
            lot["_pnl"] = proceeds - st["alloc"]
        d = _date_str(lot["close_dt"] if kind == "CLOSE" else lot["open_dt"]) or _today().date().isoformat()
        curve.append({"date": d, "value": round(_mark_cost_value(), 2)})

    # Mark still-open lots to market for the "today" snapshot.
    invested = 0.0
    market_value = 0.0
    positions: list[dict] = []
    for idx, st in open_state.items():
        lot = st["lot"]
        alloc, qty = st["alloc"], st["qty"]
        mv = qty * lot["current_price"]
        invested += alloc
        market_value += mv
        upnl = mv - alloc
        positions.append({
            "symbol": lot["symbol"],
            "sector": lot["sector"],
            "qty": round(qty, 4),
            "entry_price": lot["entry_price"],
            "current_price": lot["current_price"],
            "cost_basis": round(alloc, 2),
            "market_value": round(mv, 2),
            "unrealized_pnl": round(upnl, 2),
            "unrealized_pnl_pct": round((upnl / alloc * 100) if alloc else 0.0, 2),
            "days_held": lot["days_held"],
        })

    portfolio_value = cash + market_value
    unrealized = market_value - invested

    # Final marked "today" point (marks open lots to market vs cost during walk).
    today = _today().date().isoformat()
    if curve and curve[-1]["date"] == today:
        curve[-1]["value"] = round(portfolio_value, 2)
    else:
        curve.append({"date": today, "value": round(portfolio_value, 2)})

    closed_trades = []
    for lot in lots:
        if lot["open"]:
            continue
        alloc = lot.get("_alloc", 0.0)
        closed_trades.append({
            "symbol": lot["symbol"],
            "sector": lot["sector"],
            "entry_price": lot["entry_price"],
            "exit_price": lot["exit_price"],
            "cost_basis": round(alloc, 2),
            "pnl": round(lot.get("_pnl", 0.0), 2),
            "pnl_pct": round((lot.get("_pnl", 0.0) / alloc * 100) if alloc else 0.0, 2),
            "open_date": _date_str(lot["open_dt"]),
            "close_date": _date_str(lot["close_dt"]),
            "days_held": lot["days_held"],
            "exit_reason": lot.get("exit_reason", ""),
        })

    positions.sort(key=lambda x: x["market_value"], reverse=True)
    total = portfolio_value or 1.0
    for pos in positions:
        pos["weight_pct"] = round(pos["market_value"] / total * 100, 2)

    return {
        "book": book.upper(),
        "label": BOOK_LABELS.get(book.upper(), book),
        "initial_capital": round(initial_capital, 2),
        "cash": round(cash, 2),
        "invested": round(invested, 2),
        "market_value": round(market_value, 2),
        "portfolio_value": round(portfolio_value, 2),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_pnl": round(realized + unrealized, 2),
        "total_return_pct": round((portfolio_value - initial_capital) / initial_capital * 100, 2)
        if initial_capital else 0.0,
        "open_positions": len(positions),
        "max_slots": max_slots,
        "positions": positions,
        "closed_trades": closed_trades,
        "equity_curve": daily_curve(curve, initial_capital),
    }


def daily_curve(event_curve: list[dict], initial_capital: float) -> list[dict]:
    """Forward-fill an event-dated curve into a regular daily series from the
    first event to today. Prepends the inception (initial capital) point."""
    if not event_curve:
        today = _today().date().isoformat()
        return [{"date": today, "value": round(initial_capital, 2)}]

    # collapse to last value per date (a date may host several events)
    by_date: dict[str, float] = {}
    for pt in event_curve:
        by_date[pt["date"]] = pt["value"]
    dates = sorted(by_date)
    start = _parse(dates[0])
    end = _today()
    if not start:
        return [{"date": d, "value": by_date[d]} for d in dates]

    out: list[dict] = []
    cur = start.date()
    last = round(initial_capital, 2)
    end_d = end.date()
    guard = 0
    while cur <= end_d and guard < 4000:   # ~11y cap, defensive
        ds = cur.isoformat()
        if ds in by_date:
            last = by_date[ds]
        out.append({"date": ds, "value": last})
        cur = cur + timedelta(days=1)
        guard += 1
    return out


def reconstruct_all() -> dict[str, dict]:
    """Ledger for every book plus a capital-weighted COMBINED book."""
    books = {}
    for b in ("SWING", "LONGTERM", "MOMENTUM"):
        try:
            books[b] = reconstruct(b)
        except Exception as exc:  # one bad book must not sink the layer
            logger.error("[PIL] reconstruct(%s) failed: %s", b, exc)
            books[b] = _empty_book(b)
    books["COMBINED"] = combine(list(books.values()))
    return books


def _empty_book(book: str) -> dict:
    ic = pil_config.book_capital(book)
    today = _today().date().isoformat()
    return {
        "book": book.upper(), "label": BOOK_LABELS.get(book.upper(), book),
        "initial_capital": ic, "cash": ic, "invested": 0.0, "market_value": 0.0,
        "portfolio_value": ic, "realized_pnl": 0.0, "unrealized_pnl": 0.0,
        "total_pnl": 0.0, "total_return_pct": 0.0, "open_positions": 0,
        "max_slots": 0, "positions": [], "closed_trades": [],
        "equity_curve": [{"date": today, "value": ic}],
    }


def combine(books: list[dict]) -> dict:
    """Aggregate individual book ledgers into a COMBINED book. ₹ figures sum;
    the combined equity curve is the date-aligned sum of the book curves."""
    books = [b for b in books if b and b.get("book") != "COMBINED"]
    if not books:
        return _empty_book("COMBINED") | {"book": "COMBINED", "label": "Combined"}

    ic = sum(b["initial_capital"] for b in books)
    cash = sum(b["cash"] for b in books)
    invested = sum(b["invested"] for b in books)
    mv = sum(b["market_value"] for b in books)
    pv = sum(b["portfolio_value"] for b in books)
    realized = sum(b["realized_pnl"] for b in books)
    unrealized = sum(b["unrealized_pnl"] for b in books)

    # union of dates, forward-fill each book, then sum
    all_dates: set[str] = set()
    for b in books:
        all_dates.update(pt["date"] for pt in b["equity_curve"])
    dates = sorted(all_dates)
    combined_curve = []
    idx = {b["book"]: 0 for b in books}
    last = {b["book"]: b["initial_capital"] for b in books}
    for d in dates:
        total = 0.0
        for b in books:
            ec = b["equity_curve"]
            while idx[b["book"]] < len(ec) and ec[idx[b["book"]]]["date"] <= d:
                last[b["book"]] = ec[idx[b["book"]]]["value"]
                idx[b["book"]] += 1
            total += last[b["book"]]
        combined_curve.append({"date": d, "value": round(total, 2)})

    positions = []
    closed_trades = []
    for b in books:
        for p in b["positions"]:
            positions.append({**p, "book": b["book"]})
        for t in b["closed_trades"]:
            closed_trades.append({**t, "book": b["book"]})
    positions.sort(key=lambda x: x["market_value"], reverse=True)
    tot = pv or 1.0
    for p in positions:
        p["weight_pct"] = round(p["market_value"] / tot * 100, 2)

    return {
        "book": "COMBINED", "label": "Combined",
        "initial_capital": round(ic, 2), "cash": round(cash, 2),
        "invested": round(invested, 2), "market_value": round(mv, 2),
        "portfolio_value": round(pv, 2), "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2), "total_pnl": round(realized + unrealized, 2),
        "total_return_pct": round((pv - ic) / ic * 100, 2) if ic else 0.0,
        "open_positions": len(positions),
        "max_slots": sum(b["max_slots"] for b in books),
        "positions": positions, "closed_trades": closed_trades,
        "equity_curve": combined_curve or [{"date": _today().date().isoformat(), "value": round(pv, 2)}],
    }
