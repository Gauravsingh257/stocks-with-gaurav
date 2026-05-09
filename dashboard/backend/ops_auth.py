"""
Optional protection for operational / debug endpoints.

Set OPS_API_KEY in Railway — clients send header X-Ops-Key: <value>.
If OPS_API_KEY is unset, routes remain open (backward compatible).
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def verify_ops_key(x_ops_key: str | None = Header(default=None, alias="X-Ops-Key")) -> None:
    expected = os.getenv("OPS_API_KEY", "").strip()
    if not expected:
        return
    if not x_ops_key or x_ops_key.strip() != expected:
        raise HTTPException(status_code=403, detail="ops_key_required")
