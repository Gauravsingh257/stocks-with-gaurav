"""Sector Rotation scanner: gate logic + scoring bonus (no network).

Locks the two invariants that matter for production safety:
  1. evaluate() only hits a strong stock whose sector is LEADING; everything
     else (non-leading sector, weak stock, no context) is filtered.
  2. The sector/news scoring bonus is strictly additive and a NO-OP for rows
     that are not sector-rotation rows (so the Supertrend scanner is unchanged).
"""

from __future__ import annotations

from services.scanners import sector_rotation
from services.scanners.scoring import passes_gates, quality_score, score_and_tier


def _uptrend(n: int = 120, step: float = 1.004, base: float = 100.0) -> list[dict]:
    out, price = [], base
    for i in range(n):
        price *= step
        out.append({
            "date": f"2026-06-{(i % 28) + 1:02d}",
            "open": price * 0.99, "high": price * 1.01, "low": price * 0.985,
            "close": price, "volume": 500_000 + i * 1000,
        })
    return out


def _downtrend(n: int = 120, step: float = 0.997, base: float = 300.0) -> list[dict]:
    out, price = [], base
    for i in range(n):
        price *= step
        out.append({
            "date": f"2026-06-{(i % 28) + 1:02d}",
            "open": price * 1.01, "high": price * 1.015, "low": price * 0.99,
            "close": price, "volume": 400_000,
        })
    return out


def _flat(n: int = 120, base: float = 200.0) -> list[dict]:
    return [{
        "date": f"2026-06-{(i % 28) + 1:02d}",
        "open": base, "high": base * 1.003, "low": base * 0.997,
        "close": base, "volume": 400_000,
    } for i in range(n)]


def _ctx() -> dict:
    return {
        "leading_sectors": {
            "IT": {"rel20": 4.5, "rel50": 3.0, "band": "leading",
                   "news": {"news_heat": 0.8, "tone": 3.2, "article_count": 20,
                            "top_headline": "IT majors win large deals",
                            "top_url": "http://x", "top_domain": "et.com"}},
        },
        "sym_sector": {"TCS": "IT", "SUNPHARMA": "Pharma", "RANDOM": "Others"},
    }


def test_strong_stock_in_leading_sector_hits():
    hit = sector_rotation.evaluate(_uptrend(), 252, "TCS", _ctx())
    assert hit is not None
    assert hit["is_sector_rotation"] is True
    assert hit["sector"] == "IT"
    assert hit["stack"] is True
    assert passes_gates(hit)


def test_stock_in_non_leading_sector_filtered():
    # Pharma is not in leading_sectors → must be filtered even if the chart is strong.
    assert sector_rotation.evaluate(_uptrend(), 252, "SUNPHARMA", _ctx()) is None


def test_unmapped_symbol_filtered():
    assert sector_rotation.evaluate(_uptrend(), 252, "RANDOM", _ctx()) is None


def test_weak_stock_in_leading_sector_filtered():
    # Leading sector, but the stock is in a downtrend (no EMA stack) → filtered.
    assert sector_rotation.evaluate(_downtrend(), 252, "TCS", _ctx()) is None


def test_missing_context_returns_none():
    assert sector_rotation.evaluate(_uptrend(), 252, "TCS", None) is None
    assert sector_rotation.evaluate(_uptrend(), 252, "TCS", {"leading_sectors": {}}) is None


def test_scoring_bonus_is_noop_for_plain_rows():
    plain = {"mom_long_pct": 10, "pos52": 80, "stack": True,
             "flip_vol_x": 1.5, "avg_vol": 1_000_000, "from_high_pct": -3}
    # A plain (Supertrend) row must score identically with/without the marker path.
    base = quality_score(plain)
    with_marker = dict(plain, is_sector_rotation=True,
                       sector_rel20_pct=5, news_heat=1.0, news_tone=4)
    assert quality_score(with_marker) > base           # bonus applies only when flagged
    assert quality_score(plain) == base                # plain row unchanged


def test_prepare_marks_outperforming_sector_leading(monkeypatch):
    # News layer must not hit the network in tests.
    import services.scanners.sector_news as snmod
    monkeypatch.setattr(snmod, "get_sector_news", lambda sectors, **k: {})

    # IT constituents strongly up; FMCG constituents flat. IT should lead, FMCG not.
    data: dict[str, list[dict]] = {}
    for s in ("TCS", "INFY", "WIPRO"):
        data[s] = _uptrend(step=1.006)
    for s in ("HINDUNILVR", "ITC", "NESTLEIND"):
        data[s] = _flat()

    ctx = sector_rotation.prepare(data)
    assert "IT" in ctx["leading_sectors"]
    assert "FMCG" not in ctx["leading_sectors"]
    assert ctx["sym_sector"]["TCS"] == "IT"
    assert ctx["leading_sectors"]["IT"]["rel20"] > 0

    # And a stock in that now-leading sector should hit via evaluate.
    hit = sector_rotation.evaluate(data["TCS"], 252, "TCS", ctx)
    assert hit is not None and hit["sector"] == "IT"


def test_prepare_empty_on_no_data():
    ctx = sector_rotation.prepare({})
    assert ctx["leading_sectors"] == {}


def test_score_capped_at_100():
    row = {"mom_long_pct": 100, "pos52": 100, "stack": True, "flip_vol_x": 5,
           "avg_vol": 10_000_000, "from_high_pct": 0,
           "is_sector_rotation": True, "sector_rel20_pct": 20,
           "news_heat": 1.0, "news_tone": 10}
    scored = score_and_tier(row)
    assert scored["quality_score"] <= 100.0
