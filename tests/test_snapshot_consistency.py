"""Snapshot consistency helpers."""

from dashboard.backend.snapshot_consistency import validate_research_list_snapshot


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
