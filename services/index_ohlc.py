"""
services/index_ohlc.py — daily NSE index history (benchmarks + sector indices) from Kite.

WHY THIS EXISTS
---------------
Sector rotation needs a benchmark (NIFTY 50 / NIFTY 500) and a survivorship-free
reference series per sector. Every existing index reader goes through yfinance
(`^NSEI`, `^CNXIT`, …), and yfinance *index* tickers are blocked from Railway's
datacenter IP — `sector_strength.compute_sector_strength()` returns all-"unknown"
in production for exactly that reason. Kite serves the same indices (segment
INDICES) through the historical API the scanner already uses, so this module
fetches them there and nowhere else. No Yahoo dependency.

WHY A SEPARATE NAMESPACE
------------------------
`ohlc:universe:*` is read by the ranking and validation engines as "every
tradable stock". Writing "NIFTY 50" into it would make the ranking engine score
an index as a stock. Indices therefore live under `ohlc:index:*` and nothing in
the selection path reads them.

TRANSPORT
---------
One gzip+base64 columnar blob (the `universe_ohlc` encoding) plus a manifest and a
`latest` pointer. ~24 indices x ~1,290 bars is a few hundred KB, so every key
takes the 7-day TTL: a weekend or a failed post-close refresh can never leave a
manifest pointing at expired data (the 2026-09-07 failure mode).

FLAG
----
`SECTOR_INDEX_OHLC_ENABLED` (default OFF), set on the `scanner` service only.
Nothing reads this snapshot yet; it is the Phase-0 data foundation for sector
rotation and influences no strategy.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date

from services.universe_ohlc import _decode, _encode, _get_redis

log = logging.getLogger("services.index_ohlc")

NAMESPACE = "ohlc:index"
TTL_SEC = int(os.getenv("INDEX_OHLC_TTL_SEC", str(7 * 86400)))
# Kite caps one daily-candle request at 2,000 days; 1,900 keeps a margin and is
# ~5 years, enough to warm up monthly rotation and to validate across regimes.
LOOKBACK_DAYS = int(os.getenv("INDEX_OHLC_LOOKBACK_DAYS", "1900"))

BENCHMARK = "benchmark"
SECTOR = "sector"
THEME = "theme"

# (Kite tradingsymbol, role, our stock_universe sector it references or None).
# `sector` is a reference only — membership always comes from stock_universe,
# never from these indices.
INDEX_REGISTRY: tuple[tuple[str, str, str | None], ...] = (
    ("NIFTY 50", BENCHMARK, None),
    ("NIFTY 500", BENCHMARK, None),
    ("NIFTY BANK", SECTOR, "Finance"),
    ("NIFTY PSU BANK", SECTOR, "Finance"),
    ("NIFTY PVT BANK", SECTOR, "Finance"),
    ("NIFTY FIN SERVICE", SECTOR, "Finance"),
    ("NIFTY CAPITAL MKT", SECTOR, "Finance"),
    ("NIFTY IT", SECTOR, "IT"),
    ("NIFTY PHARMA", SECTOR, "Pharma"),
    ("NIFTY HEALTHCARE", SECTOR, "Pharma"),
    ("NIFTY AUTO", SECTOR, "Auto"),
    ("NIFTY METAL", SECTOR, "Metal"),
    ("NIFTY ENERGY", SECTOR, "Energy"),
    ("NIFTY OIL AND GAS", SECTOR, "Energy"),
    ("NIFTY FMCG", SECTOR, "FMCG"),
    ("NIFTY REALTY", SECTOR, "Realty"),
    ("NIFTY INFRA", SECTOR, "Infra"),
    ("NIFTY MEDIA", SECTOR, "Media"),
    ("NIFTY CONSR DURBL", SECTOR, "Consumer Durables"),
    ("NIFTY CHEMICALS", SECTOR, "Chemicals"),
    ("NIFTY IND DEFENCE", THEME, "Capital Goods"),
    ("NIFTY COMMODITIES", THEME, None),
    ("NIFTY CONSUMPTION", THEME, None),
    ("NIFTY SERV SECTOR", THEME, None),
)
REQUIRED = tuple(name for name, role, _ in INDEX_REGISTRY if role == BENCHMARK)
# Refuse a write that lost more than a few indices: a thin snapshot must never
# replace a good one.
MIN_INDICES = int(os.getenv("INDEX_OHLC_MIN_INDICES", str(len(INDEX_REGISTRY) - 4)))


def index_ohlc_enabled() -> bool:
    return os.getenv("SECTOR_INDEX_OHLC_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _data_key(day: str) -> str:
    return f"{NAMESPACE}:{day}:data"


def _manifest_key(day: str) -> str:
    return f"{NAMESPACE}:{day}:manifest"


def _latest_key() -> str:
    return f"{NAMESPACE}:latest"


def registry() -> list[dict]:
    return [{"name": n, "role": r, "sector": s} for n, r, s in INDEX_REGISTRY]


# ── writer (scanner worker) ───────────────────────────────────────────────────

def publish_index_ohlc(candles: dict[str, list[dict]], *, day: str | None = None) -> dict:
    """Publish one day's index history. Refuses a snapshot missing a benchmark or
    holding fewer than MIN_INDICES indices, so the previous one stays served."""
    day = day or date.today().isoformat()
    usable = {name: bars for name, bars in (candles or {}).items() if bars}
    missing_required = [n for n in REQUIRED if n not in usable]
    if missing_required or len(usable) < MIN_INDICES:
        log.warning("index_ohlc: write REFUSED — %d indices (min %d), missing benchmarks %s",
                    len(usable), MIN_INDICES, missing_required)
        return {"written": False, "reason": "below_minimum", "indices": len(usable),
                "missing_required": missing_required}

    client = _get_redis()
    if client is None:
        return {"written": False, "reason": "no_redis", "indices": len(usable)}

    blob = _encode(usable)
    manifest = {
        "day": day,
        "indices": len(usable),
        "bytes": len(blob),
        "written_at": time.time(),
        "series": {
            name: {"bars": len(bars), "first": bars[0].get("date"), "last": bars[-1].get("date")}
            for name, bars in sorted(usable.items())
        },
        "missing": sorted(n for n, _, _ in INDEX_REGISTRY if n not in usable),
    }
    try:
        pipe = client.pipeline(transaction=False)
        pipe.setex(_data_key(day), TTL_SEC, blob)
        pipe.setex(_manifest_key(day), TTL_SEC, json.dumps(manifest, separators=(",", ":")))
        pipe.setex(_latest_key(), TTL_SEC, day)
        pipe.execute()
    except Exception as exc:
        log.warning("index_ohlc: write failed (%s)", exc)
        return {"written": False, "reason": str(exc)[:200], "indices": len(usable)}

    log.info("index_ohlc: published %d indices (%.0f KB) for %s", len(usable), len(blob) / 1e3, day)
    return {"written": True, "day": day, "indices": len(usable), "bytes": len(blob),
            "missing": manifest["missing"]}


def refresh_index_ohlc(*, fetcher=None, lookback_days: int | None = None) -> dict:
    """Fetch every registered index from Kite and publish. Scanner-worker only.
    ~24 requests at Kite's 3 req/sec cap, i.e. under 10 seconds."""
    if fetcher is None:
        from services.scanners.data_layer import KiteOHLCFetcher

        fetcher = KiteOHLCFetcher()
    lookback = lookback_days or LOOKBACK_DAYS
    started = time.time()
    fetcher.load_index_instruments()
    candles: dict[str, list[dict]] = {}
    failed: list[str] = []
    for name, _, _ in INDEX_REGISTRY:
        bars = fetcher.fetch_index(name, "day", lookback)
        if bars:
            candles[name] = bars
        else:
            failed.append(name)
    result = publish_index_ohlc(candles)
    result.update({"requested": len(INDEX_REGISTRY), "fetched": len(candles), "failed": failed,
                   "elapsed_sec": round(time.time() - started, 1)})
    return result


# ── reader ────────────────────────────────────────────────────────────────────

def _read_manifest(client, day: str | None) -> dict | None:
    try:
        day = day or client.get(_latest_key())
        raw = client.get(_manifest_key(str(day))) if day else None
        manifest = json.loads(raw) if raw else None
        return manifest if isinstance(manifest, dict) else None
    except Exception:
        return None


def load_index_ohlc(names: list[str] | None = None, *, day: str | None = None) -> dict[str, list[dict]]:
    """{index name: candles}. Empty dict when no snapshot exists."""
    client = _get_redis()
    if client is None:
        return {}
    manifest = _read_manifest(client, day)
    if not manifest:
        return {}
    try:
        blob = client.get(_data_key(str(manifest.get("day"))))
        data = _decode(blob) if blob else {}
    except Exception as exc:
        log.warning("index_ohlc: read failed (%s)", exc)
        return {}
    if names:
        wanted = set(names)
        data = {k: v for k, v in data.items() if k in wanted}
    return data


def index_snapshot_status(day: str | None = None) -> dict:
    """Read-only diagnostics: is the snapshot present, complete and fresh."""
    client = _get_redis()
    if client is None:
        return {"available": False, "usable": False, "reason": "no_redis"}
    manifest = _read_manifest(client, day)
    if not manifest:
        return {"available": False, "usable": False, "reason": "no_snapshot"}
    snapshot_day = str(manifest.get("day") or "")
    try:
        data_ttl = client.ttl(_data_key(snapshot_day))
    except Exception:
        data_ttl = None
    series = manifest.get("series") or {}
    written_at = float(manifest.get("written_at") or 0)
    return {
        "available": True,
        "usable": bool(data_ttl is not None and data_ttl != -2
                       and all(n in series for n in REQUIRED)),
        "day": snapshot_day,
        "indices": manifest.get("indices"),
        "missing": manifest.get("missing") or [],
        "age_hours": round((time.time() - written_at) / 3600, 2) if written_at else None,
        "ttl_hours": round(data_ttl / 3600, 2) if isinstance(data_ttl, int) and data_ttl >= 0 else None,
        "series": series,
    }
