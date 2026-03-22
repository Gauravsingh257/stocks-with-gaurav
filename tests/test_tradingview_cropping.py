"""
tests/test_tradingview_cropping.py — Candle-cluster vs center crop for TV screenshots.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tv_mod():
    from content_engine.services import tradingview_screenshot as m

    return m


def test_crop_to_candle_cluster_finds_center_block(tv_mod, tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    p = tmp_path / "chart.png"
    im = Image.new("RGB", (200, 200), (5, 5, 8))
    d = ImageDraw.Draw(im)
    d.rectangle([50, 50, 149, 149], fill=(240, 240, 245))
    im.save(p, "PNG")

    ok = tv_mod._crop_to_candle_cluster(p)
    assert ok is True
    out = Image.open(p)
    w, h = out.size
    # ~100px content + 10% pad each axis → ~120px; allow tolerance
    assert 110 <= w <= 135
    assert 110 <= h <= 135


def test_crop_to_candle_cluster_uniform_background_fails(tv_mod, tmp_path: Path) -> None:
    from PIL import Image

    p = tmp_path / "flat.png"
    Image.new("RGB", (120, 120), (10, 10, 12)).save(p, "PNG")

    ok = tv_mod._crop_to_candle_cluster(p)
    assert ok is False


def test_crop_chart_region_fallback_center(tv_mod, tmp_path: Path, monkeypatch) -> None:
    from PIL import Image

    p = tmp_path / "flat.png"
    Image.new("RGB", (200, 200), (10, 10, 12)).save(p, "PNG")

    called: list[str] = []

    def fake_cluster(path: Path) -> bool:
        called.append("cluster")
        return False

    monkeypatch.setattr(tv_mod, "_crop_to_candle_cluster", fake_cluster)

    def fake_center(path: Path, margin_frac: float = 0.03) -> None:
        called.append("center")

    monkeypatch.setattr(tv_mod, "_center_crop_image", fake_center)

    tv_mod._crop_chart_region(p)
    assert called == ["cluster", "center"]
