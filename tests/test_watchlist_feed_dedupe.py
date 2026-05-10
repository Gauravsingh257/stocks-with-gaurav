"""Phase B — adjacent duplicate feed events collapsed."""

from dashboard.backend.services.watchlist_intel_service import _collapse_adjacent_duplicate_feed_events


def test_collapse_adjacent_duplicate_feed_events():
    hist = [
        {"symbol": "RELIANCE", "headline": "a"},
        {"symbol": "RELIANCE", "headline": "a"},
        {"symbol": "TCS", "headline": "b"},
    ]
    out = _collapse_adjacent_duplicate_feed_events(hist)
    assert len(out) == 2
    assert out[0]["symbol"] == "RELIANCE"
    assert out[1]["symbol"] == "TCS"


def test_empty_hist():
    assert _collapse_adjacent_duplicate_feed_events([]) == []
