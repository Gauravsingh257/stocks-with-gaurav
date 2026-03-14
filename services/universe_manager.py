from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class UniverseSnapshot:
    symbols: list[str]
    requested_size: int
    actual_size: int
    sources: dict[str, int]


def _clean_symbol(symbol: str) -> str | None:
    if not isinstance(symbol, str):
        return None
    value = symbol.strip().upper()
    if not value.startswith("NSE:"):
        return None
    raw = value.split(":", 1)[1]
    if not raw:
        return None
    if "-SG" in raw:
        return None
    return f"NSE:{raw}"


def _read_json_array(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    output: list[str] = []
    for row in data:
        cleaned = _clean_symbol(row)
        if cleaned:
            output.append(cleaned)
    return output


def load_nse_universe(target_size: int = 1800) -> UniverseSnapshot:
    """
    Load the broadest available NSE equity universe up to target_size.
    Priority:
      1) data/nse_universe_1800.json (if present)
      2) stock_universe_500.json
      3) stock_universe_fno.json
    """
    root = Path(__file__).resolve().parents[1]
    sources: dict[str, int] = {}
    merged: list[str] = []

    preferred = root / "data" / "nse_universe_1800.json"
    core_500 = root / "stock_universe_500.json"
    fno = root / "stock_universe_fno.json"

    for label, path in (
        ("nse_universe_1800", preferred),
        ("stock_universe_500", core_500),
        ("stock_universe_fno", fno),
    ):
        symbols = _read_json_array(path)
        sources[label] = len(symbols)
        merged.extend(symbols)

    deduped: list[str] = []
    seen: set[str] = set()
    for symbol in merged:
        if symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(symbol)

    selected = deduped[:target_size]
    return UniverseSnapshot(
        symbols=selected,
        requested_size=target_size,
        actual_size=len(selected),
        sources=sources,
    )
