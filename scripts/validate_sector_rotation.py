"""
scripts/validate_sector_rotation.py — independent recomputation of sector rotation rows.

Recomputes the headline metrics for chosen sectors and dates straight from raw
Kite bars with plain Python loops — no pandas, nothing shared with
services/sector_rotation_engine.py except the published constants — and compares
them with what is stored in `sector_rotation_daily`.

    python -m scripts.validate_sector_rotation --sectors IT Pharma Cement --dates 2026-09-25 2026-06-15

Read-only. Exit code 1 when any metric disagrees beyond tolerance.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.sector_rotation_engine import (  # noqa: E402  (constants only)
    BENCHMARK,
    BREAK_MOVE,
    CORE_SOURCES,
    DAILY,
    FFILL_LIMIT,
    MIN_CONSTITUENTS,
    MIN_TURNOVER,
)

TOL = 1e-4


def _series(bars, calendar):
    """{calendar index: close/volume} for fresh bars after the last unadjusted break."""
    pos = {d: i for i, d in enumerate(calendar)}
    close, vol = {}, {}
    for b in bars:
        d = str(b["date"])[:10]
        c = b.get("close")
        if d in pos and c is not None and c > 0:
            close[pos[d]] = float(c)
            vol[pos[d]] = float(b.get("volume") or 0)
    last_break, prev = None, None
    for i in range(len(calendar)):
        if i in close:
            if prev is not None and abs(close[i] / prev - 1) > BREAK_MOVE:
                last_break = i
            prev = close[i]
    if last_break is not None:
        close = {i: v for i, v in close.items() if i >= last_break}
        vol = {i: v for i, v in vol.items() if i >= last_break}
    return close, vol


def _cf(close, i):
    """Carried-forward close at calendar index i (limit FFILL_LIMIT sessions)."""
    for k in range(i, i - FFILL_LIMIT - 1, -1):
        if k in close:
            return close[k]
        if k < 0:
            break
    return None


def _eligible(close, vol, i):
    if i not in close:
        return False
    tos = [close[k] * vol[k] for k in range(i - 19, i + 1) if k in close]
    return len(tos) >= 15 and sum(tos) / len(tos) >= MIN_TURNOVER


def _ret(close, i):
    if i not in close:
        return None
    p = _cf(close, i - 1)
    return None if p is None else close[i] / p - 1


def _sma_window(close, i, n):
    vals = [_cf(close, k) for k in range(i - n + 1, i + 1)]
    return None if any(v is None for v in vals) or i - n + 1 < 0 else vals


def recompute(stock_bars, index_bars, members, sector, calendar):
    """Per-date dict of recomputed metrics for one sector."""
    series = {s: _series(stock_bars[s], calendar) for s in members if members[s][0] == sector and s in stock_bars}
    bench = {}
    for b in index_bars[BENCHMARK]:
        bench[str(b["date"])[:10]] = float(b["close"])
    out, level, levels = {}, None, []
    for i, d in enumerate(calendar):
        elig = [s for s, (c, v) in series.items() if _eligible(c, v, i)]
        rets = [r for r in (_ret(series[s][0], i) for s in elig) if r is not None]
        ew = sum(rets) / len(rets) if len(rets) >= MIN_CONSTITUENTS else None
        if level is None and ew is not None:
            level = 100.0 * (1 + ew)
        elif level is not None:
            level = level * (1 + (ew or 0.0))
        levels.append(level)
        out[d] = {"n": len(elig), "ew": ew, "level": level, "elig": elig}
    # RS: SMA then EMA(adjust=False) then momentum, by hand.
    rs = [None if (lv is None) else lv / bench[d] * 100 for lv, d in zip(levels, calendar, strict=True)]
    ratio_raw = []
    for i in range(len(rs)):
        w = rs[i - DAILY.ratio_len + 1: i + 1] if i >= DAILY.ratio_len - 1 else []
        ratio_raw.append(100 * rs[i] / (sum(w) / len(w)) if w and None not in w else None)
    alpha, ema, seen, ratio = 2 / (DAILY.smooth + 1), None, 0, []
    for v in ratio_raw:
        if v is not None:
            ema = v if ema is None else alpha * v + (1 - alpha) * ema
            seen += 1
        ratio.append(ema if seen >= DAILY.smooth and v is not None else None)
    for i, d in enumerate(calendar):
        o = out[d]
        o["rs_ratio"] = ratio[i]
        j = i - DAILY.mom_len
        o["rs_momentum"] = (100 * ratio[i] / ratio[j]) if j >= 0 and ratio[i] and ratio[j] else None
        k = i - DAILY.lookback
        o["ret_lookback"] = ((levels[i] / levels[k] - 1) * 100) if k >= 0 and levels[i] and levels[k] else None
    return series, out


def point_metrics(series, members, elig, i):
    above, valid = {20: 0, 50: 0, 200: 0}, {20: 0, 50: 0, 200: 0}
    nh = nl = hl = 0
    vrs, up_to, all_to, lbs = [], 0.0, 0.0, []
    for s in elig:
        close, vol = series[s]
        c = close[i]
        for n in (20, 50, 200):
            w = _sma_window(close, i, n)
            if w:
                valid[n] += 1
                above[n] += c > sum(w) / n
        w252 = [_cf(close, k) for k in range(max(0, i - 251), i + 1)]
        w252 = [x for x in w252 if x is not None]
        if len(w252) >= 240 and i >= 251:
            hl += 1
            nh += c >= max(w252)
            nl += c <= min(w252)
        prior = [vol[k] for k in range(i - 50, i) if k in vol]
        if len(prior) >= 40 and statistics.median(prior) > 0:
            vrs.append(vol[i] / statistics.median(prior))
        r = _ret(close, i)
        if r is not None:
            to = c * vol[i]
            all_to += to
            up_to += to if r > 0 else 0
        p = _cf(close, i - DAILY.lookback)
        if p:
            lbs.append((c / p - 1) * 100)
    pct = {n: (above[n] / valid[n] * 100 if valid[n] else None) for n in (20, 50, 200)}
    return {
        "pct_above_20": pct[20], "pct_above_50": pct[50], "pct_above_200": pct[200],
        "new_highs_52w": nh, "new_lows_52w": nl, "n_52w_eligible": hl,
        "vol_ratio": statistics.median(vrs) if vrs else None,
        "up_turnover_pct": up_to / all_to * 100 if all_to else None,
        "dispersion": statistics.pstdev(lbs) if len(lbs) >= 5 else None,
        "n_core": sum(1 for s in elig if members[s][1] in CORE_SOURCES),
    }


def validate(stock_bars, index_bars, members, stored_rows, sectors, dates) -> dict:
    calendar = sorted(str(b["date"])[:10] for b in index_bars[BENCHMARK])
    stored = {(r["entity"], r["date"]): r for r in stored_rows if r["timeframe"] == "D"}
    report, failures = [], 0
    for sector in sectors:
        series, daily = recompute(stock_bars, index_bars, members, sector, calendar)
        for d in dates:
            i = calendar.index(d)
            mine = dict(daily[d])
            mine.update(point_metrics(series, members, mine["elig"], i))
            mine["n_constituents"] = mine["n"]
            mine["ret_bar"] = None if mine["ew"] is None else mine["ew"] * 100
            row = stored.get((sector, d)) or {}
            checks = {}
            for k in ("n_constituents", "n_core", "ret_bar", "ret_lookback", "rs_ratio", "rs_momentum",
                      "pct_above_20", "pct_above_50", "pct_above_200", "new_highs_52w", "new_lows_52w",
                      "n_52w_eligible", "vol_ratio", "up_turnover_pct", "dispersion"):
                a, b = mine.get(k), row.get(k)
                ok = (a is None and b is None) or (
                    a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=TOL, abs_tol=TOL))
                failures += not ok
                checks[k] = {"raw": None if a is None else round(float(a), 4),
                             "stored": None if b is None else round(float(b), 4), "ok": ok}
            report.append({"sector": sector, "date": d, "checks": checks})
    return {"failures": failures, "report": report}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", nargs="+", default=["IT", "Pharma", "Cement"])
    ap.add_argument("--dates", nargs="+", required=True)
    args = ap.parse_args()

    from dashboard.backend.db.sector_rotation import read_rows
    from scripts.build_sector_rotation import load_members
    from services.index_ohlc import load_index_ohlc
    from services.universe_ohlc import load_universe_ohlc

    stock_bars, index_bars = load_universe_ohlc(), load_index_ohlc()
    members = {s: v for s, v in load_members().items() if s in stock_bars}
    rows = [r for d in args.dates for s in args.sectors for r in read_rows(timeframe="D", date=d, entity=s)]
    res = validate(stock_bars, index_bars, members, rows, args.sectors, args.dates)
    print(json.dumps(res, indent=1))
    return 1 if res["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
