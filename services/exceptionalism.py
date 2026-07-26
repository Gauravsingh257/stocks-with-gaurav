"""
services/exceptionalism.py — Layer 3 Stock Exceptionalism (Phase 2 / EP2).

The heart of the "Exceptional Stock Discovery" engine. Answers ONE question:

    "Is this stock significantly stronger than the CURRENT market environment?"

Not "is it good" (absolute) but "is it exceptional RIGHT NOW, relative to a tape
that may be weak." Built from ORTHOGONAL factors so momentum is not triple-counted:

    rs_nifty          relative strength vs NIFTY (stock 20d return − index 20d)
    rs_sector         relative strength vs its own sector
    breakout          breakout / 52-week-high structure quality
    volume            volume confirmation
    smc               SMC structural confluence (OB/FVG/BOS)
    risk              risk / structure quality (RR, stop)
    freshness         entry not already extended (READY/WATCH high)
    market_alignment  the stock's own medium-trend durability (not a dead-cat pop)

Governing principles (per product owner):
  * The MARKET never rejects a stock. Market Health only raises the *threshold*
    a stock must clear — `required_exceptionalism(health)`. Discovery never stops.
  * EXCEPTIONAL OVERRIDE: a truly exceptional stock qualifies even if its sector
    is not leading — rare, explainable, and logged.
  * Every scanned stock's exceptionalism is logged (via signals_log) so thresholds
    can be calibrated later on real outcomes — not assumptions.
  * Feature-flagged + shadow-validated; the SERVED feed is byte-identical when
    enforcement is OFF.

Flags:
  EXCEPTIONALISM_ENABLED  (default OFF) — enforce the threshold on the served feed.
  EXCEPTIONALISM_SHADOW   (default ON)  — compute + log for every scanned stock.
All weights / thresholds are env-tunable (calibrate later on production data).
"""

from __future__ import annotations

import os

DIMENSIONS = (
    "rs_nifty", "rs_sector", "breakout", "volume",
    "smc", "risk", "freshness", "market_alignment",
)

_DEFAULT_WEIGHTS = {
    "rs_nifty": 0.20, "rs_sector": 0.15, "breakout": 0.12, "volume": 0.10,
    "smc": 0.15, "risk": 0.10, "freshness": 0.08, "market_alignment": 0.10,
}


def exceptionalism_enabled() -> bool:
    return str(os.getenv("EXCEPTIONALISM_ENABLED", "0")).strip().lower() in ("1", "true", "yes", "on")


def exceptionalism_shadow_enabled() -> bool:
    return str(os.getenv("EXCEPTIONALISM_SHADOW", "1")).strip().lower() in ("1", "true", "yes", "on")


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    if v != v or v in (float("inf"), float("-inf")):   # NaN / ±Inf → floor (JSON-safe)
        return lo
    return max(lo, min(hi, v))


def _scale(value: float, lo: float, hi: float) -> float:
    if hi == lo:
        return 50.0
    return _clamp((value - lo) / (hi - lo) * 100.0)


# ── dimension derivation (all RELATIVE / orthogonal) ──────────────────────────

_FRESHNESS_MAP = {"READY": 100.0, "WATCH": 70.0, "IN_MOTION": 35.0, "MISSED": 0.0}


def derive_dimensions(
    *,
    discovery: dict | None,
    smc_band: float | None,
    rr: float | None,
    nifty_ret20: float | None,
    sector_rel20: float | None,
    entry_state: str | None = None,
) -> dict[str, float]:
    """Map available signals into the 8 orthogonal dimensions (each 0..100).

    `discovery` = DiscoveryCandidate.to_dict() (momentum_20d/50d_pct, breakout_score,
    volume_score). `sector_rel20` = the stock's SECTOR relative strength vs NIFTY
    (so stock-vs-sector = rs_nifty_raw − sector_rel20). Missing inputs are simply
    omitted → renormalized by compute_exceptionalism (never fabricated)."""
    d = discovery or {}
    dims: dict[str, float] = {}

    mom20 = d.get("momentum_20d_pct")
    mom50 = d.get("momentum_50d_pct")

    # RS vs NIFTY — the core "stronger than the index" signal (momentum MINUS breadth).
    if mom20 is not None and nifty_ret20 is not None:
        rs_nifty_raw = float(mom20) - float(nifty_ret20)
        dims["rs_nifty"] = _scale(rs_nifty_raw, _envf("EXC_RSN_LO", -8.0), _envf("EXC_RSN_HI", 18.0))
        # RS vs SECTOR — stronger than its own peers.
        if sector_rel20 is not None:
            rs_sector_raw = rs_nifty_raw - float(sector_rel20)
            dims["rs_sector"] = _scale(rs_sector_raw, _envf("EXC_RSS_LO", -8.0), _envf("EXC_RSS_HI", 15.0))

    if d.get("breakout_score") is not None:
        dims["breakout"] = _clamp(d["breakout_score"])
    if d.get("volume_score") is not None:
        dims["volume"] = _clamp(d["volume_score"])
    if smc_band is not None:
        dims["smc"] = _clamp(float(smc_band) * 10.0)  # 0..10 band → 0..100
    if rr is not None:
        from utils.confidence_v2 import risk_quality_score
        dims["risk"] = risk_quality_score(rr)
    if entry_state is not None:
        dims["freshness"] = _FRESHNESS_MAP.get(str(entry_state).upper(), 60.0)
    # Market alignment — the stock's OWN medium-trend durability (not a dead-cat
    # bounce). Orthogonal to the 20d relative-strength dims.
    if mom50 is not None:
        dims["market_alignment"] = _scale(float(mom50), _envf("EXC_MA_LO", -10.0), _envf("EXC_MA_HI", 25.0))
    return dims


def compute_exceptionalism(dims: dict[str, float]) -> dict:
    """Blend the orthogonal dimensions into a 0..100 score. Weights renormalize
    over whichever dimensions are present. Returns {score, breakdown}."""
    present = {k: _clamp(dims.get(k)) for k in DIMENSIONS if k in (dims or {}) and dims.get(k) is not None}
    if not present:
        return {"score": 0.0, "breakdown": {}}
    raw_w = {k: _envf(f"EXC_W_{k.upper()}", _DEFAULT_WEIGHTS[k]) for k in present}
    tw = sum(raw_w.values()) or 1.0
    weights = {k: raw_w[k] / tw for k in present}
    score = round(sum(weights[k] * present[k] for k in present), 2)
    breakdown = {k: {"score": round(present[k], 1), "weight": round(weights[k], 3)} for k in present}
    return {"score": score, "breakdown": breakdown}


# ── adaptive threshold + qualification ────────────────────────────────────────

def required_exceptionalism(market_health: float | None) -> float:
    """The bar a stock must clear, as a function of Market Health (0..100).

    Market Health only *tightens* the standard — it never blocks discovery.
    Healthy tape → easy (~60); deteriorating tape → harder (up to ~96). Linear
    and fully env-tunable; calibrate on production data later.
        threshold = base + (100 − health) * slope   (clamped [min, max])
    """
    base = _envf("EXC_THRESHOLD_BASE", 60.0)     # bar at perfect health
    slope = _envf("EXC_THRESHOLD_SLOPE", 0.36)   # per point of health lost
    lo = _envf("EXC_THRESHOLD_MIN", 60.0)
    hi = _envf("EXC_THRESHOLD_MAX", 96.0)
    if market_health is None:
        # No health reading → use a neutral-ish bar (mid), never block discovery.
        return _clamp(_envf("EXC_THRESHOLD_UNKNOWN", 72.0), lo, hi)
    return round(_clamp(base + (100.0 - float(market_health)) * slope, lo, hi), 1)


def qualifies(score: float, market_health: float | None, sector_band: str | None) -> tuple[bool, str, float]:
    """Decide whether a stock qualifies under today's environment.

    Returns (qualified, reason, threshold). The market tightens the bar; a lagging
    sector tightens it further (must be truly exceptional to override). An
    UNKNOWN/neutral/leading sector qualifies at the normal threshold.
    """
    threshold = required_exceptionalism(market_health)
    override_min = _envf("EXC_OVERRIDE_MIN", 90.0)
    band = (sector_band or "unknown").lower()

    if score < threshold:
        return False, f"below_threshold_{threshold:g}", threshold
    if band == "lagging":
        if score >= override_min:
            return True, "exceptional_override", threshold   # rare + logged
        return False, f"lagging_sector_needs_{override_min:g}", threshold
    return True, "qualified", threshold


def score_and_qualify(
    *, discovery, smc_band, rr, nifty_ret20, sector_rel20, sector_band,
    entry_state=None, market_health=None,
) -> dict:
    """One-call convenience: derive → score → qualify. Returns a compact,
    loggable verdict for every scanned stock (shadow dataset)."""
    dims = derive_dimensions(
        discovery=discovery, smc_band=smc_band, rr=rr,
        nifty_ret20=nifty_ret20, sector_rel20=sector_rel20, entry_state=entry_state,
    )
    scored = compute_exceptionalism(dims)
    ok, reason, threshold = qualifies(scored["score"], market_health, sector_band)
    return {
        "exceptionalism": scored["score"],
        "threshold": threshold,
        "qualifies": ok,
        "reason": reason,
        "sector_band": (sector_band or "unknown"),
        "market_health": market_health,
        "breakdown": scored["breakdown"],
    }
