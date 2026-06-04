"""
services/anchor_shadow.py
─────────────────────────
Shared core for the ENTRY_ANCHOR_MAX_GAP_PCT=10 (Anchor10) shadow programme.

Single source of truth for:
  • reconstructing the Config-A (current) vs Config-B (Anchor10) books,
  • the session metrics,
  • recording one session/day to Redis (append-only, deduped by date),
  • the breach checks (signal-count collapse / actionability / distance),
  • the C1–C5 enablement-criteria evaluation surfaced by the API.

Imported by the scheduler job (agents/runner.py), the status API
(dashboard/backend/routes/research.py) and the CLI tools under scripts/.
Observational only — it never changes engine behaviour and never enables a flag.
"""
from __future__ import annotations

import json
import os
import statistics
from dataclasses import dataclass, field

# ── bands / thresholds ────────────────────────────────────────────────────────
ACTIONABLE_PCT = 5.0
EXTENDED_PCT = 10.0
SWING_TARGET_MULT = 3.0
TARGET_FLOOR_R = 1.5

ANCHOR_GAP_DEFAULT = float(os.getenv("SHADOW_ANCHOR_GAP_PCT", "10"))

# Go/no-go + alert thresholds (mirror docs/PHASE2_ENABLEMENT_CRITERIA.md)
C1_MAX_COUNT_DROP_PCT = 20.0     # signal count must not collapse > this
C2_MIN_ACTIONABLE_PCT = 60.0     # Anchor10 actionable %
C3_MAX_AVG_DIST_PCT = 6.0        # Anchor10 avg distance from entry
C4_MIN_MEDIAN_REM_RR = 2.0       # Anchor10 median remaining RR from CMP
SESSIONS_REQUIRED = 3            # minimum stable sessions before a GO call

REDIS_SESSIONS_KEY = "shadow:anchor10:sessions"


@dataclass
class Setup:
    symbol: str
    entry: float
    stop: float
    target: float
    cmp: float
    confidence: float = 0.0
    is_at_52w_high: bool = False
    pct_below_52w_high: float | None = None
    date: str = ""
    target_cap: float | None = None    # exact pivot cap, filled in engine mode only


# ── parsing ───────────────────────────────────────────────────────────────────

def row_to_setup(r: dict) -> Setup | None:
    entry = r.get("entry") if r.get("entry") is not None else r.get("entry_price")
    stop = r.get("stop_loss")
    target = r.get("target")
    if target is None:
        target = r.get("target_1") or r.get("target_2")
        if target is None and isinstance(r.get("targets"), list) and r["targets"]:
            target = r["targets"][-1]
    cmp = r.get("cmp") if r.get("cmp") is not None else r.get("scan_cmp")
    if entry is None or stop is None or target is None or cmp is None:
        return None
    ld = r.get("layer_details") or {}
    if isinstance(ld, str):
        try:
            ld = json.loads(ld)
        except Exception:
            ld = {}
    disc = ld.get("discovery", {}) if isinstance(ld, dict) else {}
    try:
        return Setup(
            symbol=str(r.get("symbol")),
            entry=float(entry), stop=float(stop), target=float(target), cmp=float(cmp),
            confidence=float(r.get("confidence") or r.get("confidence_score") or 0),
            is_at_52w_high=bool(disc.get("is_at_52w_high")),
            pct_below_52w_high=(float(disc["pct_below_52w_high"])
                                if disc.get("pct_below_52w_high") is not None else None),
            date=str(r.get("date") or ""),
        )
    except (TypeError, ValueError):
        return None


def book_from_payload(payload: dict) -> list[Setup]:
    rows = payload.get("final_trades") or payload.get("items") or []
    return [s for r in rows if (s := row_to_setup(r))]


# ── config reconstruction (mirrors validation_engine._scored_smc_levels) ──────

def _atr_estimate(s: Setup) -> float:
    return max((s.entry - s.stop) / 1.3, s.entry * 0.03)


def cfg_current(s: Setup) -> tuple[float, float, float]:
    return s.entry, s.stop, s.target


def cfg_anchor(s: Setup, max_gap_pct: float) -> tuple[float, float, float]:
    if s.cmp <= 0 or s.entry <= 0:
        return s.entry, s.stop, s.target
    gap = abs(s.entry - s.cmp) / s.cmp * 100.0
    if gap <= max_gap_pct:
        return s.entry, s.stop, s.target
    atr = _atr_estimate(s)
    entry_b = round(s.cmp - 0.5 * atr, 2)
    stop_b = s.stop
    if stop_b >= entry_b:
        stop_b = round(entry_b - (s.entry - s.stop), 2)
    risk_b = entry_b - stop_b
    if risk_b <= 0:
        return s.entry, s.stop, s.target
    return entry_b, stop_b, round(entry_b + risk_b * SWING_TARGET_MULT, 2)


def cfg_targetcap(s: Setup) -> tuple[float, float, float]:
    if s.target_cap is not None:
        return s.entry, s.stop, s.target_cap
    if s.is_at_52w_high or not s.pct_below_52w_high or s.pct_below_52w_high <= 0:
        return s.entry, s.stop, s.target
    high52 = s.cmp / (1.0 - s.pct_below_52w_high / 100.0)
    floor = s.entry + (s.entry - s.stop) * TARGET_FLOOR_R
    return s.entry, s.stop, round(max(floor, min(s.target, high52)), 2)


def levels_for(setups: list[Setup], config: str, max_gap_pct: float = ANCHOR_GAP_DEFAULT) -> list[tuple]:
    fn = {"current": cfg_current, "anchor": lambda s: cfg_anchor(s, max_gap_pct),
          "targetcap": cfg_targetcap}[config]
    return [(*fn(s), s.cmp) for s in setups]


# ── metrics ───────────────────────────────────────────────────────────────────

def gap_from_entry(entry: float, cmp: float) -> float:
    return (cmp - entry) / entry * 100.0 if entry else 0.0


def remaining_rr(entry: float, stop: float, target: float, cmp: float) -> float | None:
    risk = cmp - stop
    return (target - cmp) / risk if risk > 0 else None


def metrics(levels: list[tuple]) -> dict:
    n = len(levels)
    if n == 0:
        return {"count": 0, "actionable": 0, "actionable_pct": 0.0,
                "avg_dist_from_entry_pct": 0.0, "median_remaining_rr": None,
                "avg_remaining_rr": None, "extended_gt10pct": 0, "extended_pct": 0.0,
                "quality_score": 0.0}
    gaps = [gap_from_entry(e, c) for e, _, _, c in levels]
    rrs = [r for e, s, t, c in levels if (r := remaining_rr(e, s, t, c)) is not None]
    actionable = sum(1 for g in gaps if abs(g) <= ACTIONABLE_PCT)
    extended = sum(1 for g in gaps if g > EXTENDED_PCT)
    pos_rr = [max(r, 0.0) for r in rrs] or [0.0]
    quality = round(50.0 * (actionable / n)
                    + 30.0 * min(statistics.median(pos_rr) / SWING_TARGET_MULT, 1.0)
                    + 20.0 * (1.0 - extended / n), 1)
    return {
        "count": n, "actionable": actionable,
        "actionable_pct": round(actionable / n * 100, 1),
        "avg_dist_from_entry_pct": round(statistics.mean(gaps), 1),
        "median_remaining_rr": round(statistics.median(rrs), 2) if rrs else None,
        "avg_remaining_rr": round(statistics.mean(rrs), 2) if rrs else None,
        "extended_gt10pct": extended, "extended_pct": round(extended / n * 100, 1),
        "quality_score": quality,
    }


# ── breach checks (per session, evaluated on the Anchor10 book) ───────────────

def breach_alerts(m_a: dict, m_b: dict) -> list[str]:
    alerts: list[str] = []
    if m_a.get("count"):
        drop = (m_a["count"] - m_b["count"]) / m_a["count"] * 100.0
        if drop > C1_MAX_COUNT_DROP_PCT:
            alerts.append(f"signal count dropped {drop:.0f}% (>{C1_MAX_COUNT_DROP_PCT:.0f}%): "
                          f"{m_a['count']}→{m_b['count']}")
    if m_b.get("actionable_pct", 0) < C2_MIN_ACTIONABLE_PCT:
        alerts.append(f"actionable {m_b['actionable_pct']}% < {C2_MIN_ACTIONABLE_PCT:.0f}%")
    if m_b.get("avg_dist_from_entry_pct", 0) > C3_MAX_AVG_DIST_PCT:
        alerts.append(f"avg distance {m_b['avg_dist_from_entry_pct']}% > {C3_MAX_AVG_DIST_PCT:.0f}%")
    return alerts


# ── session recording (Redis, append-only, deduped by date) ───────────────────

def build_session(setups: list[Setup], date: str, scan_id: str,
                  anchor_gap: float = ANCHOR_GAP_DEFAULT) -> dict:
    m_a = metrics(levels_for(setups, "current", anchor_gap))
    m_b = metrics(levels_for(setups, "anchor", anchor_gap))
    return {
        "date": date, "scan_id": scan_id, "anchor_gap_pct": anchor_gap,
        "A_current": m_a, "B_anchor": m_b,
        "count_drop_pct": (round((m_a["count"] - m_b["count"]) / m_a["count"] * 100, 1)
                           if m_a["count"] else 0.0),
        "breaches": breach_alerts(m_a, m_b),
    }


def load_sessions(redis_client) -> list[dict]:
    if redis_client is None:
        return []
    try:
        raw = redis_client.get(REDIS_SESSIONS_KEY)
        return json.loads(raw) if raw else []
    except Exception:
        return []


def record_session(setups: list[Setup], date: str, scan_id: str, redis_client,
                   anchor_gap: float = ANCHOR_GAP_DEFAULT) -> dict:
    """Compute and APPEND today's session (idempotent: replaces any row with the
    same date). Persists the full list back to Redis. Returns the session dict."""
    session = build_session(setups, date, scan_id, anchor_gap)
    sessions = [s for s in load_sessions(redis_client) if s.get("date") != date]
    sessions.append(session)
    sessions.sort(key=lambda s: s.get("date", ""))
    if redis_client is not None:
        try:
            redis_client.set(REDIS_SESSIONS_KEY, json.dumps(sessions))
        except Exception:
            pass
    return session


# ── C1–C5 enablement-criteria evaluation (for the status API) ─────────────────

def _session_passes(s: dict) -> dict:
    b = s.get("B_anchor", {})
    return {
        "C1_count_stable": s.get("count_drop_pct", 0) <= C1_MAX_COUNT_DROP_PCT,
        "C2_actionable": b.get("actionable_pct", 0) >= C2_MIN_ACTIONABLE_PCT,
        "C3_avg_distance": b.get("avg_dist_from_entry_pct", 1e9) <= C3_MAX_AVG_DIST_PCT,
        "C4_median_rr": (b.get("median_remaining_rr") or 0) >= C4_MIN_MEDIAN_REM_RR,
    }


def evaluate_criteria(sessions: list[dict], anchor_gap: float = ANCHOR_GAP_DEFAULT) -> dict:
    """C1–C5 PASS/FAIL across the recorded sessions, for the status endpoint."""
    n = len(sessions)
    per = [_session_passes(s) for s in sessions]
    c1 = all(p["C1_count_stable"] for p in per) if per else False
    c2 = all(p["C2_actionable"] for p in per) if per else False
    c3 = all(p["C3_avg_distance"] for p in per) if per else False
    c4 = all(p["C4_median_rr"] for p in per) if per else False
    c5 = (n >= SESSIONS_REQUIRED) and c1 and c2 and c3 and c4   # stability over the window
    ready = c5
    overall = "READY" if ready else ("COLLECTING" if n < SESSIONS_REQUIRED else "NOT_READY")
    latest = sessions[-1] if sessions else None
    latest_summary = None
    if latest:
        a, b = latest.get("A_current", {}), latest.get("B_anchor", {})
        latest_summary = {
            "date": latest.get("date"),
            "scan_id": latest.get("scan_id"),
            "current_count": a.get("count", 0),               # current engine signal count
            "anchor_count": b.get("count", 0),                # Anchor10 signal count
            "actionable_pct": b.get("actionable_pct", 0.0),   # daily actionable %
            "avg_distance_pct": b.get("avg_dist_from_entry_pct", 0.0),  # daily avg distance
            "median_remaining_rr": b.get("median_remaining_rr"),
            "count_drop_pct": latest.get("count_drop_pct", 0.0),
            "breaches": latest.get("breaches", []),
        }
    return {
        "anchor_gap_pct": anchor_gap,
        "session_count": n,
        "sessions_required": SESSIONS_REQUIRED,
        "criteria": {
            "C1_count_stable": {"pass": c1, "rule": f"count drop <= {C1_MAX_COUNT_DROP_PCT:.0f}% every session"},
            "C2_actionable": {"pass": c2, "rule": f"Anchor10 actionable% >= {C2_MIN_ACTIONABLE_PCT:.0f}% every session"},
            "C3_avg_distance": {"pass": c3, "rule": f"Anchor10 avg distance <= {C3_MAX_AVG_DIST_PCT:.0f}% every session"},
            "C4_median_rr": {"pass": c4, "rule": f"Anchor10 median remaining RR >= {C4_MIN_MEDIAN_REM_RR} every session"},
            "C5_stable_window": {"pass": c5, "rule": f">= {SESSIONS_REQUIRED} sessions with C1–C4 all holding"},
        },
        "overall": overall,
        "latest": latest_summary,
        "recommendation": (
            "Criteria met — prepare ENTRY_ANCHOR_MAX_GAP_PCT=10 production enablement (monitor 2 live sessions, keep rollback ready)."
            if ready else
            f"Keep collecting — {n}/{SESSIONS_REQUIRED} sessions; STRUCTURAL_TARGET_CAP stays 0."
        ),
        "sessions": sessions,
    }
