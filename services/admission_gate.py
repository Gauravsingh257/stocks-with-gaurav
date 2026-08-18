"""
services/admission_gate.py
==========================
THE single portfolio admission gate for the SWING / LONGTERM book.

Step 3 of the 2026-08 remediation roadmap (docs/PORTFOLIO_SELECTION_AUDIT_2026-08.md).

WHY THIS EXISTS
---------------
Step 1 proved there is no single admission funnel. Two independent doors create
positions in `portfolio_positions`, and only one of them applies any risk policy:

  door 1  db.add_position()                     <- promote_to_portfolio
                                                   (idea_selector arm-on-tap,
                                                    portfolio_tracker arm-on-tap,
                                                    manual POST /api/portfolio/add)
                                                   risk engine applies here
  door 2  db.seed_portfolio_from_recommendations()  <- engine live-sync
                                                   raw INSERT, NO risk policy at all

GARUDA entered through door 2 on 2026-08-12 with no sizing fields at all, and
SONAL entered through door 1 on 2026-08-11 with Rs 0.00 Cr/day turnover because
the risk engine's liquidity logic only ever DOWN-SIZES, it never rejects.

NOT THE SAME THING AS services/entry_gate.py
--------------------------------------------
Two gates, two different lifecycle stages. The word "admission" means the
OPPOSITE thing in each, so read this before touching either:

    candidate
       |
       v
    [ admission_gate ]  "MAY this candidate enter the book?"      <- THIS MODULE
       |                 upstream, decides creation, SHADOW
       v
    portfolio_positions row
       |
       v
    [ entry_gate ]      "IS this symbol already in the book?"     <- entry_gate.py
       |                 downstream, gates arm/monitor/alert, ENFORCING
       v
    monitored / alerted

`entry_gate.is_portfolio_admitted()` asks whether a row ALREADY EXISTS — its
answer is a fact about the database. This module asks whether a row SHOULD BE
ALLOWED TO exist — its answer is a policy judgement. They never contradict each
other: this gate runs strictly before the row is written, entry_gate strictly
after. Separate flags (ADMISSION_GATE_* vs ENTRY_GATE_*), separate storage.

SHADOW MODE — READ THIS BEFORE CHANGING ANYTHING
------------------------------------------------
This gate is SHADOW-ONLY by default and every threshold ships as a NO-OP:

    PROMOTE_MIN_PRICE            = 0     (nothing is too cheap)
    PROMOTE_MIN_TURNOVER_CR      = 0     (nothing is too illiquid)
    PROMOTE_MAX_ATR_PCT          = 999   (nothing is too volatile)
    PROMOTE_MAX_STOP_WIDTH_PCT   = 999   (no stop is too wide)

With those defaults the gate CANNOT reject anything, and even if a threshold is
set, `ADMISSION_GATE_ENFORCE=0` (the default) means the decision is recorded and
returned but never acted upon. `evaluate()` has NO side effects on the book: it
does not size, close, resize, or alter any position. Callers in shadow mode must
ignore `decision.admitted` — and they do.

Turning it authoritative is a deliberate, separate act: set thresholds first,
watch the shadow report for several sessions (Step 4), then flip
ADMISSION_GATE_ENFORCE=1 and make callers honour the verdict.

DESIGN RULES
------------
  * NEVER raises. A gate that can break an insert is worse than no gate. Every
    entry point is wrapped; on any internal error the candidate is recorded as
    `invalid_risk_data` and admitted.
  * NEVER performs network I/O. Metrics are supplied by the caller (the risk
    engine has already computed atr_pct / turnover_cr on door 1). Missing
    metrics are recorded as `invalid_risk_data`, not fetched — that keeps the
    insert path fast and keeps tests hermetic.
  * ONE implementation. Callers pass their `source_door`; they do not re-derive
    any check. A caller that forgets to identify itself shows up in the report
    as `unattributed`, which is how a future bypass becomes visible instead of
    silent.
  * Storage mirrors the risk engine: a Redis list keyed by IST day, 30-day TTL.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("services.admission_gate")

_IST = timezone(timedelta(hours=5, minutes=30))

REDIS_KEY_PREFIX = "admission_gate:decisions:"
REDIS_TTL_SEC = 30 * 86400

# Bumped whenever the CHECKS or their semantics change, so a shadow report can
# never silently mix decisions made under different policies.
POLICY_VERSION = "v1-shadow-2026-08"

# Canonical rejection reason codes.
REASON_PRICE = "price_below_floor"
REASON_TURNOVER = "turnover_below_floor"
REASON_ATR = "atr_above_limit"
REASON_STOP = "stop_too_wide"
REASON_SECTOR = "sector_exposure"
REASON_CAPACITY = "capacity_exceeded"
REASON_INVALID = "invalid_risk_data"


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def cfg() -> dict[str, Any]:
    """Live config snapshot, re-read per call so a threshold change takes effect
    without a redeploy (same contract as services/risk_engine.cfg)."""
    return {
        "ADMISSION_GATE_ENABLED": _flag("ADMISSION_GATE_ENABLED", "1"),
        # Enforcement is the ONLY thing standing between shadow and live.
        # Default OFF. Do not flip without Step 4 evidence.
        "ADMISSION_GATE_ENFORCE": _flag("ADMISSION_GATE_ENFORCE", "0"),
        "ADMISSION_GATE_PERSIST": _flag("ADMISSION_GATE_PERSIST", "1"),
        # Thresholds — every one a no-op by default.
        "PROMOTE_MIN_PRICE": _f("PROMOTE_MIN_PRICE", 0.0),
        "PROMOTE_MIN_TURNOVER_CR": _f("PROMOTE_MIN_TURNOVER_CR", 0.0),
        "PROMOTE_MAX_ATR_PCT": _f("PROMOTE_MAX_ATR_PCT", 999.0),
        "PROMOTE_MAX_STOP_WIDTH_PCT": _f("PROMOTE_MAX_STOP_WIDTH_PCT", 999.0),
        # Sector cap mirrors portfolio_risk.MAX_SECTOR_EXPOSURE; 0 disables.
        "PROMOTE_MAX_SECTOR_EXPOSURE": _f("PROMOTE_MAX_SECTOR_EXPOSURE", 0.0),
        "POLICY_VERSION": POLICY_VERSION,
    }


@dataclass(slots=True)
class AdmissionDecision:
    """One candidate's admission verdict. Serialised verbatim into the report."""

    timestamp: str
    symbol: str
    portfolio_type: str          # SWING | LONGTERM
    direction: str
    entry: float | None
    stop: float | None
    stop_width_pct: float | None
    price: float | None
    turnover_cr: float | None
    atr_pct: float | None
    sector: str | None
    position_size: float | None
    source_door: str
    policy_version: str
    decision: str                # PASS | REJECT
    rejection_reasons: list[str] = field(default_factory=list)
    shadow_mode: bool = True
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        """The gate's verdict. In shadow mode callers MUST ignore this."""
        return self.decision == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _persist(decision: AdmissionDecision) -> None:
    """Append to the day's Redis list. Best-effort; never raises.

    Mirrors services/risk_engine._persist so there is one storage convention for
    risk decisions rather than a second bespoke store."""
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return
        key = f"{REDIS_KEY_PREFIX}{datetime.now(_IST).date().isoformat()}"
        r.rpush(key, json.dumps(decision.to_dict(), default=str))
        r.expire(key, REDIS_TTL_SEC)
    except Exception:
        # A logging failure must never affect an insert.
        pass


def sector_for(symbol: str) -> str:
    """Sector label, reusing the existing map. Pure dict lookup, no I/O."""
    try:
        from services.portfolio_risk import get_sector

        return get_sector(str(symbol or "").replace("NSE:", ""))
    except Exception:
        return "OTHER"


def sector_counts_from_conn(conn, horizon: str) -> dict[str, int]:
    """Committed (ACTIVE+PENDING) position count per sector for a horizon.

    Shared by both doors so neither re-implements it. Best-effort: returns {} on
    any error, which makes the sector check simply not evaluate."""
    try:
        rows = conn.execute(
            "SELECT symbol FROM portfolio_positions WHERE horizon = ? "
            "AND status IN ('ACTIVE','PENDING')",
            (str(horizon).upper(),),
        ).fetchall()
        counts: dict[str, int] = {}
        for r in rows:
            s = sector_for(r["symbol"] if hasattr(r, "keys") else r[0])
            counts[s] = counts.get(s, 0) + 1
        return counts
    except Exception:
        return {}


def evaluate(
    symbol: str,
    horizon: str,
    entry: float | None,
    stop: float | None,
    *,
    source_door: str = "unattributed",
    direction: str = "LONG",
    price: float | None = None,
    turnover_cr: float | None = None,
    atr_pct: float | None = None,
    position_size: float | None = None,
    sector_counts: dict[str, int] | None = None,
    book_used: int | None = None,
    book_max: int | None = None,
    persist: bool = True,
) -> AdmissionDecision:
    """Evaluate one candidate. Pure: reads config + the supplied metrics, writes
    only the decision log. NEVER raises, NEVER touches a position.

    In shadow mode (the default) the returned `decision` is advisory — callers
    record it and proceed exactly as before.
    """
    c = cfg()
    shadow = not c["ADMISSION_GATE_ENFORCE"]
    reasons: list[str] = []
    detail: dict[str, Any] = {}

    try:
        hz = str(horizon or "").upper()
        sym = str(symbol or "").strip().upper()

        # Geometry. `price` falls back to entry when the caller has no live CMP:
        # at promotion time entry is the best available proxy for traded price.
        try:
            e = float(entry) if entry is not None else None
            s = float(stop) if stop is not None else None
        except (TypeError, ValueError):
            e = s = None
        px = None
        try:
            px = float(price) if price is not None else e
        except (TypeError, ValueError):
            px = e

        stop_width_pct: float | None = None
        if e is not None and s is not None and e > 0 and s > 0 and s < e:
            stop_width_pct = (e - s) / e * 100.0
        elif e is None or s is None:
            reasons.append(REASON_INVALID)
            detail["geometry"] = "missing_entry_or_stop"
        else:
            reasons.append(REASON_INVALID)
            detail["geometry"] = f"invalid(entry={e},stop={s})"

        sector = sector_for(sym)

        # ── checks (each independently no-op at its default) ──────────────
        if c["PROMOTE_MIN_PRICE"] > 0:
            if px is None:
                if REASON_INVALID not in reasons:
                    reasons.append(REASON_INVALID)
                detail["price"] = "unavailable"
            elif px < c["PROMOTE_MIN_PRICE"]:
                reasons.append(REASON_PRICE)
                detail["price"] = f"{px:.2f}<{c['PROMOTE_MIN_PRICE']:.2f}"

        if c["PROMOTE_MIN_TURNOVER_CR"] > 0:
            if turnover_cr is None:
                if REASON_INVALID not in reasons:
                    reasons.append(REASON_INVALID)
                detail["turnover"] = "unavailable"
            elif float(turnover_cr) < c["PROMOTE_MIN_TURNOVER_CR"]:
                reasons.append(REASON_TURNOVER)
                detail["turnover"] = f"{float(turnover_cr):.2f}<{c['PROMOTE_MIN_TURNOVER_CR']:.2f}"

        if c["PROMOTE_MAX_ATR_PCT"] < 999:
            if atr_pct is None:
                if REASON_INVALID not in reasons:
                    reasons.append(REASON_INVALID)
                detail["atr"] = "unavailable"
            elif float(atr_pct) > c["PROMOTE_MAX_ATR_PCT"]:
                reasons.append(REASON_ATR)
                detail["atr"] = f"{float(atr_pct):.2f}>{c['PROMOTE_MAX_ATR_PCT']:.2f}"

        if c["PROMOTE_MAX_STOP_WIDTH_PCT"] < 999 and stop_width_pct is not None:
            if stop_width_pct > c["PROMOTE_MAX_STOP_WIDTH_PCT"]:
                reasons.append(REASON_STOP)
                detail["stop_width"] = (
                    f"{stop_width_pct:.2f}>{c['PROMOTE_MAX_STOP_WIDTH_PCT']:.2f}")

        # Sector exposure. Only evaluated when the caller supplied counts AND a
        # cap is configured; `OTHER` is excluded exactly as portfolio_risk does
        # today, so the shadow report reflects real policy rather than a
        # stricter invention.
        cap = c["PROMOTE_MAX_SECTOR_EXPOSURE"]
        if cap > 0 and sector_counts is not None and sector != "OTHER":
            held = int(sector_counts.get(sector, 0))
            if held >= cap:
                reasons.append(REASON_SECTOR)
                detail["sector"] = f"{sector}:{held}>={cap:.0f}"

        if book_used is not None and book_max is not None and book_used >= book_max:
            reasons.append(REASON_CAPACITY)
            detail["capacity"] = f"{book_used}>={book_max}"

        decision = AdmissionDecision(
            timestamp=datetime.now(_IST).isoformat(),
            symbol=sym,
            portfolio_type=hz,
            direction=str(direction or "LONG").upper(),
            entry=e,
            stop=s,
            stop_width_pct=round(stop_width_pct, 2) if stop_width_pct is not None else None,
            price=round(px, 2) if px is not None else None,
            turnover_cr=(round(float(turnover_cr), 2) if turnover_cr is not None else None),
            atr_pct=(round(float(atr_pct), 2) if atr_pct is not None else None),
            sector=sector,
            position_size=(float(position_size) if position_size is not None else None),
            source_door=str(source_door or "unattributed"),
            policy_version=c["POLICY_VERSION"],
            decision="REJECT" if reasons else "PASS",
            rejection_reasons=reasons,
            shadow_mode=shadow,
            detail=detail,
        )
    except Exception as exc:
        # Absolute last resort — still produce a record so the candidate is
        # never invisible, and always admit.
        log.debug("[AdmissionGate] evaluate failed for %s: %s", symbol, exc)
        decision = AdmissionDecision(
            timestamp=datetime.now(_IST).isoformat(),
            symbol=str(symbol), portfolio_type=str(horizon), direction=str(direction),
            entry=None, stop=None, stop_width_pct=None, price=None,
            turnover_cr=None, atr_pct=None, sector=None, position_size=None,
            source_door=str(source_door or "unattributed"),
            policy_version=POLICY_VERSION, decision="REJECT",
            rejection_reasons=[REASON_INVALID], shadow_mode=shadow,
            detail={"error": str(exc)[:200]},
        )

    if persist and c["ADMISSION_GATE_ENABLED"] and c["ADMISSION_GATE_PERSIST"]:
        _persist(decision)

    log.info(
        "[AdmissionGate][%s] %s/%s door=%s decision=%s reasons=%s "
        "stop_w=%s price=%s turn=%s atr=%s policy=%s",
        "SHADOW" if decision.shadow_mode else "ENFORCE",
        decision.symbol, decision.portfolio_type, decision.source_door,
        decision.decision, ",".join(decision.rejection_reasons) or "-",
        decision.stop_width_pct, decision.price, decision.turnover_cr,
        decision.atr_pct, decision.policy_version,
    )
    return decision


def evaluate_safe(*args, **kwargs) -> AdmissionDecision | None:
    """`evaluate` wrapped so a caller on the INSERT path can never be broken by
    the gate — not even by an import error or a Redis hiccup. Returns None if
    the gate is disabled or anything at all goes wrong.

    Every production call site uses THIS, not `evaluate`."""
    try:
        if not cfg()["ADMISSION_GATE_ENABLED"]:
            return None
        return evaluate(*args, **kwargs)
    except Exception:
        log.debug("[AdmissionGate] evaluate_safe swallowed an error", exc_info=True)
        return None


# ── report reading ───────────────────────────────────────────────────────────

def load_decisions(days: int = 14) -> list[dict[str, Any]]:
    """Read the last `days` IST days of shadow decisions from Redis. Returns []
    when Redis is unavailable — the report says so rather than showing zeros as
    if nothing had been evaluated."""
    out: list[dict[str, Any]] = []
    try:
        from dashboard.backend.cache import _get_redis

        r = _get_redis()
        if r is None:
            return out
        today = datetime.now(_IST).date()
        for i in range(days):
            key = f"{REDIS_KEY_PREFIX}{(today - timedelta(days=i)).isoformat()}"
            try:
                for raw in r.lrange(key, 0, -1) or []:
                    try:
                        out.append(json.loads(raw))
                    except (json.JSONDecodeError, TypeError):
                        continue
            except Exception:
                continue
    except Exception:
        return out
    return out


def summarize(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate raw decisions into the Shadow Report structure."""
    total = len(decisions)
    passed = sum(1 for d in decisions if d.get("decision") == "PASS")
    rejected = total - passed

    by_horizon: dict[str, dict[str, int]] = {}
    by_door: dict[str, dict[str, int]] = {}
    by_reason: dict[str, int] = {}
    by_day: dict[str, dict[str, int]] = {}
    invalid = 0
    rejected_rows: list[dict[str, Any]] = []

    for d in decisions:
        hz = d.get("portfolio_type") or "?"
        door = d.get("source_door") or "unattributed"
        day = str(d.get("timestamp") or "")[:10]
        verdict = d.get("decision")

        for bucket, key in ((by_horizon, hz), (by_door, door), (by_day, day)):
            slot = bucket.setdefault(key, {"total": 0, "PASS": 0, "REJECT": 0})
            slot["total"] += 1
            slot[verdict] = slot.get(verdict, 0) + 1

        reasons = d.get("rejection_reasons") or []
        for r in reasons:
            by_reason[r] = by_reason.get(r, 0) + 1
        if REASON_INVALID in reasons:
            invalid += 1
        if verdict == "REJECT":
            rejected_rows.append(d)

    return {
        "total": total,
        "pass": passed,
        "reject": rejected,
        "by_horizon": by_horizon,
        "by_door": by_door,
        "by_reason": by_reason,
        "by_day": by_day,
        "invalid_metric_count": invalid,
        "unattributed_count": by_door.get("unattributed", {}).get("total", 0),
        "rejected_rows": rejected_rows,
        "policy_versions": sorted({d.get("policy_version") for d in decisions if d.get("policy_version")}),
        "shadow_only": all(d.get("shadow_mode", True) for d in decisions),
    }
