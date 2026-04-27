from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("services.decision_engine")


class DecisionRecord(Protocol):
    symbol: str
    confidence_score: float
    layer1_pass: bool
    layer2_pass: bool
    layer3_pass: bool
    final_selected: bool
    near_setup: bool
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


def _mark_near_setup(record: DecisionRecord, smc_score: float) -> None:
    near_setup = 4.0 <= smc_score < 5.0
    try:
        record.near_setup = near_setup
    except (AttributeError, TypeError):
        pass
    if record.smc is not None:
        try:
            record.smc["near_setup"] = near_setup
        except TypeError:
            pass


def _band_rank(record: DecisionRecord) -> tuple[int, float, float]:
    smc_score = _smc_score_value(record)
    if smc_score >= 5.0:
        band = 3
    elif smc_score >= 3.5:
        band = 2
    else:
        band = 1
    return band, smc_score, float(record.confidence_score)


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
        for record in sorted(signals, key=_band_rank, reverse=True)
        if _smc_score_value(record) >= 3.5
    ]
    return _unique(candidates, limit, excluded)


def relaxed_filter(signals: list[DecisionRecord], limit: int = 10, excluded: set[str] | None = None) -> list[DecisionRecord]:
    return _unique([record for record in signals if _smc_score_value(record) >= 2.0], limit, excluded)


def _priority_bucket(records: list[DecisionRecord], limit: int) -> tuple[list[DecisionRecord], list[DecisionRecord], list[DecisionRecord]]:
    for record in records:
        _mark_near_setup(record, _smc_score_value(record))

    # Relaxed bands: 2–3 SMC conditions can land in 3.5–5.0 (MEDIUM) without full 6.0 confluence.
    final_trades = _unique([record for record in records if _smc_score_value(record) >= 5.0], limit)
    used = {_symbol_key(record) for record in final_trades}

    watchlist = _unique(
        [record for record in records if 3.5 <= _smc_score_value(record) < 5.0],
        limit,
        excluded=used,
    )
    used.update(_symbol_key(record) for record in watchlist)

    discovery = _unique(
        [record for record in records if 2.0 <= _smc_score_value(record) < 3.5],
        limit,
        excluded=used,
    )
    return final_trades, watchlist, discovery


def build_decision_output(records: list[DecisionRecord], limit: int = 10) -> DecisionOutput:
    """Split full-pipeline records into one server-owned decision section per symbol."""
    ordered = sorted(records, key=_band_rank, reverse=True)
    final_trades, watchlist, discovery = _priority_bucket(ordered, limit)

    used = {_symbol_key(record) for record in final_trades}
    watchlist = [record for record in watchlist if _symbol_key(record) not in used]
    discovery = [record for record in discovery if _symbol_key(record) not in used]

    if len(watchlist) == 0:
        watchlist = near_valid_setups(ordered, limit=min(3, limit), excluded=used)
    used.update(_symbol_key(record) for record in watchlist)
    discovery = [record for record in discovery if _symbol_key(record) not in used]

    if len(discovery) == 0:
        discovery = relaxed_filter(ordered, limit=limit, excluded=used)

    log.debug(
        "decision_buckets final=%s watchlist=%s discovery=%s",
        len(final_trades),
        len(watchlist),
        len(discovery),
    )

    return DecisionOutput(final_trades=final_trades, watchlist=watchlist, discovery=discovery)
