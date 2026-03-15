"""
dashboard/backend/rate_limit.py
Per-IP rate limit: 60 requests per minute (sliding window).
Returns 429 Too Many Requests with Retry-After when exceeded.
"""

import logging
import time
from collections import defaultdict
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("dashboard.ratelimit")

LIMIT_PER_MINUTE = 60
WINDOW_SECONDS = 60

# ip -> list of request timestamps (pruned to last WINDOW_SECONDS)
_counters: dict[str, list[float]] = defaultdict(list)
_lock = Lock()
# Cap number of IPs we track to avoid unbounded growth
_MAX_IPS = 10_000


def _client_ip(request: Request) -> str:
    """Prefer X-Forwarded-For (Railway/Vercel) else request.client.host."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.scope.get("client"):
        return request.scope["client"][0]
    return "unknown"


# Paths that do not count toward rate limit (health checks, readiness)
SKIP_PATHS = {"/health", "/api/system/health"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in SKIP_PATHS:
            return await call_next(request)
        ip = _client_ip(request)
        now = time.monotonic()
        cutoff = now - WINDOW_SECONDS

        with _lock:
            # Prune old entries for this IP
            times = _counters[ip]
            while times and times[0] < cutoff:
                times.pop(0)
            if len(times) >= LIMIT_PER_MINUTE:
                retry_after = int(times[0] + WINDOW_SECONDS - now) if times else WINDOW_SECONDS
                retry_after = max(1, min(retry_after, WINDOW_SECONDS))
                log.warning("Rate limit exceeded for IP %s", ip)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Limit is 60 per minute per IP.",
                        "retry_after_seconds": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            times.append(now)
            # Housekeep: if we have too many IPs, drop oldest bucket (simple eviction)
            if len(_counters) > _MAX_IPS:
                keys_to_drop = sorted(_counters.keys(), key=lambda k: _counters[k][0] if _counters[k] else 0)[: _MAX_IPS // 10]
                for k in keys_to_drop:
                    del _counters[k]

        response = await call_next(request)
        # Add rate limit headers to successful responses
        with _lock:
            times = _counters.get(ip, [])
            remaining = max(0, LIMIT_PER_MINUTE - len([t for t in times if t > cutoff]))
        response.headers["X-RateLimit-Limit"] = str(LIMIT_PER_MINUTE)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
