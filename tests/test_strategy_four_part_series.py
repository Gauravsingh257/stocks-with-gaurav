"""Tests for 4-part strategy carousel (cheatsheet paging + ID resolution)."""

from __future__ import annotations

from content_engine.services.image_generator import (
    _resolve_strategy_id_from_post,
    generate_cheatsheet_post,
)


def test_resolve_strategy_id_from_post_orb_title() -> None:
    post = {
        "strategy_id": "orb",
        "title": "🔔 OPENING RANGE BREAKOUT (ORB)",
        "content": "",
        "tags": [],
    }
    assert _resolve_strategy_id_from_post(post) == "orb"


def test_resolve_strategy_id_from_title_alias() -> None:
    post = {
        "title": "🟦 ORDER BLOCK (OB)",
        "content": "",
        "tags": [],
    }
    assert _resolve_strategy_id_from_post(post) == "order_block"


def test_cheatsheet_forced_two_pages_returns_two_paths() -> None:
    paths = generate_cheatsheet_post(
        "orb",
        theme_name="light",
        forced_pages=[[0, 1, 2], [3]],
        page_series_labels=[(3, 4), (4, 4)],
        return_all_paths=True,
    )
    assert isinstance(paths, list)
    assert len(paths) == 2
    assert all(p.endswith(".png") for p in paths)
