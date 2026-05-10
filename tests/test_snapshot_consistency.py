"""Snapshot consistency helpers."""

from unittest.mock import patch

from dashboard.backend.snapshot_consistency import equity_ltp_from_snapshot, validate_research_list_snapshot


def test_validate_research_list_ok_minimal():
    ok, issues = validate_research_list_snapshot({"items": [], "count": 0})
    assert ok
    assert issues == []


def test_validate_research_list_missing_items():
    ok, issues = validate_research_list_snapshot({"count": 0})
    assert not ok
    assert "missing_items" in issues


def test_validate_research_list_items_wrong_type():
    ok, issues = validate_research_list_snapshot({"items": "bad"})
    assert not ok
    assert "items_not_list" in issues


def test_equity_ltp_from_snapshot_reads_redis_prices():
    snap = {
        "active_trades": [
            {"symbol": "NSE:RELIANCE"},
            {"symbol": "TCS"},
        ]
    }

    def fake_ltp(sym: str):
        return {"RELIANCE": 2500.0, "TCS": 3900.5}.get(sym)

    with patch("dashboard.backend.cache.get_ltp", side_effect=fake_ltp):
        out = equity_ltp_from_snapshot(snap)
    assert out["RELIANCE"] == 2500.0
    assert out["TCS"] == 3900.5
