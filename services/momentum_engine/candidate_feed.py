"""
services/momentum_engine/candidate_feed.py
===========================================
Phase 2 — the ONLY source of Momentum candidates.

A candidate must be:
    Discovery-passed (layer1_pass = 1)   AND
    rejected by SMC for a STRUCTURAL reason (no_BOS / no_liquidity_sweep /
    no_order_block).

The feed is strictly read-only: it consumes the validation output the existing
pipeline already produces (in-memory records at scan time, or the persisted
`signals_log` for shadow/backtest). It NEVER re-runs Discovery, modifies
Discovery, or weakens SMC validation.
"""

from __future__ import annotations

import json
import logging

from .models import MomentumCandidate

log = logging.getLogger("services.momentum_engine.candidate_feed")

# The structural SMC misses that define "moving without a pullback setup".
STRUCTURAL_REJECT_REASONS = frozenset({
    "no_bos", "no_liquidity_sweep", "no_order_block",
})


def _is_structural(reasons: list | tuple | None) -> tuple[bool, tuple[str, ...]]:
    if not reasons:
        return False, ()
    norm = tuple(str(r).strip() for r in reasons if r)
    hit = tuple(r for r in norm if r.lower() in STRUCTURAL_REJECT_REASONS)
    return (len(hit) > 0), norm


def from_records(records: list[dict]) -> list[MomentumCandidate]:
    """Pure transform of validation records (one dict per symbol from a scan)
    into Momentum candidates. Selects only discovery-passed + structural-SMC-
    rejected rows. Safe on partial/None fields.
    """
    out: list[MomentumCandidate] = []
    for r in records or []:
        try:
            if not r.get("layer1_pass"):
                continue
            if r.get("final_selected"):
                continue  # SMC (or downstream) already took it — hands off
            reasons = r.get("rejection_reason") or r.get("rejection_reasons")
            if isinstance(reasons, str):
                try:
                    reasons = json.loads(reasons)
                except Exception:
                    reasons = [reasons]
            structural, norm = _is_structural(reasons)
            if not structural:
                continue
            cmp = r.get("cmp")
            if cmp is None or float(cmp) <= 0:
                continue
            out.append(MomentumCandidate(
                symbol=str(r.get("symbol") or "").replace("NSE:", "").strip().upper(),
                horizon=str(r.get("horizon") or "SWING").upper(),
                scan_date=str(r.get("date") or ""),
                cmp=float(cmp),
                discovery_score=_flt(r.get("discovery_score")),
                breakout_score=_flt(r.get("breakout_score")),
                smc_rejection_reasons=norm,
            ))
        except Exception as exc:
            log.debug("candidate transform skipped (%s): %s", r.get("symbol"), exc)
    return out


def from_signals_log(day: str | None = None, horizon: str = "SWING",
                     limit: int = 5000) -> list[MomentumCandidate]:
    """Read the persisted SMC-reject stream from `signals_log` (shadow/backtest).
    Read-only. `day=None` → the most recent scan date present."""
    try:
        from dashboard.backend.db.schema import get_connection
    except Exception as exc:
        log.warning("candidate_feed: db unavailable (%s)", exc)
        return []
    conn = get_connection()
    try:
        if day is None:
            row = conn.execute(
                "SELECT MAX(date) FROM signals_log WHERE horizon = ?", (horizon.upper(),)
            ).fetchone()
            day = row[0] if row else None
            if not day:
                return []
        rows = conn.execute(
            """
            SELECT symbol, horizon, date, cmp, rejection_reason, layer1_pass, final_selected
            FROM signals_log
            WHERE date = ? AND horizon = ? AND layer1_pass = 1 AND final_selected = 0
            LIMIT ?
            """,
            (day, horizon.upper(), limit),
        ).fetchall()
        return from_records([dict(r) for r in rows])
    finally:
        conn.close()


def _flt(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
