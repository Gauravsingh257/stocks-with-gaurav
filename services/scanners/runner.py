"""
services/scanners/runner.py — Scanner orchestrator (producer core).

One run:
  1. Resolve the curated universe (liquid by default).
  2. For each distinct interval (day/week), fetch the universe's OHLC ONCE
     (max lookback needed) — so N scanners on the same interval share one fetch.
  3. For every scanner: evaluate each symbol → liquidity gate → quality score →
     rank best-first → build snapshot payload → write to Redis (gate + LKG).
  4. Write the scanner index (catalog).

Pure orchestration; the heavy Kite I/O is isolated in data_layer. Designed to be
called by scripts/scanner_cron.py. Returns a summary dict for logging/health.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from services.scanners.registry import get_scanners
from services.scanners.universe import get_scanner_universe
from services.scanners.scoring import passes_gates, score_and_tier
from services.scanners import snapshot_store

log = logging.getLogger("services.scanners.runner")
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).isoformat()


def run_all(mode: str = "liquid", fetcher=None) -> dict:
    """Run every registered scanner and write snapshots. Returns a summary."""
    from services.scanners.data_layer import KiteOHLCFetcher

    scanners = get_scanners()

    if fetcher is None:
        fetcher = KiteOHLCFetcher()
    user = fetcher.verify_token()
    n_inst = fetcher.load_instruments()
    log.info("scanner run: kite ok (user=%s) instruments=%d", user, n_inst)

    # Resolve universe AFTER instruments load so the curated "liquid" set can be
    # augmented with Kite's live F&O underlyings (current + self-maintaining),
    # fixing coverage gaps in the static lists. "full" mode is left untouched.
    universe = get_scanner_universe(mode)
    if mode != "full":
        try:
            fno = fetcher.load_fno_underlyings()
            if fno:
                before = len(universe)
                seen = set(universe)
                universe = universe + [s for s in sorted(fno) if s not in seen]
                log.info("scanner universe: +%d live F&O underlyings (%d→%d)",
                         len(universe) - before, before, len(universe))
        except Exception as exc:
            log.warning("scanner universe: F&O augmentation skipped (%s)", exc)
    log.info("scanner run: mode=%s universe=%d scanners=%d", mode, len(universe), len(scanners))

    # Group scanners by interval; fetch each interval's OHLC once at max lookback.
    by_interval: dict[str, int] = {}
    for sc in scanners:
        by_interval[sc.interval] = max(by_interval.get(sc.interval, 0), sc.lookback_days)

    ohlc_by_interval: dict[str, dict] = {}
    fetch_errors: dict[str, int] = {}
    for interval, lookback in by_interval.items():
        data, errs = fetcher.fetch_universe(universe, interval, lookback)
        ohlc_by_interval[interval] = data
        fetch_errors[interval] = errs
        log.info("scanner fetch: interval=%s fetched=%d errors=%d", interval, len(data), errs)

    summary = {"mode": mode, "universe_size": len(universe), "scanners": [], "computed_at": _now_ist_iso()}
    index_entries: list[dict] = []

    for sc in scanners:
        data = ohlc_by_interval.get(sc.interval, {})
        rows: list[dict] = []
        scanned = 0
        as_of = None
        for sym, candles in data.items():
            scanned += 1
            if candles:
                as_of = candles[-1].get("date") or as_of
            try:
                metrics = sc.evaluate(candles, sc.bars_per_year)
            except Exception as exc:
                log.debug("evaluate failed %s/%s (%s)", sc.name, sym, exc)
                continue
            if not metrics:
                continue
            if not passes_gates(metrics):
                continue
            row = score_and_tier(metrics)
            row["symbol"] = sym
            rows.append(row)

        rows.sort(key=lambda r: r.get("quality_score", 0), reverse=True)

        payload = {
            "scanner": sc.name,
            "timeframe": sc.timeframe,
            "label": sc.label,
            "description": sc.description,
            "as_of": as_of,
            "computed_at": _now_ist_iso(),
            "universe_mode": mode,
            "universe_size": len(universe),
            "scanned": scanned,
            "fetch_errors": fetch_errors.get(sc.interval, 0),
            "hits": len(rows),
            "rows": rows,
        }
        written = snapshot_store.write_snapshot(sc.name, sc.timeframe, payload, allow_empty=True)
        log.info("scanner %s/%s: hits=%d written=%s", sc.name, sc.timeframe, len(rows), written)

        summary["scanners"].append(
            {"name": sc.name, "timeframe": sc.timeframe, "hits": len(rows), "written": written, "as_of": as_of}
        )
        index_entries.append(
            {
                "name": sc.name,
                "timeframe": sc.timeframe,
                "label": sc.label,
                "description": sc.description,
                "hits": len(rows),
                "as_of": as_of,
                "computed_at": payload["computed_at"],
            }
        )

    snapshot_store.write_index(index_entries)
    return summary
