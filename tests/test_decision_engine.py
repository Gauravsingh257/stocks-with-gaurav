from dataclasses import dataclass

from services.decision_engine import build_decision_output, relaxed_filter


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

    assert 1 <= len(output.final_trades) <= 3
    assert output.discovery
    all_symbols = [record.symbol for bucket in (output.final_trades, output.watchlist, output.discovery) for record in bucket]
    assert len(all_symbols) == len(set(all_symbols))


def test_relaxed_filter_uses_smc_score_threshold():
    records = [
        DummyDecisionRecord("NSE:LOW", 0.9, smc={"score": 2.9}),
        DummyDecisionRecord("NSE:PASS", 0.4, smc={"score": 3.0}),
    ]

    assert [record.symbol for record in relaxed_filter(records)] == ["NSE:PASS"]


def test_decision_output_prioritizes_final_over_duplicate_watchlist():
    records = [
        DummyDecisionRecord("NSE:DUP", 0.95, layer2_pass=True, smc={"score": 4.0}),
        DummyDecisionRecord("NSE:DUP", 0.70, layer3_pass=True, smc={"score": 5.0}),
        DummyDecisionRecord("NSE:WATCH", 0.80, layer2_pass=True, smc={"score": 4.0}),
        DummyDecisionRecord("NSE:DISC", 0.60, layer1_pass=True, smc={"score": 3.0}),
    ]

    output = build_decision_output(records, limit=3)

    assert [record.symbol for record in output.final_trades] == ["NSE:DUP"]
    assert "NSE:DUP" not in [record.symbol for record in output.watchlist]
    assert "NSE:DUP" not in [record.symbol for record in output.discovery]