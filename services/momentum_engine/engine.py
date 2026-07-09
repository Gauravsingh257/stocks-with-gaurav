"""
services/momentum_engine/engine.py
===================================
The Momentum Continuation Engine orchestrator — ties Phases 2–6 into a single,
deterministic, fully-logged decision per candidate:

    candidate → [master flag] → [regime router] → metrics → eligibility
              → entry model → ranking → MomentumDecision

Pure evaluation: `evaluate_candidate` takes the candidate + its candles (+ NIFTY
for RS) so it is unit-testable with no I/O. `run` is the thin batch driver that
pulls the SMC-reject feed and a data provider, evaluates each, and (in shadow)
only logs — it NEVER creates portfolio positions. Live promotion is Phase 7/12
and stays behind MOMENTUM_SHADOW_ONLY until then.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import candidate_feed, eligibility, entry_models, ranking, router
from .config import cfg
from .metrics import compute_metrics
from .models import MomentumCandidate, MomentumDecision

log = logging.getLogger("services.momentum_engine.engine")
_IST = timezone(timedelta(hours=5, minutes=30))


def _active_flags(c: dict) -> dict[str, Any]:
    return {k: c[k] for k in (
        "MOMENTUM_ENGINE_ENABLED", "MOMENTUM_SHADOW_ONLY",
        "MOMENTUM_REGIME_GATE_ENABLED", "MOMENTUM_ALLOCATION_PCT")}


def _persist(payload: dict[str, Any]) -> None:
    """Best-effort audit trail to Redis (mirrors risk_engine). Never raises."""
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is None:
            return
        key = f"momentum_engine:decisions:{date.today().isoformat()}"
        r.rpush(key, json.dumps(payload, default=str))
        r.expire(key, 30 * 86400)
    except Exception:
        pass


def evaluate_candidate(candidate: MomentumCandidate, candles: list[dict],
                       nifty: list[dict] | None = None,
                       sector_score: float = 0.5,
                       regime: str | None = None) -> MomentumDecision:
    """Deterministic per-candidate pipeline. Pure (no I/O besides the audit log)."""
    c = cfg()
    flags = _active_flags(c)
    sym = candidate.symbol

    def _decide(accepted: bool, stage: str, reason: str, **extra) -> MomentumDecision:
        d = MomentumDecision(symbol=sym, horizon=candidate.horizon, accepted=accepted,
                             stage=stage, reason=reason, regime=extra.get("regime"),
                             quality_score=extra.get("quality_score"),
                             entry=extra.get("entry"), eligibility=extra.get("eligibility"),
                             ranking=extra.get("ranking"), flags=flags)
        (log.info if accepted else log.debug)(
            "[Momentum] %s accepted=%s stage=%s reason=%s score=%s",
            sym, accepted, stage, reason, extra.get("quality_score"))
        _persist(d.to_dict())
        return d

    # Master flag — engine inert unless explicitly enabled.
    if not c["MOMENTUM_ENGINE_ENABLED"]:
        return _decide(False, "disabled", "engine_disabled")

    # Regime gate (SMC-first is already guaranteed by the feed).
    allowed, reg = router.regime_allows(regime)
    if not allowed:
        return _decide(False, "router", f"regime_not_allowed({reg})", regime=reg)

    # Metrics.
    m = compute_metrics(candles, nifty,
                        base_lookback=c["MOM_VCP_BASE_LOOKBACK"])
    if m is None:
        return _decide(False, "eligibility", "insufficient_data", regime=reg)

    # Eligibility.
    elig = eligibility.evaluate(m)
    if not elig.passed:
        return _decide(False, "eligibility", ",".join(elig.failures) or "ineligible",
                       regime=reg, eligibility=elig.to_dict())

    # Entry model.
    sig = entry_models.detect_entry(m, candles)
    if sig is None:
        return _decide(False, "entry", "no_entry_model_triggered",
                       regime=reg, eligibility=elig.to_dict())

    # Ranking.
    q = ranking.score(m, sig, discovery_breakout_score=candidate.breakout_score,
                      sector_score=sector_score)
    return _decide(True, "ranked", f"{sig.model}:{sig.reason}", regime=reg,
                   quality_score=q.score, entry=sig.to_dict(),
                   eligibility=elig.to_dict(), ranking=q.to_dict())


DataProvider = Callable[[str], tuple[list[dict], list[dict] | None]]
"""symbol -> (candles, nifty_candles). Injected so the engine stays testable and
data-source-agnostic (Kite in prod, yfinance/fixtures in tests/backtest)."""


def run(day: str | None = None, horizon: str = "SWING",
        data_provider: DataProvider | None = None,
        sector_score_provider: Callable[[str], float] | None = None) -> dict[str, Any]:
    """Batch driver: pull the SMC-reject feed for `day`, evaluate each candidate,
    return a ranked summary. SHADOW-SAFE — never promotes anything.
    """
    c = cfg()
    started = datetime.now(_IST).isoformat()
    candidates = candidate_feed.from_signals_log(day=day, horizon=horizon)
    if not candidates:
        return {"day": day, "horizon": horizon, "candidates": 0, "accepted": [],
                "started": started, "shadow": c["MOMENTUM_SHADOW_ONLY"]}

    if data_provider is None:
        data_provider = _default_data_provider

    reg = router.current_regime()
    accepted: list[dict] = []
    counts = {"evaluated": 0, "accepted": 0}
    for cand in candidates:
        try:
            candles, nifty = data_provider(cand.symbol)
        except Exception as exc:
            log.debug("data provider failed %s: %s", cand.symbol, exc)
            continue
        if not candles:
            continue
        counts["evaluated"] += 1
        ss = sector_score_provider(cand.symbol) if sector_score_provider else 0.5
        d = evaluate_candidate(cand, candles, nifty, sector_score=ss, regime=reg)
        if d.accepted:
            counts["accepted"] += 1
            accepted.append(d.to_dict())

    accepted.sort(key=lambda x: (x.get("quality_score") or 0), reverse=True)
    log.info("[Momentum] run day=%s regime=%s candidates=%d evaluated=%d accepted=%d (shadow=%s)",
             day, reg, len(candidates), counts["evaluated"], counts["accepted"],
             c["MOMENTUM_SHADOW_ONLY"])
    return {"day": day, "horizon": horizon, "regime": reg,
            "candidates": len(candidates), **counts,
            "shadow": c["MOMENTUM_SHADOW_ONLY"], "accepted": accepted, "started": started}


def _default_data_provider(symbol: str) -> tuple[list[dict], list[dict] | None]:
    """Production data provider — daily OHLC via yfinance (works on Railway for
    equities). Cached NIFTY. Best-effort; returns ([], None) on failure."""
    import yfinance as yf
    from .metrics import _closes  # noqa: F401  (kept import local/lazy)

    def _bars(sym: str) -> list[dict]:
        df = yf.Ticker(f"{sym}.NS").history(period="1y")
        if df is None or df.empty:
            return []
        return [{"open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"]),
                 "close": float(r["Close"]), "volume": float(r["Volume"]), "date": str(i)[:10]}
                for i, r in df.iterrows()]

    candles = _bars(symbol)
    nifty = _default_data_provider._nifty_cache  # type: ignore[attr-defined]
    if nifty is None:
        try:
            import yfinance as _yf
            ndf = _yf.Ticker("^NSEI").history(period="1y")
            nifty = [{"close": float(r["Close"]), "high": float(r["High"]),
                      "low": float(r["Low"]), "open": float(r["Open"]),
                      "volume": float(r["Volume"]), "date": str(i)[:10]}
                     for i, r in ndf.iterrows()] if ndf is not None and not ndf.empty else None
        except Exception:
            nifty = None
        _default_data_provider._nifty_cache = nifty  # type: ignore[attr-defined]
    return candles, nifty


_default_data_provider._nifty_cache = None  # type: ignore[attr-defined]
