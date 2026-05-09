"""
Lightweight in-process latency samples for /api/* routes (Railway single-worker friendly).

Not a replacement for APM — feeds /api/system/debug/platform only.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Dict, List

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_MAX_SAMPLES = 2500
_samples: deque[tuple[str, float]] = deque(maxlen=_MAX_SAMPLES)


class ApiLatencyMiddleware(BaseHTTPMiddleware):
    """Inner timing for /api/* — register this middleware FIRST so it sits closest to routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        t0 = time.perf_counter_ns()
        response = await call_next(request)
        ms = (time.perf_counter_ns() - t0) / 1_000_000
        record_latency(path, ms)
        return response


def record_latency(path: str, ms: float) -> None:
    if path.startswith("/api/"):
        _samples.append((path, ms))


def get_latency_summary() -> Dict[str, Any]:
    if not _samples:
        return {"samples": 0, "avg_ms": None, "p95_ms": None, "slow_gt_500ms": 0}
    vals = sorted(ms for _, ms in _samples)
    n = len(vals)
    avg = sum(vals) / n
    p95_idx = min(n - 1, int(round(0.95 * (n - 1))))
    p95 = vals[p95_idx]
    slow = sum(1 for ms in vals if ms > 500.0)
    return {
        "samples": n,
        "avg_ms": round(avg, 2),
        "p95_ms": round(p95, 2),
        "slow_gt_500ms": slow,
        "max_ms": round(max(vals), 2),
    }


def top_slow_paths(limit: int = 12) -> List[Dict[str, Any]]:
    """Rough path rollup from samples (last window only)."""
    by_path: Dict[str, list[float]] = {}
    for path, ms in _samples:
        by_path.setdefault(path, []).append(ms)
    rows = []
    for path, arr in by_path.items():
        rows.append({
            "path": path,
            "count": len(arr),
            "avg_ms": round(sum(arr) / len(arr), 2),
            "max_ms": round(max(arr), 2),
        })
    rows.sort(key=lambda x: -x["avg_ms"])
    return rows[:limit]
