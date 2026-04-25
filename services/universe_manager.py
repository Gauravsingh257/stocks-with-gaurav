from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

log = logging.getLogger("services.universe_manager")

NSE_EQUITY_CSV_URLS = (
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
)


@dataclass(slots=True)
class UniverseSnapshot:
    symbols: list[str]
    requested_size: int
    actual_size: int
    sources: dict[str, int]
    total_size: int = 0
    cache_path: str | None = None
    cache_date: str | None = None
    source_errors: dict[str, str] | None = None


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


def _cache_is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()
    except OSError:
        return False
    return modified == date.today()


def _read_cached_universe(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("symbols") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    output: list[str] = []
    for row in rows:
        value = row.get("symbol") if isinstance(row, dict) else row
        cleaned = _clean_symbol(str(value))
        if cleaned:
            output.append(cleaned)
    return output


def _write_cached_universe(path: Path, symbols: list[str], source: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "count": len(symbols),
            "symbols": symbols,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Unable to write NSE universe cache %s: %s", path, exc)


def _fetch_nse_equity_csv(url: str, timeout: int = 20) -> list[str]:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/csv,application/csv,text/plain,*/*",
            "Referer": "https://www.nseindia.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public NSE URLs
        raw = response.read().decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(raw.splitlines())
    output: list[str] = []
    for row in reader:
        symbol = row.get("SYMBOL") or row.get("Symbol") or row.get("symbol")
        if not symbol:
            continue
        series = str(row.get("SERIES") or row.get("Series") or "EQ").strip().upper()
        # Keep all ordinary equity-like rows; avoid debt/ETF/index rows that do not behave like stocks.
        if series and series not in {"EQ", "BE", "BZ", "SM", "ST", "SZ"}:
            continue
        cleaned = _clean_symbol(f"NSE:{symbol}")
        if cleaned:
            output.append(cleaned)
    return output


def _load_dynamic_nse_universe(cache_path: Path) -> tuple[list[str], dict[str, str]]:
    errors: dict[str, str] = {}
    if os.getenv("NSE_UNIVERSE_DYNAMIC", "1").strip().lower() not in {"1", "true", "yes"}:
        return [], {"dynamic_loader": "disabled"}

    cached = _read_cached_universe(cache_path)
    if cached and _cache_is_fresh(cache_path):
        return cached, errors

    for url in NSE_EQUITY_CSV_URLS:
        try:
            symbols = _fetch_nse_equity_csv(url)
            if len(symbols) >= int(os.getenv("NSE_UNIVERSE_MIN_DYNAMIC", "1500")):
                _write_cached_universe(cache_path, symbols, url)
                return symbols, errors
            errors[url] = f"only {len(symbols)} symbols returned"
        except Exception as exc:
            errors[url] = str(exc)[:240]

    # Stale cache is still better than falling back to a 365-symbol static universe.
    if cached:
        errors["stale_cache"] = "using stale cached NSE universe after provider failure"
        return cached, errors
    return [], errors


def load_nse_universe(target_size: int = 1800) -> UniverseSnapshot:
    """
    Load the broadest available NSE equity universe up to target_size.
    Priority:
      1) daily cached live NSE equity CSV/API universe
      2) data/nse_universe_full.json / data/nse_universe_1800.json
      3) stock_universe_500.json
      4) stock_universe_fno.json
    """
    root = Path(__file__).resolve().parents[1]
    sources: dict[str, int] = {}
    source_errors: dict[str, str] = {}
    merged: list[str] = []

    cache_path = Path(os.getenv("NSE_UNIVERSE_CACHE", str(root / "data" / "nse_universe_full.json")))
    preferred = root / "data" / "nse_universe_1800.json"
    core_500 = root / "stock_universe_500.json"
    fno = root / "stock_universe_fno.json"

    dynamic_symbols, source_errors = _load_dynamic_nse_universe(cache_path)
    sources["nse_dynamic_cache"] = len(dynamic_symbols)
    merged.extend(dynamic_symbols)

    for label, path in (
        ("nse_universe_full", cache_path),
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

    requested = max(1, int(target_size))
    selected = deduped[:requested]
    min_required = int(os.getenv("NSE_UNIVERSE_MIN_SCAN", "1500"))
    if requested >= min_required and len(selected) < min_required:
        log.warning(
            "NSE universe below minimum coverage: requested=%d selected=%d min_required=%d sources=%s errors=%s",
            requested,
            len(selected),
            min_required,
            sources,
            source_errors,
        )
    return UniverseSnapshot(
        symbols=selected,
        requested_size=requested,
        actual_size=len(selected),
        sources=sources,
        total_size=len(deduped),
        cache_path=str(cache_path),
        cache_date=date.today().isoformat() if _cache_is_fresh(cache_path) else None,
        source_errors=source_errors or None,
    )
