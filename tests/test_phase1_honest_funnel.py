"""
Phase 1 — make the existing selection funnel honest.

As in Phase 0, the property that matters most is REVERSIBILITY: with every
PHASE1_* flag off, each touched path must behave exactly as it did on main.
"""

from __future__ import annotations

import pytest

from engine.swing import structural_target, structural_targets_enabled
from services.regime_governor import (
    SECTOR_LEADING,
    SECTOR_NONE,
    SECTOR_NOT_LAGGING,
    _sector_allows,
    sector_unknown_strict,
)
from services.validation_engine import (
    ENTRY_ANCHOR_MAX_GAP_PCT,
    entry_anchor_max_gap,
    strict_funnel_enabled,
    tight_entry_gap_enabled,
)

# ── flags all default OFF ─────────────────────────────────────────────────────

@pytest.mark.parametrize("fn,env", [
    (strict_funnel_enabled, "PHASE1_STRICT_FUNNEL"),
    (tight_entry_gap_enabled, "PHASE1_TIGHT_ENTRY_GAP"),
    (sector_unknown_strict, "PHASE1_SECTOR_UNKNOWN_STRICT"),
    (structural_targets_enabled, "PHASE1_STRUCTURAL_TARGETS"),
])
def test_phase1_flags_default_off(monkeypatch, fn, env):
    monkeypatch.delenv(env, raising=False)
    assert fn() is False


def test_unified_feed_flag_defaults_off(monkeypatch):
    from dashboard.backend.routes.research import unified_feed_enabled

    monkeypatch.delenv("PHASE1_UNIFIED_FEED", raising=False)
    assert unified_feed_enabled() is False


# ── entry gap ─────────────────────────────────────────────────────────────────

def test_entry_gap_unchanged_by_default(monkeypatch):
    monkeypatch.delenv("PHASE1_TIGHT_ENTRY_GAP", raising=False)
    assert entry_anchor_max_gap() == ENTRY_ANCHOR_MAX_GAP_PCT
    assert entry_anchor_max_gap() == pytest.approx(0.30)


def test_entry_gap_tightens_under_flag(monkeypatch):
    monkeypatch.setenv("PHASE1_TIGHT_ENTRY_GAP", "1")
    monkeypatch.delenv("PHASE1_ENTRY_GAP_PCT", raising=False)
    assert entry_anchor_max_gap() == pytest.approx(0.08)


def test_entry_gap_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("PHASE1_TIGHT_ENTRY_GAP", "1")
    monkeypatch.setenv("PHASE1_ENTRY_GAP_PCT", "12")
    assert entry_anchor_max_gap() == pytest.approx(0.12)


def test_research_levels_shares_one_gap_definition(monkeypatch):
    """The strict SMC path and the scored-SMC fallback must not disagree about
    what 'fillable' means, or a pick can be fillable in one and not the other."""
    from services.research_levels import RESEARCH_MAX_ENTRY_VS_CLOSE_PCT, _max_entry_gap

    monkeypatch.delenv("PHASE1_TIGHT_ENTRY_GAP", raising=False)
    assert _max_entry_gap() == RESEARCH_MAX_ENTRY_VS_CLOSE_PCT
    monkeypatch.setenv("PHASE1_TIGHT_ENTRY_GAP", "1")
    assert _max_entry_gap() == pytest.approx(0.08)


# ── sector bypass (F-10) ──────────────────────────────────────────────────────

def test_unknown_passes_not_lagging_by_default(monkeypatch):
    """Documents the pre-existing F-10 behaviour that ships unchanged."""
    monkeypatch.delenv("PHASE1_SECTOR_UNKNOWN_STRICT", raising=False)
    assert _sector_allows("unknown", SECTOR_NOT_LAGGING) is True


def test_unassigned_is_admissible_by_default(monkeypatch):
    """PRODUCT DECISION: an unclassifiable stock must NOT be skipped. Coverage is
    a limit of our reference data, not a property of the company — and the names
    outside the NIFTY Total Market list are exactly the small/micro caps where
    the outsized moves happen. Unassigned competes normally; only the
    diversification cap constrains it."""
    monkeypatch.setenv("PHASE1_SECTOR_UNKNOWN_STRICT", "1")
    monkeypatch.delenv("SECTOR_UNASSIGNED_BLOCKS", raising=False)
    assert _sector_allows("unknown", SECTOR_NOT_LAGGING) is True


def test_unassigned_can_be_blocked_by_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("SECTOR_UNASSIGNED_BLOCKS", "1")
    assert _sector_allows("unknown", SECTOR_NOT_LAGGING) is False
    # A known-good band must be untouched by the fix.
    assert _sector_allows("leading", SECTOR_NOT_LAGGING) is True
    assert _sector_allows("neutral", SECTOR_NOT_LAGGING) is True
    assert _sector_allows("lagging", SECTOR_NOT_LAGGING) is False


def test_strict_flag_does_not_alter_other_requirements(monkeypatch):
    monkeypatch.setenv("PHASE1_SECTOR_UNKNOWN_STRICT", "1")
    assert _sector_allows("unknown", SECTOR_NONE) is True
    assert _sector_allows("unknown", SECTOR_LEADING) is False


class _Item:
    def __init__(self, symbol: str):
        self.symbol = symbol


def _diversify(monkeypatch, sector: str, n: int):
    import services.regime_governor as rg

    monkeypatch.setattr(rg, "_sector_of", lambda sym, st: sector)
    items = [_Item(f"S{i}") for i in range(n)]
    return rg.enforce_sector_diversification(
        items, symbol_of=lambda i: i.symbol, strength={"sectors": {}}
    )


def test_unknown_bypasses_cap_by_default(monkeypatch):
    monkeypatch.delenv("PHASE1_SECTOR_UNKNOWN_STRICT", raising=False)
    kept, diag = _diversify(monkeypatch, "Unknown", 6)
    assert len(kept) == 6 and diag["dropped"] == 0


def test_unknown_capped_as_one_bucket_under_flag(monkeypatch):
    monkeypatch.setenv("PHASE1_SECTOR_UNKNOWN_STRICT", "1")
    monkeypatch.setenv("MAX_PER_SECTOR", "2")
    kept, diag = _diversify(monkeypatch, "Unknown", 6)
    assert len(kept) == 2
    assert diag["dropped"] == 4
    assert diag["per_sector"] == {"Unassigned": 2}


def test_known_sector_cap_identical_under_both_flag_states(monkeypatch):
    monkeypatch.setenv("MAX_PER_SECTOR", "2")
    monkeypatch.delenv("PHASE1_SECTOR_UNKNOWN_STRICT", raising=False)
    kept_off, _ = _diversify(monkeypatch, "Banking", 6)
    monkeypatch.setenv("PHASE1_SECTOR_UNKNOWN_STRICT", "1")
    kept_on, _ = _diversify(monkeypatch, "Banking", 6)
    assert len(kept_off) == len(kept_on) == 2


# ── structural targets / the upside ceiling ───────────────────────────────────

def _rising_candles(n: int = 200, start: float = 100.0) -> list[dict]:
    """Uptrend with regular pivot highs so pivots are findable."""
    out = []
    price = start
    for i in range(n):
        price *= 1.004
        bump = 1.03 if i % 20 == 10 else 1.0
        high = price * bump
        out.append({"open": price, "close": price, "high": high, "low": price * 0.99})
    return out


def test_structural_target_extends_beyond_fixed_r():
    """The 15% / 24% ceilings were `3R x capped stop`. When real overhead supply
    sits further away, the target must be allowed to reach it."""
    entry, risk = 100.0, 2.0
    fixed_ceiling = entry + risk * 3.0          # the legacy +6% / "3R" target

    # Overhead supply well beyond 3R: the target must be allowed to reach it
    # instead of stopping at the arithmetic ceiling.
    far = [{"open": 100, "close": 100, "high": 100, "low": 99} for _ in range(200)]
    far[100] = {"open": 100, "close": 100, "high": 112.0, "low": 99}
    target, basis = structural_target(far, entry, risk, default_r=3.0)
    assert basis == "structural"
    assert target == pytest.approx(112.0)   # 6R — past the 3R ceiling, inside the 8R cap
    assert target > fixed_ceiling, "structural target must be able to exceed the 3R ceiling"

    # Supply sitting close pulls the target IN — the mechanism is symmetric, not
    # a one-way licence to inflate upside.
    near = [{"open": 100, "close": 100, "high": 100, "low": 99} for _ in range(200)]
    near[100] = {"open": 100, "close": 100, "high": 104.5, "low": 99}
    target_near, basis_near = structural_target(near, entry, risk, default_r=3.0)
    assert basis_near == "structural"
    assert target_near == pytest.approx(104.5)
    assert target_near < fixed_ceiling


def test_structural_target_respects_max_r():
    candles = [{"open": 100, "close": 100, "high": 100_000, "low": 99} for _ in range(200)]
    target, basis = structural_target(candles, 100.0, 2.0, default_r=3.0, max_r=8.0)
    assert basis == "structural_capped_at_max_r"
    assert target == pytest.approx(100.0 + 2.0 * 8.0)


def test_structural_target_falls_back_when_no_overhead():
    """An all-time-high breakout has no pivot above it. There is no structural
    level to read, so the configured multiple must be kept rather than a
    projection invented."""
    candles = [{"open": 10, "close": 10, "high": 10.0, "low": 9} for _ in range(200)]
    target, basis = structural_target(candles, 100.0, 2.0, default_r=3.0)
    assert basis == "fixed_r_no_overhead"
    assert target == pytest.approx(106.0)


def test_structural_target_zero_risk_is_safe():
    target, basis = structural_target(_rising_candles(), 100.0, 0.0, default_r=3.0)
    assert basis == "fixed_r"
    assert target == pytest.approx(100.0)


def test_swing_target_r_default_reproduces_legacy(monkeypatch):
    """SWING_TARGET_R exists to make the multiple visible, not to change it."""
    monkeypatch.delenv("SWING_TARGET_R", raising=False)
    monkeypatch.delenv("PHASE1_STRUCTURAL_TARGETS", raising=False)
    entry, sl = 100.0, 95.0
    assert round(entry + (entry - sl) * float(__import__("os").getenv("SWING_TARGET_R", "3.0")), 2) == 115.0


# ── outcomes reporting ────────────────────────────────────────────────────────

def test_outcomes_hit_rate_includes_expiries():
    """An expiry is a non-win: capital was committed and the thesis did not pay.
    Excluding them reported 66.7% on a population whose all-in rate was 26.8%."""
    target_hit, stop_hit, expired = 5, 5, 61
    resolved = target_hit + stop_hit + expired
    decisive = target_hit + stop_hit
    assert round(target_hit / decisive * 100, 2) == 50.0        # old headline
    assert round(target_hit / resolved * 100, 2) == 7.04        # honest headline
    assert resolved == 71


def test_outcomes_expiry_share_is_reported():
    resolved, expired = 71, 61
    assert round(expired / resolved * 100, 2) == 85.92


# ── exceptionalism must not readmit what the funnel rejected ──────────────────

class _Rec:
    """Minimal stand-in for LayerValidationRecord."""

    def __init__(self, sym, l1, l2, l3, exc, entry=100.0):
        self.symbol = sym
        self.layer1_pass, self.layer2_pass, self.layer3_pass = l1, l2, l3
        self.entry = entry
        self.exceptionalism = {"qualifies": True, "exceptionalism": exc}
        self.final_selected = False
        self.rejection_reason = []


def _records():
    return [
        _Rec("CLEAN", True, True, True, 95.0),      # passes the funnel
        _Rec("NO_L1", False, True, True, 99.0),     # highest score, fails Layer 1
        _Rec("NO_L2", True, False, True, 98.0),     # fails Layer 2
    ]


def test_exceptionalism_readmits_layer1_failures_by_default(monkeypatch):
    """Documents the pre-existing behaviour that ships unchanged: the gate runs
    last and rewrites final_selected using only `qualifies` + a tradable entry."""
    from services.validation_engine import apply_exceptionalism_final_gate

    monkeypatch.delenv("PHASE1_STRICT_FUNNEL", raising=False)
    selected, n = apply_exceptionalism_final_gate(_records(), soft_ceiling=20)
    assert {r.symbol for r in selected} == {"CLEAN", "NO_L1", "NO_L2"}
    assert n == 3


def test_exceptionalism_respects_strict_funnel(monkeypatch):
    """With the strict funnel on, a stock the funnel rejected must NOT be
    readmitted by scoring well — otherwise the flag is a no-op in production,
    where EXCEPTIONALISM_ENABLED is set."""
    from services.validation_engine import apply_exceptionalism_final_gate

    monkeypatch.setenv("PHASE1_STRICT_FUNNEL", "1")
    recs = _records()
    selected, n = apply_exceptionalism_final_gate(recs, soft_ceiling=20)
    assert {r.symbol for r in selected} == {"CLEAN"}
    assert n == 1
    # and the rejected ones must be marked rejected, not left stale
    assert {r.symbol for r in recs if r.final_selected} == {"CLEAN"}


def test_exceptionalism_ceiling_still_applies_under_strict(monkeypatch):
    from services.validation_engine import apply_exceptionalism_final_gate

    monkeypatch.setenv("PHASE1_STRICT_FUNNEL", "1")
    recs = [_Rec(f"S{i}", True, True, True, 90.0 + i) for i in range(5)]
    selected, n = apply_exceptionalism_final_gate(recs, soft_ceiling=2)
    assert len(selected) == 2 and n == 5
    assert [r.symbol for r in selected] == ["S4", "S3"]   # highest score first


def test_unassigned_still_capped_for_concentration(monkeypatch):
    """Admissible is not the same as unlimited — six unclassified names must not
    all survive a max-2-per-sector cap."""
    monkeypatch.setenv("PHASE1_SECTOR_UNKNOWN_STRICT", "1")
    monkeypatch.setenv("MAX_PER_SECTOR", "2")
    kept, diag = _diversify(monkeypatch, "Unknown", 6)
    assert len(kept) == 2 and diag["dropped"] == 4


def test_provider_type_errors_do_not_drop_a_symbol():
    """yfinance sometimes returns a STRING where a number belongs. That raised
    `'<=' not supported between str and int` inside _norm_*, which
    analyze_fundamentals swallowed as a gather error — silently dropping the
    symbol from BOTH fundamentals and sector coverage."""
    from services.fundamental_analysis import _build_snapshot_from_info, _num

    assert _num("12.5") == 12.5
    assert _num("n/a") is None
    assert _num(None) is None
    assert _num(float("nan")) is None
    assert _num(True) is None

    hostile = {
        "trailingPE": "18.4", "priceToBook": "not-a-number",
        "returnOnEquity": "0.21", "revenueGrowth": float("nan"),
        "earningsGrowth": "", "debtToEquity": "45.3",
        "heldPercentInsiders": "0.55", "marketCap": "1234567890",
        "sector": "Basic Materials", "industry": "Specialty Chemicals",
    }
    snap = _build_snapshot_from_info("NSE:TEST", hostile)   # must not raise
    assert snap.data_source == "yfinance"
    assert snap.sector == "Basic Materials"
    assert snap.raw_pe == 18.4
    assert snap.raw_pb is None
