"""
services/momentum_portfolio_manager.py
=======================================
Domain logic for the INDEPENDENT Momentum Portfolio. Owns its own lifecycle,
sizing, replacement, and a momentum-specific exit engine. It does NOT import or
touch the Swing / Long-Term portfolio_manager, risk_engine, or portfolio_tracker
— those stay exactly as they are. Everything is config-driven
(services/momentum_engine/config.cfg) and the whole book is gated OFF until
MOMENTUM_PORTFOLIO_ENABLED=1.

Lifecycle: arm (PENDING) → tap (ACTIVE) → momentum exit engine → CLOSED/journal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from services.momentum_engine.config import cfg
from services.momentum_engine.research import trailing

log = logging.getLogger("services.momentum_portfolio_manager")
_IST = timezone(timedelta(hours=5, minutes=30))


# ── Sizing (INDEPENDENT of the Swing risk engine) ─────────────────────────────
def size_position(entry: float, stop: float) -> tuple[float, float]:
    """Risk-normalised notional + weight% from the momentum capital profile."""
    c = cfg()
    cap = c["MOMENTUM_NOTIONAL_CAPITAL"]
    risk_amt = cap * c["MOMENTUM_RISK_PER_TRADE_PCT"] / 100.0
    per_share = entry - stop
    if per_share <= 0 or cap <= 0:
        return 0.0, 0.0
    notional = (risk_amt / per_share) * entry
    return round(notional, 2), round(notional / cap * 100.0, 2)


def _in_swing(symbol: str) -> bool:
    """Read-only check: is the symbol already ACTIVE in the Swing/LT book?"""
    try:
        from dashboard.backend.db.portfolio import get_active_position_by_symbol
        return get_active_position_by_symbol(symbol) is not None
    except Exception:
        return False


def _make_room(new_score: float) -> bool:
    """Ensure a free slot for a candidate scoring `new_score`. Prefer expiring the
    weakest PENDING; only displace an ACTIVE if the new score beats the weakest
    by the configured edge. Returns True if room exists/was made."""
    from dashboard.backend.db import momentum_portfolio as db
    counts = db.get_counts()
    if counts["used"] < counts["max"]:
        return True
    # Book is full — find the weakest committed row.
    pend = [p for p in db.get_portfolio(include_pending=True) if p["status"] == "PENDING"]
    weakest_pending = min(pend, key=lambda p: p.get("quality_score") or 0, default=None)
    if weakest_pending and (weakest_pending.get("quality_score") or 0) < new_score:
        db.expire_pending(weakest_pending["id"], "REPLACED")
        return True
    lowest = db.lowest_active_score()
    edge = cfg()["MOMENTUM_REPLACEMENT_MIN_EDGE"]
    if lowest and new_score > (lowest.get("quality_score") or 0) + edge:
        # Displace the weakest ACTIVE at its current price (a managed exit).
        px = float(lowest.get("current_price") or lowest["entry_price"])
        db.close_position(lowest["id"], px, "REPLACED")
        log.info("[MomentumPortfolio] replaced %s (score %.1f) with candidate score %.1f",
                 lowest["symbol"], lowest.get("quality_score") or 0, new_score)
        return True
    return False


def arm(candidate: dict) -> int | None:
    """Arm a ranked momentum candidate as PENDING. `candidate` carries the entry
    plan + features + score. Honors duplicate-exposure, capacity, and quality
    replacement. Returns the new position id, or None if not armed."""
    c = cfg()
    if not c["MOMENTUM_PORTFOLIO_ENABLED"]:
        return None
    from dashboard.backend.db import momentum_portfolio as db

    sym = candidate["symbol"].strip().upper()
    if db.get_active_by_symbol(sym):
        return None  # already committed
    if not c["MOMENTUM_ALLOW_DUP_EXPOSURE"] and _in_swing(sym):
        log.info("[MomentumPortfolio] skip %s — dup exposure (active in Swing)", sym)
        return None

    entry = float(candidate["entry_price"]); stop = float(candidate["stop_loss"])
    if stop >= entry:
        return None
    score = float(candidate.get("quality_score") or 0)
    if not _make_room(score):
        log.info("[MomentumPortfolio] full — %s score %.1f below replacement bar", sym, score)
        return None

    notional, weight = size_position(entry, stop)
    try:
        return db.add_position({
            "symbol": sym, "entry_price": entry, "stop_loss": stop, "status": "PENDING",
            "target_1": candidate.get("target_1"), "target_2": candidate.get("target_2"),
            "arm_ref_price": candidate.get("arm_ref_price") or entry,
            "quality_score": score, "rank": candidate.get("rank"),
            "entry_model": candidate.get("entry_model"),
            "risk_model": f"{c['MOMENTUM_STOP_METHOD']}/{c['MOMENTUM_TRAIL_METHOD']}",
            "regime": candidate.get("regime"), "sector": candidate.get("sector"),
            "rs_20d": candidate.get("rs_20d"), "volume_ratio": candidate.get("volume_ratio"),
            "trend_quality": candidate.get("trend_quality"), "atr_pct": candidate.get("atr_pct"),
            "base_atr_pct": candidate.get("base_atr_pct"), "breakout_score": candidate.get("breakout_score"),
            "extension_atr": candidate.get("extension_atr"),
            "entry_reason": candidate.get("entry_reason"),
            "position_size": notional, "risk_weight_pct": weight,
            "reasoning": candidate.get("reasoning"),
        })
    except ValueError as exc:
        log.debug("[MomentumPortfolio] arm skipped %s: %s", sym, exc)
        return None


# ── Trigger + expiry (arm-on-tap) ─────────────────────────────────────────────
def process_pending(cmp_provider: Callable[[list[str]], dict[str, float]]) -> dict[str, int]:
    from dashboard.backend.db import momentum_portfolio as db
    c = cfg()
    pend = db.get_portfolio(include_pending=True)
    pend = [p for p in pend if p["status"] == "PENDING"]
    if not pend:
        return {"triggered": 0, "expired": 0}
    now = datetime.now(_IST)
    prices = cmp_provider([p["symbol"] for p in pend]) or {}
    trig = exp = 0
    for p in pend:
        # expiry by age
        try:
            created = datetime.fromisoformat(str(p["created_at"]).replace(" ", "T"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=_IST)
            if (now - created).days > c["MOMENTUM_PENDING_MAX_DAYS"]:
                if db.expire_pending(p["id"], "EXPIRED_TIMEOUT"):
                    exp += 1
                continue
        except Exception:
            pass
        px = prices.get(p["symbol"])
        if px is None:
            continue
        if px >= float(p["entry_price"]):          # breakout trigger tapped
            if db.activate_pending(p["id"], float(p["entry_price"])):
                trig += 1
    if trig or exp:
        log.info("[MomentumPortfolio] pending: %d triggered, %d expired", trig, exp)
    return {"triggered": trig, "expired": exp}


# ── Momentum exit engine (INDEPENDENT profile) ────────────────────────────────
def process_active(data_provider: Callable[[str], tuple[float | None, list[dict]]]) -> dict[str, int]:
    """Run the momentum exit engine over every ACTIVE position. `data_provider`
    returns (cmp, recent_candles) per symbol. Applies, in priority: target →
    stop/trail → hard max-loss → time-exit; otherwise updates breakeven + trail.
    """
    from dashboard.backend.db import momentum_portfolio as db
    c = cfg()
    active = [p for p in db.get_portfolio(include_pending=False) if p["status"] == "ACTIVE"]
    if not active:
        return {"exited": 0, "updated": 0}
    today = datetime.now(_IST).date()
    exited = updated = 0

    for p in active:
        try:
            cmp, candles = data_provider(p["symbol"])
        except Exception:
            continue
        if cmp is None:
            continue
        entry = float(p["entry_price"])
        init_stop = float(p.get("initial_stop") or p["stop_loss"])
        live_stop = float(p["stop_loss"])
        risk = entry - init_stop
        if risk <= 0:
            continue

        high_since = max(float(p.get("high_since_entry") or cmp), cmp)
        low_since = min(float(p.get("low_since_entry") or cmp), cmp)
        pl_pct = round((cmp - entry) / entry * 100, 2)
        mfe_r = round((high_since - entry) / risk, 3)
        mae_r = round((low_since - entry) / risk, 3)
        try:
            ent = datetime.fromisoformat(str(p.get("entered_at") or p["created_at"]).replace(" ", "T")).date()
            days_held = (today - ent).days
        except Exception:
            days_held = p.get("days_held", 0)
        target = p.get("target_2") or p.get("target_1")

        exit_reason = exit_px = None
        if target and cmp >= float(target):
            exit_reason, exit_px = "TARGET_HIT", cmp
        elif cmp <= live_stop:
            exit_reason = "STOP_HIT" if abs(live_stop - init_stop) < 1e-9 else "TRAIL_STOP"
            exit_px = live_stop
        elif pl_pct <= -c["MOMENTUM_MAX_LOSS_PCT"]:
            exit_reason, exit_px = "MAX_LOSS", cmp
        elif days_held >= c["MOMENTUM_TIME_EXIT_DAYS"]:
            exit_reason, exit_px = "TIME_EXIT", cmp

        if exit_reason:
            db.update_position(p["id"], current_price=cmp, profit_loss_pct=pl_pct,
                               high_since_entry=high_since, low_since_entry=low_since,
                               mfe_r=mfe_r, mae_r=mae_r, days_held=days_held)
            db.close_position(p["id"], exit_px, exit_reason)
            exited += 1
            continue

        # Not exiting → advance the trailing stop (only ever raises it).
        new_stop = live_stop
        if mfe_r >= c["MOMENTUM_BREAKEVEN_R"]:
            new_stop = max(new_stop, entry)
        tp = {"k": c["MOMENTUM_TRAIL_K"], "n": c["MOMENTUM_TRAIL_EMA_N"],
              "lookback": c["MOMENTUM_TRAIL_STRUCT_LOOKBACK"]}
        atr = _atr(candles)
        proposed = trailing.trail_stop(c["MOMENTUM_TRAIL_METHOD"], candles, atr, tp) if candles else None
        if proposed is not None:
            new_stop = max(new_stop, min(proposed, cmp * 0.999))

        db.update_position(
            p["id"], current_price=cmp, profit_loss_pct=pl_pct,
            drawdown_pct=round(min(pl_pct, 0.0), 2), high_since_entry=high_since,
            low_since_entry=low_since, mfe_r=mfe_r, mae_r=mae_r, days_held=days_held,
            stop_loss=round(new_stop, 2) if new_stop > live_stop else live_stop)
        updated += 1

    if exited:
        log.info("[MomentumPortfolio] exit engine: %d exited, %d updated", exited, updated)
    return {"exited": exited, "updated": updated}


def _atr(candles: list[dict], n: int = 14) -> float:
    if not candles or len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"]); l = float(candles[i]["low"]); pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    k = min(n, len(trs))
    return sum(trs[-k:]) / k if k else 0.0


def get_summary() -> dict:
    """Full independent portfolio snapshot for the API/UI."""
    from dashboard.backend.db import momentum_portfolio as db
    counts = db.get_counts()
    return {
        "name": "Momentum Portfolio",
        "positions": db.get_portfolio(include_pending=True),
        "count": counts["active"], "pending": counts["pending"],
        "used": counts["used"], "max": counts["max"],
        "journal_stats": db.get_journal_stats(),
        "enabled": cfg()["MOMENTUM_PORTFOLIO_ENABLED"],
    }
