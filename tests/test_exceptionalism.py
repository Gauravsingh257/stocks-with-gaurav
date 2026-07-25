"""EP2 tests — Stock Exceptionalism (relative, threshold, override)."""

from __future__ import annotations

from services import exceptionalism as ex
from services.exceptionalism import (
    compute_exceptionalism, derive_dimensions, exceptionalism_enabled,
    exceptionalism_shadow_enabled, qualifies, required_exceptionalism,
    score_and_qualify,
)


# ── flags ─────────────────────────────────────────────────────────────────────

def test_flags_defaults(monkeypatch):
    monkeypatch.delenv("EXCEPTIONALISM_ENABLED", raising=False)
    monkeypatch.delenv("EXCEPTIONALISM_SHADOW", raising=False)
    assert exceptionalism_enabled() is False      # enforcement OFF by default
    assert exceptionalism_shadow_enabled() is True  # data collection ON by default


# ── RELATIVE, not absolute ────────────────────────────────────────────────────

def _disc(mom20=12.0, mom50=15.0, br=70.0, vol=60.0):
    return {"momentum_20d_pct": mom20, "momentum_50d_pct": mom50,
            "breakout_score": br, "volume_score": vol}


def test_exceptionalism_is_relative_to_market():
    """Same stock (+12% in 20d) is MORE exceptional when the market is weak."""
    weak = score_and_qualify(discovery=_disc(), smc_band=6.0, rr=3.0,
                             nifty_ret20=-2.0, sector_rel20=0.0, sector_band="leading",
                             entry_state="READY", market_health=30)
    strong = score_and_qualify(discovery=_disc(), smc_band=6.0, rr=3.0,
                               nifty_ret20=10.0, sector_rel20=0.0, sector_band="leading",
                               entry_state="READY", market_health=80)
    # +12% while Nifty −2% is exceptional; +12% while Nifty +10% is not.
    assert weak["exceptionalism"] > strong["exceptionalism"]


def test_rs_sector_dimension():
    # Outperforming the index but NOT its own (hot) sector → lower rs_sector.
    hot_sector = derive_dimensions(discovery=_disc(), smc_band=6, rr=3,
                                   nifty_ret20=0.0, sector_rel20=15.0)  # sector way ahead
    weak_sector = derive_dimensions(discovery=_disc(), smc_band=6, rr=3,
                                    nifty_ret20=0.0, sector_rel20=-5.0)
    assert weak_sector["rs_sector"] > hot_sector["rs_sector"]


def test_compute_renormalizes_missing_dims():
    out = compute_exceptionalism({"rs_nifty": 90, "smc": 60})
    assert 60 <= out["score"] <= 90
    assert abs(sum(v["weight"] for v in out["breakdown"].values()) - 1.0) < 1e-6


# ── adaptive threshold: market TIGHTENS, never blocks ─────────────────────────

def test_threshold_monotonic_decreasing_in_health():
    # Weaker market ⇒ higher bar.
    assert required_exceptionalism(90) < required_exceptionalism(50) < required_exceptionalism(20)


def test_threshold_clamped_and_unknown_neutral():
    assert 60 <= required_exceptionalism(100) <= 96
    assert 60 <= required_exceptionalism(0) <= 96
    assert 60 <= required_exceptionalism(None) <= 96  # never blocks on missing health


def test_market_tightens_not_blocks():
    # A mediocre score qualifies in a healthy tape but not a weak one …
    assert qualifies(70, market_health=85, sector_band="neutral")[0] is True
    assert qualifies(70, market_health=20, sector_band="neutral")[0] is False
    # … but a genuinely exceptional stock still qualifies even in a weak tape.
    assert qualifies(95, market_health=20, sector_band="neutral")[0] is True


# ── exceptional override (lagging sector) ─────────────────────────────────────

def test_lagging_sector_needs_override(monkeypatch):
    monkeypatch.setenv("EXC_OVERRIDE_MIN", "90")
    # Clears threshold but sector lagging and not exceptional enough → rejected.
    ok, reason, _ = qualifies(82, market_health=70, sector_band="lagging")
    assert ok is False and "lagging" in reason


def test_exceptional_override_surfaces(monkeypatch):
    monkeypatch.setenv("EXC_OVERRIDE_MIN", "90")
    ok, reason, _ = qualifies(93, market_health=70, sector_band="lagging")
    assert ok is True and reason == "exceptional_override"


def test_leading_sector_qualifies_at_threshold():
    ok, reason, _ = qualifies(75, market_health=70, sector_band="leading")
    assert ok is True and reason == "qualified"


# ── integration verdict shape ─────────────────────────────────────────────────

def test_score_and_qualify_shape():
    v = score_and_qualify(discovery=_disc(), smc_band=7.0, rr=3.5, nifty_ret20=-1.0,
                          sector_rel20=2.0, sector_band="leading", entry_state="READY",
                          market_health=40)
    for k in ("exceptionalism", "threshold", "qualifies", "reason", "breakdown"):
        assert k in v
    assert 0 <= v["exceptionalism"] <= 100
