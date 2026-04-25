from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DecisionRecord(Protocol):
    symbol: str
    confidence_score: float
    layer1_pass: bool
    layer2_pass: bool
    layer3_pass: bool
    final_selected: bool
    smc: dict | None
    rejection_reason: list[str]

    def to_trade_card(self) -> dict: ...


@dataclass(slots=True)
class DecisionOutput:
    final_trades: list[DecisionRecord]
    watchlist: list[DecisionRecord]
    discovery: list[DecisionRecord]

    def to_dict(self) -> dict:
        return {
            "final_trades": [record.to_trade_card() for record in self.final_trades],
            "watchlist": [record.to_trade_card() for record in self.watchlist],
            "discovery": [record.to_trade_card() for record in self.discovery],
            "fallback": [record.to_trade_card() for record in self.discovery],
        }


def _rank_key(record: DecisionRecord) -> tuple[float, float]:
    smc_score = 0.0
    if record.smc:
        try:
            smc_score = float(record.smc.get("confirmation_score", record.smc.get("score", 0.0)))
        except (TypeError, ValueError):
            smc_score = 0.0
    return float(record.confidence_score), smc_score


def build_decision_output(records: list[DecisionRecord], limit: int = 10) -> DecisionOutput:
    """Split full-pipeline records into one server-owned decision section per symbol."""
    ordered = sorted(records, key=_rank_key, reverse=True)
    final_trades: list[DecisionRecord] = []
    watchlist: list[DecisionRecord] = []
    discovery: list[DecisionRecord] = []
    seen: set[str] = set()

    for record in ordered:
        symbol_key = str(record.symbol).replace("NSE:", "").replace(".NS", "").upper()
        if symbol_key in seen:
            continue
        seen.add(symbol_key)

        if record.layer3_pass:
            if len(final_trades) < limit:
                final_trades.append(record)
        elif record.layer2_pass:
            if len(watchlist) < limit:
                watchlist.append(record)
        elif len(discovery) < limit:
            discovery.append(record)

    return DecisionOutput(final_trades=final_trades, watchlist=watchlist, discovery=discovery)
