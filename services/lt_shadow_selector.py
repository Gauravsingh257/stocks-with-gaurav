"""
services/lt_shadow_selector.py — Phase 4A: a Long-Term-specific selector, SHADOW ONLY.

Why it exists
-------------
The Long-Term "Final Trade Ideas" (signals_log.final_selected, which the site
serves and the portfolio promotes from) come out of the same validation funnel
as Swing: Layer 1 is 5/20/50-day momentum + volume spike + breakout, Layer 2 the
shared quality gate, Layer 3 the SMC band, then the shared Phase-2 ranker,
exceptionalism and governor. The only Long-Term-specific parts are the level
builder and the SMC band. Fundamentals reach the choice only through Layer 2.

What it does
------------
On every logged LONGTERM validation scan, rank the scan's own eligible pool with
the Long-Term weight vector that ALREADY exists in services/ranking_engine.py
(fund 0.30, growth 0.20, quality 0.18, tech 0.14 — sentiment 0.10 is dropped
because no news provider is wired in production, liquidity 0.08 because it is a
floor here, not a score; the rest renormalise). Fundamentals use the corrected
debt/equity unit. The pick count is matched to the control's count on the same
scan, so a comparison changes WHICH stocks, never HOW MANY.

No new signal and no fitted weight is introduced. The weights are the existing
design prior — the point of the shadow is to find out whether they carry edge.

What it never does
------------------
It reads the scan's records and writes only to its own `lt_shadow_*` tables.
Nothing in ranking, selection, the served feed, the portfolio or the engine
reads those tables. There is no enforcement flag: turning this into a live
selector would be a separate, deliberate change after calibration.

Flag: LT_SHADOW_SELECTOR_ENABLED (default OFF). Rollback = unset it.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("services.lt_shadow_selector")

SELECTOR_VERSION = "lt-shadow-v1"

# The LONGTERM branch of ranking_engine._score_candidates, minus sentiment and
# liquidity (see module docstring). Renormalised at scoring time.
WEIGHTS: dict[str, float] = {"fundamental": 0.30, "growth": 0.20, "quality": 0.18, "technical": 0.14}


# Explicit allowlist of behaviour flags that shape the control funnel or the data
# both sides see. Flags only — never add a credential here.
CONTROL_ENV_KEYS: tuple[str, ...] = (
    "PHASE0_NO_SYNTHETIC", "PHASE0_KITE_OHLC", "PHASE1_STRICT_FUNNEL", "PHASE1_TIGHT_ENTRY_GAP",
    "PHASE1_ENTRY_GAP_PCT", "PHASE1_SECTOR_UNKNOWN_STRICT", "PHASE2_SMC_AS_SCORE", "PHASE2_HORIZONS",
    "ENTRY_ANCHOR_MAX_GAP_PCT", "STRUCTURAL_TARGET_CAP", "VALIDATION_LAYER1_MIN_SCORE",
    "EXCEPTIONALISM_ENABLED", "EXCEPTIONALISM_SHADOW", "EXCEPTIONALISM_SOFT_CEILING",
    "REGIME_GOVERNOR_ENABLED", "GOVERNOR_FORCE_STATE", "SECTOR_LEADERSHIP_SCORING_ENABLED",
    "SECTOR_DIVERSIFICATION_ENABLED", "SECTOR_UNASSIGNED_BLOCKS", "MARKET_HEALTH_ENABLED",
    "DISCOVERY_MIN_UNIQUE_SYMBOLS", "RESEARCH_DATA_SOURCE", "RESEARCH_MIN_MCAP_CR",
    "RESEARCH_REQUIRE_REAL_DATA", "FUND_DE_UNIT_FIX", "FUND_FETCH_CONCURRENCY", "FUNDAMENTALS_CACHE_TTL_H",
    "FUND_COVERAGE_FIX",
)


def lt_shadow_enabled() -> bool:
    return os.getenv("LT_SHADOW_SELECTOR_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def log_top_n() -> int:
    return max(1, int(os.getenv("LT_SHADOW_LOG_TOP_N", "30")))


@dataclass(slots=True)
class ShadowInput:
    symbol: str
    cmp: float | None
    avg_turnover_cr: float | None
    layer2_pass: bool
    entry: float | None
    stop_loss: float | None
    targets: list[float]
    setup: str | None
    technical_score: float | None      # 0..100, from the scan's quality block
    smc_score: float | None            # 0..100, LONGTERM band (context only, not scored)
    control_selected: bool
    fund_source: str | None
    fundamental_score_de_fixed: float | None
    fundamental_score_legacy: float | None
    growth: float | None               # (revenue_growth + earnings_growth) / 2, 0..1
    quality: float | None              # (roce + roe + management_quality_de_fixed) / 3, 0..1
    debt_equity_ratio: float | None
    raw_debt_equity_legacy: float | None


@dataclass(slots=True)
class ShadowPick:
    symbol: str
    rank: int | None                   # None only for a control pick the shadow could not rank
    score: float | None
    selected: bool
    control_selected: bool
    components: dict[str, float]
    inp: ShadowInput
    exclusion: str | None = None       # why an ineligible control pick was not rankable


@dataclass(slots=True)
class ShadowRun:
    scan_id: str
    date: str
    budget: int
    universe: int
    eligible: int
    control_count: int
    picks: list[ShadowPick] = field(default_factory=list)
    exclusions: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def selected(self) -> list[ShadowPick]:
        return [p for p in self.picks if p.selected]

    @property
    def overlap(self) -> int:
        return sum(1 for p in self.selected if p.control_selected)


def config(min_turnover_cr: float) -> dict[str, Any]:
    return {
        "selector_version": SELECTOR_VERSION,
        "weights": WEIGHTS,
        "eligibility": {
            "min_avg_turnover_cr": min_turnover_cr,
            "layer2_quality_pass": True,
            "real_fundamentals": True,
            "tradable_longterm_plan": True,
        },
        "budget": "control final_selected count on the same scan",
        "debt_equity": "provider percent / 100 (fundamental_analysis.debt_equity_ratio)",
        "log_top_n": log_top_n(),
        # Logging only (Phase 4B), selection unchanged: every control pick is also
        # logged with its shadow rank or exclusion, and each run records how much of
        # the common-support pool had real fundamentals.
        "log_schema": 2,
        # The control is only a stable control while these stay put. Recording them
        # per run makes a mid-collection flag flip visible instead of silently
        # mixing two control regimes into one comparison.
        "control_env": {k: os.getenv(k) for k in CONTROL_ENV_KEYS},
    }


def _f(v: Any) -> float | None:
    """Finite float or None. NaN escapes `is None` checks and breaks a sort."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def input_from_record(record: Any, fund: Any, smc_score: float | None) -> ShadowInput:
    """Read-only projection of a LayerValidationRecord + FundamentalSnapshot."""
    disc = getattr(record, "discovery", None) or {}
    qual = getattr(record, "quality", None) or {}
    real = fund is not None and getattr(fund, "data_source", "hash") != "hash"
    growth = quality = None
    if real:
        growth = (fund.revenue_growth + fund.earnings_growth) / 2
        mgmt = getattr(fund, "management_quality_de_fixed", None)
        if mgmt is not None:
            quality = (fund.roce + fund.roe + mgmt) / 3
    targets = [t for t in (_f(x) for x in (getattr(record, "targets", None) or [])) if t is not None]
    return ShadowInput(
        symbol=record.symbol,
        cmp=_f(getattr(record, "cmp", None)),
        avg_turnover_cr=_f(disc.get("avg_turnover_cr")),
        layer2_pass=bool(getattr(record, "layer2_pass", False)),
        entry=_f(getattr(record, "entry", None)),
        stop_loss=_f(getattr(record, "stop_loss", None)),
        targets=targets,
        setup=getattr(record, "setup", None),
        technical_score=_f(qual.get("technical_score")),
        smc_score=_f(smc_score),
        control_selected=bool(getattr(record, "final_selected", False)),
        fund_source=getattr(fund, "data_source", None) if fund is not None else None,
        fundamental_score_de_fixed=_f(getattr(fund, "fundamental_score_de_fixed", None)) if real else None,
        fundamental_score_legacy=_f(getattr(fund, "fundamental_score", None)) if real else None,
        growth=_f(growth),
        quality=_f(quality),
        debt_equity_ratio=_f(getattr(fund, "debt_equity_ratio", None)) if real else None,
        raw_debt_equity_legacy=_f(getattr(fund, "raw_debt_equity", None)) if real else None,
    )


def _exclusion(i: ShadowInput, min_turnover_cr: float, *, need_fundamentals: bool = True) -> str | None:
    if i.cmp is None or i.avg_turnover_cr is None:
        return "no_ohlc"
    if i.avg_turnover_cr < min_turnover_cr:
        return "illiquid"
    if not i.layer2_pass:
        return "failed_layer2_quality"
    if need_fundamentals and i.fundamental_score_de_fixed is None:
        return "no_real_fundamentals"
    if i.entry is None:
        return "no_tradable_plan"
    return None


def _percentile(values: dict[str, float | None]) -> dict[str, float | None]:
    """Percentile rank in [0, 1], ties averaged, None stays None. Order-independent."""
    present = sorted((v, s) for s, v in values.items() if v is not None)
    out: dict[str, float | None] = {s: None for s in values}
    if not present:
        return out
    n = max(len(present) - 1, 1)
    i = 0
    while i < len(present):
        j = i
        while j + 1 < len(present) and present[j + 1][0] == present[i][0]:
            j += 1
        pct = ((i + j) / 2) / n if len(present) > 1 else 1.0
        for k in range(i, j + 1):
            out[present[k][1]] = pct
        i = j + 1
    return out


def select(inputs: Iterable[ShadowInput], *, scan_id: str, date: str, min_turnover_cr: float,
           top_n: int | None = None) -> ShadowRun:
    """Pure and deterministic: the same inputs in any order give the same run."""
    inputs = sorted(inputs, key=lambda i: i.symbol)
    control_count = sum(1 for i in inputs if i.control_selected)
    exclusions: dict[str, int] = {}
    pool: list[ShadowInput] = []
    for i in inputs:
        why = _exclusion(i, min_turnover_cr)
        if why:
            exclusions[why] = exclusions.get(why, 0) + 1
        else:
            pool.append(i)

    pcts = {
        "fundamental": _percentile({i.symbol: i.fundamental_score_de_fixed for i in pool}),
        "growth": _percentile({i.symbol: i.growth for i in pool}),
        "quality": _percentile({i.symbol: i.quality for i in pool}),
        "technical": _percentile({i.symbol: i.technical_score for i in pool}),
    }
    scored: list[tuple[float, ShadowInput, dict[str, float]]] = []
    for i in pool:
        comps = {k: pcts[k][i.symbol] for k in WEIGHTS if pcts[k][i.symbol] is not None}
        total = sum(WEIGHTS[k] for k in comps)
        score = sum(WEIGHTS[k] * v for k, v in comps.items()) / total if total else 0.0
        scored.append((round(score, 6), i, {k: round(v, 4) for k, v in comps.items()}))
    scored.sort(key=lambda t: (-t[0], t[1].symbol))

    budget = control_count
    keep = max(top_n or log_top_n(), budget)
    # Common support = everything that clears the non-fundamental filters. Its
    # fundamentals coverage is what provider throttling takes away from the shadow.
    support = sum(1 for i in inputs if _exclusion(i, min_turnover_cr, need_fundamentals=False) is None)
    coverage = {
        "universe_real_fundamentals": sum(1 for i in inputs if i.fundamental_score_de_fixed is not None),
        "support_ex_fundamentals": support,
        "support_with_fundamentals": len(pool),
        "support_coverage_pct": round(len(pool) / support * 100, 1) if support else None,
    }
    run = ShadowRun(scan_id=scan_id, date=date, budget=budget, universe=len(inputs),
                    eligible=len(pool), control_count=control_count, exclusions=exclusions, coverage=coverage)
    for rank, (score, i, comps) in enumerate(scored, start=1):
        if rank <= keep or i.control_selected:
            run.picks.append(ShadowPick(symbol=i.symbol, rank=rank, score=score, selected=rank <= budget,
                                        control_selected=i.control_selected, components=comps, inp=i))
    for i in inputs:
        why = _exclusion(i, min_turnover_cr)
        if i.control_selected and why:
            run.picks.append(ShadowPick(symbol=i.symbol, rank=None, score=None, selected=False,
                                        control_selected=True, components={}, inp=i, exclusion=why))
    return run


def run_lt_shadow(scan_id: str, date: str, records: list, fundamental_map: dict, min_turnover_cr: float,
                  smc_score_of: Callable[[Any], float | None]) -> ShadowRun:
    """Build inputs from a finished LONGTERM scan, select, persist. Never mutates `records`."""
    inputs = [input_from_record(r, fundamental_map.get(r.symbol), smc_score_of(r)) for r in records]
    run = select(inputs, scan_id=scan_id, date=date, min_turnover_cr=min_turnover_cr)
    from dashboard.backend.db.lt_shadow import write_run

    write_run(run, config(min_turnover_cr))
    log.info(
        "[LT-SHADOW] %s: universe=%d eligible=%d budget=%d shadow∩control=%d/%d excluded=%s coverage=%s",
        scan_id, run.universe, run.eligible, run.budget, run.overlap, run.control_count,
        json.dumps(run.exclusions, sort_keys=True), json.dumps(run.coverage, sort_keys=True),
    )
    return run


def pick_row(run: ShadowRun, p: ShadowPick) -> dict[str, Any]:
    inp = asdict(p.inp)
    return {
        "scan_id": run.scan_id, "date": run.date, "symbol": p.symbol, "rank": p.rank,
        "score": p.score, "selected": int(p.selected), "control_selected": int(p.control_selected),
        "cmp": p.inp.cmp, "entry": p.inp.entry, "stop_loss": p.inp.stop_loss,
        "targets": json.dumps(p.inp.targets), "setup": p.inp.setup,
        "components": json.dumps(p.components, sort_keys=True), "exclusion": p.exclusion,
        "inputs": json.dumps({k: v for k, v in inp.items()
                              if k not in ("symbol", "cmp", "entry", "stop_loss", "targets", "setup")},
                             sort_keys=True),
    }
