from dataclasses import dataclass

import pandas as pd

from services.decision_engine import build_decision_output, relaxed_filter
from services.validation_engine import _scored_smc_levels


@dataclass
class DummyDecisionRecord:
    symbol: str
    confidence_score: float
    layer1_pass: bool = False
    layer2_pass: bool = False
    layer3_pass: bool = False
    final_selected: bool = False
    smc: dict | None = None
    rejection_reason: list[str] | None = None

    def to_trade_card(self) -> dict:
        return {"symbol": self.symbol}


def test_decision_output_falls_back_when_strict_smc_is_empty():
    records = [
        DummyDecisionRecord("NSE:AAA", 0.82, smc={"score": 2.0}),
        DummyDecisionRecord("NSE:BBB", 0.75, layer1_pass=True, smc={"score": 4.0}),
        DummyDecisionRecord("NSE:CCC", 0.70, smc={"score": 3.5}),
        DummyDecisionRecord("NSE:DDD", 0.60, smc={"score": 3.0}),
    ]

    output = build_decision_output(records, limit=3)

    assert output.final_trades == []
    assert [record.symbol for record in output.watchlist] == ["NSE:BBB"]
    assert output.discovery
    all_symbols = [record.symbol for bucket in (output.final_trades, output.watchlist, output.discovery) for record in bucket]
    assert len(all_symbols) == len(set(all_symbols))


def test_relaxed_filter_uses_smc_score_threshold():
    records = [
        DummyDecisionRecord("NSE:LOW", 0.9, smc={"score": 1.9}),
        DummyDecisionRecord("NSE:PASS", 0.4, smc={"score": 2.0}),
    ]

    assert [record.symbol for record in relaxed_filter(records)] == ["NSE:PASS"]


def test_decision_output_prioritizes_final_over_duplicate_watchlist():
    records = [
        DummyDecisionRecord("NSE:DUP", 0.95, layer2_pass=True, smc={"score": 4.0}),
        DummyDecisionRecord("NSE:DUP", 0.70, layer3_pass=True, smc={"score": 6.0}),
        DummyDecisionRecord("NSE:WATCH", 0.80, layer2_pass=True, smc={"score": 4.0}),
        DummyDecisionRecord("NSE:DISC", 0.60, layer1_pass=True, smc={"score": 3.0}),
    ]

    output = build_decision_output(records, limit=3)

    assert [record.symbol for record in output.final_trades] == ["NSE:DUP"]
    assert "NSE:DUP" not in [record.symbol for record in output.watchlist]
    assert "NSE:DUP" not in [record.symbol for record in output.discovery]


def test_decision_output_uses_soft_score_bands_and_near_setup_flag():
    records = [
        DummyDecisionRecord("NSE:FINAL", 0.70, smc={"score": 6.0}),
        DummyDecisionRecord("NSE:NEAR", 0.95, smc={"score": 5.0}),
        DummyDecisionRecord("NSE:WATCH", 0.90, smc={"score": 4.0}),
        DummyDecisionRecord("NSE:DISC", 0.85, smc={"score": 2.0}),
        DummyDecisionRecord("NSE:EARLY", 0.80, smc={"score": 1.0}),
    ]

    output = build_decision_output(records, limit=10)

    assert [record.symbol for record in output.final_trades] == ["NSE:FINAL"]
    assert [record.symbol for record in output.watchlist] == ["NSE:NEAR", "NSE:WATCH"]
    assert [record.symbol for record in output.discovery] == ["NSE:DISC", "NSE:EARLY"]
    near_record = output.watchlist[0]
    assert near_record.near_setup is True
    assert near_record.smc["near_setup"] is True


def test_scored_smc_levels_allow_partial_fvg_confirmation():
    rows = []
    for idx in range(40):
        close = 100 + idx * 0.5
        rows.append(
            {
                "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=idx),
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100000,
            }
        )
    frame = pd.DataFrame(rows)
    confirmation = {
        "confirmation_score": 30.0,
        "tier": "PARTIAL",
        "order_block": None,
        "liquidity_zone": [108.0, 110.0],
        "structure": "NEUTRAL",
        "missing": ["no_order_block", "no_BOS"],
    }

    levels = _scored_smc_levels("NSE:PARTIAL", frame, "SWING", confirmation)

    assert levels is not None
    _entry, _stop, _targets, _setup, meta = levels
    assert meta["score"] == 3.0
    assert meta["near_setup"] is False