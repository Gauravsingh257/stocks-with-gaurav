"""
services/risk_engine.py
=======================
Configurable, reversible risk engine for portfolio promotions and exits.

Backed by the 2026-07 historical simulation (58 closed trades): a stop-width cap
(~10% swing / ~15% long-term) plus risk-normalized position sizing lifted profit
factor 1.25 -> 1.68 and cut max drawdown ~53% -> ~11%, while a trend-break
(200-DMA + RS<0) exit beat a fixed max-loss exit (PF up, drawdown down, ZERO
winners hurt). See docs/RISK_ENGINE.md.

Design rules (per product requirements):
  - NOTHING is hardcoded — every threshold is an env var with an evidence-based
    default.
  - Every component is independently disable-able WITHOUT redeploy (flags read
    live from the environment on each call).
  - When a component is OFF the engine reproduces the OLD behavior exactly
    (equal-weight sizing, no cap, stale-cull), so rollback is instant.
  - Every promotion / exit decision is logged in full (old decision, new
    decision, size, stop width, ATR%, liquidity, trend, RS, reason, active
    flags) and best-effort persisted to Redis for later audit.

Pure-ish: sizing/cap math is deterministic and unit-testable. Trend-break needs
live 200-DMA + RS, which are fetched best-effort and cached per IST day.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from services.market_data_validation import finite_ohlc_frame, finite_or_none

log = logging.getLogger("services.risk_engine")

_IST = timezone(timedelta(hours=5, minutes=30))


# ── Config (all env-driven; defaults are the evidence-based band) ─────────────
def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def cfg() -> dict[str, Any]:
    """Snapshot of the live configuration (read fresh so env changes take effect
    without a redeploy). Also used verbatim in the decision log."""
    return {
        # master + component flags (each independently reversible)
        "RISK_ENGINE_ENABLED": _flag("RISK_ENGINE_ENABLED", "1"),
        "RISK_SIZING_ENABLED": _flag("RISK_SIZING_ENABLED", "1"),
        "STOP_CAP_ENABLED": _flag("STOP_CAP_ENABLED", "1"),
        "LIQUIDITY_ADJ_ENABLED": _flag("LIQUIDITY_ADJ_ENABLED", "1"),
        "TREND_BREAK_EXIT_ENABLED": _flag("TREND_BREAK_EXIT_ENABLED", "1"),
        # sizing
        "PORTFOLIO_NOTIONAL_CAPITAL": _f("PORTFOLIO_NOTIONAL_CAPITAL", 1_000_000.0),
        "RISK_PER_TRADE_PCT": _f("RISK_PER_TRADE_PCT", 1.0),
        "RISK_MAX_WEIGHT_PCT": _f("RISK_MAX_WEIGHT_PCT", 15.0),   # cap any single position's notional weight
        # stop-width caps (evidence: 8-10% swing sweet spot; wider tail is junk)
        "MAX_STOP_PCT": _f("MAX_STOP_PCT", 10.0),
        "MAX_STOP_LONGTERM_PCT": _f("MAX_STOP_LONGTERM_PCT", 15.0),
        # liquidity + ATR sizing
        "ATR_SIZE_REF_PCT": _f("ATR_SIZE_REF_PCT", 4.0),         # above this daily ATR%, scale size down
        "LIQ_MIN_TURNOVER_CR": _f("LIQ_MIN_TURNOVER_CR", 2.0),   # below this ₹Cr/day, scale size down
        "LIQ_MIN_SIZE_FACTOR": _f("LIQ_MIN_SIZE_FACTOR", 0.15),  # never shrink below this fraction
        # trend-break exit
        "TREND_BREAK_MIN_DAYS": _i("TREND_BREAK_MIN_DAYS", 3),
        "TREND_BREAK_DMA_BUFFER": _f("TREND_BREAK_DMA_BUFFER", 0.02),  # 2% below 200-DMA
        "TREND_BREAK_RS_LOOKBACK": _i("TREND_BREAK_RS_LOOKBACK", 20),
        # legacy stale-cull (kept for rollback; trend-break supersedes it when on)
        "PORTFOLIO_STALE_EXIT": _flag("PORTFOLIO_STALE_EXIT", "1"),
        "MAX_PORTFOLIO_POSITIONS": _i("PORTFOLIO_MAX_SWING", 20),
    }


# ── Decision records (fully logged) ───────────────────────────────────────────
@dataclass(slots=True)
class SizingDecision:
    symbol: str
    horizon: str
    accepted: bool
    reason: str
    stop_width_pct: float
    # old (current system) vs new engine
    old_accepted: bool
    old_position_value: float      # equal-weight notional (old sizing)
    new_position_value: float      # risk-normalized + liquidity-adjusted
    shares: int
    risk_weight_pct: float         # new position as % of capital
    risk_amount: float             # ₹ risked (capital * risk_per_trade%)
    atr_pct: float | None
    turnover_cr: float | None
    liquidity_factor: float
    atr_factor: float
    flags: dict[str, Any] = field(default_factory=dict)

    def to_log(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExitDecision:
    symbol: str
    should_exit: bool
    reason: str
    cmp: float
    dma200: float | None
    below_dma: bool
    rs_vs_nifty: float | None
    days_held: int
    flags: dict[str, Any] = field(default_factory=dict)

    def to_log(self) -> dict[str, Any]:
        return asdict(self)


# ── Audit trail (structured log + best-effort Redis) ──────────────────────────
def _persist(kind: str, payload: dict[str, Any]) -> None:
    """Append a decision to a Redis list for later audit. Best-effort; never
    raises. Keyed by IST day so it is easy to pull a day's decisions."""
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is None:
            return
        import json
        key = f"risk_engine:{kind}:{date.today().isoformat()}"
        r.rpush(key, json.dumps(payload, default=str))
        r.expire(key, 30 * 86400)
    except Exception:
        pass


# ── Liquidity / ATR metrics (best-effort, cached per IST day) ─────────────────
_liq_cache: dict[str, tuple[str, tuple[float | None, float | None]]] = {}


def liquidity_metrics(symbol: str) -> tuple[float | None, float | None]:
    """(atr_pct, turnover_cr) from ~20 recent daily bars. Cached once per IST
    day. Returns (None, None) on any failure so callers treat it as 'unknown'
    (no size penalty rather than a wrong penalty)."""
    today = date.today().isoformat()
    hit = _liq_cache.get(symbol)
    if hit and hit[0] == today:
        return hit[1]
    out: tuple[float | None, float | None] = (None, None)
    try:
        import yfinance as yf
        sym = symbol.replace("NSE:", "").strip().upper()
        df = finite_ohlc_frame(yf.Ticker(f"{sym}.NS").history(period="2mo"),
                               symbol=sym, context="risk_engine.liquidity")
        if df is not None and not df.empty and len(df) >= 15:
            h, l, c, v = df["High"], df["Low"], df["Close"], df["Volume"]
            import numpy as np
            tr = np.maximum(h - l, np.maximum(abs(h - c.shift()), abs(l - c.shift())))
            atr = float(tr.tail(14).mean())
            last = float(c.iloc[-1])
            atr_pct = atr / last * 100 if last > 0 else None
            turnover_cr = float((c.tail(20) * v.tail(20)).mean()) / 1e7  # ₹Cr/day
            # Never hand a non-finite metric to sizing: None means "no penalty"
            # (the documented fail-safe), NaN would silently compare False.
            out = (finite_or_none(atr_pct), finite_or_none(turnover_cr))
    except Exception as exc:
        log.debug("liquidity_metrics failed %s: %s", symbol, exc)
    _liq_cache[symbol] = (today, out)
    return out


# ── 1+2+4: promotion sizing (risk-normalized + stop cap + liquidity/ATR) ──────
def evaluate_promotion(
    symbol: str,
    horizon: str,
    entry: float,
    stop: float,
    *,
    atr_pct: float | None = None,
    turnover_cr: float | None = None,
) -> SizingDecision:
    """Decide whether to accept a promotion and at what size.

    When RISK_ENGINE_ENABLED=0 this returns the OLD behavior verbatim (accept,
    equal-weight sizing, no cap). Otherwise it applies, each independently
    flag-gated: stop-width cap (reject absurd stops), risk-normalized sizing
    (size ∝ risk budget / stop distance), and liquidity/ATR down-sizing.
    """
    c = cfg()
    horizon = (horizon or "SWING").upper()
    capital = c["PORTFOLIO_NOTIONAL_CAPITAL"]
    max_pos = c["MAX_PORTFOLIO_POSITIONS"] or 20
    equal_weight_value = capital / max_pos  # the OLD sizing (fixed equal slots)

    stop_width_pct = ((entry - stop) / entry * 100.0) if entry > 0 else 0.0
    old_accepted = stop < entry < float("inf") and entry > 0  # old path: any valid long

    flags = {k: c[k] for k in (
        "RISK_ENGINE_ENABLED", "RISK_SIZING_ENABLED", "STOP_CAP_ENABLED",
        "LIQUIDITY_ADJ_ENABLED",
    )}

    def _finalize(accepted: bool, reason: str, new_value: float,
                  liq_f: float, atr_f: float) -> SizingDecision:
        shares = int(new_value / entry) if entry > 0 else 0
        risk_amount = capital * c["RISK_PER_TRADE_PCT"] / 100.0
        d = SizingDecision(
            symbol=symbol.replace("NSE:", ""), horizon=horizon,
            accepted=accepted, reason=reason,
            stop_width_pct=round(stop_width_pct, 2),
            old_accepted=old_accepted, old_position_value=round(equal_weight_value, 2),
            new_position_value=round(new_value, 2), shares=shares,
            risk_weight_pct=round(new_value / capital * 100.0, 2) if capital else 0.0,
            risk_amount=round(risk_amount, 2),
            atr_pct=None if atr_pct is None else round(atr_pct, 2),
            turnover_cr=None if turnover_cr is None else round(turnover_cr, 2),
            liquidity_factor=round(liq_f, 3), atr_factor=round(atr_f, 3),
            flags=flags,
        )
        log.info("[RiskEngine][PROMOTE] %s/%s accept=%s reason=%s stop=%.1f%% "
                 "size=%.0f (old=%.0f) wt=%.1f%% liqF=%.2f atrF=%.2f atr%%=%s turnCr=%s",
                 d.symbol, horizon, accepted, reason, stop_width_pct,
                 new_value, equal_weight_value, d.risk_weight_pct, liq_f, atr_f,
                 d.atr_pct, d.turnover_cr)
        _persist("promotions", d.to_log())
        return d

    # Master off → pure legacy behavior.
    if not c["RISK_ENGINE_ENABLED"]:
        return _finalize(old_accepted, "engine_disabled_legacy", equal_weight_value, 1.0, 1.0)

    if entry <= 0 or stop <= 0 or stop >= entry:
        return _finalize(False, "invalid_geometry", 0.0, 1.0, 1.0)

    # (2) Stop-width cap
    cap = c["MAX_STOP_LONGTERM_PCT"] if horizon == "LONGTERM" else c["MAX_STOP_PCT"]
    if c["STOP_CAP_ENABLED"] and stop_width_pct > cap:
        return _finalize(False, f"stop_too_wide({stop_width_pct:.1f}%>{cap:.0f}%)",
                         0.0, 1.0, 1.0)

    # (1) Risk-normalized base size, or (fallback) old equal-weight.
    if not c["RISK_SIZING_ENABLED"]:
        return _finalize(True, "accept_equal_weight(sizing_off)", equal_weight_value, 1.0, 1.0)

    risk_amount = capital * c["RISK_PER_TRADE_PCT"] / 100.0
    risk_per_share = entry - stop
    base_value = (risk_amount / risk_per_share) * entry if risk_per_share > 0 else 0.0

    # (4) Liquidity + ATR down-sizing (only ever REDUCES size; never rejects).
    liq_f = atr_f = 1.0
    if c["LIQUIDITY_ADJ_ENABLED"]:
        a, t = atr_pct, turnover_cr
        if a is None or t is None:
            ma, mt = liquidity_metrics(symbol)
            a = a if a is not None else ma
            t = t if t is not None else mt
        atr_pct = a if atr_pct is None else atr_pct
        turnover_cr = t if turnover_cr is None else turnover_cr
        if atr_pct and atr_pct > c["ATR_SIZE_REF_PCT"]:
            atr_f = max(c["LIQ_MIN_SIZE_FACTOR"], c["ATR_SIZE_REF_PCT"] / atr_pct)
        if turnover_cr is not None and turnover_cr < c["LIQ_MIN_TURNOVER_CR"] and c["LIQ_MIN_TURNOVER_CR"] > 0:
            liq_f = max(c["LIQ_MIN_SIZE_FACTOR"], turnover_cr / c["LIQ_MIN_TURNOVER_CR"])

    new_value = base_value * liq_f * atr_f
    # Never let one position exceed the max weight cap.
    max_value = capital * c["RISK_MAX_WEIGHT_PCT"] / 100.0
    if new_value > max_value:
        new_value = max_value
    return _finalize(True, "accept_risk_sized", new_value, liq_f, atr_f)


# ── 3: trend-break exit (200-DMA + RS<0) ──────────────────────────────────────
_rs_cache: dict[str, tuple[str, float | None]] = {}


def _rs_vs_nifty(symbol: str, lookback: int) -> float | None:
    """Stock N-day return minus NIFTY N-day return (%). Cached per IST day."""
    today = date.today().isoformat()
    hit = _rs_cache.get(symbol)
    if hit and hit[0] == today:
        return hit[1]
    rs: float | None = None
    try:
        import yfinance as yf
        sym = symbol.replace("NSE:", "").strip().upper()
        sdf = finite_ohlc_frame(yf.Ticker(f"{sym}.NS").history(period="3mo"),
                                symbol=sym, context="risk_engine.rs")
        ndf = finite_ohlc_frame(yf.Ticker("^NSEI").history(period="3mo"),
                                symbol="^NSEI", context="risk_engine.rs")
        if sdf is not None and not sdf.empty and len(sdf) > lookback and ndf is not None and len(ndf) > lookback:
            sc, nc = sdf["Close"], ndf["Close"]
            sr = (sc.iloc[-1] / sc.iloc[-1 - lookback] - 1) * 100
            nr = (nc.iloc[-1] / nc.iloc[-1 - lookback] - 1) * 100
            rs = finite_or_none(sr - nr)
    except Exception as exc:
        log.debug("_rs_vs_nifty failed %s: %s", symbol, exc)
    _rs_cache[symbol] = (today, rs)
    return rs


def evaluate_trend_break_exit(symbol: str, cmp: float, days_held: int,
                              dma200: float | None) -> ExitDecision:
    """A held position exits when it has decisively lost its 200-DMA AND turned
    RS-negative vs NIFTY — the evidence-preferred exit (beats a fixed max-loss;
    zero winner damage). Flag-gated; when off, returns should_exit=False so the
    caller falls back to the legacy stale-cull.
    """
    c = cfg()
    flags = {k: c[k] for k in ("TREND_BREAK_EXIT_ENABLED", "TREND_BREAK_MIN_DAYS")}

    def _mk(should: bool, reason: str, below: bool, rs: float | None) -> ExitDecision:
        d = ExitDecision(symbol=symbol.replace("NSE:", ""), should_exit=should,
                         reason=reason, cmp=round(cmp, 2), dma200=dma200,
                         below_dma=below, rs_vs_nifty=None if rs is None else round(rs, 2),
                         days_held=days_held, flags=flags)
        if should:
            log.info("[RiskEngine][EXIT] %s TREND_BREAK cmp=%.2f dma200=%s rs=%s days=%d",
                     d.symbol, cmp, dma200, d.rs_vs_nifty, days_held)
            _persist("exits", d.to_log())
        return d

    if not c["RISK_ENGINE_ENABLED"] or not c["TREND_BREAK_EXIT_ENABLED"]:
        return _mk(False, "trend_break_disabled", False, None)
    if days_held < c["TREND_BREAK_MIN_DAYS"]:
        return _mk(False, "too_fresh", False, None)
    if not dma200 or dma200 <= 0:
        return _mk(False, "no_dma200", False, None)
    below = cmp < dma200 * (1.0 - c["TREND_BREAK_DMA_BUFFER"])
    if not below:
        return _mk(False, "above_dma200", False, None)
    rs = _rs_vs_nifty(symbol, c["TREND_BREAK_RS_LOOKBACK"])
    if rs is None:
        return _mk(False, "rs_unavailable(fail_safe_hold)", below, None)
    if rs < 0:
        return _mk(True, "trend_break_200dma_and_rs_negative", below, rs)
    return _mk(False, "rs_still_positive", below, rs)
