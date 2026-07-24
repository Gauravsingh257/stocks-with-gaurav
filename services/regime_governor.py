"""
services/regime_governor.py

Market-Regime Governor (PR1) — promotes the read-only regime/sector *shadow*
(`services/ranking_engine._shadow_log_regime_sector`) into an ENFORCED,
graduated exposure gate for the long-only research + portfolio pipelines.

Design (per the approved plan):

  * Graduated, NOT binary. As the market deteriorates the engine tightens
    progressively — smaller idea cap, higher confidence/RR/SMC bars, stricter
    sector requirement, lower suggested exposure — instead of flipping from
    "BUY" to "NO BUY". A BEAR market can still surface a rare exceptional
    relative-strength name, but usually yields zero because the bar is high.

  * Cash is a first-class outcome. Quantity is an OUTPUT of quality:
    0 / 1 / 2 / 5 / 10 ideas are all valid. Nothing is force-filled.

  * Flag-gated + reversible. `REGIME_GOVERNOR_ENABLED` (default OFF) means the
    callers behave byte-identically to today (the shadow keeps logging). Every
    threshold is env-tunable live (no redeploy).

Reuses existing, production-proven inputs:
  * `services.market_regime.detect_regime()`  — NIFTY EMA/ADX/slope + (new) 200DMA & 52w-high.
  * `services.sector_strength.compute_sector_strength()` / `classify_symbol()`.

This module has NO side effects on import and never raises to its callers:
`classify_market_state()` / `exposure_state()` degrade to a non-blocking
UNKNOWN state on any data problem, so a yfinance outage can never empty the
research page by accident.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("services.regime_governor")

# ── Market states (ordered best → worst) ──────────────────────────────────────
STRONG_BULL = "STRONG_BULL"
WEAK_BULL = "WEAK_BULL"
SIDEWAYS = "SIDEWAYS"
CORRECTION = "CORRECTION"
BEAR = "BEAR"
UNKNOWN = "UNKNOWN"

# Severity order for "at this state or worse" comparisons (higher = worse).
_SEVERITY = {STRONG_BULL: 0, WEAK_BULL: 1, SIDEWAYS: 2, CORRECTION: 3, BEAR: 4, UNKNOWN: -1}

# Sector requirement modes for a policy.
SECTOR_NONE = "none"                      # keep all
SECTOR_NOT_LAGGING = "require_not_lagging" # drop hard-lagging sectors
SECTOR_LEADING = "require_leading"        # keep only leading sectors


# ── Flag / env helpers ────────────────────────────────────────────────────────

def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def governor_enabled() -> bool:
    """Master switch. OFF (default) ⟹ callers behave exactly as before."""
    return _truthy(os.getenv("REGIME_GOVERNOR_ENABLED", "0"))


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


# ── Policy ────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class GovernorPolicy:
    state: str
    max_ideas: int
    min_confidence: float      # 0..100
    min_rr: float
    min_smc_band: float        # 0..10 band (only enforced where an SMC band exists)
    sector_requirement: str    # SECTOR_NONE | SECTOR_NOT_LAGGING | SECTOR_LEADING
    exposure_pct: int          # suggested invested %
    label: str                 # e.g. "🟢 Aggressive"
    advisory: str

    @property
    def cash_pct(self) -> int:
        return max(0, 100 - int(self.exposure_pct))

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "max_ideas": self.max_ideas,
            "min_confidence": self.min_confidence,
            "min_rr": self.min_rr,
            "min_smc_band": self.min_smc_band,
            "sector_requirement": self.sector_requirement,
            "exposure_pct": self.exposure_pct,
            "cash_pct": self.cash_pct,
            "label": self.label,
            "advisory": self.advisory,
        }


def _exposure_label(exposure_pct: int) -> str:
    if exposure_pct >= 90:
        return "🟢 Aggressive"
    if exposure_pct >= 60:
        return "🟢 Normal"
    if exposure_pct >= 30:
        return "🟡 Defensive"
    return "🔴 Risk-Off"


def _advisory(exposure_pct: int) -> str:
    if exposure_pct >= 90:
        return "Full risk-on — broad participation across quality longs."
    if exposure_pct >= 60:
        return "Normal exposure — selective longs, keep some dry powder."
    if exposure_pct >= 30:
        return "Defensive — reduce size; only high-quality leaders qualify."
    return "Risk-off — heavy cash; only exceptional relative-strength setups qualify."


# Per-state defaults (all env-tunable, no redeploy). Mirrors the approved table.
_POLICY_DEFAULTS: dict[str, dict] = {
    STRONG_BULL: dict(max_ideas=15, min_conf=55, min_rr=2.0, min_smc=5.0, sector=SECTOR_NONE, exposure=100),
    WEAK_BULL:   dict(max_ideas=8,  min_conf=60, min_rr=2.5, min_smc=5.0, sector=SECTOR_NONE, exposure=75),
    SIDEWAYS:    dict(max_ideas=5,  min_conf=65, min_rr=2.5, min_smc=5.5, sector=SECTOR_NONE, exposure=50),
    CORRECTION:  dict(max_ideas=3,  min_conf=70, min_rr=3.0, min_smc=6.0, sector=SECTOR_NOT_LAGGING, exposure=30),
    BEAR:        dict(max_ideas=2,  min_conf=78, min_rr=3.0, min_smc=6.5, sector=SECTOR_LEADING, exposure=15),
}


def get_policy(state: str) -> GovernorPolicy:
    """Return the graduated policy for a market state (env-overridable).

    UNKNOWN is intentionally permissive (non-blocking): on a data outage we
    fall back to STRONG_BULL-equivalent caps so the governor never empties the
    page just because ^NSEI failed to fetch.
    """
    key = state if state in _POLICY_DEFAULTS else STRONG_BULL
    d = _POLICY_DEFAULTS[key]
    exposure = _envi(f"GOVERNOR_EXPOSURE_{key}", d["exposure"]) if state != UNKNOWN else 100
    return GovernorPolicy(
        state=state,
        max_ideas=_envi(f"GOVERNOR_MAX_IDEAS_{key}", d["max_ideas"]) if state != UNKNOWN else 9999,
        min_confidence=_envf(f"GOVERNOR_MIN_CONF_{key}", d["min_conf"]) if state != UNKNOWN else 0.0,
        min_rr=_envf(f"GOVERNOR_MIN_RR_{key}", d["min_rr"]) if state != UNKNOWN else 0.0,
        min_smc_band=_envf(f"GOVERNOR_MIN_SMC_{key}", d["min_smc"]) if state != UNKNOWN else 0.0,
        sector_requirement=os.getenv(f"GOVERNOR_SECTOR_{key}", d["sector"]) if state != UNKNOWN else SECTOR_NONE,
        exposure_pct=exposure,
        label=_exposure_label(exposure),
        advisory=_advisory(exposure),
    )


# ── Classification ────────────────────────────────────────────────────────────

@dataclass(slots=True)
class MarketStateResult:
    state: str
    regime_raw: str = UNKNOWN         # underlying detect_regime() label
    confidence: float = 0.0
    nifty_close: float = 0.0
    above_200dma: bool = False
    pct_from_52w_high: float = 0.0
    adx: float = 0.0
    trend_slope: float = 0.0
    source: str = "computed"          # "computed" | "forced" | "unknown_fallback"

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "regime_raw": self.regime_raw,
            "confidence": round(self.confidence, 3),
            "nifty_close": self.nifty_close,
            "above_200dma": self.above_200dma,
            "pct_from_52w_high": self.pct_from_52w_high,
            "adx": self.adx,
            "trend_slope": self.trend_slope,
            "source": self.source,
        }


def classify_market_state(regime=None) -> MarketStateResult:
    """Map the raw NIFTY regime into one of the 6 graduated market states.

    `GOVERNOR_FORCE_STATE` (test/validation only) pins the state deterministically.
    Never raises — any failure degrades to a non-blocking UNKNOWN.
    """
    forced = os.getenv("GOVERNOR_FORCE_STATE", "").strip().upper()
    if forced in _SEVERITY:
        return MarketStateResult(state=forced, regime_raw=forced, source="forced")

    try:
        if regime is None:
            from services.market_regime import detect_regime
            regime = detect_regime()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("regime detect failed, defaulting UNKNOWN: %s", exc)
        return MarketStateResult(state=UNKNOWN, source="unknown_fallback")

    if regime is None or getattr(regime, "regime", "UNKNOWN") == "UNKNOWN":
        return MarketStateResult(state=UNKNOWN, regime_raw="UNKNOWN", source="unknown_fallback")

    above200 = bool(getattr(regime, "above_200dma", False))
    pct_high = float(getattr(regime, "pct_from_52w_high", 0.0) or 0.0)
    ema_s = float(getattr(regime, "ema_short", 0.0) or 0.0)
    ema_l = float(getattr(regime, "ema_long", 0.0) or 0.0)
    close = float(getattr(regime, "nifty_close", 0.0) or 0.0)
    adx = float(getattr(regime, "adx", 0.0) or 0.0)
    slope = float(getattr(regime, "trend_slope", 0.0) or 0.0)

    ema_bull = ema_s > ema_l
    price_above_short = close > ema_s

    # Thresholds (env-tunable)
    bull_max_from_high = _envf("GOVERNOR_STRONG_BULL_MAX_PCT_FROM_HIGH", 8.0)
    correction_from_high = _envf("GOVERNOR_CORRECTION_PCT_FROM_HIGH", 10.0)
    bear_from_high = _envf("GOVERNOR_BEAR_PCT_FROM_HIGH", 20.0)
    adx_trend = _envf("GOVERNOR_ADX_TREND", 22.0)

    # Ordered decision tree (best→worst boundaries evaluated worst-first for
    # the down-trend legs, then up-trend legs).
    if (not above200) and (pct_high >= bear_from_high or (not ema_bull and slope < 0)):
        state = BEAR
    elif not above200:
        # below the 200DMA but not deeply broken → shallow/early correction
        state = CORRECTION
    elif above200 and pct_high >= correction_from_high and (not price_above_short or slope < 0):
        # above the 200DMA but pulling back meaningfully off the highs
        state = CORRECTION
    elif (
        above200 and ema_bull and price_above_short
        and pct_high <= bull_max_from_high and (adx >= adx_trend or slope > 0)
    ):
        state = STRONG_BULL
    elif above200 and (price_above_short or ema_bull):
        state = WEAK_BULL
    else:
        state = SIDEWAYS

    return MarketStateResult(
        state=state,
        regime_raw=getattr(regime, "regime", "UNKNOWN"),
        confidence=float(getattr(regime, "confidence", 0.0) or 0.0),
        nifty_close=round(close, 2),
        above_200dma=above200,
        pct_from_52w_high=round(pct_high, 2),
        adx=round(adx, 2),
        trend_slope=round(slope, 4),
    )


# ── Idea/record governing (generic core + two wrappers) ───────────────────────

def _rr_of(entry, stop, targets) -> float | None:
    try:
        entry = float(entry)
        stop = float(stop)
        tgts = [float(t) for t in (targets or []) if t is not None]
        if entry <= 0 or stop <= 0 or not tgts:
            return None
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        reward = abs(max(tgts) - entry)
        return reward / risk
    except (TypeError, ValueError):
        return None


def _sector_band(symbol: str, strength: dict | None) -> str:
    try:
        from services.sector_strength import classify_symbol
        return str(classify_symbol(symbol, strength).get("band") or "unknown")
    except Exception:
        return "unknown"


def _sector_allows(band: str, requirement: str) -> bool:
    if requirement == SECTOR_LEADING:
        return band == "leading"
    if requirement == SECTOR_NOT_LAGGING:
        return band != "lagging"
    return True  # SECTOR_NONE


@dataclass(slots=True)
class GovernDiagnostics:
    state: str
    policy: dict
    considered: int = 0
    kept: int = 0
    killed_confidence: int = 0
    killed_rr: int = 0
    killed_smc: int = 0
    killed_sector: int = 0
    capped: int = 0
    kept_symbols: list[str] = field(default_factory=list)
    killed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "policy": self.policy,
            "considered": self.considered,
            "kept": self.kept,
            "killed_confidence": self.killed_confidence,
            "killed_rr": self.killed_rr,
            "killed_smc": self.killed_smc,
            "killed_sector": self.killed_sector,
            "capped": self.capped,
            "kept_symbols": self.kept_symbols,
            "killed": self.killed[:50],
        }


def _govern(
    items: list,
    *,
    symbol_of,
    conf_of,
    rr_of,
    smc_band_of,
    state_result: MarketStateResult,
    strength: dict | None,
    policy: GovernorPolicy,
) -> tuple[list, GovernDiagnostics]:
    diag = GovernDiagnostics(state=state_result.state, policy=policy.to_dict(), considered=len(items))
    kept: list = []
    for it in items:
        sym = symbol_of(it)
        conf = conf_of(it)
        if conf is not None and conf < policy.min_confidence:
            diag.killed_confidence += 1
            diag.killed.append({"symbol": sym, "reason": "confidence", "value": round(float(conf), 2)})
            continue
        rr = rr_of(it)
        if rr is not None and rr < policy.min_rr:
            diag.killed_rr += 1
            diag.killed.append({"symbol": sym, "reason": "rr", "value": round(float(rr), 2)})
            continue
        if smc_band_of is not None and policy.min_smc_band > 0:
            band = smc_band_of(it)
            if band is not None and band < policy.min_smc_band:
                diag.killed_smc += 1
                diag.killed.append({"symbol": sym, "reason": "smc_band", "value": round(float(band), 2)})
                continue
        if policy.sector_requirement != SECTOR_NONE and sym:
            band = _sector_band(str(sym), strength)
            if not _sector_allows(band, policy.sector_requirement):
                diag.killed_sector += 1
                diag.killed.append({"symbol": sym, "reason": f"sector_{band}"})
                continue
        kept.append(it)

    # Cap to policy.max_ideas (items are assumed pre-sorted best-first by caller).
    if len(kept) > policy.max_ideas:
        diag.capped = len(kept) - policy.max_ideas
        kept = kept[: policy.max_ideas]

    diag.kept = len(kept)
    diag.kept_symbols = [str(symbol_of(x)) for x in kept]
    return kept, diag


def apply_to_ideas(ideas: list, horizon: str, state_result: MarketStateResult | None = None,
                   strength: dict | None = None) -> tuple[list, GovernDiagnostics]:
    """Govern a list of ranking `RankedIdea`-like objects (ranking→portfolio path).

    RankedIdea already passed the strict SMC materialization gate, so no SMC-band
    filter is applied here; confidence / RR / sector / cap are enforced.
    Caller must pass ideas already sorted best-first (ranking pipeline does).
    """
    if state_result is None:
        state_result = classify_market_state()
    policy = get_policy(state_result.state)
    return _govern(
        ideas,
        symbol_of=lambda i: getattr(i, "symbol", None),
        conf_of=lambda i: getattr(i, "confidence_score", None),
        rr_of=lambda i: _rr_of(getattr(i, "entry_price", None), getattr(i, "stop_loss", None), getattr(i, "targets", None)),
        smc_band_of=None,
        state_result=state_result,
        strength=strength,
        policy=policy,
    )


def apply_to_records(records: list, horizon: str, smc_band_of,
                     state_result: MarketStateResult | None = None,
                     strength: dict | None = None) -> tuple[list, GovernDiagnostics]:
    """Govern validation `LayerValidationRecord`-like objects (public feed path).

    `smc_band_of(record) -> float(0..10)` supplies the SMC band for the min-band
    filter. Caller passes records already sorted best-first.
    """
    if state_result is None:
        state_result = classify_market_state()
    policy = get_policy(state_result.state)
    return _govern(
        records,
        symbol_of=lambda r: getattr(r, "symbol", None),
        conf_of=lambda r: getattr(r, "confidence_score", None),
        rr_of=lambda r: _rr_of(getattr(r, "entry", None), getattr(r, "stop_loss", None), getattr(r, "targets", None)),
        smc_band_of=smc_band_of,
        state_result=state_result,
        strength=strength,
        policy=policy,
    )


# ── Sector leadership: multiplicative scoring + diversification (PR2) ─────────

def sector_scoring_enabled() -> bool:
    """Flag: multiply stock score by its sector-strength multiplier before ranking."""
    return _truthy(os.getenv("SECTOR_LEADERSHIP_SCORING_ENABLED", "0"))


def sector_diversification_enabled() -> bool:
    """Flag: cap the number of ideas per sector (max_per_sector)."""
    return _truthy(os.getenv("SECTOR_DIVERSIFICATION_ENABLED", "0"))


def max_per_sector() -> int:
    return max(1, _envi("MAX_PER_SECTOR", 2))


# Band → score multiplier. Leading sectors get a meaningful boost, lagging a
# heavy penalty; unknown is neutral (honest — no fabricated tilt). Env-tunable.
_BAND_MULT_DEFAULT = {"leading": 1.15, "neutral": 1.0, "lagging": 0.6, "unknown": 1.0}


def sector_multiplier(symbol: str, strength: dict | None = None) -> float:
    """Multiplier in [~0.4, ~1.3] for a stock's sector-strength band.

    Used as `final_score = stock_score * sector_multiplier` so leaders float up
    and laggards are heavily penalised *before* final ranking. Never raises;
    unknown/unmapped sectors return 1.0 (neutral), so a data gap can't distort
    ranking or silently drop a name.
    """
    try:
        band = _sector_band(str(symbol), strength)
    except Exception:
        return 1.0
    default = _BAND_MULT_DEFAULT.get(band, 1.0)
    return _envf(f"SECTOR_MULT_{band.upper()}", default)


def _sector_of(symbol: str, strength: dict | None) -> str:
    try:
        from services.sector_strength import classify_symbol
        return str(classify_symbol(symbol, strength).get("sector") or "Unknown")
    except Exception:
        return "Unknown"


def enforce_sector_diversification(items: list, symbol_of, strength: dict | None = None,
                                   cap: int | None = None) -> tuple[list, dict]:
    """Keep at most `cap` items per sector, preserving the caller's (best-first)
    order. Returns (kept, diagnostics). "Unknown" sectors are NOT capped
    (we don't group honestly-unknown names into one bucket).
    """
    cap = cap if cap is not None else max_per_sector()
    strength = strength if strength is not None else _safe_strength()
    kept: list = []
    per_sector: dict[str, int] = {}
    dropped: list[dict] = []
    for it in items:
        sym = str(symbol_of(it) or "")
        sector = _sector_of(sym, strength)
        if sector in ("Unknown", "Others", ""):
            kept.append(it)
            continue
        used = per_sector.get(sector, 0)
        if used >= cap:
            dropped.append({"symbol": sym, "sector": sector})
            continue
        per_sector[sector] = used + 1
        kept.append(it)
    return kept, {"cap": cap, "kept": len(kept), "dropped": len(dropped),
                  "per_sector": per_sector, "dropped_items": dropped[:50]}


def _safe_strength() -> dict:
    try:
        from services.sector_strength import compute_sector_strength
        return compute_sector_strength()
    except Exception:
        return {}


# ── API / UI payload ──────────────────────────────────────────────────────────

def exposure_state(state_result: MarketStateResult | None = None,
                   strength: dict | None = None) -> dict:
    """The exposure/regime block for the Research feed + Command Center + the
    `GET /api/market/state` endpoint.

    Safe to call even when the governor is disabled — it then acts as an
    *informational* market-health readout (it does not change what the feed
    serves; the callers only ENFORCE when `governor_enabled()`).
    """
    try:
        if state_result is None:
            state_result = classify_market_state()
        policy = get_policy(state_result.state)
        leading: list[str] = []
        lagging: list[str] = []
        sector_bands: dict[str, str] = {}
        try:
            strength = strength if strength is not None else _safe_strength()
            leading = list(strength.get("leading") or [])
            secs = strength.get("sectors") or {}
            for name, sv in secs.items():
                band = (sv or {}).get("band", "unknown")
                sector_bands[name] = band
                if band == "lagging":
                    lagging.append(name)
        except Exception:
            leading, lagging, sector_bands = [], [], {}
        return {
            "sector_leadership_enabled": sector_scoring_enabled(),
            "sector_diversification_enabled": sector_diversification_enabled(),
            "max_per_sector": max_per_sector(),
            "lagging_sectors": lagging,
            "sector_bands": sector_bands,
            "governor_enabled": governor_enabled(),
            "market_state": state_result.state,
            "regime_raw": state_result.regime_raw,
            "confidence": round(state_result.confidence, 3),
            "nifty_close": state_result.nifty_close,
            "above_200dma": state_result.above_200dma,
            "pct_from_52w_high": state_result.pct_from_52w_high,
            "adx": state_result.adx,
            "exposure_pct": policy.exposure_pct,
            "cash_pct": policy.cash_pct,
            "exposure_label": policy.label,
            "suggested_max_ideas": policy.max_ideas,
            "min_confidence": policy.min_confidence,
            "min_rr": policy.min_rr,
            "sector_requirement": policy.sector_requirement,
            "leading_sectors": leading,
            "advisory": policy.advisory,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # pragma: no cover - never break a caller
        log.debug("exposure_state failed: %s", exc)
        return {
            "governor_enabled": governor_enabled(),
            "market_state": UNKNOWN,
            "exposure_pct": 100,
            "cash_pct": 0,
            "exposure_label": _exposure_label(100),
            "advisory": "Market state unavailable.",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
