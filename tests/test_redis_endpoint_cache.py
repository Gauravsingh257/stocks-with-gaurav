"""Tests for canonical snapshot keys and discovery Redis write gate."""

from dashboard.backend.redis_endpoint_cache import (
    canonical_live_key,
    discovery_bucket_total_items,
    normalize_discovery_payload_for_storage,
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


def test_legacy_valid_discovery_user_visible_structural():
    base = {
        "scan_id": "ok",
        "items": [],
        "final_trades": [],
        "watchlist": [],
        "discovery": [],
    }
    assert valid_discovery_user_visible({**base, "watchlist": [{"sym": "X"}]})
