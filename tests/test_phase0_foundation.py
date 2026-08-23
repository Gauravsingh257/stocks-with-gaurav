"""
Phase 0 foundation hardening.

The single most important property under test is REVERSIBILITY: with every
PHASE0_* flag off, each touched code path must behave exactly as it did before.
Phase 0 changes what the system knows, not yet what it decides.
"""

from __future__ import annotations

import asyncio

import pytest

from services.data_quality import evaluate_symbol_quality
from services.factor_pipeline import build_factor_row
from services.fundamental_analysis import _hash_snapshot
from services.outcome_labeling import HORIZONS, compute_label, label_coverage
from services.technical_scanner import _snapshot_hash


def _bars(n: int, start: float = 100.0, step: float = 0.01) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        close = start * ((1 + step) ** i)
        out.append({
            "date": f"2026-01-{i:02d}" if i < 32 else f"2026-02-{i - 31:02d}",
            "open": close, "close": close,
            "high": close * 1.005, "low": close * 0.995, "volume": 100000,
        })
    return out


def _sentiment(symbol: str = "TEST"):
    import services.news_analysis as na

    return asyncio.run(na.analyze_news_sentiment([symbol]))[symbol]


# ── outcome labelling ─────────────────────────────────────────────────────────

def test_label_computes_forward_mfe_mae():
    label = compute_label("TEST", "2026-01-05", _bars(31))
    assert label is not None
    assert label.fwd_pct[5] == pytest.approx(5.101, abs=0.01)
    # A rising series: MFE above the close, MAE above zero but below it.
    assert label.mfe_pct[10] > label.fwd_pct[10] > label.mae_pct[10]


def test_unelapsed_window_is_null_not_truncated():
    """The corpus is younger than the 60-day window. A +60d label must be NULL
    rather than silently computed from whatever bars happen to exist — that is
    what would quietly bias every calibration built on the dataset."""
    label = compute_label("TEST", "2026-01-05", _bars(31))
    assert label.fwd_pct[60] is None
    assert label.mfe_pct[60] is None
    assert label.mae_pct[60] is None
    assert label.bars_available == 26


def test_days_to_target_counts_trading_days():
    label = compute_label("TEST", "2026-01-05", _bars(31))
    assert label.days_to_target == 10  # first bar whose HIGH clears +10%


def test_days_to_target_none_when_never_reached():
    flat = [{"date": f"2026-01-{i:02d}", "close": 100.0, "high": 100.5, "low": 99.5}
            for i in range(1, 32)]
    label = compute_label("TEST", "2026-01-05", flat)
    assert label.days_to_target is None


def test_excess_return_uses_benchmark():
    bench = [{"date": f"2026-01-{i:02d}", "close": 200 * (1.002 ** i), "high": 0, "low": 0}
             for i in range(1, 32)]
    label = compute_label("TEST", "2026-01-05", _bars(31), benchmark_bars=bench)
    assert label.excess_pct(10) == pytest.approx(
        label.fwd_pct[10] - label.bench_fwd_pct[10], abs=1e-6
    )


def test_no_price_at_or_before_scan_date_returns_none():
    """A symbol that had not listed yet must produce no label at all, rather
    than anchoring to its first-ever bar and inventing a return."""
    assert compute_label("TEST", "2025-01-01", _bars(31)) is None


def test_row_covers_every_horizon_column():
    row = compute_label("TEST", "2026-01-05", _bars(31)).to_row()
    for horizon in HORIZONS:
        for prefix in ("fwd", "mfe", "mae", "bench_fwd", "excess"):
            assert f"{prefix}_{horizon}d_pct" in row


def test_label_coverage_reports_per_horizon():
    labels = [compute_label("TEST", "2026-01-05", _bars(31))]
    coverage = label_coverage(labels)
    assert coverage["by_horizon"]["5d"]["coverage_pct"] == 100.0
    assert coverage["by_horizon"]["60d"]["coverage_pct"] == 0.0


# ── quality gate renormalization ──────────────────────────────────────────────

def test_quality_score_unchanged_when_all_data_present():
    """Flag-off reversibility: the additive maths must be untouched."""
    result = evaluate_symbol_quality(
        "TEST", _snapshot_hash("TEST"), _hash_snapshot("TEST"), _sentiment()
    )
    assert result.score == pytest.approx(0.90, abs=1e-9)


def test_missing_sentiment_does_not_depress_score():
    """Hash sentiment is always >= 0.35, so every symbol has been collecting the
    +0.10 sentiment credit for free. Removing the provider without redistributing
    its weight would drop every score by 0.10 against an unchanged 0.45 pass
    threshold and mass-reject the universe."""
    both = evaluate_symbol_quality(
        "TEST", _snapshot_hash("TEST"), _hash_snapshot("TEST"), _sentiment()
    )
    without = evaluate_symbol_quality(
        "TEST", _snapshot_hash("TEST"), _hash_snapshot("TEST"), None
    )
    assert without.score == pytest.approx(both.score, abs=1e-9)
    assert "sentiment_data_unavailable" in without.reasons


def test_missing_fundamentals_and_sentiment_still_gradeable():
    result = evaluate_symbol_quality("TEST", _snapshot_hash("TEST"), None, None)
    assert result.score == pytest.approx(0.90, abs=1e-9)
    assert result.data_authenticity == "unavailable"


def test_missing_technical_is_a_hard_reject():
    """Every other check keys off technicals; without bars there is nothing to
    grade, and a pass would be meaningless."""
    result = evaluate_symbol_quality("TEST", None, _hash_snapshot("TEST"), _sentiment())
    assert result.passed is False
    assert result.reasons == ["technical_data_unavailable"]


# ── factor rows degrade rather than fabricate ─────────────────────────────────

def test_factor_row_omits_unavailable_groups():
    row = build_factor_row("TEST", _snapshot_hash("TEST"), None, None)
    assert row.available_groups == ("technical",)
    assert "growth" not in row.factors
    assert "news_sentiment" not in row.factors
    assert "trend" in row.factors
    assert row.fundamental_score is None


def test_factor_row_full_when_all_present():
    row = build_factor_row("TEST", _snapshot_hash("TEST"), _hash_snapshot("TEST"), _sentiment())
    assert set(row.available_groups) == {"technical", "fundamental", "sentiment"}
    assert "growth" in row.factors and "news_sentiment" in row.factors


# ── ranking renormalizes over present factors ─────────────────────────────────

def test_score_candidates_ignores_missing_factors():
    from services.ranking_engine import _score_candidates

    rows = [
        build_factor_row("A", _snapshot_hash("A"), _hash_snapshot("A"), _sentiment("A")),
        build_factor_row("B", _snapshot_hash("B"), None, None),
    ]
    scored = _score_candidates(rows, "SWING")
    assert len(scored) == 2
    # A partial row must still receive a finite, comparable 0..1 score rather
    # than being zeroed out for data it never had.
    for _row, score in scored:
        assert 0.0 <= score <= 1.0


def test_percentile_opt_preserves_none():
    from services.ranking_engine import _percentile_opt

    assert _percentile_opt([0.5, None, 0.9]) == [0.0, None, 1.0]
    assert _percentile_opt([None, None]) == [None, None]


# ── synthetic providers are genuinely off under the flag ──────────────────────

def test_sentiment_returns_empty_when_synthetic_disabled(monkeypatch):
    import services.news_analysis as na

    monkeypatch.setenv("PHASE0_NO_SYNTHETIC", "1")
    assert asyncio.run(na.analyze_news_sentiment(["A", "B"])) == {}


def test_sentiment_still_synthetic_by_default(monkeypatch):
    import services.news_analysis as na

    monkeypatch.delenv("PHASE0_NO_SYNTHETIC", raising=False)
    out = asyncio.run(na.analyze_news_sentiment(["A", "B"]))
    assert set(out) == {"A", "B"}


def test_technical_scan_emits_nothing_rather_than_hash(monkeypatch):
    """With no universe snapshot published, the honest answer is an empty map —
    NOT sha256(ticker) scores that would carry 0.76 of the SWING rank weight."""
    import services.technical_scanner as ts

    monkeypatch.setenv("PHASE0_NO_SYNTHETIC", "1")
    monkeypatch.setattr(ts, "_scan_from_universe_snapshot", lambda symbols: {})
    assert asyncio.run(ts.scan_technical(["A", "B"])) == {}


def test_technical_scan_hashes_by_default(monkeypatch):
    import services.technical_scanner as ts

    monkeypatch.delenv("PHASE0_NO_SYNTHETIC", raising=False)
    out = asyncio.run(ts.scan_technical(["A", "B"]))
    assert set(out) == {"A", "B"}


# ── sector classification ─────────────────────────────────────────────────────

def test_sector_flag_defaults_off_and_keeps_legacy_map(monkeypatch):
    from engine.swing import get_sector

    monkeypatch.delenv("PHASE0_REAL_SECTORS", raising=False)
    assert get_sector("NSE:HDFCBANK") == "Banking"
    assert get_sector("NSE:SOMETHINGUNMAPPED") == "Others"


def test_resolve_sector_is_unknown_not_guessed(monkeypatch):
    import services.sector_classification as sc

    monkeypatch.setattr(sc, "_memo", {})
    monkeypatch.setattr(sc, "_yfinance_sector", lambda s: None)
    monkeypatch.setattr(sc, "_legacy_sector", lambda s: None)
    assert sc.resolve_sector("NOTAREALTICKER") == sc.UNKNOWN


def test_resolve_sector_prefers_nse_official(monkeypatch):
    import services.sector_classification as sc

    monkeypatch.setattr(sc, "_memo", {"RELIANCE": "Energy"})
    monkeypatch.setattr(sc, "_yfinance_sector", lambda s: "SHOULD_NOT_WIN")
    assert sc.resolve_sector("NSE:RELIANCE") == "Energy"


def test_portfolio_risk_keeps_other_for_unknown(monkeypatch):
    """`check_sector_limit` treats OTHER as uncapped; a genuinely unknown sector
    must keep that contract rather than being grouped into a fake bucket."""
    import services.portfolio_risk as pr
    import services.sector_classification as sc

    monkeypatch.setenv("PHASE0_REAL_SECTORS", "1")
    monkeypatch.setattr(sc, "_memo", {})
    monkeypatch.setattr(sc, "_yfinance_sector", lambda s: None)
    monkeypatch.setattr(sc, "_legacy_sector", lambda s: None)
    assert pr.get_sector("NOTAREALTICKER") == "OTHER"


# ── universe OHLC snapshot ────────────────────────────────────────────────────

def test_universe_ohlc_flag_defaults_off(monkeypatch):
    from services.universe_ohlc import kite_ohlc_enabled

    monkeypatch.delenv("PHASE0_KITE_OHLC", raising=False)
    assert kite_ohlc_enabled() is False


def test_universe_ohlc_roundtrip_encoding():
    from services.universe_ohlc import _decode, _encode

    payload = {"AAA": _bars(5), "BBB": _bars(3)}
    restored = _decode(_encode(payload))
    assert set(restored) == {"AAA", "BBB"}
    assert len(restored["AAA"]) == 5
    assert restored["AAA"][0]["close"] == pytest.approx(payload["AAA"][0]["close"])


def test_universe_ohlc_refuses_thin_snapshot(monkeypatch):
    """A half-failed fetch must never replace a good snapshot with a thin one —
    a randomly varying peer set is the exact defect this module exists to fix."""
    import services.universe_ohlc as uo

    monkeypatch.setattr(uo, "_get_redis", lambda: pytest.fail("must not reach redis"))
    result = uo.publish_universe_ohlc({"AAA": _bars(5)}, min_symbols=200)
    assert result["written"] is False
    assert result["reason"] == "below_min_symbols"


def test_load_universe_ohlc_empty_without_redis(monkeypatch):
    import services.universe_ohlc as uo

    monkeypatch.setattr(uo, "_get_redis", lambda: None)
    assert uo.load_universe_ohlc(["AAA"]) == {}
    assert uo.snapshot_status()["available"] is False


# ── fundamentals extraction ───────────────────────────────────────────────────

def test_margin_is_none_on_non_positive_revenue():
    """Some NSE financial-sector filings report negative Total Revenue; a margin
    computed off it is arithmetically valid and completely meaningless."""
    from scripts.backfill_fundamentals_quarterly import _margin

    assert _margin(50.0, -100.0) is None
    assert _margin(50.0, 0.0) is None
    assert _margin(50.0, 200.0) == 25.0


def test_roce_uses_capital_employed_not_equity_only():
    """The live scorer assigns roce = roe. Real ROCE is EBIT / (debt + equity)."""
    from scripts.backfill_fundamentals_quarterly import _roce

    assert _roce(100.0, 300.0, 700.0) == 10.0
    assert _roce(100.0, None, None) is None
    assert _roce(None, 300.0, 700.0) is None


# ── the caller guard must not void the whole layer when a provider is absent ──

def test_quality_layer_survives_absent_sentiment_provider(monkeypatch):
    """PHASE0_NO_SYNTHETIC makes analyze_news_sentiment return {} because no news
    API is wired. run_validation_scan must still evaluate the quality gate from
    the providers it DOES have — the original guard required all three non-None,
    which silently failed Layer 2 for every symbol (measured: 395 -> 0 on a
    400-symbol production sample) and, with the strict funnel, emptied the feed.
    """
    result = evaluate_symbol_quality("TEST", _snapshot_hash("TEST"), _hash_snapshot("TEST"), None)
    assert result.passed is True
    assert result.score > 0

    # ...and with fundamentals absent too, technicals alone still grade.
    only_tech = evaluate_symbol_quality("TEST", _snapshot_hash("TEST"), None, None)
    assert only_tech.passed is True


def test_validation_engine_guard_requires_only_technicals():
    """Guards against a regression to `tech and fund and sent`."""
    import inspect

    import services.validation_engine as ve

    src = inspect.getsource(ve.run_validation_scan)
    assert "if tech is not None and fund is not None and sent is not None:" not in src, (
        "the quality-gate caller must not require all three providers"
    )
    assert "if tech is not None:" in src
