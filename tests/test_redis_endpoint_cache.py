"""Tests for canonical snapshot keys and discovery Redis write gate."""

from unittest.mock import patch

from dashboard.backend.redis_endpoint_cache import (
    canonical_live_key,
    discovery_bucket_total_items,
    normalize_discovery_payload_for_storage,
    serve_cached_research_list,
    valid_discovery_redis_write,
    valid_discovery_user_visible,
)


def test_canonical_discovery_key_no_params():
    assert canonical_live_key("discovery") == "snapshot:discovery"


def test_valid_discovery_redis_write_nonempty_buckets():
    base = {
        "scan_id": "ok",
        "items": [],
        "final_trades": [],
        "watchlist": [],
        "discovery": [],
    }
    assert discovery_bucket_total_items(base) == 0
    assert not valid_discovery_redis_write(base)

    assert valid_discovery_redis_write({**base, "watchlist": [{"sym": "X"}]})
    assert discovery_bucket_total_items({**base, "watchlist": [{"sym": "X"}]}) == 1


def test_normalize_discovery_payload_fills_items_from_final():
    p = normalize_discovery_payload_for_storage(
        {"scan_id": "x", "final_trades": [{"s": "A"}], "watchlist": [], "discovery": []}
    )
    assert isinstance(p["items"], list)
    assert len(p["items"]) == 1


@patch("dashboard.backend.redis_endpoint_cache.load_last_known_good")
@patch("dashboard.backend.redis_endpoint_cache.load_live_snapshot")
def test_serve_cached_research_list_empty_live_uses_lkg(mock_live, mock_lkg):
    mock_live.return_value = {"items": [], "count": 0}
    mock_lkg.return_value = {"items": [{"symbol": "NSE:TEST"}], "count": 1}
    out = serve_cached_research_list("swing")
    assert out is not None
    assert out.get("snapshot_stale_reason") == "empty_live_guard_preferred_lkg"
    assert len(out.get("items") or []) == 1


def test_legacy_valid_discovery_user_visible_structural():
    base = {
        "scan_id": "ok",
        "items": [],
        "final_trades": [],
        "watchlist": [],
        "discovery": [],
    }
    assert valid_discovery_user_visible({**base, "watchlist": [{"sym": "X"}]})
