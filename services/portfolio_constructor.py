"""
services/portfolio_constructor.py — Phase 3.3 helpers.

Currently exposes one helper: ``apply_sector_cap`` which thins a list of
``RankedIdea``-like objects so no single sector exceeds ``max_per_sector``
positions. Higher confidence ideas win the slot; lower confidence ideas in
the same sector are dropped.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Sequence

log = logging.getLogger("services.portfolio_constructor")


def _confidence(idea) -> float:
    val = getattr(idea, "confidence_score", None)
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sector(idea) -> str:
    val = getattr(idea, "sector", None) or "UNKNOWN"
    try:
        return str(val).strip() or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def apply_sector_cap(
    ideas: Sequence,
    max_per_sector: int | None = None,
) -> list:
    """
    Drop lower-confidence duplicates so no sector has more than ``max_per_sector``
    ideas. Returns a new list preserving the original input order for survivors.

    ``UNKNOWN`` sector is treated as a sentinel — it bypasses the cap so legacy
    rows without a sector tag still flow through.

    Default cap is ``RESEARCH_SECTOR_CAP`` env (or 2 if unset).
    """
    if max_per_sector is None:
        try:
            max_per_sector = int(os.getenv("RESEARCH_SECTOR_CAP", "2"))
        except ValueError:
            max_per_sector = 2

    if max_per_sector <= 0 or not ideas:
        return list(ideas)

    # Sort by confidence DESC to give the strongest ideas first claim on
    # each sector slot, but remember original positions so we can restore order.
    indexed = list(enumerate(ideas))
    indexed.sort(key=lambda it: _confidence(it[1]), reverse=True)

    counts: dict[str, int] = {}
    keep_indices: set[int] = set()
    dropped: list[tuple[str, str]] = []  # (symbol, sector) for log

    for orig_idx, idea in indexed:
        sector = _sector(idea)
        if sector == "UNKNOWN":
            keep_indices.add(orig_idx)
            continue
        used = counts.get(sector, 0)
        if used < max_per_sector:
            counts[sector] = used + 1
            keep_indices.add(orig_idx)
        else:
            dropped.append((getattr(idea, "symbol", "?"), sector))

    if dropped:
        log.info(
            "[sector_cap] dropped %d ideas exceeding cap=%d: %s",
            len(dropped), max_per_sector,
            ", ".join(f"{s}({sec})" for s, sec in dropped[:6]),
        )

    return [idea for i, idea in enumerate(ideas) if i in keep_indices]


__all__ = ["apply_sector_cap"]
