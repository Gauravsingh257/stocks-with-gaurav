"""
services/sector_rotation_view.py — shape stored sector-rotation rows for the API (Phase 2).

PRESENTATION ONLY. Every number comes from `sector_rotation_daily` as Phase 1
wrote it; this module selects the columns for the chosen benchmark, orders the
trail and rates data freshness. It never touches price data and never
recomputes a Phase 1 metric.

The one derived value is acceleration against the whole market, which Phase 1
stores only against NIFTY 50. It is the difference of two stored momentum
values, using Phase 1's own definition and lag:
    rs_accel_mkt[t] = rs_momentum_mkt[t] - rs_momentum_mkt[t - accel_len]
where t - accel_len counts stored rows of that timeframe.

Language rule: analytics only. Quadrant names describe measured relative
strength; nothing here recommends an action.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from services.sector_rotation_engine import DAILY, MARKET_ENTITY, WEEKLY

IST = timezone(timedelta(hours=5, minutes=30))
# The scanner republishes after the close (~15:45 + ~13 min crawl) and the
# history job runs at 16:40 IST, so a session's rows exist from ~16:45.
EXPECTED_READY_IST = (16, 45)

BENCHMARKS: dict[str, dict] = {
    "market": {
        "key": "market",
        "label": "Whole market",
        "short": "vs whole market",
        "description": "Equal-weight average of every liquid NSE equity in the universe. "
                       "Isolates how the sector moves relative to the typical stock.",
    },
    "nifty50": {
        "key": "nifty50",
        "label": "NIFTY 50",
        "short": "vs NIFTY 50",
        "description": "The cap-weighted NIFTY 50 index. Sectors are equal-weight, so this "
                       "comparison also carries the small-vs-large-cap effect.",
    },
}
DEFAULT_BENCHMARK = "market"

DEFINITIONS = {
    "rs_ratio": "Relative strength: the sector's trend against the benchmark, normalised so 100 is in line.",
    "rs_momentum": "Momentum of relative strength: whether that ratio is rising (>100) or falling (<100).",
    "rs_accel": "Acceleration: change in momentum over the last few bars.",
    "pct_above": "Breadth: share of the sector's liquid stocks trading above their 20/50/200-day average.",
    "new_highs_52w": "Stocks closing at a 52-week high / low.",
    "vol_ratio": "Volume participation: typical stock's volume vs its own 50-day median (1.0 = normal).",
    "up_turnover_pct": "Share of the sector's traded value in stocks that rose.",
    "dispersion": "Spread of the sector's stock returns over the lookback — high means the sector is not moving together.",
    "confidence": "How much to trust the reading: size of the sector and how many sector labels are provider-sourced.",
    "provider_share": "Share of the sector's stocks whose sector label comes from the provider's industry field "
                      "(about 71% agreement with NSE's official classification) rather than NSE or a manual assignment.",
}

QUADRANTS = ("leading", "weakening", "lagging", "improving")

_METRICS = (
    "n_constituents", "n_core", "provider_share", "confidence", "ret_bar", "ret_lookback",
    "pct_above_20", "pct_above_50", "pct_above_200", "new_highs_52w", "new_lows_52w",
    "n_52w_eligible", "vol_ratio", "up_turnover_pct", "dispersion",
)


def _params(tf: str):
    return DAILY if tf == "D" else WEEKLY


def _coords(row: dict, bench: str) -> tuple[float | None, float | None, str | None]:
    if bench == "nifty50":
        return row.get("rs_ratio"), row.get("rs_momentum"), row.get("quadrant")
    return row.get("rs_ratio_mkt"), row.get("rs_momentum_mkt"), row.get("quadrant_mkt")


def expected_session(now: datetime) -> date:
    """Latest session whose rows should exist at `now` (weekends skipped; holidays
    are not known here, so a holiday reads as one session behind)."""
    now = now.astimezone(IST)
    d = now.date()
    if now.weekday() >= 5 or (now.hour, now.minute) < EXPECTED_READY_IST:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def freshness(as_of: str | None, now: datetime) -> dict:
    if not as_of:
        return {"status": "unknown", "as_of": None, "sessions_behind": None,
                "note": "No sector rotation data has been computed yet."}
    have = date.fromisoformat(as_of)
    want = expected_session(now)
    behind, d = 0, have
    while d < want:
        d += timedelta(days=1)
        if d.weekday() < 5:
            behind += 1
    if behind == 0:
        return {"status": "fresh", "as_of": as_of, "expected": want.isoformat(), "sessions_behind": 0,
                "note": f"Includes the {have.strftime('%d %b %Y')} close."}
    return {"status": "stale", "as_of": as_of, "expected": want.isoformat(), "sessions_behind": behind,
            "note": f"Latest data is from the {have.strftime('%d %b %Y')} close, {behind} session"
                    f"{'s' if behind > 1 else ''} behind (a market holiday also shows as a gap)."}


def build_rotation_payload(rows: list[dict], *, tf: str, bench: str, trail: int, now: datetime) -> dict:
    """`rows`: stored rows of one timeframe (sector + market kinds) for the most
    recent `trail + accel_len` dates. Returns the API payload."""
    bench = bench if bench in BENCHMARKS else DEFAULT_BENCHMARK
    accel_len = _params(tf).accel_len
    dates = sorted({r["date"] for r in rows})
    trail_dates = dates[-trail:]
    by_entity: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_entity.setdefault(r["entity"], {})[r["date"]] = r
    latest = dates[-1] if dates else None

    def _entity_view(name: str, hist: dict[str, dict]) -> dict | None:
        cur = hist.get(latest) if latest else None
        if cur is None:
            return None
        x, y, quad = _coords(cur, bench)
        if bench == "nifty50":
            accel = cur.get("rs_accel")
        else:
            i = dates.index(latest)
            prev = hist.get(dates[i - accel_len]) if i - accel_len >= 0 else None
            py = _coords(prev, bench)[1] if prev else None
            accel = round(y - py, 6) if (y is not None and py is not None) else None
        out = {"sector": name, "rs_ratio": x, "rs_momentum": y, "rs_accel": accel, "quadrant": quad,
               "rs_ratio_core": cur.get("rs_ratio_core") if bench == "nifty50" else None,
               "as_of": cur.get("as_of"), "partial": bool(cur.get("partial")),
               "written_at": cur.get("written_at"), "source_mode": cur.get("source_mode")}
        for m in _METRICS:
            out[m] = cur.get(m)
        out["trail"] = []
        for d in trail_dates:
            h = hist.get(d)
            if h is None:
                continue
            tx, ty, _ = _coords(h, bench)
            if tx is not None and ty is not None:
                out["trail"].append({"date": d, "rs_ratio": tx, "rs_momentum": ty})
        out["plottable"] = (x is not None and y is not None and cur.get("confidence") != "insufficient")
        return out

    market = _entity_view(MARKET_ENTITY, by_entity.get(MARKET_ENTITY, {}))
    sectors = []
    for name in sorted(by_entity):
        if name == MARKET_ENTITY:
            continue
        v = _entity_view(name, by_entity[name])
        if v is not None:
            if market and market.get("ret_lookback") is not None and v.get("ret_lookback") is not None:
                v["ret_vs_market"] = round(v["ret_lookback"] - market["ret_lookback"], 6)
            else:
                v["ret_vs_market"] = None
            sectors.append(v)

    counts = {q: sum(1 for s in sectors if s["plottable"] and s["quadrant"] == q) for q in QUADRANTS}
    as_of = (market or {}).get("as_of") or latest
    written = max((s.get("written_at") or "" for s in sectors), default="") or None
    p = _params(tf)
    return {
        "enabled": True,
        "timeframe": tf,
        "timeframe_label": "Daily" if tf == "D" else "Weekly",
        "lookback_label": f"{p.lookback} sessions" if tf == "D" else f"{p.lookback} weeks",
        "benchmark": BENCHMARKS[bench],
        "benchmarks": [{"key": k, "label": v["label"]} for k, v in BENCHMARKS.items()],
        "as_of": as_of,
        "week_partial": bool(market and market.get("partial")),
        "written_at": written,
        "freshness": freshness(as_of, now),
        "trail_length": len(trail_dates),
        "trail_dates": trail_dates,
        "quadrant_counts": counts,
        "market": market,
        "sectors": sectors,
        "definitions": DEFINITIONS,
        "calc_version": (rows[0].get("calc_version") if rows else None),
        "disclaimer": "Analytics only. Measured relative strength and breadth of NSE sectors; "
                      "not a recommendation to buy, sell or hold any security.",
    }
