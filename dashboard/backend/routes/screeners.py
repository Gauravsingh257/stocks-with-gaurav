"""
dashboard/backend/routes/screeners.py — Screeners (scanner suite) read API.

PURE Redis read path: serves the snapshots written by the separate cron worker
(scripts/scanner_cron.py). Never calls Kite, never computes — a request is a
single Redis GET, so the site stays fast under any load.

Endpoints:
  GET /api/screeners                      → catalog of available scanners (public)
  GET /api/screeners/{name}/{timeframe}   → ranked results (PAID: PREMIUM/ADMIN)

Paywall: unauthenticated / FREE users receive a LOCKED teaser (hit count + tier
breakdown + blurred sample) instead of full rows. PREMIUM/ADMIN get full data.

Safety: only scanners present in the registry are served (no arbitrary Redis key
reads). LKG fallback is handled inside snapshot_store, so a producer hiccup never
blanks the section.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from services.scanners import snapshot_store
from services.scanners.registry import get_scanners, find_scanner

try:
    from dashboard.backend.routes.auth import get_optional_user
except Exception:  # pragma: no cover — keep router importable even if auth shifts
    def get_optional_user(authorization: str | None = None) -> dict | None:
        return None

router = APIRouter(tags=["screeners"])
log = logging.getLogger("dashboard.screeners")


def _is_entitled(user: dict | None) -> bool:
    return bool(user) and str(user.get("role", "")).upper() in ("PREMIUM", "ADMIN")


def _tier_breakdown(rows: list[dict]) -> dict:
    out = {"quality": 0, "momentum": 0, "speculative": 0}
    for r in rows:
        t = r.get("tier")
        if t in out:
            out[t] += 1
    return out


@router.get("/api/screeners")
def list_screeners():
    """Public catalog of available scanners (drives the section + teaser).

    Prefers the cron-written index; falls back to the registry definitions so the
    catalog still renders before the first producer run completes.
    """
    idx = snapshot_store.read_index()
    if idx and isinstance(idx.get("scanners"), list) and idx["scanners"]:
        return {"scanners": idx["scanners"], "source": "index"}
    # Fallback: registry definitions (no run yet).
    cat = [
        {
            "name": s.name,
            "timeframe": s.timeframe,
            "label": s.label,
            "description": s.description,
            "hits": None,
            "as_of": None,
            "computed_at": None,
        }
        for s in get_scanners()
    ]
    return {"scanners": cat, "source": "registry"}


@router.get("/api/screeners/{name}/{timeframe}")
def get_screener(name: str, timeframe: str, user: dict | None = Depends(get_optional_user)):
    """Ranked results for one scanner+timeframe. Paid-gated."""
    # Validate against the registry — never read an arbitrary Redis key.
    if find_scanner(name, timeframe) is None:
        raise HTTPException(status_code=404, detail="Unknown scanner or timeframe")

    snap = snapshot_store.read_snapshot(name, timeframe)
    if not snap:
        # No live + no LKG yet (producer hasn't run). Not an error — empty state.
        return JSONResponse(
            {
                "scanner": name, "timeframe": timeframe,
                "rows": [], "hits": 0, "as_of": None, "computed_at": None,
                "snapshot_source": "none", "snapshot_stale": True,
                "pending": True, "locked": not _is_entitled(user),
            }
        )

    rows = snap.get("rows") if isinstance(snap.get("rows"), list) else []
    base = {
        "scanner": name,
        "timeframe": timeframe,
        "label": snap.get("label"),
        "description": snap.get("description"),
        "as_of": snap.get("as_of"),
        "computed_at": snap.get("computed_at"),
        "universe_mode": snap.get("universe_mode"),
        "universe_size": snap.get("universe_size"),
        "hits": snap.get("hits", len(rows)),
        "snapshot_source": snap.get("snapshot_source"),
        "snapshot_stale": snap.get("snapshot_stale", False),
        "tiers": _tier_breakdown(rows),
    }

    if _is_entitled(user):
        base["rows"] = rows
        base["locked"] = False
        return base

    # ── Locked teaser ─────────────────────────────────────────────────────────
    sample = []
    for r in rows[:5]:
        sample.append({"quality_score": r.get("quality_score"), "tier": r.get("tier"), "flip": r.get("flip")})
    base["rows"] = []
    base["locked"] = True
    base["upgrade_required"] = True
    base["sample_locked"] = sample   # scores/tiers only — no symbols or levels
    return base
