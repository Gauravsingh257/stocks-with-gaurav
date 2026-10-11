"""
dashboard/backend/routes/terminal.py

Phase 2 + 3 — REST endpoints for the AI Trade Opportunity Terminal.

  Phase 2
    GET  /api/trades             → ranked standardized signals (active + today's queue)
    GET  /api/trades/{symbol}    → detailed setup explanation for one ticker
    GET  /api/discovery-feed     → recent events (NEW_SETUP / SWEEP / LIFECYCLE …)
    GET  /api/terminal/health    → liveness + Redis status (no auth)

  Phase 3
    GET  /api/summary            → AI summary panel (top 3, best, market bias)
    GET  /api/preferences        → user preferences (default user)
    POST /api/preferences        → upsert user preferences
    GET  /api/journal            → user trade journal entries
    POST /api/journal            → add a journal entry
    PATCH/api/journal/{id}       → update an entry (close, edit pnl, notes)
    DELETE /api/journal/{id}     → remove a journal entry
    GET  /api/performance        → win rate, avg RR, best/worst setups
    GET  /api/alerts             → recent lifecycle alerts (bell icon)

Optional API-key gate via env var ``TERMINAL_API_KEY`` on the PUBLIC market-data
endpoints. When unset, those endpoints are open (matches the rest of the
dashboard). When set, callers must send ``X-API-Key`` header or ``?api_key=``.

USER-SCOPED endpoints (preferences, journal, performance, trades/{sym}/taken)
are NOT behind that key: they require a verified JWT, and the user is ALWAYS
derived from the token. Until 2026-10-11 they took ``?user_id=`` from the
client (default "default") behind the open-mode key, so anyone could read,
write or delete any user's data, and every visitor shared one "default" row.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from dashboard.backend.routes.auth import get_current_user, get_optional_user
from dashboard.backend.terminal_events import (
    EVENTS_RING_SIZE,
    get_recent_events,
    get_signal_by_symbol,
    read_active_trades,
    read_today_signals,
)

router = APIRouter(prefix="/api", tags=["terminal"])


# ─────────────────────────────────────────────────────────────────────────
# Optional API-key auth
# ─────────────────────────────────────────────────────────────────────────

def _require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    api_key: Optional[str] = Query(default=None),
) -> None:
    expected = os.getenv("TERMINAL_API_KEY", "").strip()
    if not expected:
        return  # open mode
    provided = (x_api_key or api_key or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _store_key(user: dict) -> str:
    """user_store key for a verified JWT user.

    Namespaced ("uid:<id>") rather than the bare id: while these endpoints were
    open, anyone could have written rows under "1", "2", … — a bare-id key would
    hand those planted rows to the real account. Legacy rows (including the
    shared "default") stay orphaned and untouched; nothing is deleted.
    """
    return f"uid:{int(user['sub'])}"


def _prefs_for(user: dict | None) -> dict:
    """The caller's preferences, or the built-in defaults when anonymous."""
    from dashboard.backend.user_store import DEFAULT_PREFERENCES, get_preferences
    if user is None:
        return dict(DEFAULT_PREFERENCES)
    return get_preferences(_store_key(user))


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────

@router.get("/trades", dependencies=[Depends(_require_api_key)])
def list_trades(
    limit: int = Query(default=50, ge=1, le=200),
    direction: Optional[str] = Query(default=None, pattern="^(LONG|SHORT|long|short|BUY|SELL|buy|sell)$"),
    setup: Optional[str] = Query(default=None, pattern="^[A-Da-d]$"),
    setups: Optional[str] = Query(default=None, description="Comma-separated setup letters, e.g. A,B,C"),
    status: Optional[str] = Query(default=None, pattern="^(WAITING|APPROACHING|TAPPED|TRIGGERED|RUNNING|TARGET_HIT|STOP_HIT)$"),
    strategy: Optional[str] = Query(default=None, description="intraday | swing | all"),
    risk: Optional[str] = Query(default=None, description="conservative | aggressive"),
    symbol: Optional[str] = Query(default=None, description="Case-insensitive partial ticker match, e.g. REL or RELIANCE"),
    apply_prefs: bool = Query(default=True),
    user: dict | None = Depends(get_optional_user),
):
    """Ranked standardized signals — active trades first, then today's queue.

    Each item conforms to the public schema:
      ``symbol, direction, entry, sl, target, rr, setup, confidence,
        status, analysis{...}, intelligence{...}, narrative, ranking_score``.

    When ``apply_prefs=true`` (default), the caller's preferences from
    ``/api/preferences`` bias the ranking and may filter low-quality results.
    """
    active = read_active_trades()
    todays = read_today_signals()

    # Merge: active trades dominate per symbol, then append non-duplicate signals.
    seen: set[str] = set()
    merged: list[dict] = []
    for t in active:
        sym = t.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(t)
    for s in reversed(todays):  # newest first
        sym = s.get("symbol", "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(s)

    # Phase 3 — apply user preferences + ranking
    prefs = None
    if apply_prefs:
        try:
            prefs = _prefs_for(user)
            # `direction=BOTH` means no filter — drop it before passing to ranker
            if (prefs.get("direction") or "BOTH").upper() == "BOTH":
                prefs = {**prefs, "direction": ""}
        except Exception:
            prefs = None
    try:
        from dashboard.backend.intelligence import rank_signals
        merged = rank_signals(merged, prefs=prefs)
    except Exception:
        pass

    if direction:
        # Normalise BUY→LONG, SELL→SHORT so both naming conventions work
        d = direction.upper()
        if d == "BUY":
            d = "LONG"
        elif d == "SELL":
            d = "SHORT"
        merged = [m for m in merged if m.get("direction") == d]
    if setup:
        s = setup.upper()
        merged = [m for m in merged if m.get("setup") == s]
    if setups:
        # Comma-separated multi-setup filter, e.g. setups=A,B
        setup_set = {s.strip().upper() for s in setups.split(",") if s.strip()}
        if setup_set:
            merged = [m for m in merged if m.get("setup") in setup_set]
    if status:
        st = status.upper()
        merged = [m for m in merged if m.get("status") == st]
    if strategy and strategy.lower() not in ("all", ""):
        # Best-effort: map signal fields → intraday / swing bucket
        strat = strategy.lower()
        def _match_strategy(item: dict) -> bool:
            horizon = (
                (item.get("intelligence") or {}).get("expected_move_time", "")
                or item.get("expected_move_time", "")
                or item.get("strategy", "")
                or ""
            ).lower()
            setup_name = (item.get("setup") or "").upper()
            if strat == "intraday":
                # Setup A/C/D are intraday-biased; also check horizon keyword
                return setup_name in ("A", "C", "D") or bool(horizon and any(k in horizon for k in ("intraday", "day", "hour", "1-2h", "few hours")))
            if strat == "swing":
                return setup_name == "B" or bool(horizon and any(k in horizon for k in ("swing", "week", "day", "2-3 day")))
            return True
        merged = [m for m in merged if _match_strategy(m)]
    if risk and risk.lower() not in ("all", ""):
        rk = risk.lower()
        if rk == "conservative":
            # Drop grade-C signals in conservative mode
            merged = [m for m in merged if (m.get("confidence") or "") != "C"]
        elif rk == "aggressive":
            # Aggressive: keep everything, but surface higher-RR trades first
            # (no filtering — ordering already done by rank_signals)
            pass
    if symbol:
        sym_q = symbol.strip().upper()
        merged = [m for m in merged if sym_q in (m.get("symbol") or "").upper()]

    return {
        "trades": merged[:limit],
        "count": min(len(merged), limit),
        "total": len(merged),
        "active_count": len(active),
        "signal_count": len(todays),
        "applied_preferences": bool(prefs),
    }


@router.get("/trades/{symbol}", dependencies=[Depends(_require_api_key)])
def get_trade(symbol: str):
    """Detailed setup explanation for a single ticker."""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol_required")
    record = get_signal_by_symbol(sym)
    if not record:
        raise HTTPException(status_code=404, detail=f"no_signal_for_{sym}")
    return record


@router.get("/discovery-feed", dependencies=[Depends(_require_api_key)])
def get_discovery_feed(limit: int = Query(default=30, ge=1, le=EVENTS_RING_SIZE)):
    """Recent terminal events (newest first)."""
    events = get_recent_events(limit=limit)
    return {"events": events, "count": len(events)}


@router.get("/terminal/health")
def terminal_health():
    """Open health endpoint — used by frontend to detect live mode."""
    try:
        from dashboard.backend.cache import is_redis_available
        redis_up = bool(is_redis_available())
    except Exception:
        redis_up = False
    return {
        "ok": True,
        "redis": redis_up,
        "auth_required": bool(os.getenv("TERMINAL_API_KEY", "").strip()),
        "events_ring_size": EVENTS_RING_SIZE,
    }


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 — Intelligence + User Loop
# ─────────────────────────────────────────────────────────────────────────

@router.get("/summary", dependencies=[Depends(_require_api_key)])
def get_summary(user: dict | None = Depends(get_optional_user)):
    """AI summary panel: top 3 trades, best opportunity, market bias.

    Public. Personalised with the caller's preferences when a valid token is
    sent; built-in defaults otherwise (never another user's row)."""
    try:
        from dashboard.backend.intelligence import summarize
        from dashboard.backend.state_bridge import get_engine_snapshot
        snap = get_engine_snapshot() or {}
        regime = snap.get("regime") or snap.get("market_regime") or {}
        prefs = _prefs_for(user)
        if (prefs.get("direction") or "BOTH").upper() == "BOTH":
            prefs = {**prefs, "direction": ""}
        active = read_active_trades()
        todays = read_today_signals()
        seen: set[str] = set()
        merged: list[dict] = []
        for t in active:
            sym = t.get("symbol", "")
            if sym and sym not in seen:
                seen.add(sym); merged.append(t)
        for s in reversed(todays):
            sym = s.get("symbol", "")
            if sym and sym not in seen:
                seen.add(sym); merged.append(s)
        return summarize(merged, regime=regime, prefs=prefs)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"summary_failed: {exc}")


# ── User-scoped: verified JWT required; identity never comes from the client ──
# (GET /api/journal is shadowed in practice by journal_router's public engine
# journal, which is registered first — it is secured here anyway.)

@router.get("/preferences")
def get_user_preferences(user: dict = Depends(get_current_user)):
    from dashboard.backend.user_store import get_preferences
    return get_preferences(_store_key(user))


@router.post("/preferences")
def post_user_preferences(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    from dashboard.backend.user_store import set_preferences
    return set_preferences(_store_key(user), payload)


@router.get("/journal")
def get_user_journal(
    status: Optional[str] = Query(default=None, pattern="^(OPEN|CLOSED|open|closed)$"),
    limit: int = Query(default=200, ge=1, le=2000),
    user: dict = Depends(get_current_user),
):
    from dashboard.backend.user_store import list_journal
    rows = list_journal(user_id=_store_key(user), limit=limit, status=status)
    return {"entries": rows, "count": len(rows)}


@router.post("/journal")
def post_user_journal(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    from dashboard.backend.user_store import add_journal_entry
    rec = add_journal_entry(_store_key(user), payload)
    if not rec:
        raise HTTPException(status_code=400, detail="invalid_journal_payload")
    return rec


@router.patch("/journal/{entry_id}")
def patch_user_journal(entry_id: int, payload: dict = Body(...), user: dict = Depends(get_current_user)):
    from dashboard.backend.user_store import update_journal_entry
    # update/delete are scoped by (user_id, id), so another user's entry id
    # simply doesn't match → 404, indistinguishable from "doesn't exist".
    rec = update_journal_entry(_store_key(user), entry_id, payload)
    if not rec:
        raise HTTPException(status_code=404, detail="journal_entry_not_found")
    return rec


@router.delete("/journal/{entry_id}")
def delete_user_journal(entry_id: int, user: dict = Depends(get_current_user)):
    from dashboard.backend.user_store import delete_journal_entry
    ok = delete_journal_entry(_store_key(user), entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="journal_entry_not_found")
    return {"ok": True, "id": entry_id}


@router.get("/performance")
def get_user_performance(user: dict = Depends(get_current_user)):
    from dashboard.backend.user_store import compute_performance
    return compute_performance(_store_key(user))


@router.get("/alerts", dependencies=[Depends(_require_api_key)])
def get_user_alerts(limit: int = Query(default=50, ge=1, le=200)):
    from dashboard.backend.alerts import get_recent_alerts
    alerts = get_recent_alerts(limit=limit)
    return {"alerts": alerts, "count": len(alerts)}


# ─────────────────────────────────────────────────────────────────────────
# Decision Engine — user feedback loop
# ─────────────────────────────────────────────────────────────────────────

@router.post("/trades/{symbol}/taken")
def mark_trade_taken(
    symbol: str,
    payload: dict = Body(default={}),
    user: dict = Depends(get_current_user),
):
    """
    Mark a signal as 'trade taken' — creates a OPEN journal entry.

    Accepts optional overrides in the request body:
      qty, notes, confidence (1-10 personal conviction)
    """
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol_required")
    record = get_signal_by_symbol(sym)
    if not record:
        # Allow user to mark without a live signal (manual entry).
        record = {"symbol": sym}
    user_id = _store_key(user)
    try:
        from dashboard.backend.user_store import add_journal_entry, list_journal
        # Check if already open to avoid duplicate.
        existing = [e for e in list_journal(user_id=user_id, limit=50) if e.get("symbol") == sym and e.get("status") == "OPEN"]
        if existing:
            return {"ok": True, "duplicate": True, "entry": existing[0]}
        entry_payload = {
            "symbol": sym,
            "direction": record.get("direction", "LONG"),
            "entry": record.get("entry"),
            "stop": record.get("sl") or record.get("stop"),
            "target": record.get("target"),
            "rr": record.get("rr"),
            "setup": record.get("setup"),
            "status": "OPEN",
            "source": "terminal",
            "notes": payload.get("notes", ""),
            "qty": payload.get("qty"),
            "confidence": payload.get("confidence"),
        }
        rec = add_journal_entry(user_id, entry_payload)
        if not rec:
            raise HTTPException(status_code=400, detail="journal_create_failed")
        return {"ok": True, "duplicate": False, "entry": rec}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mark_taken_failed: {exc}")


@router.get("/pnl/daily", dependencies=[Depends(_require_api_key)])
def get_daily_pnl(
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD, defaults to today IST"),
    user: dict | None = Depends(get_optional_user),
):
    """
    Daily PnL summary for the user loop.

    Returns:
      date, realized_r, total_pnl, wins, losses, win_rate,
      streak (consecutive wins +ve / losses -ve), trades[]
    """
    import csv
    import os
    import time
    from datetime import datetime, timezone, timedelta

    # IST = UTC+5:30
    IST = timezone(timedelta(hours=5, minutes=30))
    today_str = date or datetime.now(IST).strftime("%Y-%m-%d")

    trades_today: list[dict] = []

    # ── Source 1: the caller's own journal closed today (authenticated only)
    # Anonymous callers skip straight to the engine's public daily P&L below.
    try:
        from dashboard.backend.user_store import list_journal
        entries = list_journal(user_id=_store_key(user), status="CLOSED", limit=500) if user else []
        for e in entries:
            closed_at = e.get("closed_at")
            if isinstance(closed_at, (int, float)):
                day = datetime.fromtimestamp(closed_at, tz=IST).strftime("%Y-%m-%d")
                if day == today_str:
                    trades_today.append({
                        "symbol": e.get("symbol"),
                        "direction": e.get("direction"),
                        "pnl": e.get("pnl") or 0.0,
                        "rr": e.get("rr"),
                        "setup": e.get("setup"),
                        "source": "journal",
                    })
    except Exception:
        pass

    # ── Source 2: daily_pnl_log.csv (engine-written) ────────────────────
    csv_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "daily_pnl_log.csv")
    csv_path = os.path.normpath(csv_path)
    if not trades_today and os.path.exists(csv_path):
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_date = (row.get("date") or row.get("Date") or "").strip()[:10]
                    if row_date == today_str:
                        trades_today.append({
                            "symbol": row.get("symbol") or row.get("Symbol", "—"),
                            "direction": row.get("direction", "—"),
                            "pnl": float(row.get("pnl") or row.get("PnL") or 0),
                            "rr": float(row.get("rr") or row.get("R") or 0) if (row.get("rr") or row.get("R")) else None,
                            "setup": row.get("setup") or row.get("Setup"),
                            "source": "csv",
                        })
        except Exception:
            pass

    wins = [t for t in trades_today if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades_today if (t.get("pnl") or 0) < 0]
    total_pnl = round(sum(t.get("pnl") or 0 for t in trades_today), 2)
    realized_r = round(sum(t.get("rr") or 0 for t in trades_today if (t.get("pnl") or 0) > 0)
                       - sum(abs(t.get("rr") or 0) for t in trades_today if (t.get("pnl") or 0) <= 0), 2)
    win_rate = round(len(wins) / len(trades_today) * 100, 1) if trades_today else 0.0

    # Streak: +N = consecutive wins, -N = consecutive losses
    streak = 0
    if trades_today:
        last_sign = 1 if (trades_today[-1].get("pnl") or 0) > 0 else -1
        for t in reversed(trades_today):
            sign = 1 if (t.get("pnl") or 0) > 0 else -1
            if sign == last_sign:
                streak += last_sign
            else:
                break

    return {
        "date": today_str,
        "realized_r": realized_r,
        "total_pnl": total_pnl,
        "wins": len(wins),
        "losses": len(losses),
        "total": len(trades_today),
        "win_rate": win_rate,
        "streak": streak,
        "trades": trades_today,
    }


# ─────────────────────────────────────────────────────────────────────────
# Mini-chart OHLC — used by OpportunityCard
# ─────────────────────────────────────────────────────────────────────────

@router.get("/chart/{symbol}", dependencies=[Depends(_require_api_key)])
def get_chart(
    symbol: str,
    interval: str = Query(default="5m", description="1m | 5m | 15m | 1h | 1D"),
    days: int = Query(default=0, ge=0, le=30, description="Override fetch window (0 = auto)"),
):
    """
    OHLC candles for the OpportunityCard mini-chart.

    Returns ``{ symbol, interval, bars: [{time, open, high, low, close}], count }``.
    Delegates to the existing charts._fetch_ohlc() which uses Kite historical_data
    with a 60-second in-process cache so per-card fetches are cheap.
    Falls back to an empty bars list (not 5xx) when Kite is unavailable so cards
    degrade gracefully during non-market hours or before login.
    """
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol_required")

    try:
        from dashboard.backend.routes.charts import (
            INTERVAL_MAP,
            DAYS_FOR_INTERVAL,
            _fetch_ohlc,
            _kite_symbol,
        )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"charts_module_unavailable: {exc}")

    kite_interval = INTERVAL_MAP.get(interval)
    if not kite_interval:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown interval '{interval}'. Use: 1m, 5m, 15m, 1h, 1D",
        )

    fetch_days = days or DAYS_FOR_INTERVAL.get(kite_interval, 3)
    kite_sym = _kite_symbol(sym)

    try:
        raw_candles = _fetch_ohlc(kite_sym, kite_interval, fetch_days)
    except RuntimeError as exc:
        err = str(exc)
        # Kite not configured / token expired → return empty bars (graceful degradation)
        if any(kw in err for kw in ("unavailable", "KITE_ACCESS_TOKEN", "invalid", "token")):
            return {"symbol": sym, "interval": interval, "bars": [], "count": 0, "kite_error": err}
        raise HTTPException(status_code=502, detail=err)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    bars = [
        {"time": c["time"], "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}
        for c in raw_candles
    ]
    return {"symbol": sym, "interval": interval, "bars": bars, "count": len(bars)}
