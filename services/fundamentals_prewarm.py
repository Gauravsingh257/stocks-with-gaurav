"""
services/fundamentals_prewarm.py — fill the fundamentals cache BEFORE the morning scans.

Why
---
The Swing (08:30 IST) and Long-Term (08:40 IST) scans fetch Yahoo `.info` for the
whole ~2,187-name universe at FUND_FETCH_CONCURRENCY=8 — roughly 1,000 requests a
minute. Measured 2026-09-27: Yahoo answers ~900 of those, then raises
YFRateLimitError for every further request for ~4-5 minutes. The failures were
cached as "no fundamentals" for 24h, so production coverage swung 709-1,981/2,200
by day and the missing names were systematically the late-alphabet ones.

A sustained ~50 requests/minute never tripped the limit. Pacing the scans
themselves would delay them by ~40 minutes toward the open, so instead this job
refreshes the cache out of band at that pace, and the scans then read a warm
cache and make almost no provider calls.

What it does
------------
- Refreshes every symbol whose cache entry is missing or older than `max_age_h`.
- Order is a deterministic hash of (date, symbol): reproducible, and no symbol
  is permanently at the tail where a budget would run out.
- A global pacer caps the request rate; a rate-limit response pauses every worker
  for `cooldown_s`; failed symbols get `retry_passes` more attempts at the end.
- Stops at `deadline_s` no matter what, so it can never run into the scans.
- Only real provider answers are written (see FUND_COVERAGE_FIX in
  fundamental_analysis). It never fabricates or extends the life of old data:
  a symbol that still fails is simply left for the scan's own fetch.

It changes no scoring, ranking or selection code — only how many symbols have
real fundamentals when the scans run.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from services import fundamental_analysis as fa

log = logging.getLogger("services.fundamentals_prewarm")


class Pacer:
    """Shared token schedule: at most `rpm` request starts per minute across threads,
    with a global pause after a rate-limit response."""

    def __init__(self, rpm: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = 60.0 / max(rpm, 0.1)
        self._next = 0.0
        self._lock = threading.Lock()
        self._clock, self._sleep = clock, sleep

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            self._sleep(start - now)

    def cooldown(self, seconds: float) -> None:
        with self._lock:
            self._next = max(self._next, self._clock() + seconds)


def prewarm_order(symbols: list[str], day: str) -> list[str]:
    """Deterministic for a given day, uncorrelated with the alphabet."""
    return sorted(dict.fromkeys(symbols), key=lambda s: hashlib.sha256(f"{day}:{s}".encode()).hexdigest())


def _is_rate_limit(info: dict) -> bool:
    reason = getattr(info, "reason", "")
    return "RateLimit" in reason or "TooMany" in reason


def prewarm(symbols: list[str], *, rpm: float = 50.0, workers: int = 2, max_age_h: float = 12.0,
            retry_passes: int = 2, cooldown_s: float = 300.0, deadline_s: float = 90 * 60,
            day: str | None = None, clock: Callable[[], float] = time.monotonic,
            sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    start = clock()
    order = prewarm_order(symbols, day or date.today().isoformat())
    todo = [s for s in order if (age := fa.cache_age_hours(s)) is None or age >= max_age_h]
    pacer = Pacer(rpm, clock, sleep)
    status: dict[str, str] = {}
    requests = 0
    rate_limits = 0
    lock = threading.Lock()
    stopped = False

    def one(sym: str) -> None:
        nonlocal requests, rate_limits, stopped
        if clock() - start >= deadline_s:
            stopped = True
            return
        pacer.wait()
        if clock() - start >= deadline_s:   # a cooldown may have slept past it
            stopped = True
            return
        info = fa._fetch_yf_info(sym)
        st = fa.fetch_status(info)
        if st != "transient":
            fa._save_snapshot_from_info(sym, info)
        with lock:
            requests += 1
            status[sym] = st
            if st == "transient" and _is_rate_limit(info):
                rate_limits += 1
                pacer.cooldown(cooldown_s)

    passes = []
    batch = todo
    for n in range(1 + retry_passes):
        if not batch or stopped:
            break
        if n:
            sleep(min(cooldown_s, max(0.0, deadline_s - (clock() - start))))
        with ThreadPoolExecutor(max(1, workers)) as ex:
            list(ex.map(one, batch))
        failed = {s for s in batch if status.get(s) == "transient"}
        passes.append({"attempted": len(batch), "transient_failures": len(failed)})
        batch = [s for s in order if s in failed]

    counts = Counter(status.get(s, "not_attempted") for s in todo)
    return {
        "universe": len(order),
        "already_fresh": len(order) - len(todo),
        "refreshed": len(todo),
        "status": dict(counts),
        "requests": requests,
        "rate_limit_responses": rate_limits,
        "passes": passes,
        "stopped_at_deadline": stopped,
        "elapsed_s": round(clock() - start, 1),
        "still_failed": sorted(s for s in todo if status.get(s) in ("transient", None)),
    }
