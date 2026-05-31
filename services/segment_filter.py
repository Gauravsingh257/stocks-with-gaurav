"""
services/segment_filter.py
==========================
NSE market-segment exclusion for the research universe.

Problem this solves (2026-05-31): the research engine was surfacing
Trade-to-Trade (-BE), surveillance (-BZ) and SME (-SM) segment stocks as
high-conviction Final Trade Ideas — e.g. KSHITIJPOL (₹4.74, KSHITIJPOL-BE)
and KOPRAN-BE. Those segments are delivery-only (no intraday), thin, and
frequently under surveillance for price manipulation — not appropriate for
a tradeable recommendation, and their charts don't even resolve in Kite
under the bare symbol.

A base symbol is treated as "T2T/SME-only" (excluded) iff Kite's NSE
instrument dump lists it with a -BE/-BZ/-SM suffix AND there is NO plain
(EQ) listing for the same base. So a stock that has a normal EQ listing is
never excluded; only ones whose ONLY listing is the surveillance segment.

Best-effort + fail-open: any failure (no Kite, no token, network) returns
an EMPTY set so the caller never loses its universe. Result is cached to a
daily JSON file so we hit Kite's instrument dump at most once per day.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

log = logging.getLogger("services.segment_filter")

_SUFFIXES = ("-BE", "-BZ", "-SM")
_ROOT = Path(__file__).resolve().parents[1]
_CACHE_DIR = _ROOT / "data"


def _cache_path(day: str) -> Path:
    return _CACHE_DIR / f"nse_t2t_sme_{day}.json"


def _kite_client():
    """Best-effort KiteConnect for the instrument dump. None on any failure."""
    try:
        from config.kite_auth import get_access_token, get_api_key  # type: ignore
    except Exception:
        try:
            from dashboard.backend.kite_auth import (  # type: ignore
                get_access_token,
                get_api_key,
            )
        except Exception:
            return None
    try:
        api_key = get_api_key()
        token = get_access_token()
        if not api_key or not token:
            return None
        from kiteconnect import KiteConnect

        k = KiteConnect(api_key=api_key)
        k.set_access_token(token)
        return k
    except Exception as exc:
        log.warning("segment_filter: kite client unavailable: %s", exc)
        return None


def _build_exclusions() -> set[str]:
    """Build the {NSE:BASE} exclusion set from the live Kite NSE dump.
    Returns empty set on any failure (fail-open)."""
    kite = _kite_client()
    if kite is None:
        return set()
    try:
        instruments = kite.instruments("NSE")
    except Exception as exc:
        log.warning("segment_filter: instruments() failed: %s", exc)
        return set()

    eq_plain: set[str] = set()
    suffixed: dict[str, str] = {}  # base -> full tradingsymbol
    for row in instruments:
        if (row.get("instrument_type") or "") != "EQ":
            continue
        ts = (row.get("tradingsymbol") or "").strip().upper()
        if not ts:
            continue
        matched = next((s for s in _SUFFIXES if ts.endswith(s)), None)
        if matched:
            suffixed[ts[: -len(matched)]] = ts
        else:
            eq_plain.add(ts)

    # Exclude only bases whose ONLY listing is the surveillance/SME segment.
    exclusions = {f"NSE:{base}" for base in suffixed if base not in eq_plain}
    return exclusions


def get_t2t_sme_exclusions() -> set[str]:
    """Daily-cached set of {NSE:BASE} symbols to exclude. Fail-open (empty)."""
    try:
        day = date.today().isoformat()
        path = _cache_path(day)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return set(data)
            except Exception:
                pass  # corrupt cache — rebuild
        exclusions = _build_exclusions()
        if exclusions:
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(sorted(exclusions)), encoding="utf-8")
            except Exception as exc:
                log.warning("segment_filter: cache write failed: %s", exc)
        return exclusions
    except Exception as exc:
        log.warning("segment_filter: get_t2t_sme_exclusions failed: %s", exc)
        return set()


def filter_universe(symbols: list[str]) -> tuple[list[str], int]:
    """Return (filtered_symbols, removed_count). Flag-gated by
    RESEARCH_EXCLUDE_T2T_SME (default on). Fail-open + sanity-capped: if the
    exclusion set would remove an implausibly large share (>25%) of the
    universe, it is treated as suspect and NOT applied."""
    if os.getenv("RESEARCH_EXCLUDE_T2T_SME", "1").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        return symbols, 0
    excl = get_t2t_sme_exclusions()
    if not excl:
        return symbols, 0
    keep = [s for s in symbols if s.strip().upper() not in excl]
    removed = len(symbols) - len(keep)
    # Sanity cap: never let a malformed exclusion set gut the universe.
    if symbols and removed > 0.25 * len(symbols):
        log.warning(
            "segment_filter: exclusion would remove %d/%d (>25%%) — skipping as suspect",
            removed,
            len(symbols),
        )
        return symbols, 0
    return keep, removed
