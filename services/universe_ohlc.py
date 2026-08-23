"""
services/universe_ohlc.py — PHASE 0 reliable market data for cross-sectional work.

THE PROBLEM
-----------
The teardown measured the research scans pulling per-symbol yfinance bars and
getting 1,153/2,200 symbols on one scan and 1,987/2,200 on another THE SAME DAY.
Cross-sectional ranking compares a stock against its peers; when the peer set is
a different random half of the market every morning, a percentile rank is not a
measurement of anything.

WHY A SNAPSHOT AND NOT "JUST SWITCH TO KITE"
--------------------------------------------
Three constraints make the obvious fix wrong:

  1. `DataIngestion._resolve_token()` raises NotImplementedError. Setting
     RESEARCH_DATA_SOURCE=kite today returns an EMPTY frame for every symbol
     (the exception is swallowed) — a total blackout, not an upgrade.
  2. Kite historical is capped at 3 req/sec. 2,200 symbols is ~12 minutes, and
     SWING + LONGTERM scan separately, so ~25 minutes of a rate-limited key
     shared with the live trading engine.
  3. The research scheduler runs INSIDE the web service. The existing Kite layer
     (services/scanners/data_layer.py) states in its own docstring that it runs
     "exclusively in the cron worker — never on the web request path."

So the fetch moves to the scanner worker, which already owns the Kite plumbing,
already throttles correctly, and is already isolated from both the web service
and the trading engine. It publishes ONE snapshot per day; both horizons read it.
One fetch replaces two, and every engine compares the same universe on the same
bars.

TRANSPORT
---------
Redis, mirroring services/scanners/snapshot_store.py (the proven pattern): a
non-empty write gate, a last-known-good fallback, and an isolated `ohlc:universe:*`
namespace. Payloads are gzip+base64 columnar arrays and sharded, because 2,200
symbols x 420 bars as plain JSON is ~40 MB and does not belong in one key.

FLAG
----
`PHASE0_KITE_OHLC` (default OFF). When off, nothing reads or writes the snapshot
and every engine keeps fetching exactly as it does today.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import time
from datetime import date

log = logging.getLogger("services.universe_ohlc")

NAMESPACE = "ohlc:universe"
MANIFEST_SUFFIX = "manifest"
LIVE_TTL_SEC = int(os.getenv("UNIVERSE_OHLC_TTL_SEC", str(50 * 3600)))    # ~2 days
LKG_TTL_SEC = int(os.getenv("UNIVERSE_OHLC_LKG_TTL_SEC", str(7 * 86400)))  # 7 days
SHARD_SIZE = int(os.getenv("UNIVERSE_OHLC_SHARD_SIZE", "250"))

# Columnar field order inside a shard. Storing parallel arrays instead of a list
# of dicts removes the repeated key names, which is most of the payload size.
_FIELDS = ("date", "open", "high", "low", "close", "volume")


def kite_ohlc_enabled() -> bool:
    """Master flag. Default OFF -> engines fetch per-symbol exactly as today."""
    return os.getenv("PHASE0_KITE_OHLC", "0").strip().lower() in ("1", "true", "yes", "on")


def _bare(symbol: str) -> str:
    return str(symbol or "").replace("NSE:", "").replace(".NS", "").strip().upper()


def _get_redis():
    """Pooled client on the web side, direct connection on the worker side —
    the same dual path services/scanners/snapshot_store.py uses."""
    try:
        from dashboard.backend.cache import _get_redis as _pooled

        client = _pooled()
        if client is not None:
            return client
    except Exception:
        pass
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis as _redis_lib

        client = _redis_lib.from_url(url, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:
        log.warning("universe_ohlc: redis unavailable (%s)", exc)
        return None


def _manifest_key(day: str) -> str:
    return f"{NAMESPACE}:{day}:{MANIFEST_SUFFIX}"


def _shard_key(day: str, index: int) -> str:
    return f"{NAMESPACE}:{day}:{index}"


def _latest_key() -> str:
    return f"{NAMESPACE}:latest"


# ── encoding ──────────────────────────────────────────────────────────────────

def _encode(chunk: dict[str, list[dict]]) -> str:
    columnar = {
        symbol: {field: [bar.get(field) for bar in bars] for field in _FIELDS}
        for symbol, bars in chunk.items()
    }
    raw = json.dumps(columnar, separators=(",", ":"), default=str).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii")


def _decode(blob: str) -> dict[str, list[dict]]:
    raw = gzip.decompress(base64.b64decode(blob.encode("ascii")))
    columnar = json.loads(raw.decode("utf-8"))
    out: dict[str, list[dict]] = {}
    for symbol, columns in columnar.items():
        dates = columns.get("date") or []
        out[symbol] = [
            {field: columns[field][i] for field in _FIELDS if field in columns}
            for i in range(len(dates))
        ]
    return out


# ── writer (scanner worker) ───────────────────────────────────────────────────

def publish_universe_ohlc(candles: dict[str, list[dict]], *, day: str | None = None,
                          min_symbols: int = 200) -> dict:
    """Publish one day's full-universe bars. Returns a manifest dict.

    Write gate: a snapshot with fewer than `min_symbols` symbols is REFUSED, so a
    half-failed fetch can never replace a good snapshot with a thin one — which
    is the exact failure mode (a randomly varying peer set) this module exists to
    stop.
    """
    day = day or date.today().isoformat()
    usable = {_bare(s): bars for s, bars in (candles or {}).items() if bars}
    if len(usable) < min_symbols:
        log.warning(
            "universe_ohlc: write REFUSED — only %d symbols (min %d). Keeping previous snapshot.",
            len(usable), min_symbols,
        )
        return {"written": False, "reason": "below_min_symbols", "symbols": len(usable)}

    client = _get_redis()
    if client is None:
        log.warning("universe_ohlc: write skipped (no redis)")
        return {"written": False, "reason": "no_redis", "symbols": len(usable)}

    symbols = sorted(usable)
    shards = [symbols[i : i + SHARD_SIZE] for i in range(0, len(symbols), SHARD_SIZE)]
    index: dict[str, int] = {}
    total_bytes = 0
    try:
        pipe = client.pipeline(transaction=False)
        for shard_no, shard_symbols in enumerate(shards):
            blob = _encode({s: usable[s] for s in shard_symbols})
            total_bytes += len(blob)
            pipe.setex(_shard_key(day, shard_no), LIVE_TTL_SEC, blob)
            for symbol in shard_symbols:
                index[symbol] = shard_no
        manifest = {
            "day": day,
            "symbols": len(symbols),
            "shards": len(shards),
            "shard_size": SHARD_SIZE,
            "index": index,
            "bytes": total_bytes,
            "written_at": time.time(),
        }
        payload = json.dumps(manifest, separators=(",", ":"))
        pipe.setex(_manifest_key(day), LKG_TTL_SEC, payload)
        pipe.setex(_latest_key(), LKG_TTL_SEC, day)
        pipe.execute()
    except Exception as exc:
        log.warning("universe_ohlc: write failed (%s)", exc)
        return {"written": False, "reason": str(exc)[:200], "symbols": len(usable)}

    log.info(
        "universe_ohlc: published %d symbols in %d shards (%.1f MB) for %s",
        len(symbols), len(shards), total_bytes / 1e6, day,
    )
    return {"written": True, "day": day, "symbols": len(symbols),
            "shards": len(shards), "bytes": total_bytes}


# ── reader (research / web service) ───────────────────────────────────────────

def _read_manifest(client, day: str | None) -> dict | None:
    if day is None:
        try:
            day = client.get(_latest_key())
        except Exception:
            return None
    if not day:
        return None
    try:
        raw = client.get(_manifest_key(str(day)))
    except Exception:
        return None
    if not raw:
        return None
    try:
        manifest = json.loads(raw)
        return manifest if isinstance(manifest, dict) else None
    except Exception:
        return None


def load_universe_ohlc(symbols: list[str] | None = None, *, day: str | None = None) -> dict[str, list[dict]]:
    """Read the published snapshot. Returns {BARE_SYMBOL: candles}.

    Only the shards actually needed are fetched, so asking for 50 symbols does
    not pull 40 MB. An empty dict means "no snapshot" — callers must fall back to
    their existing per-symbol fetch rather than treating it as an empty market.
    """
    client = _get_redis()
    if client is None:
        return {}
    manifest = _read_manifest(client, day)
    if not manifest:
        return {}

    index: dict[str, int] = manifest.get("index") or {}
    snapshot_day = str(manifest.get("day") or "")
    wanted = {_bare(s) for s in symbols} if symbols else set(index)
    needed_shards = sorted({index[s] for s in wanted if s in index})
    if not needed_shards:
        return {}

    out: dict[str, list[dict]] = {}
    try:
        blobs = client.mget([_shard_key(snapshot_day, n) for n in needed_shards])
    except Exception as exc:
        log.warning("universe_ohlc: shard read failed (%s)", exc)
        return {}
    for blob in blobs:
        if not blob:
            continue
        try:
            for symbol, bars in _decode(blob).items():
                if symbol in wanted:
                    out[symbol] = bars
        except Exception as exc:
            log.warning("universe_ohlc: shard decode failed (%s)", exc)
    return out


def load_universe_frames(symbols: list[str] | None = None, *, day: str | None = None) -> dict[str, object]:
    """Same as `load_universe_ohlc` but as pandas DataFrames keyed by the
    caller's original symbol spelling (`NSE:X`), which is what the research
    engines already pass around. Empty dict when no snapshot exists."""
    bars = load_universe_ohlc(symbols, day=day)
    if not bars:
        return {}
    import pandas as pd  # noqa: PLC0415

    out: dict[str, object] = {}
    for original in (symbols or list(bars)):
        candles = bars.get(_bare(original))
        if not candles:
            continue
        frame = pd.DataFrame(candles)
        if frame.empty or "close" not in frame.columns:
            continue
        out[original] = frame
    return out


def snapshot_status(day: str | None = None) -> dict:
    """Is there a usable snapshot, how big, how old. Read-only diagnostics."""
    client = _get_redis()
    if client is None:
        return {"available": False, "reason": "no_redis"}
    manifest = _read_manifest(client, day)
    if not manifest:
        return {"available": False, "reason": "no_snapshot"}
    written_at = float(manifest.get("written_at") or 0)
    return {
        "available": True,
        "day": manifest.get("day"),
        "symbols": manifest.get("symbols"),
        "shards": manifest.get("shards"),
        "bytes": manifest.get("bytes"),
        "age_hours": round((time.time() - written_at) / 3600, 2) if written_at else None,
        "stale": bool(written_at and (time.time() - written_at) > LIVE_TTL_SEC),
    }


# ── producer entry point (called by the scanner cron) ─────────────────────────

def refresh_universe_ohlc(*, target_universe: int | None = None,
                          lookback_days: int | None = None) -> dict:
    """Fetch the full universe from Kite and publish it. Scanner-worker only.

    Deliberately NOT importable-and-safe to call from the web service: it takes
    ~12 minutes at Kite's 3 req/sec cap. The scanner worker is the right home —
    it already holds the throttled fetcher and runs nowhere near a request.
    """
    from services.scanners.data_layer import KiteOHLCFetcher
    from services.universe_manager import load_nse_universe

    target = target_universe or int(os.getenv("UNIVERSE_OHLC_TARGET", "2200"))
    lookback = lookback_days or int(os.getenv("UNIVERSE_OHLC_LOOKBACK_DAYS", "600"))

    snapshot = load_nse_universe(target)
    symbols = [_bare(s) for s in snapshot.symbols]
    log.info("universe_ohlc: fetching %d symbols x %dd via Kite (3 req/s cap)", len(symbols), lookback)

    started = time.time()
    fetcher = KiteOHLCFetcher()
    log.info("universe_ohlc: kite token ok for %s", fetcher.verify_token())
    log.info("universe_ohlc: %d NSE-EQ instrument tokens", fetcher.load_instruments())
    candles, errors = fetcher.fetch_universe(symbols, "day", lookback)
    elapsed = round(time.time() - started, 1)
    log.info("universe_ohlc: fetched %d/%d symbols in %ss (%d errors/missing)",
             len(candles), len(symbols), elapsed, errors)

    result = publish_universe_ohlc(candles)
    result.update({
        "requested": len(symbols),
        "fetched": len(candles),
        "errors": errors,
        "coverage_pct": round(len(candles) / max(len(symbols), 1) * 100, 2),
        "elapsed_sec": elapsed,
    })
    return result
