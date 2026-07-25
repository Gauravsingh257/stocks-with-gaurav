"""
services/market_health.py — Layer 1 Market Intelligence (Phase 2 / EP1).

Produces a CONTINUOUS Market Health Score (0–100) instead of a discrete
Bull/Bear label. Health is a weighted blend of independent market signals:

    trend      — reuse services.market_regime (EMA/200DMA/ADX/52w on ^NSEI)
    breadth    — % of the scanned universe above its 50/200-DMA + adv/decline
                 (derived IN-SCAN from candles we already fetch; no new calls)
    volatility — India VIX level (best-effort; lower = healthier)
    rotation   — how many sectors are leading (reuse services.sector_strength)

Health drives *selectivity*, never a hard on/off. In Phase-2 the number is
consumed by the exceptionalism threshold (EP2); in EP1 it is purely additive
intelligence surfaced on /api/market/state + the UI. Missing inputs are
renormalized away (pattern from utils.confidence_v2) so a data outage lowers
precision, never blanks the page.

Flag: `MARKET_HEALTH_ENABLED` (default OFF). When off, callers behave exactly as
today (the discrete regime_governor path is untouched).
"""

from __future__ import annotations

import logging
import os
from datetime import date

log = logging.getLogger("services.market_health")

_BREADTH_REDIS_PREFIX = "market:breadth:"

# Sub-score weights (env-tunable). Renormalized over whichever inputs exist.
_DEFAULT_WEIGHTS = {"trend": 0.35, "breadth": 0.30, "volatility": 0.15, "rotation": 0.20}


def market_health_enabled() -> bool:
    return str(os.getenv("MARKET_HEALTH_ENABLED", "0")).strip().lower() in ("1", "true", "yes", "on")


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


def _breadth_redis_key() -> str:
    return f"{_BREADTH_REDIS_PREFIX}{date.today().isoformat()}"


# ── breadth (derived from the universe candles the scan already fetched) ───────

def breadth_from_candles(data: dict[str, list[dict]], *, cache: bool = True) -> dict:
    """Compute market breadth from {symbol: candles}. Returns
    {pct_above_200, pct_above_50, adv_decline_ratio, advancers, decliners,
     breadth_score(0..100), sampled}. Written to Redis so compute_market_health()
    (and the API) can read it without re-fetching. Never raises."""
    above200 = above50 = adv = dec = n200 = n50 = 0
    sampled = 0
    for _sym, candles in (data or {}).items():
        try:
            closes = [float(c["close"]) for c in candles if c.get("close") is not None]
        except Exception:
            continue
        if len(closes) < 51:
            continue
        sampled += 1
        last = closes[-1]
        prev = closes[-2]
        if last > prev:
            adv += 1
        elif last < prev:
            dec += 1
        sma50 = sum(closes[-50:]) / 50.0
        n50 += 1
        if last > sma50:
            above50 += 1
        if len(closes) >= 200:
            sma200 = sum(closes[-200:]) / 200.0
            n200 += 1
            if last > sma200:
                above200 += 1

    pct_above_200 = round(above200 / n200 * 100, 1) if n200 else None
    pct_above_50 = round(above50 / n50 * 100, 1) if n50 else None
    adv_decline = round(adv / dec, 2) if dec else (float(adv) if adv else None)

    # breadth_score: mostly % above 200-DMA (the secular participation gauge),
    # nudged by % above 50-DMA. If 200-DMA sample is thin, fall back to 50-DMA.
    if pct_above_200 is not None and pct_above_50 is not None:
        breadth_score = round(0.65 * pct_above_200 + 0.35 * pct_above_50, 1)
    elif pct_above_50 is not None:
        breadth_score = pct_above_50
    else:
        breadth_score = None

    result = {
        "pct_above_200dma": pct_above_200,
        "pct_above_50dma": pct_above_50,
        "adv_decline_ratio": adv_decline,
        "advancers": adv,
        "decliners": dec,
        "breadth_score": breadth_score,
        "sampled": sampled,
        "generated_for": date.today().isoformat(),
    }
    if cache and sampled:
        try:
            from dashboard.backend.cache import set as cache_set
            cache_set(_breadth_redis_key(), result, ttl_seconds=int(os.getenv("MARKET_BREADTH_TTL_SEC", "21600")))
        except Exception as exc:
            log.debug("breadth cache write failed: %s", exc)
    return result


def get_cached_breadth() -> dict | None:
    try:
        from dashboard.backend.cache import get as cache_get
        v = cache_get(_breadth_redis_key())
        return v if isinstance(v, dict) else None
    except Exception:
        return None


# ── sub-scores ────────────────────────────────────────────────────────────────

def _trend_subscore(regime) -> float | None:
    """0..100 from the trend regime. Above 200DMA + bullish EMA stack + rising
    slope near highs → high; below 200DMA + falling → low."""
    if regime is None or getattr(regime, "regime", "UNKNOWN") == "UNKNOWN":
        return None
    score = 50.0
    if getattr(regime, "above_200dma", False):
        score += 20
    else:
        score -= 20
    if float(getattr(regime, "ema_short", 0) or 0) > float(getattr(regime, "ema_long", 0) or 0):
        score += 10
    else:
        score -= 10
    slope = float(getattr(regime, "trend_slope", 0) or 0)
    score += max(-10.0, min(10.0, slope * 100.0))
    pct_high = float(getattr(regime, "pct_from_52w_high", 0) or 0)
    score += max(-10.0, 10.0 - pct_high)  # near highs adds, far below subtracts
    return _clamp(score)


def _volatility_subscore() -> float | None:
    """0..100 from India VIX (best-effort; lower VIX = healthier). India VIX may
    be blocked on Railway → returns None (renormalized away). ~11 → ~90, ~30 → ~10."""
    try:
        import yfinance as yf
        df = yf.Ticker(os.getenv("INDIA_VIX_TICKER", "^INDIAVIX")).history(period="5d")
        if df is None or df.empty:
            return None
        vix = float(df["Close"].iloc[-1])
    except Exception:
        return None
    lo, hi = _envf("VIX_HEALTHY", 11.0), _envf("VIX_STRESSED", 30.0)
    if hi <= lo:
        return 50.0
    # linear: vix<=lo → 100, vix>=hi → 0
    return _clamp((hi - vix) / (hi - lo) * 100.0)


def _rotation_subscore(strength: dict | None) -> float | None:
    """0..100 from breadth of sector leadership: more leading sectors (of those
    with a known band) = healthier participation."""
    if not strength:
        return None
    sectors = strength.get("sectors") or {}
    known = [v for v in sectors.values() if (v or {}).get("band") in ("leading", "neutral", "lagging")]
    if not known:
        return None
    leading = sum(1 for v in known if v["band"] == "leading")
    lagging = sum(1 for v in known if v["band"] == "lagging")
    # net leadership share, mapped to 0..100 (all leading → 100, all lagging → 0)
    net = (leading - lagging) / len(known)
    return _clamp(50.0 + net * 50.0)


def _opportunity_level(score: float) -> str:
    if score >= 70:
        return "RICH"        # broad opportunity — be aggressive
    if score >= 50:
        return "NORMAL"
    if score >= 30:
        return "SELECTIVE"   # only strong setups
    return "SCARCE"          # only exceptional setups


def health_to_state(score: float) -> str:
    """Map a 0..100 health score to the Phase-1 discrete state vocabulary, so the
    two representations stay consistent."""
    if score >= 75:
        return "STRONG_BULL"
    if score >= 60:
        return "WEAK_BULL"
    if score >= 45:
        return "SIDEWAYS"
    if score >= 30:
        return "CORRECTION"
    return "BEAR"


# ── composite ─────────────────────────────────────────────────────────────────

def compute_market_health(regime=None, strength: dict | None = None) -> dict:
    """Blend the sub-scores into a 0..100 Market Health Score. Never raises."""
    try:
        if regime is None:
            from services.market_regime import detect_regime
            regime = detect_regime()
    except Exception:
        regime = None
    if strength is None:
        try:
            from services.sector_strength import compute_sector_strength
            strength = compute_sector_strength()
        except Exception:
            strength = None

    breadth = get_cached_breadth()
    subs = {
        "trend": _trend_subscore(regime),
        "breadth": (breadth or {}).get("breadth_score") if breadth else None,
        "volatility": _volatility_subscore(),
        "rotation": _rotation_subscore(strength),
    }
    present = {k: v for k, v in subs.items() if v is not None}
    if not present:
        return {"available": False, "score": None, "opportunity_level": None, "subscores": subs}

    raw_w = {k: _envf(f"HEALTH_W_{k.upper()}", _DEFAULT_WEIGHTS[k]) for k in present}
    tw = sum(raw_w.values()) or 1.0
    weights = {k: raw_w[k] / tw for k in present}
    score = round(sum(weights[k] * present[k] for k in present), 1)

    return {
        "available": True,
        "score": score,
        "opportunity_level": _opportunity_level(score),
        "derived_state": health_to_state(score),
        "subscores": {k: (round(v, 1) if v is not None else None) for k, v in subs.items()},
        "weights": {k: round(w, 3) for k, w in weights.items()},
        "breadth": breadth,
    }
