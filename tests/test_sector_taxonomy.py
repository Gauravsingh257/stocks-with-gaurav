"""Regression: sector bands must be keyed by labels stocks actually carry.

2026-10-11 audit: `SECTOR_INDEX_TICKER` contains "Banking" and "Insurance", but
`resolve_sector()` classifies every bank as "Finance" — so the only sector flagged
`leading` (Banking, rel20 +2.04) reached 0 of 2,425 stocks. The sector-leadership
signal was structurally dead.
"""

import pytest

import services.sector_strength as ss


def test_banking_and_insurance_are_not_stock_labels():
    """The orphan keys that caused the dead signal — proven, not assumed."""
    canon = ss.canonical_sectors()
    assert canon, "canonical label set must resolve"
    assert "Banking" not in canon
    assert "Insurance" not in canon
    assert "Finance" in canon


def test_orphan_sector_keys_detects_the_dead_keys():
    assert ss.orphan_sector_keys() == ["Banking", "Insurance"]


@pytest.mark.parametrize("symbol", ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK"])
def test_banks_classify_as_finance(monkeypatch, symbol):
    """Banking strength must reach stocks labelled Finance, not a label nothing has."""
    monkeypatch.setenv("PHASE0_REAL_SECTORS", "1")
    from engine.swing import get_sector

    assert get_sector(symbol) == "Finance"


def test_sector_index_map_is_canonical_and_bridges_banking_to_finance():
    m = ss.sector_index_map()
    assert m, "index registry must provide a mapping"
    canon = ss.canonical_sectors()
    for sector in m:
        assert sector in canon, f"{sector} is not a label any stock carries"
    # The bridge that was missing: a financial index backs the Finance label.
    assert m.get("Finance") == "NIFTY FIN SERVICE"
    assert m.get("Pharma") == "NIFTY PHARMA"


def test_sector_index_map_picks_one_primary_per_sector():
    """Finance is referenced by 5 indices; exactly one must win, deterministically."""
    m = ss.sector_index_map()
    assert len(set(m.values())) == len(m), "an index may back only one sector"
    for _ in range(3):
        assert ss.sector_index_map() == m, "mapping must be deterministic"


def test_canonical_path_is_flag_gated_and_defaults_off(monkeypatch):
    monkeypatch.delenv("SECTOR_STRENGTH_CANONICAL", raising=False)
    assert ss.canonical_strength_enabled() is False
    monkeypatch.setenv("SECTOR_STRENGTH_CANONICAL", "1")
    assert ss.canonical_strength_enabled() is True


def test_canonical_bands_use_canonical_keys_and_report_unmapped(monkeypatch):
    """Bands come out keyed by stock labels; thresholds are untouched."""
    # 60 bars. `_ret(cl, n)` compares cl[-1] to cl[-(n+1)], so the step must sit
    # INSIDE the 20-bar window for a non-zero 20-day return.
    bench = [100.0] * 60                          # NIFTY flat -> ret20 = ret50 = 0
    strong = [100.0] * 41 + [110.0] * 19          # +10% over both 20d and 50d
    bars = lambda cl: [{"close": c} for c in cl]  # noqa: E731

    def fake_load(names=None, *, day=None):
        out = {ss.CANONICAL_BENCHMARK_INDEX: bars(bench)}
        for n in (names or []):
            if n != ss.CANONICAL_BENCHMARK_INDEX:
                out[n] = bars(strong)
        return out

    import services.index_ohlc as io
    monkeypatch.setattr(io, "load_index_ohlc", fake_load)
    res = ss.compute_sector_strength_from_index_snapshot(cache=False)

    canon = ss.canonical_sectors()
    assert res["sectors"], "expected bands"
    for sector in res["sectors"]:
        assert sector in canon
    assert "Banking" not in res["sectors"] and "Insurance" not in res["sectors"]
    assert "Finance" in res["sectors"] and res["sectors"]["Finance"]["band"] == "leading"
    # Unmapped sectors are reported honestly rather than silently dropped.
    assert "unmapped_sectors" in res
    assert all(s in canon for s in res["unmapped_sectors"])
    assert not set(res["unmapped_sectors"]) & set(res["sectors"])


def test_canonical_path_never_raises_when_snapshot_missing(monkeypatch):
    import services.index_ohlc as io
    monkeypatch.setattr(io, "load_index_ohlc", lambda names=None, **k: {})
    res = ss.compute_sector_strength_from_index_snapshot(cache=False)
    assert res["leading"] == []
    assert all(v["band"] == "unknown" for v in res["sectors"].values())
