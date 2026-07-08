"""
dashboard/backend/services/risk_audit.py
=========================================
Read-only aggregation for the Risk Engine Dashboard. Consumes the audit logs the
engine already writes (Redis lists `risk_engine:promotions:{date}` /
`risk_engine:exits:{date}`) plus the current portfolio book (read-only) to
produce a daily summary. Introduces NO trading logic — it only reads and counts.
"""

from __future__ import annotations

import json
import logging
from datetime import date as _date
from statistics import mean

log = logging.getLogger("dashboard.risk_audit")


def read_decisions(kind: str, day: str) -> list[dict]:
    """Load the day's decision log (kind = 'promotions' | 'exits'). Best-effort."""
    try:
        from dashboard.backend.cache import _get_redis
        r = _get_redis()
        if r is None:
            return []
        raw = r.lrange(f"risk_engine:{kind}:{day}", 0, -1) or []
        out = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except Exception:
                continue
        return out
    except Exception as exc:
        log.debug("read_decisions %s/%s failed: %s", kind, day, exc)
        return []


def _hist(values: list[float], edges: list[float]) -> list[dict]:
    """Bucket values into [edges[i], edges[i+1]) ranges; last bucket is open-ended."""
    buckets = []
    for i in range(len(edges)):
        lo = edges[i]
        hi = edges[i + 1] if i + 1 < len(edges) else None
        n = sum(1 for v in values if v >= lo and (hi is None or v < hi))
        label = f"{lo:g}–{hi:g}" if hi is not None else f"{lo:g}+"
        buckets.append({"range": label, "count": n})
    return buckets


def _portfolio_snapshot() -> dict:
    """Current live book: portfolio heat, avg stop width, size distribution,
    sector exposure. Read-only (uses stored position_size + stop, falls back to
    equal-weight for legacy rows)."""
    try:
        from dashboard.backend.db.portfolio import get_portfolio
        from services.risk_engine import cfg
        try:
            from engine.swing import get_sector
        except Exception:
            def get_sector(_s):  # type: ignore
                return "Others"
    except Exception:
        return {}

    c = cfg()
    capital = c["PORTFOLIO_NOTIONAL_CAPITAL"] or 1.0
    max_pos = c["MAX_PORTFOLIO_POSITIONS"] or 20
    equal_notional = capital / max_pos

    active = [p for p in get_portfolio(include_closed=False) if p.get("status") == "ACTIVE"]
    stop_widths: list[float] = []
    weights: list[float] = []          # position notional as % of capital
    heat = 0.0                          # total capital-at-risk if every stop hit (% of capital)
    sector_risk: dict[str, float] = {}
    sized_count = 0

    for p in active:
        e = float(p.get("entry_price") or 0); sl = float(p.get("stop_loss") or 0)
        if e <= 0 or sl <= 0 or sl >= e:
            continue
        sw = (e - sl) / e * 100.0
        stop_widths.append(sw)
        notional = float(p.get("position_size") or 0) or equal_notional
        if p.get("position_size"):
            sized_count += 1
        wt_pct = notional / capital * 100.0
        weights.append(wt_pct)
        risk_pct = wt_pct * sw / 100.0          # notional% × stopwidth% = capital-at-risk%
        heat += risk_pct
        sec = get_sector(p["symbol"]) or "Others"
        sector_risk[sec] = sector_risk.get(sec, 0.0) + risk_pct

    return {
        "active_positions": len(active),
        "engine_sized": sized_count,
        "legacy_sized": len(active) - sized_count,
        "portfolio_heat_pct": round(heat, 2),   # total capital at risk if all stops hit
        "avg_stop_width_pct": round(mean(stop_widths), 2) if stop_widths else None,
        "stop_width_distribution": _hist(stop_widths, [0, 4, 6, 8, 10, 15]),
        "position_weight_distribution": _hist(weights, [0, 2, 4, 6, 8, 10]),
        "sector_exposure": [
            {"sector": s, "risk_pct": round(v, 2)}
            for s, v in sorted(sector_risk.items(), key=lambda kv: -kv[1])
        ],
    }


def daily_summary(day: str | None = None) -> dict:
    """Full read-only summary for one day."""
    day = day or _date.today().isoformat()
    promos = read_decisions("promotions", day)
    exits = read_decisions("exits", day)

    accepted = [p for p in promos if p.get("accepted")]
    rejected = [p for p in promos if not p.get("accepted")]
    stop_cap_rejects = [p for p in rejected if "stop_too_wide" in str(p.get("reason") or "")]
    invalid_rejects = [p for p in rejected if "invalid" in str(p.get("reason") or "")]
    sizing_adjusted = [p for p in accepted
                       if p.get("new_position_value") is not None
                       and p.get("old_position_value") is not None
                       and abs(p["new_position_value"] - p["old_position_value"]) > 1e-6]
    liquidity_adjusted = [p for p in accepted
                          if (p.get("liquidity_factor") or 1) < 1 or (p.get("atr_factor") or 1) < 1]

    acc_stop_widths = [p["stop_width_pct"] for p in accepted if p.get("stop_width_pct") is not None]
    acc_weights = [p["risk_weight_pct"] for p in accepted if p.get("risk_weight_pct") is not None]

    # Counterfactual: what the LEGACY engine would have done vs the new engine.
    legacy_accepts = [p for p in promos if p.get("old_accepted")]
    rejected_but_legacy_took = [p for p in promos if p.get("old_accepted") and not p.get("accepted")]
    notional_legacy = sum(float(p.get("old_position_value") or 0) for p in legacy_accepts)
    notional_new = sum(float(p.get("new_position_value") or 0) for p in accepted)

    # Config flags in force (from the most recent promotion decision, else live cfg).
    flags = {}
    if promos and isinstance(promos[-1].get("flags"), dict):
        flags = promos[-1]["flags"]
    else:
        try:
            from services.risk_engine import cfg
            flags = {k: cfg()[k] for k in (
                "RISK_ENGINE_ENABLED", "RISK_SIZING_ENABLED", "STOP_CAP_ENABLED",
                "LIQUIDITY_ADJ_ENABLED")}
        except Exception:
            flags = {}

    return {
        "date": day,
        "promotions": {
            "total": len(promos),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "stop_cap_rejections": len(stop_cap_rejects),
            "invalid_rejections": len(invalid_rejects),
            "sizing_adjustments": len(sizing_adjusted),
            "liquidity_adjustments": len(liquidity_adjusted),
            "avg_stop_width_pct": round(mean(acc_stop_widths), 2) if acc_stop_widths else None,
            "accepted_weight_distribution": _hist(acc_weights, [0, 2, 4, 6, 8, 10]),
            "rejections": [
                {"symbol": p.get("symbol"), "horizon": p.get("horizon"),
                 "stop_width_pct": p.get("stop_width_pct"), "reason": p.get("reason")}
                for p in rejected
            ][:50],
        },
        "exits": {
            "trend_break": len(exits),
            "detail": [
                {"symbol": e.get("symbol"), "cmp": e.get("cmp"), "dma200": e.get("dma200"),
                 "rs_vs_nifty": e.get("rs_vs_nifty"), "days_held": e.get("days_held")}
                for e in exits
            ][:50],
        },
        "counterfactual": {
            "legacy_would_accept": len(legacy_accepts),
            "new_accepted": len(accepted),
            "rejected_by_new_that_legacy_took": len(rejected_but_legacy_took),
            "notional_legacy_equal_weight": round(notional_legacy, 0),
            "notional_new_risk_weighted": round(notional_new, 0),
        },
        "flags": flags,
        "portfolio": _portfolio_snapshot(),
    }
