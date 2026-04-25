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
    fallback: list[DecisionRecord]

    def to_dict(self) -> dict:
        return {
            "final_trades": [record.to_trade_card() for record in self.final_trades],
            "watchlist": [record.to_trade_card() for record in self.watchlist],
            "fallback": [record.to_trade_card() for record in self.fallback],
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
    """Split full-pipeline records into final trades, watchlist, and last-resort fallback.

    Final trades require all layers. Watchlist requires discovery + quality and a
    near SMC confirmation score. Fallback is only returned if both final and
    watchlist are empty, and is still composed from stocks that entered the
    pipeline rather than arbitrary static picks.
    """
    ordered = sorted(records, key=_rank_key, reverse=True)
    final_trades = [record for record in ordered if record.final_selected][:limit]
    watchlist = [
        record for record in ordered
        if not record.final_selected
        and record.layer1_pass
        and record.layer2_pass
        and str((record.smc or {}).get("tier", "")).upper() == "NEAR_SETUP"
    ][:limit]
    fallback: list[DecisionRecord] = []
    if not final_trades and not watchlist:
        fallback = [record for record in ordered if record.layer1_pass][:limit]
    return DecisionOutput(final_trades=final_trades, watchlist=watchlist, fallback=fallback)
