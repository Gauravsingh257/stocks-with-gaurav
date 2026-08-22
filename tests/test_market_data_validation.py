"""Non-finite market data must never reach engines, portfolio maths, or the DB.

Regression cover for the 2026-08-21 production incident: Yahoo/yfinance
retro-corrupted the daily bar for effectively every NSE equity (NaN OHLC, intact
volume). `float('nan')` is a valid float so nothing rejected it, and SQLite —
which has no NaN — bound it as NULL, producing

    IntegrityError: NOT NULL constraint failed: momentum_positions.profit_loss_pct

which aborted the entire Momentum cycle on its first holding.
"""

from __future__ import annotations

import math
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="mdv_test_"))

import pytest  # noqa: E402

from services.market_data_validation import (  # noqa: E402
    all_finite,
    finite_fields,
    finite_or_none,
    is_finite_number,
    sanitize_candles,
)

NAN, INF = float("nan"), float("inf")


def _bar(date: str, close: float = 100.0, volume: float = 1000.0) -> dict:
    return {"open": close, "high": close, "low": close, "close": close,
            "volume": volume, "date": date}


# ── predicates ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", [1, 1.5, -3, 0, 0.0, "2.5"])
def test_is_finite_number_accepts_real_numbers(value):
    assert is_finite_number(value) is True


@pytest.mark.parametrize("value", [None, NAN, INF, -INF, "abc", "", True, False, [], {}])
def test_is_finite_number_rejects_everything_else(value):
    assert is_finite_number(value) is False


def test_bool_of_nan_is_true_which_is_why_truthiness_is_not_a_validity_test():
    # The exact trap the production code fell into.
    assert bool(NAN) is True
    assert is_finite_number(NAN) is False


def test_finite_or_none():
    assert finite_or_none("12.5") == 12.5
    assert finite_or_none(NAN) is None
    assert finite_or_none(INF) is None
    assert finite_or_none(None) is None


def test_all_finite():
    assert all_finite([1, 2.0, "3"]) is True
    assert all_finite([1, NAN]) is False


# ── candle sanitisation ──────────────────────────────────────────────────────
def test_sanitize_drops_the_exact_production_bar():
    """The real shape Yahoo returned: NaN OHLC, real volume."""
    candles = [
        _bar("2026-08-19", 314.10),
        _bar("2026-08-20", 321.10),
        {"open": NAN, "high": NAN, "low": NAN, "close": NAN,
         "volume": 389899.0, "date": "2026-08-21"},
    ]
    clean = sanitize_candles(candles, symbol="BLSE")
    assert len(clean) == 2
    assert [c["date"] for c in clean] == ["2026-08-19", "2026-08-20"]
    # cmp falls back to the previous VALID close — not a fabricated value.
    assert clean[-1]["close"] == 321.10


def test_sanitize_rejects_inf_and_partial_corruption():
    candles = [_bar("2026-08-20"), {**_bar("2026-08-21"), "high": INF},
               {**_bar("2026-08-22"), "low": NAN}]
    assert [c["date"] for c in sanitize_candles(candles)] == ["2026-08-20"]


def test_sanitize_preserves_order_and_does_not_mutate_input():
    candles = [_bar("2026-08-18"), {**_bar("2026-08-19"), "close": NAN}, _bar("2026-08-20")]
    before = [dict(c) for c in candles]
    clean = sanitize_candles(candles)
    assert [c["date"] for c in clean] == ["2026-08-18", "2026-08-20"]
    assert candles == before  # untouched


def test_sanitize_normalises_non_finite_volume_but_keeps_the_bar():
    """Volume never drives a price decision, so the bar survives."""
    clean = sanitize_candles([_bar("2026-08-20", 100.0, volume=NAN)])
    assert len(clean) == 1
    assert clean[0]["volume"] == 0.0
    assert clean[0]["close"] == 100.0


def test_sanitize_handles_empty_and_none():
    assert sanitize_candles(None) == []
    assert sanitize_candles([]) == []


def test_sanitize_logs_the_rejection(caplog):
    """A dropped bar is a data-quality event — it must be visible."""
    with caplog.at_level("WARNING"):
        sanitize_candles([{**_bar("2026-08-21"), "close": NAN}], symbol="BLSE", timeframe="1d")
    assert "BLSE" in caplog.text and "2026-08-21" in caplog.text and "1d" in caplog.text


# ── DB write boundary ────────────────────────────────────────────────────────
def test_finite_fields_strips_non_finite_and_keeps_the_rest():
    safe = finite_fields({"current_price": NAN, "profit_loss_pct": NAN,
                          "days_held": 3, "status": "ACTIVE", "stop_loss": 95.5},
                         symbol="BLSE")
    assert safe == {"days_held": 3, "status": "ACTIVE", "stop_loss": 95.5}


def test_finite_fields_passes_none_through_for_nullable_columns():
    assert finite_fields({"mfe_r": None}) == {"mfe_r": None}


# ── the mechanism that made this a crash rather than a bad number ────────────
def test_sqlite_binds_nan_as_null_which_breaks_not_null():
    """Documents WHY a float produced a NOT NULL error."""
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, pl REAL NOT NULL DEFAULT 0)")
    c.execute("INSERT INTO t (id, pl) VALUES (1, 0)")
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        c.execute("UPDATE t SET pl=? WHERE id=1", (NAN,))
    c.execute("UPDATE t SET pl=? WHERE id=1", (12.5,))  # sanity: finite still works
    assert c.execute("SELECT pl FROM t").fetchone()[0] == 12.5


# ── provider normalisation end-to-end ────────────────────────────────────────
def test_to_candles_rejects_nan_rows_from_a_dataframe():
    pd = pytest.importorskip("pandas")
    from services.momentum_candidate_pipeline import _to_candles

    df = pd.DataFrame(
        {"Open": [310.9, 314.0, NAN], "High": [313.2, 328.1, NAN],
         "Low": [310.0, 312.7, NAN], "Close": [310.6, 321.1, NAN],
         "Volume": [119023, 1500843, 389899]},
        index=pd.to_datetime(["2026-08-19", "2026-08-20", "2026-08-21"]),
    )
    candles = _to_candles(df, symbol="BLSE")
    assert len(candles) == 2
    assert candles[-1]["close"] == 321.1
    assert all(math.isfinite(c["close"]) for c in candles)


def test_fetch_cmp_never_returns_non_finite(monkeypatch):
    import services.trade_tracker as tt

    class _FastInfo(dict):
        pass

    class _Ticker:
        fast_info = _FastInfo({"lastPrice": NAN, "regularMarketPrice": NAN})

    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Ticker": lambda *_a, **_k: _Ticker()}))
    assert tt._fetch_cmp("BLSE") is None
