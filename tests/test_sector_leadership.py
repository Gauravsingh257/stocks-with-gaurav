"""Tests for PR2 — sector-leadership multiplicative scoring + diversification."""

from __future__ import annotations

from dataclasses import dataclass

from services import regime_governor as gov
from services.regime_governor import (
    enforce_sector_diversification,
    max_per_sector,
    sector_diversification_enabled,
    sector_multiplier,
    sector_scoring_enabled,
)


@dataclass
class Idea:
    symbol: str


# ── flags default off ─────────────────────────────────────────────────────────

def test_flags_default_off(monkeypatch):
    monkeypatch.delenv("SECTOR_LEADERSHIP_SCORING_ENABLED", raising=False)
    monkeypatch.delenv("SECTOR_DIVERSIFICATION_ENABLED", raising=False)
    assert sector_scoring_enabled() is False
    assert sector_diversification_enabled() is False
    assert max_per_sector() == 2  # default


# ── multiplier ────────────────────────────────────────────────────────────────

def test_sector_multiplier_bands(monkeypatch):
    bands = {"L": "leading", "N": "neutral", "G": "lagging", "U": "unknown"}
    monkeypatch.setattr(gov, "_sector_band", lambda sym, strength: bands[sym])
    assert sector_multiplier("L") > 1.0          # leader boosted
    assert sector_multiplier("N") == 1.0
    assert sector_multiplier("G") < 1.0          # laggard penalised heavily
    assert sector_multiplier("U") == 1.0         # unknown = neutral (honest)
    # ordering: leader > neutral > laggard
    assert sector_multiplier("L") > sector_multiplier("N") > sector_multiplier("G")


def test_sector_multiplier_env_override(monkeypatch):
    monkeypatch.setattr(gov, "_sector_band", lambda sym, strength: "leading")
    monkeypatch.setenv("SECTOR_MULT_LEADING", "1.5")
    assert sector_multiplier("X") == 1.5


def test_sector_multiplier_never_raises(monkeypatch):
    def boom(sym, strength):
        raise RuntimeError("no data")
    monkeypatch.setattr(gov, "_sector_band", boom)
    assert sector_multiplier("X") == 1.0  # degrades to neutral, never raises


# ── diversification ───────────────────────────────────────────────────────────

def test_diversification_caps_per_sector(monkeypatch):
    sectors = {
        "A": "Banking", "B": "Banking", "C": "Banking",   # 3 banks → cap to 2
        "D": "IT", "E": "IT",                              # 2 IT → both kept
        "F": "Pharma",                                     # 1 pharma
    }
    monkeypatch.setattr(gov, "_sector_of", lambda sym, strength: sectors[sym])
    ideas = [Idea(s) for s in ["A", "B", "C", "D", "E", "F"]]
    kept, diag = enforce_sector_diversification(ideas, symbol_of=lambda i: i.symbol, strength={}, cap=2)
    kept_syms = [i.symbol for i in kept]
    assert kept_syms == ["A", "B", "D", "E", "F"]   # C dropped (3rd bank), order preserved
    assert diag["dropped"] == 1
    assert diag["per_sector"]["Banking"] == 2


def test_diversification_unknown_not_capped(monkeypatch):
    monkeypatch.setattr(gov, "_sector_of", lambda sym, strength: "Unknown")
    ideas = [Idea(s) for s in ["A", "B", "C", "D"]]
    kept, diag = enforce_sector_diversification(ideas, symbol_of=lambda i: i.symbol, strength={}, cap=2)
    assert len(kept) == 4  # honestly-unknown names are never grouped/capped
    assert diag["dropped"] == 0


def test_diversification_respects_env_cap(monkeypatch):
    monkeypatch.setattr(gov, "_sector_of", lambda sym, strength: "Banking")
    monkeypatch.setenv("MAX_PER_SECTOR", "1")
    ideas = [Idea(s) for s in ["A", "B", "C"]]
    kept, diag = enforce_sector_diversification(ideas, symbol_of=lambda i: i.symbol, strength={})
    assert [i.symbol for i in kept] == ["A"]
    assert diag["cap"] == 1
