"""
services/momentum_candidate_pipeline.py
========================================
Phase-B candidate sourcing for the Momentum Portfolio.

Candidates START from the existing Discovery output (signals_log rows with
layer1_pass=1) — NOT limited to SMC rejects — deduped and ranked by discovery
confidence. Each is then run through the momentum engine's own stages
(eligibility → entry model → ranking) using the SAME functions the research
harness validated. Never re-runs or modifies Discovery / SMC.

Returns arm-ready candidate dicts (entry plan + features + discovery_rank +
momentum_rank + selection reason), regime-gated. Empty in a non-permitted
regime — so in a downtrend the portfolio simply does nothing.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date

log = logging.getLogger("services.momentum_candidate_pipeline")

# Benchmark tickers tried in order. The index symbol is preferred; the NIFTY ETF
# is an EQUITY ticker and stays reachable if index quotes are unavailable from
# the container (the same yfinance index limitation the sector scanner hit).
_BENCHMARK_TICKERS = ("^NSEI", "NIFTYBEES.NS")

# (day, candles) — the benchmark is fetched at most once per calendar day.
_nifty_cache: tuple[str, list[dict]] | None = None


def _to_candles(df) -> list[dict]:
    if df is None or df.empty:
        return []
    return [{"open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"]),
             "close": float(r["Close"]), "volume": float(r.get("Volume") or 0), "date": str(i)[:10]}
            for i, r in df.iterrows()]


def default_data_provider(symbol: str) -> tuple[float | None, list[dict]]:
    """(cmp, daily candles) via yfinance — works for equities on Railway."""
    try:
        import yfinance as yf
        sym = symbol.replace("NSE:", "").strip().upper()
        candles = _to_candles(yf.Ticker(f"{sym}.NS").history(period="1y"))
        return (candles[-1]["close"] if candles else None), candles
    except Exception as exc:
        log.debug("data provider failed %s: %s", symbol, exc)
        return None, []


def default_nifty_provider() -> list[dict]:
    """Daily NIFTY candles for relative strength, cached per calendar day.

    Every eligibility decision needs this: without a benchmark series `rs_20d` is
    None and the leadership gate fails as `rs_unknown` for EVERY candidate. So
    the live path must never run with an empty benchmark — it is fetched here
    rather than left to the caller.
    """
    global _nifty_cache
    today = date.today().isoformat()
    if _nifty_cache and _nifty_cache[0] == today and _nifty_cache[1]:
        return _nifty_cache[1]
    candles: list[dict] = []
    try:
        import yfinance as yf
        for tkr in _BENCHMARK_TICKERS:
            try:
                candles = _to_candles(yf.Ticker(tkr).history(period="1y"))
            except Exception as exc:
                log.debug("benchmark %s failed: %s", tkr, exc)
                continue
            if len(candles) > 20:
                log.info("[MomentumPipeline] benchmark %s: %d bars", tkr, len(candles))
                break
            candles = []
    except Exception as exc:
        log.warning("[MomentumPipeline] benchmark fetch failed: %s", exc)
    if not candles:
        log.error("[MomentumPipeline] NO benchmark series — RS is unavailable, "
                  "every candidate will fail the rs_unknown gate")
    _nifty_cache = (today, candles)
    return candles


class CandidateBatch(list):
    """Ranked candidates that also carry the per-stage funnel diagnostics.

    Subclasses `list` so every existing caller (and the injectable candidate
    provider used in tests) keeps working unchanged.
    """

    def __init__(self, items=(), funnel: dict | None = None):
        super().__init__(items)
        self.funnel: dict = funnel or {}


def _discovery_symbols(day: str | None, limit: int) -> list[dict]:
    """Discovery-passed rows for the latest scan day (layer1_pass=1), distinct
    symbols, ranked by discovery confidence. Read-only."""
    try:
        from dashboard.backend.db.schema import get_connection
    except Exception:
        return []
    conn = get_connection()
    try:
        if day is None:
            row = conn.execute("SELECT MAX(date) FROM signals_log WHERE layer1_pass=1").fetchone()
            day = row[0] if row else None
            if not day:
                return []
        rows = conn.execute(
            "SELECT symbol, cmp, confidence FROM signals_log "
            "WHERE date=? AND layer1_pass=1 ORDER BY confidence DESC", (day,)
        ).fetchall()
        seen, out = set(), []
        for r in rows:
            s = str(r["symbol"] or "").replace("NSE:", "").strip().upper()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append({"symbol": s, "cmp": r["cmp"], "confidence": r["confidence"]})
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


def _gate_name(failure: str) -> str:
    """'rs_below_min(3.1<5)' → 'rs_below_min' so failures aggregate."""
    return re.sub(r"\(.*\)$", "", failure).strip()


def get_ranked_candidates(day: str | None = None, nifty: list[dict] | None = None,
                          data_provider=None, nifty_provider=None) -> CandidateBatch:
    """Evaluate discovery candidates through the momentum engine, return the
    accepted ones ranked by Momentum Quality Score (best first).

    The result is a `CandidateBatch` (a list) whose `.funnel` explains exactly
    where the pool was lost — so an empty result is always attributable to a
    named stage rather than to silence.
    """
    from services.momentum_engine import eligibility, entry_models, ranking, router
    from services.momentum_engine.metrics import compute_metrics
    from services.momentum_engine.config import cfg
    try:
        from engine.swing import get_sector
    except Exception:
        def get_sector(_s):  # type: ignore
            return "Others"

    c = cfg()
    allowed, reg = router.regime_allows()
    if not allowed:
        log.info("[MomentumPipeline] regime %s not permitted — 0 candidates", reg)
        return CandidateBatch([], {"regime": reg, "regime_allowed": False, "accepted": 0,
                                   "blocked_by": "regime_gate"})

    data_provider = data_provider or default_data_provider
    if nifty is None:
        nifty = (nifty_provider or default_nifty_provider)()
    disc = _discovery_symbols(day, c["MOMENTUM_CANDIDATE_LIMIT"])
    funnel = {
        "regime": reg, "regime_allowed": True,
        "benchmark_bars": len(nifty or []),
        "discovery_pool": len(disc), "no_data": 0, "no_metrics": 0,
        "eligibility_failed": 0, "no_entry_model": 0, "accepted": 0,
    }
    gate_hits: Counter = Counter()
    rejects: list[dict] = []
    accepted: list[dict] = []
    for drank, d in enumerate(disc, 1):
        sym = d["symbol"]
        try:
            _cmp, candles = data_provider(sym)
        except Exception:
            funnel["no_data"] += 1
            continue
        if not candles:
            funnel["no_data"] += 1
            continue
        m = compute_metrics(candles, nifty)
        if m is None:
            funnel["no_metrics"] += 1
            continue
        elig = eligibility.evaluate(m)
        if not elig.passed:
            funnel["eligibility_failed"] += 1
            gate_hits.update(_gate_name(x) for x in elig.failures)
            if len(rejects) < 10:
                rejects.append({"symbol": sym, "stage": "eligibility",
                                "reasons": list(elig.failures)})
            continue
        sig = entry_models.detect_entry(m, candles)
        if sig is None:
            funnel["no_entry_model"] += 1
            if len(rejects) < 10:
                rejects.append({"symbol": sym, "stage": "entry_model",
                                "reasons": [f"volume_ratio={m.get('volume_ratio')}",
                                            f"base_atr_pct={m.get('base_atr_pct')}"]})
            continue
        q = ranking.score(m, sig, discovery_breakout_score=None, sector_score=0.5)
        accepted.append({
            "symbol": sym, "entry_price": sig.trigger, "stop_loss": sig.stop,
            "target_1": None, "target_2": None,   # momentum trails; no fixed target
            "arm_ref_price": m["last"], "quality_score": q.score,
            "entry_model": sig.model, "regime": reg, "sector": get_sector(sym),
            "rs_20d": m.get("rs_20d"), "volume_ratio": m.get("volume_ratio"),
            "trend_quality": m.get("trend_quality"), "atr_pct": m.get("atr_pct"),
            "base_atr_pct": m.get("base_atr_pct"), "breakout_score": None,
            "extension_atr": m.get("extension_atr"),
            "entry_reason": sig.reason, "discovery_rank": drank,
            "selection_reason": f"discovery#{drank}, {sig.model}, RS {m.get('rs_20d')}, quality {q.score}",
            "reasoning": json.dumps({"why_qualified": elig.to_dict(), "why_ranked": q.to_dict()}, default=str),
        })
    accepted.sort(key=lambda x: x["quality_score"], reverse=True)
    for i, cand in enumerate(accepted, 1):
        cand["momentum_rank"] = i
    funnel["accepted"] = len(accepted)
    funnel["top_gate_failures"] = dict(gate_hits.most_common(8))
    funnel["sample_rejects"] = rejects
    log.info("[MomentumPipeline] regime=%s benchmark=%dbars discovery=%d → no_data=%d "
             "elig_fail=%d no_entry=%d → %d ranked | gates=%s",
             reg, funnel["benchmark_bars"], len(disc), funnel["no_data"],
             funnel["eligibility_failed"], funnel["no_entry_model"], len(accepted),
             funnel["top_gate_failures"])
    return CandidateBatch(accepted, funnel)
