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

log = logging.getLogger("services.momentum_candidate_pipeline")


def default_data_provider(symbol: str) -> tuple[float | None, list[dict]]:
    """(cmp, daily candles) via yfinance — works for equities on Railway."""
    try:
        import yfinance as yf
        sym = symbol.replace("NSE:", "").strip().upper()
        df = yf.Ticker(f"{sym}.NS").history(period="1y")
        if df is None or df.empty:
            return None, []
        candles = [{"open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"]),
                    "close": float(r["Close"]), "volume": float(r["Volume"]), "date": str(i)[:10]}
                   for i, r in df.iterrows()]
        return (candles[-1]["close"] if candles else None), candles
    except Exception as exc:
        log.debug("data provider failed %s: %s", symbol, exc)
        return None, []


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


def get_ranked_candidates(day: str | None = None, nifty: list[dict] | None = None,
                          data_provider=None) -> list[dict]:
    """Evaluate discovery candidates through the momentum engine, return the
    accepted ones ranked by Momentum Quality Score (best first)."""
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
        return []

    data_provider = data_provider or default_data_provider
    disc = _discovery_symbols(day, c["MOMENTUM_CANDIDATE_LIMIT"])
    accepted: list[dict] = []
    for drank, d in enumerate(disc, 1):
        sym = d["symbol"]
        try:
            _cmp, candles = data_provider(sym)
        except Exception:
            continue
        if not candles:
            continue
        m = compute_metrics(candles, nifty)
        if m is None:
            continue
        elig = eligibility.evaluate(m)
        if not elig.passed:
            continue
        sig = entry_models.detect_entry(m, candles)
        if sig is None:
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
    log.info("[MomentumPipeline] regime=%s discovery=%d → %d ranked candidates", reg, len(disc), len(accepted))
    return accepted
