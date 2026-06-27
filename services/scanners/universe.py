"""
services/scanners/universe.py — Curated liquid NSE universe for scanners.

Default mode = "liquid": F&O names + the core-500 list, de-duplicated and
stripped of SME / illiquid-segment suffixes. This is the tradeable universe the
scanners run on by default (cleaner, faster, no penny-stock noise). A final
liquidity floor (avg-volume) is still applied at scan time by the runner's gate.

Mode = "full": delegates to the existing services.universe_manager.load_nse_universe
so the broad ~2000-symbol universe is available as an opt-in.

Symbols are returned WITHOUT the "NSE:" prefix (e.g. "HDFCBANK"), ready for the
Kite instrument-token lookup in data_layer.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("services.scanners.universe")

_ROOT = Path(__file__).resolve().parents[2]

# Segment suffixes we never scan (SME / trade-to-trade / surveillance).
_EXCLUDE_SUFFIXES = ("-SM", "-SME", "-ST", "-BE", "-BZ", "-SZ", "-SG")


def _strip_prefix(sym: str) -> str:
    s = str(sym).strip().upper()
    if s.startswith("NSE:"):
        s = s.split(":", 1)[1]
    return s.strip()


def _is_excluded(sym: str) -> bool:
    return any(sym.endswith(suf) for suf in _EXCLUDE_SUFFIXES)


def _load_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("scanner universe: cannot read %s (%s)", path, exc)
        return []
    rows = data if isinstance(data, list) else data.get("symbols", []) if isinstance(data, dict) else []
    out: list[str] = []
    for row in rows:
        val = row.get("symbol") if isinstance(row, dict) else row
        sym = _strip_prefix(str(val))
        if sym and not _is_excluded(sym):
            out.append(sym)
    return out


def get_scanner_universe(mode: str = "liquid") -> list[str]:
    """Return de-duplicated list of plain NSE symbols for the chosen mode.

    mode="liquid" (default): F&O + core-500, curated.
    mode="full": broad NSE universe via services.universe_manager.
    """
    mode = (mode or "liquid").strip().lower()

    if mode == "full":
        try:
            from services.universe_manager import load_nse_universe
            snap = load_nse_universe(int(os.getenv("SCANNER_FULL_TARGET", "2000")))
            syms = [_strip_prefix(s) for s in snap.symbols]
            return _dedupe([s for s in syms if s and not _is_excluded(s)])
        except Exception as exc:
            log.warning("scanner universe: full mode failed (%s); falling back to liquid", exc)
            # fall through to liquid

    fno = _load_list(_ROOT / "stock_universe_fno.json")
    core = _load_list(_ROOT / "stock_universe_500.json")
    merged = _dedupe(fno + core)
    if not merged:
        # Last-resort safety net: never return empty (would blank the scanner).
        log.warning("scanner universe: liquid lists empty; using full universe fallback")
        return get_scanner_universe("full")
    return merged


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out
