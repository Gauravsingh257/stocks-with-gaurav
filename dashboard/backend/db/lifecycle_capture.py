"""
dashboard/backend/db/lifecycle_capture.py
=========================================
Fills the four remaining gaps in the lifecycle record:

  1. chart_entry_json / chart_exit_json  real OHLC windows around entry and exit
  2. context_json                        confidence breakdown + sector strength
  3. algorithm_hash                      a producer, so the column means something
  4. MANUAL / PAPER                      writers for the two wired-but-empty sources

Everything here is best-effort and additive. A trade's record must never fail to
be written because a chart could not be fetched, so every capture path returns
None on error and the caller stores what it has. Where a value is unavailable it
stays absent rather than being filled with a placeholder — an empty field is
honest, an invented one is not.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from .schema import get_connection
from .trade_lifecycle import init_lifecycle_db, upsert, make_uuid

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

CHART_BARS_BEFORE = int(os.getenv("LIFECYCLE_CHART_BARS_BEFORE", "30"))
CHART_BARS_AFTER = int(os.getenv("LIFECYCLE_CHART_BARS_AFTER", "10"))


# ── 1. Chart capture ─────────────────────────────────────────────────────────

def fetch_ohlc_window(symbol: str, around: str | datetime | None,
                      before: int = CHART_BARS_BEFORE,
                      after: int = CHART_BARS_AFTER) -> dict | None:
    """Daily OHLC around a moment, via the Kite client the trackers already use.

    Returns None (not an empty chart) when the data is unavailable, so the UI
    can say "not captured" instead of rendering a blank plot that looks like a
    flat market.
    """
    try:
        if isinstance(around, str):
            anchor = datetime.fromisoformat(around.replace(" ", "T").replace("Z", "+00:00"))
        else:
            anchor = around or datetime.now(_IST)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=_IST)

        from services.fvg_tap_engine import _get_fvg_tap_kite_client
        kite = _get_fvg_tap_kite_client()
        if kite is None:
            return None
        sym = symbol if symbol.startswith("NSE:") else f"NSE:{symbol}"
        d = kite.ltp(sym)
        tok = int(list(d.values())[0]["instrument_token"]) if d else None
        if not tok:
            return None

        # Calendar padding for weekends/holidays — trading days are sparser.
        frm = anchor - timedelta(days=int(before * 1.6) + 7)
        to = anchor + timedelta(days=int(after * 1.6) + 7)
        bars = kite.historical_data(tok, frm.replace(tzinfo=None), to.replace(tzinfo=None), "day")
        if not bars:
            return None
        series = [{
            "d": b["date"].date().isoformat() if hasattr(b["date"], "date") else str(b["date"])[:10],
            "o": round(float(b["open"]), 2), "h": round(float(b["high"]), 2),
            "l": round(float(b["low"]), 2), "c": round(float(b["close"]), 2),
            "v": int(b.get("volume") or 0),
        } for b in bars if b.get("close")]
        if not series:
            return None
        anchor_day = anchor.date().isoformat()
        idx = next((i for i, s in enumerate(series) if s["d"] >= anchor_day), len(series) - 1)
        return {
            "symbol": sym, "interval": "day", "anchor": anchor_day,
            "anchor_index": max(idx - max(idx - before, 0), 0),
            "bars": series[max(idx - before, 0): idx + after + 1],
            "captured_at": datetime.now(_IST).isoformat(),
        }
    except Exception as exc:
        logger.debug("[LifecycleCapture] OHLC fetch failed for %s: %s", symbol, exc)
        return None


# ── 2. Context enrichment ────────────────────────────────────────────────────

def build_context(symbol: str, *, confidence: float | None = None,
                  reasoning: str | None = None, extra: dict | None = None) -> dict:
    """Confidence breakdown + sector strength + regime, as far as each is known.

    Absent components are omitted, never zero-filled: a confidence breakdown
    that shows 0 for an unmeasured factor reads as "we measured it and it was
    bad", which is a different and wrong claim.
    """
    ctx: dict = {"symbol": symbol}
    if confidence is not None:
        ctx["confidence"] = confidence
    if reasoning:
        ctx["reasoning"] = reasoning

    try:
        from services.feedback_analyzer import SWING_FACTORS  # noqa: F401
        if isinstance(extra, dict):
            factors = {k: v for k, v in extra.items()
                       if k in ("trend", "momentum", "breakout", "mtf_alignment",
                                "liquidity", "volume_expansion", "growth", "quality",
                                "institutional_accumulation") and v is not None}
            if factors:
                ctx["confidence_breakdown"] = factors
    except Exception:
        pass

    try:
        from services.sector_rotation import get_symbol_sector_strength  # type: ignore
        s = get_symbol_sector_strength(symbol)
        if s:
            ctx["sector_strength"] = s
    except Exception:
        # Sector strength is computed from constituents and is not always
        # available; leaving it out is better than guessing.
        pass

    try:
        from services.regime_governor import current_regime  # type: ignore
        r = current_regime()
        if r:
            ctx["market_regime"] = r
    except Exception:
        pass

    if isinstance(extra, dict):
        for k in ("atr_pct", "sector", "regime", "rs_20d", "entry_model",
                  "quality_score", "turnover_cr", "risk_weight_pct"):
            if extra.get(k) is not None:
                ctx.setdefault(k, extra[k])
    return ctx


# ── 3. algorithm_hash producer ───────────────────────────────────────────────

_HASH_ENV_KEYS = (
    "PORTFOLIO_MAX_SWING", "PORTFOLIO_MAX_LONGTERM", "PORTFOLIO_REENTRY_GUARD",
    "PORTFOLIO_REENTRY_COOLDOWN_DAYS", "PORTFOLIO_REENTRY_SAME_ENTRY_PCT",
    "PORTFOLIO_STALE_EXIT_MIN_DAYS", "PORTFOLIO_PENDING_MAX_DAYS",
    "PORTFOLIO_PENDING_MAX_SLIP_PCT", "RISK_ENGINE_ENABLED",
    "TREND_BREAK_EXIT_ENABLED", "MOMENTUM_MAX_POSITIONS",
)


def algorithm_hash(engine: str = "SMC", version: str | None = None) -> str:
    """Stable fingerprint of the parameters that decide behaviour.

    Two trades sharing a hash were produced under the same rules, so a version
    label alone (which people forget to bump) can't silently blur two different
    configurations together. Short digest — this identifies a config, it is not
    a security primitive.
    """
    parts = [engine, version or ""]
    for k in _HASH_ENV_KEYS:
        parts.append(f"{k}={os.getenv(k, '')}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def backfill_algorithm_hash() -> dict:
    """Stamp rows that predate the producer, grouped by engine + version."""
    init_lifecycle_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT engine, engine_version FROM trade_lifecycle "
            "WHERE algorithm_hash IS NULL"
        ).fetchall()
        n = 0
        for r in rows:
            h = algorithm_hash(r["engine"] or "SMC", r["engine_version"])
            cur = conn.execute(
                "UPDATE trade_lifecycle SET algorithm_hash = ? WHERE algorithm_hash IS NULL "
                "AND COALESCE(engine,'') = COALESCE(?,'') "
                "AND COALESCE(engine_version,'') = COALESCE(?,'')",
                (h, r["engine"], r["engine_version"]),
            )
            n += cur.rowcount or 0
        conn.commit()
        return {"ok": True, "stamped": n}
    except Exception as exc:
        logger.error("[LifecycleCapture] algorithm_hash backfill failed: %s", exc)
        return {"ok": False, "reason": str(exc)}
    finally:
        conn.close()


# ── 4. MANUAL / PAPER writers ────────────────────────────────────────────────

def record_manual_trade(payload: dict, source: str = "MANUAL") -> str:
    """Write a manually-entered or paper trade into the ledger.

    These sources were wired into the schema and filters but had no producer, so
    the options existed and always returned nothing. This is that producer.
    Charts and context are captured best-effort at write time.
    """
    src = source.upper()
    if src not in ("MANUAL", "PAPER"):
        raise ValueError("source must be MANUAL or PAPER")

    symbol = str(payload["symbol"]).strip().upper()
    if not symbol.startswith("NSE:"):
        symbol = f"NSE:{symbol}"

    entry = float(payload["entry_price"])
    sl = float(payload.get("stop_loss") or 0) or None
    exit_price = payload.get("exit_price")
    exit_price = float(exit_price) if exit_price not in (None, "") else None
    status = str(payload.get("status") or ("MANUAL_CLOSED" if exit_price else "ACTIVE")).upper()

    pnl = round((exit_price - entry) / entry * 100, 2) if (exit_price and entry) else None
    rr = None
    if pnl is not None and sl and entry > sl:
        risk = (entry - sl) / entry * 100
        rr = round(pnl / risk, 3) if risk else None

    sid = str(payload.get("external_id") or payload.get("id")
              or f"{symbol}-{payload.get('entry_at') or datetime.now(_IST).isoformat()}")
    ver = os.getenv("MANUAL_BOOK_VERSION", f"{src} v1")

    rec = {
        "uuid": make_uuid(src, src.lower(), sid),
        "source": src, "portfolio": src, "engine": "MANUAL",
        "stage": "POSITION",
        "setup": payload.get("setup"), "strategy": payload.get("strategy"),
        "symbol": symbol, "direction": (payload.get("direction") or "LONG").upper(),
        "confidence": payload.get("confidence"),
        "entry_price": entry, "stop_loss": sl,
        "target_1": payload.get("target_1"), "target_2": payload.get("target_2"),
        "target_3": payload.get("target_3"),
        "idea_at": payload.get("idea_at") or payload.get("entry_at"),
        "entry_trigger_at": payload.get("entry_at"),
        "entry_fill_at": payload.get("entry_at"),
        "exit_at": payload.get("exit_at"), "exit_price": exit_price,
        "exit_reason": payload.get("exit_reason"), "status": status,
        "pnl_pct": pnl, "rr_realized": rr,
        "holding_days": payload.get("holding_days"),
        "entry_reason": payload.get("entry_reason"),
        "exit_note": payload.get("exit_note"),
        "engine_version": ver,
        "algorithm_hash": algorithm_hash("MANUAL", ver),
        "recommendation_json": json.dumps(payload, default=str),
        "context_json": json.dumps(build_context(
            symbol, confidence=payload.get("confidence"),
            reasoning=payload.get("entry_reason"), extra=payload), default=str),
        "source_table": src.lower(), "source_id": sid,
        "is_legacy": 0,
    }

    chart_in = fetch_ohlc_window(symbol, payload.get("entry_at"))
    if chart_in:
        rec["chart_entry_json"] = json.dumps(chart_in)
    if payload.get("exit_at"):
        chart_out = fetch_ohlc_window(symbol, payload.get("exit_at"))
        if chart_out:
            rec["chart_exit_json"] = json.dumps(chart_out)

    return upsert(rec, event=f"{src}_TRADE_RECORDED")


# ── Chart backfill for existing rows ─────────────────────────────────────────

def capture_missing_charts(limit: int = 25) -> dict:
    """Attach entry/exit charts to rows that don't have them yet.

    Rate-limited by `limit` because each row costs a broker call; run repeatedly
    from the tracker rather than sweeping the whole ledger at once.
    """
    init_lifecycle_db()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT uuid, symbol, entry_fill_at, exit_at FROM trade_lifecycle "
            "WHERE executed = 1 AND chart_entry_json IS NULL "
            "ORDER BY datetime(COALESCE(exit_at, created_at)) DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()

    done = failed = 0
    for r in [dict(x) for x in rows]:
        entry_chart = fetch_ohlc_window(r["symbol"], r["entry_fill_at"])
        if not entry_chart:
            failed += 1
            continue
        exit_chart = fetch_ohlc_window(r["symbol"], r["exit_at"]) if r["exit_at"] else None
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE trade_lifecycle SET chart_entry_json = ?, chart_exit_json = ?, "
                "updated_at = ? WHERE uuid = ?",
                (json.dumps(entry_chart),
                 json.dumps(exit_chart) if exit_chart else None,
                 datetime.now(_IST).isoformat(), r["uuid"]),
            )
            conn.commit()
            done += 1
        except Exception:
            failed += 1
        finally:
            conn.close()
    return {"ok": True, "captured": done, "unavailable": failed, "scanned": len(rows)}
