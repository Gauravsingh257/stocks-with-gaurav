"""Momentum candidate pipeline — benchmark (RS) wiring + funnel diagnostics.

Regression cover for the defect that kept the live Momentum Portfolio empty:
the production chain (tracker → MomentumPortfolioManager → get_ranked_candidates)
never supplied a NIFTY series, so `rs_20d` was always None and EVERY candidate
was rejected by the `rs_unknown` eligibility gate. No network — providers are
injected.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="mom_pipe_test_"))

import pytest  # noqa: E402

import services.momentum_candidate_pipeline as pipe  # noqa: E402
from tests.test_momentum_engine import strong_leader  # noqa: E402


@pytest.fixture(autouse=True)
def _pool(monkeypatch):
    """Two-name discovery pool, live regime gate bypassed, benchmark cache cleared."""
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "0")
    monkeypatch.setattr(pipe, "_discovery_symbols",
                        lambda day, limit: [{"symbol": "LEADER", "cmp": 192.6, "confidence": 90},
                                            {"symbol": "LEADER2", "cmp": 192.6, "confidence": 80}])
    monkeypatch.setattr(pipe, "_nifty_cache", None, raising=False)


def _data_provider(symbol):
    candles, _ = strong_leader()
    return candles[-1]["close"], candles


def _benchmark():
    return strong_leader()[1]


def test_pipeline_fetches_benchmark_when_caller_passes_none():
    """The live path passes nifty=None — the pipeline must source it itself,
    otherwise RS is unknown and nothing can ever qualify."""
    batch = pipe.get_ranked_candidates(data_provider=_data_provider,
                                       nifty_provider=_benchmark)
    assert batch.funnel["benchmark_bars"] > 20
    assert batch.funnel["accepted"] == len(batch) == 2
    assert batch[0]["rs_20d"] is not None
    assert "rs_unknown" not in str(batch.funnel.get("top_gate_failures", {}))


def test_missing_benchmark_is_reported_not_silent():
    """If the benchmark is genuinely unavailable, every name fails on
    `rs_unknown` — and the funnel must say so instead of returning a bare []."""
    batch = pipe.get_ranked_candidates(data_provider=_data_provider,
                                       nifty_provider=lambda: [])
    assert len(batch) == 0
    assert batch.funnel["benchmark_bars"] == 0
    assert batch.funnel["eligibility_failed"] == 2
    assert batch.funnel["top_gate_failures"].get("rs_unknown") == 2


def test_funnel_accounts_for_every_discovery_symbol():
    batch = pipe.get_ranked_candidates(data_provider=lambda s: (None, []),
                                       nifty_provider=_benchmark)
    f = batch.funnel
    assert f["discovery_pool"] == 2 and f["no_data"] == 2
    assert (f["no_data"] + f["no_metrics"] + f["eligibility_failed"]
            + f["no_entry_model"] + f["accepted"]) == f["discovery_pool"]


def test_regime_block_is_attributed(monkeypatch):
    monkeypatch.setenv("MOMENTUM_REGIME_GATE_ENABLED", "1")
    monkeypatch.setattr("services.momentum_engine.router.current_regime",
                        lambda: "TRENDING_DOWN")
    batch = pipe.get_ranked_candidates(data_provider=_data_provider,
                                       nifty_provider=_benchmark)
    assert len(batch) == 0
    assert batch.funnel["blocked_by"] == "regime_gate"
    assert batch.funnel["regime_allowed"] is False


def test_candidate_batch_is_a_plain_list():
    """Existing callers/tests treat the result as a list — keep that contract."""
    batch = pipe.CandidateBatch([{"symbol": "X"}], {"accepted": 1})
    assert isinstance(batch, list) and batch[0]["symbol"] == "X"
    assert (batch or []) == [{"symbol": "X"}]


def test_benchmark_provider_caches_per_day(monkeypatch):
    calls = {"n": 0}

    class _T:
        def __init__(self, *_a, **_k):
            calls["n"] += 1

        def history(self, **_k):
            import pandas as pd
            return pd.DataFrame({"Open": [1.0] * 30, "High": [1.0] * 30, "Low": [1.0] * 30,
                                 "Close": [1.0] * 30, "Volume": [1] * 30})

    monkeypatch.setattr(pipe, "_nifty_cache", None, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "yfinance",
                        type("m", (), {"Ticker": _T})())
    first = pipe.default_nifty_provider()
    second = pipe.default_nifty_provider()
    assert len(first) == 30 and second == first
    assert calls["n"] == 1  # second call served from the per-day cache
