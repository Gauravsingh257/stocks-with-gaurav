"""Tests for canonical snapshot keys and discovery validation guards."""

from dashboard.backend.redis_endpoint_cache import (
    canonical_live_key,
    valid_discovery_payload,
    valid_discovery_user_visible,
)


def test_canonical_discovery_key_no_params():
    assert canonical_live_key("discovery") == "snapshot:discovery"


def test_valid_discovery_user_visible_requires_actionable_buckets():
    base = {
        "scan_id": "ok",
        "items": [],
        "final_trades": [],
        "watchlist": [],
        "discovery": [],
    }
    assert valid_discovery_payload(base)
    assert not valid_discovery_user_visible(base)

    assert valid_discovery_user_visible({**base, "watchlist": [{"sym": "X"}]})
