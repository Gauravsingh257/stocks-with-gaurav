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


def _symbol_key(record: DecisionRecord) -> str:
    return str(record.symbol).replace("NSE:", "").replace(".NS", "").upper()


def _smc_score_value(record: DecisionRecord) -> float:
    smc = record.smc or {}
    try:
        if "smc_score" in smc:
            return float(smc.get("smc_score") or 0.0)
        if "score" in smc:
            return float(smc.get("score") or 0.0)
        if "confirmation_score" in smc:
            return float(smc.get("confirmation_score") or 0.0) / 10.0
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _unique(records: list[DecisionRecord], limit: int, excluded: set[str] | None = None) -> list[DecisionRecord]:
    picked: list[DecisionRecord] = []
    seen = set(excluded or set())
    for record in records:
        symbol_key = _symbol_key(record)
        if symbol_key in seen:
            continue
        seen.add(symbol_key)
        picked.append(record)
        if len(picked) >= limit:
            break
    return picked


def top_n_by_score(all_signals: list[DecisionRecord], n: int = 3, excluded: set[str] | None = None) -> list[DecisionRecord]:
    """Return the strongest available records when strict SMC produces no final trades."""
    ordered = sorted(all_signals, key=_rank_key, reverse=True)
    return _unique(ordered, n, excluded)


def near_valid_setups(signals: list[DecisionRecord], limit: int = 3, excluded: set[str] | None = None) -> list[DecisionRecord]:
    """Return near setups using quality/discovery pass or meaningful SMC evidence."""
    candidates = [
        record
        for record in sorted(signals, key=_rank_key, reverse=True)
        if record.layer2_pass or record.layer1_pass or _smc_score_value(record) >= 4.0
    ]
    return _unique(candidates, limit, excluded)


def relaxed_filter(signals: list[DecisionRecord], limit: int = 10, excluded: set[str] | None = None) -> list[DecisionRecord]:
    return _unique([record for record in signals if _smc_score_value(record) >= 3.0], limit, excluded)


def build_decision_output(records: list[DecisionRecord], limit: int = 10) -> DecisionOutput:
    """Split full-pipeline records into one server-owned decision section per symbol."""
    ordered = sorted(records, key=_rank_key, reverse=True)
    final_trades: list[DecisionRecord] = []
    watchlist: list[DecisionRecord] = []
    discovery: list[DecisionRecord] = []
    seen: set[str] = set()

    for record in ordered:
        symbol_key = _symbol_key(record)
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

    used = {_symbol_key(record) for record in final_trades}
    if len(final_trades) == 0:
        final_trades = top_n_by_score(ordered, n=min(3, limit))
        used = {_symbol_key(record) for record in final_trades}

    watchlist = [record for record in watchlist if _symbol_key(record) not in used]
    discovery = [record for record in discovery if _symbol_key(record) not in used]

    if len(watchlist) == 0:
        watchlist = near_valid_setups(ordered, limit=min(3, limit), excluded=used)
    used.update(_symbol_key(record) for record in watchlist)
    discovery = [record for record in discovery if _symbol_key(record) not in used]

    if len(discovery) == 0:
        discovery = relaxed_filter(ordered, limit=limit, excluded=used)

    print({
        "final": len(final_trades),
        "watchlist": len(watchlist),
        "discovery": len(discovery),
    })

    return DecisionOutput(final_trades=final_trades, watchlist=watchlist, discovery=discovery)
