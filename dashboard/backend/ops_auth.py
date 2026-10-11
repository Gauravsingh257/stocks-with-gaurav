"""
Protection for operational / debug endpoints.

Set OPS_API_KEY in Railway — clients send header X-Ops-Key: <value>.

FAIL CLOSED: if OPS_API_KEY is unset or empty, every guarded route is DENIED.
Until 2026-10-11 an unset key meant "open" (backward compatible), and the key
was in fact unset in production — so /api/system/debug/{cache,platform,
signals,watchlist-sync} were public and POST /api/auth/upgrade let any
logged-in user make themselves PREMIUM. A missing secret must never widen access.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


def verify_ops_key(x_ops_key: str | None = Header(default=None, alias="X-Ops-Key")) -> None:
    expected = os.getenv("OPS_API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=403, detail="ops_key_not_configured")
    provided = (x_ops_key or "").strip()
    # Constant-time compare so the key can't be recovered by response timing.
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="ops_key_required")
