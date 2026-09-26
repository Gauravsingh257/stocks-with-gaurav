"""
services/sector_rotation_engine.py — read-only sector rotation calculations (Phase 1).

PURE FUNCTIONS, NO I/O. Input is the Kite daily bars the scanner already
publishes (`ohlc:universe:*`, `ohlc:index:*`) plus sector membership from
`stock_universe`; output is plain row dicts for `sector_rotation_daily`.
Nothing in ranking, selection, portfolio or trading imports this module.

DEFINITIONS (all deterministic; changing one means bumping CALC_VERSION)
------------------------------------------------------------------------
Calendar      The NIFTY 50 trading dates. Every series is reindexed onto it.
              A stock with no bar on a date is carried forward for price-level
              maths (up to FFILL_LIMIT sessions) but is NOT "fresh": it earns no
              return that day and is not eligible. An index with no bar is
              carried forward and flagged `is_filled`.
Breaks        A one-day close move beyond ±BREAK_MOVE is a corporate action the
              provider did not adjust (splits are adjusted; demergers are not —
              ABFRL, RAYMOND, KESORAMIND …). History before the last break is
              dropped for that stock so DMAs / 52w highs are not distorted.
Eligible      Fresh bar AND trailing 20-session mean turnover ≥ MIN_TURNOVER.
Sector level  Equal-weight index: level_t = level_{t-1} × (1 + mean eligible
              constituent return_t). Needs ≥ MIN_CONSTITUENTS returns that day.
RS            rs = level / NIFTY50 × 100
              rs_ratio    = EMA_smooth(100 × rs / SMA_ratio_len(rs))
              rs_momentum = 100 × rs_ratio / rs_ratio[t − mom_len]
              rs_accel    = rs_momentum − rs_momentum[t − accel_len]
              quadrant    = leading (≥100, ≥100) / weakening (≥100, <100) /
                            lagging (<100, <100) / improving (<100, ≥100)
Breadth       % of eligible constituents with close > SMA20 / SMA50 / SMA200
              (denominator: eligible constituents with a full window).
52w           Close ≥ max (≤ min) of the trailing 252 closes, needing ≥ 240 bars.
Volume        vol_ratio = median over eligible constituents of volume_t / MEDIAN
              volume of the prior 50 sessions (volume is right-skewed; against
              the mean a normal day reads ~0.6, against the median ~1.0). up_turnover_pct = turnover of
              eligible constituents that rose ÷ turnover of all that traded.
Dispersion    Cross-sectional std (%) of eligible constituents' lookback returns.
Market RS     `rs_ratio_mkt` / `rs_momentum_mkt` / `quadrant_mkt` repeat RS against
              the equal-weight ALL EQUITIES level instead of NIFTY 50. Sector
              levels are equal-weight, so RS vs the cap-weighted NIFTY 50 also
              carries the small-vs-large-cap effect (on 2026-09-25 the whole
              equal-weight market read 103.9 vs NIFTY 50). The _mkt pair isolates
              the sector effect; both are stored.
Weak sources  `provider_share` = share of eligible constituents whose sector came
              from the provider tier (≈71% agreement with NSE official, measured
              2026-09-27). `rs_ratio_core` repeats RS on NSE-official + manual
              constituents only. `confidence` grades each row.

Weekly rows are keyed by the Friday of the ISO week (`as_of` = the last trading
date included). Weekly RS runs on week-end levels with WEEKLY params; breadth is
the week-end reading; 52w highs/lows count stocks that printed one on any day of
the week; volume uses mean daily volume of the week vs the prior 10 weeks.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

CALC_VERSION = "sr-1.1"
BENCHMARK = "NIFTY 50"
MARKET_ENTITY = "ALL EQUITIES"

FFILL_LIMIT = 5
BREAK_MOVE = 0.35
MIN_TURNOVER = float(os.getenv("SECTOR_ROT_MIN_TURNOVER_CR", "1.0")) * 1e7
MIN_CONSTITUENTS = 3
MIN_CORE = 5
MIN_DISPERSION_N = 5

UNRESOLVED_SECTORS = frozenset({"", "Unknown", "Unassigned"})
CORE_SOURCES = frozenset({"nse_official", "manual"})


@dataclass(frozen=True)
class TFParams:
    ratio_len: int
    smooth: int
    mom_len: int
    accel_len: int
    lookback: int


DAILY = TFParams(ratio_len=50, smooth=5, mom_len=10, accel_len=5, lookback=20)
WEEKLY = TFParams(ratio_len=20, smooth=3, mom_len=4, accel_len=2, lookback=4)


# ── helpers ───────────────────────────────────────────────────────────────────

def _frame(bars_by_key: dict[str, list[dict]], field: str, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    cols = {}
    for key, bars in bars_by_key.items():
        vals = {}
        for b in bars or []:
            v = b.get(field)
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                vals[pd.Timestamp(str(b.get("date"))[:10])] = f
        if vals:
            cols[key] = pd.Series(vals)
    if not cols:
        return pd.DataFrame(index=calendar)
    return pd.DataFrame(cols).reindex(calendar)


def _drop_pre_break(close: pd.DataFrame) -> pd.DataFrame:
    """NaN every bar before a stock's last unadjusted corporate-action gap."""
    prev = close.ffill().shift(1)
    move = (close / prev - 1).abs()
    brk = move > BREAK_MOVE
    if not brk.any().any():
        return close
    out = close.copy()
    for col in brk.columns[brk.any()]:
        last = brk.index[brk[col]].max()
        out.loc[out.index < last, col] = np.nan
    return out


def rs_metrics(level: pd.Series, bench: pd.Series, p: TFParams) -> pd.DataFrame:
    rs = level / bench * 100.0
    ratio_raw = 100.0 * rs / rs.rolling(p.ratio_len, min_periods=p.ratio_len).mean()
    rs_ratio = ratio_raw.ewm(span=p.smooth, adjust=False, min_periods=p.smooth).mean()
    rs_mom = 100.0 * rs_ratio / rs_ratio.shift(p.mom_len)
    accel = rs_mom - rs_mom.shift(p.accel_len)
    return pd.DataFrame({"rs_ratio": rs_ratio, "rs_momentum": rs_mom, "rs_accel": accel})


def quadrant(ratio, mom) -> str | None:
    if ratio is None or mom is None:
        return None
    if ratio >= 100:
        return "leading" if mom >= 100 else "weakening"
    return "improving" if mom >= 100 else "lagging"


def confidence(n: int, provider_share: float | None) -> str:
    if n < MIN_CONSTITUENTS:
        return "insufficient"
    share = 1.0 if provider_share is None else provider_share
    if n >= 15 and share <= 0.5:
        return "high"
    if n >= 8 and share <= 0.8:
        return "medium"
    return "low"


def week_key(ts: pd.Timestamp) -> str:
    d = ts.date()
    return (d + timedelta(days=4 - d.weekday())).isoformat()


def _clean(v, nd: int = 6):
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return round(f, nd) if math.isfinite(f) else None


def _pct(num: pd.Series, den: pd.Series) -> pd.Series:
    return (num / den.where(den > 0)) * 100.0


# ── panel ─────────────────────────────────────────────────────────────────────

@dataclass
class Panel:
    calendar: pd.DatetimeIndex
    close: pd.DataFrame        # carried-forward closes (post break removal)
    fresh: pd.DataFrame
    ret: pd.DataFrame          # daily return on fresh bars only
    turnover: pd.DataFrame
    elig: pd.DataFrame
    above: dict[int, pd.DataFrame]
    above_valid: dict[int, pd.DataFrame]
    new_high: pd.DataFrame
    new_low: pd.DataFrame
    hl_valid: pd.DataFrame
    vol_ratio: pd.DataFrame
    volume: pd.DataFrame
    breaks_dropped: int


def build_panel(stock_bars: dict[str, list[dict]], calendar: pd.DatetimeIndex) -> Panel:
    raw_close = _frame(stock_bars, "close", calendar)
    raw_close = raw_close.where(raw_close > 0)
    close0 = _drop_pre_break(raw_close)
    breaks = int((close0.notna() != raw_close.notna()).any().sum())
    volume = _frame(stock_bars, "volume", calendar).reindex(columns=close0.columns)

    fresh = close0.notna()
    close = close0.ffill(limit=FFILL_LIMIT)
    ret = close.pct_change(fill_method=None).where(fresh)
    vol = volume.where(fresh)
    turnover = (close * vol).where(fresh)
    to20 = turnover.rolling(20, min_periods=15).mean()
    elig = fresh & (to20 >= MIN_TURNOVER)

    above, above_valid = {}, {}
    for k in (20, 50, 200):
        sma = close.rolling(k, min_periods=k).mean()
        above_valid[k] = elig & sma.notna()
        above[k] = (close > sma) & above_valid[k]

    hi = close.rolling(252, min_periods=240).max()
    lo = close.rolling(252, min_periods=240).min()
    hl_valid = elig & hi.notna()
    new_high = (close >= hi) & hl_valid
    new_low = (close <= lo) & hl_valid

    base = vol.shift(1).rolling(50, min_periods=40).median()
    vol_ratio = (vol / base.where(base > 0)).where(elig)

    return Panel(calendar, close, fresh, ret, turnover, elig, above, above_valid,
                 new_high, new_low, hl_valid, vol_ratio, vol, breaks)


def _ew_level(ret: pd.DataFrame, mask: pd.DataFrame, min_n: int) -> tuple[pd.Series, pd.Series]:
    r = ret.where(mask)
    n = r.notna().sum(axis=1)
    ew = r.mean(axis=1).where(n >= min_n)
    level = 100.0 * (1.0 + ew.fillna(0.0)).cumprod()
    return level.where(ew.notna().cumsum() > 0), ew


# ── per-entity series ─────────────────────────────────────────────────────────

def _sector_daily(pn: Panel, cols: list[str], core_cols: list[str], weak_cols: list[str],
                  bench: pd.Series, mkt_level: pd.Series | None) -> pd.DataFrame:
    el = pn.elig[cols]
    n = el.sum(axis=1)
    level, ew = _ew_level(pn.ret[cols], el, MIN_CONSTITUENTS)
    df = pd.DataFrame(index=pn.calendar)
    df["level"] = level
    df["n_constituents"] = n
    df["n_core"] = pn.elig[core_cols].sum(axis=1) if core_cols else 0
    df["provider_share"] = (pn.elig[weak_cols].sum(axis=1) / n.where(n > 0)) if weak_cols else 0.0
    df["ret_bar"] = ew * 100.0
    df["ret_lookback"] = (level / level.shift(DAILY.lookback) - 1) * 100.0
    df = df.join(rs_metrics(level, bench, DAILY))
    _add_mkt(df, level, mkt_level, DAILY)
    if core_cols:
        core_level, _ = _ew_level(pn.ret[core_cols], pn.elig[core_cols], MIN_CORE)
        df["rs_ratio_core"] = rs_metrics(core_level, bench, DAILY)["rs_ratio"]
    else:
        df["rs_ratio_core"] = np.nan
    for k in (20, 50, 200):
        df[f"pct_above_{k}"] = _pct(pn.above[k][cols].sum(axis=1), pn.above_valid[k][cols].sum(axis=1))
    df["new_highs_52w"] = pn.new_high[cols].sum(axis=1)
    df["new_lows_52w"] = pn.new_low[cols].sum(axis=1)
    df["n_52w_eligible"] = pn.hl_valid[cols].sum(axis=1)
    df["vol_ratio"] = pn.vol_ratio[cols].median(axis=1)
    r = pn.ret[cols].where(el)
    to = pn.turnover[cols].where(el & r.notna())
    df["up_turnover_pct"] = _pct(to.where(r > 0).sum(axis=1), to.sum(axis=1))
    lb = (pn.close[cols] / pn.close[cols].shift(DAILY.lookback) - 1).where(el) * 100.0
    df["dispersion"] = lb.std(axis=1, ddof=0).where(lb.notna().sum(axis=1) >= MIN_DISPERSION_N)
    return df


def _sector_weekly(pn: Panel, daily: pd.DataFrame, cols: list[str], core_cols: list[str],
                   bench: pd.Series, weeks: pd.Series, mkt_level: pd.Series | None) -> pd.DataFrame:
    last_idx = pd.Series(pn.calendar, index=pn.calendar).groupby(weeks.values).max()
    wk = daily.loc[last_idx.values].copy()
    wk.index = last_idx.index
    wk["as_of"] = [pd.Timestamp(d).date().isoformat() for d in last_idx.values]
    bench_w = bench.loc[last_idx.values]
    bench_w.index = last_idx.index
    level_w = wk["level"]
    wk["ret_bar"] = level_w.pct_change(fill_method=None) * 100.0
    wk["ret_lookback"] = (level_w / level_w.shift(WEEKLY.lookback) - 1) * 100.0
    rs = rs_metrics(level_w, bench_w, WEEKLY)
    for c in rs.columns:
        wk[c] = rs[c]
    if mkt_level is not None:
        mw = mkt_level.loc[last_idx.values]
        mw.index = last_idx.index
        _add_mkt(wk, level_w, mw, WEEKLY)
    if core_cols:
        core_level, _ = _ew_level(pn.ret[core_cols], pn.elig[core_cols], MIN_CORE)
        cl = core_level.loc[last_idx.values]
        cl.index = last_idx.index
        wk["rs_ratio_core"] = rs_metrics(cl, bench_w, WEEKLY)["rs_ratio"]

    g = weeks.values
    el_end = pn.elig[cols].loc[last_idx.values]
    el_end.index = last_idx.index
    wk["new_highs_52w"] = pn.new_high[cols].groupby(g).max().sum(axis=1)
    wk["new_lows_52w"] = pn.new_low[cols].groupby(g).max().sum(axis=1)

    vmean = pn.volume[cols].groupby(g).mean()
    base = vmean.shift(1).rolling(10, min_periods=8).median()
    wk["vol_ratio"] = (vmean / base.where(base > 0)).where(el_end).median(axis=1)

    close_w = pn.close[cols].loc[last_idx.values]
    close_w.index = last_idx.index
    rw = (close_w / close_w.shift(1) - 1).where(el_end)
    to_w = pn.turnover[cols].groupby(g).sum(min_count=1).where(el_end & rw.notna())
    wk["up_turnover_pct"] = _pct(to_w.where(rw > 0).sum(axis=1), to_w.sum(axis=1))
    lb = (close_w / close_w.shift(WEEKLY.lookback) - 1).where(el_end) * 100.0
    wk["dispersion"] = lb.std(axis=1, ddof=0).where(lb.notna().sum(axis=1) >= MIN_DISPERSION_N)
    return wk


def _add_mkt(df: pd.DataFrame, level: pd.Series, mkt_level: pd.Series | None, p: TFParams) -> None:
    if mkt_level is None:
        return
    m = rs_metrics(level, mkt_level, p)
    df["rs_ratio_mkt"] = m["rs_ratio"]
    df["rs_momentum_mkt"] = m["rs_momentum"]


def _index_daily(level: pd.Series, filled: pd.Series, bench: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"level": level, "is_filled": filled})
    df["ret_bar"] = level.pct_change(fill_method=None) * 100.0
    df["ret_lookback"] = (level / level.shift(DAILY.lookback) - 1) * 100.0
    return df.join(rs_metrics(level, bench, DAILY))


def _index_weekly(daily: pd.DataFrame, bench: pd.Series, calendar, weeks: pd.Series) -> pd.DataFrame:
    last_idx = pd.Series(calendar, index=calendar).groupby(weeks.values).max()
    wk = daily.loc[last_idx.values, ["level", "is_filled"]].copy()
    wk.index = last_idx.index
    wk["as_of"] = [pd.Timestamp(d).date().isoformat() for d in last_idx.values]
    bw = bench.loc[last_idx.values]
    bw.index = last_idx.index
    wk["ret_bar"] = wk["level"].pct_change(fill_method=None) * 100.0
    wk["ret_lookback"] = (wk["level"] / wk["level"].shift(WEEKLY.lookback) - 1) * 100.0
    rs = rs_metrics(wk["level"], bw, WEEKLY)
    for c in rs.columns:
        wk[c] = rs[c]
    return wk


# ── public entry point ────────────────────────────────────────────────────────

def compute_sector_rotation(
    stock_bars: dict[str, list[dict]],
    index_bars: dict[str, list[dict]],
    members: dict[str, tuple[str, str]],
) -> dict:
    """Compute every daily + weekly row. `members` = {SYMBOL: (sector, sector_source)}
    for current-universe equities. Returns {"rows": [...], "meta": {...}}."""
    if BENCHMARK not in index_bars or not index_bars[BENCHMARK]:
        raise ValueError(f"benchmark {BENCHMARK!r} missing from index bars")
    calendar = pd.DatetimeIndex(sorted({pd.Timestamp(str(b["date"])[:10]) for b in index_bars[BENCHMARK]}))
    bench = _frame({BENCHMARK: index_bars[BENCHMARK]}, "close", calendar)[BENCHMARK]

    members = {s.upper(): v for s, v in members.items() if v and v[0] not in UNRESOLVED_SECTORS}
    usable = {s: b for s, b in stock_bars.items() if s.upper() in members}
    pn = build_panel({s.upper(): b for s, b in usable.items()}, calendar)
    present = [c for c in pn.close.columns if pn.fresh[c].any()]

    by_sector: dict[str, list[str]] = {}
    for s in present:
        by_sector.setdefault(members[s][0], []).append(s)
    by_sector = dict(sorted(by_sector.items()))

    weeks = pd.Series([week_key(d) for d in calendar], index=calendar)
    last_week = weeks.iloc[-1]
    partial_last = calendar[-1].weekday() != 4

    rows: list[dict] = []

    def emit(entity: str, kind: str, daily: pd.DataFrame, weekly: pd.DataFrame) -> None:
        for tf, frame in (("D", daily), ("W", weekly)):
            for idx, rec in frame.iterrows():
                if kind != "index" and not (rec.get("n_constituents") or 0):
                    continue
                if pd.isna(rec.get("level")):
                    continue
                key = idx.date().isoformat() if tf == "D" else idx
                row = {
                    "date": key, "timeframe": tf, "entity": entity, "kind": kind,
                    "as_of": key if tf == "D" else rec.get("as_of"),
                    "partial": bool(tf == "W" and idx == last_week and partial_last),
                    "calc_version": CALC_VERSION,
                }
                for col in ("n_constituents", "n_core", "provider_share", "level", "ret_bar",
                            "ret_lookback", "rs_ratio", "rs_momentum", "rs_accel", "rs_ratio_core",
                            "pct_above_20", "pct_above_50", "pct_above_200", "new_highs_52w",
                            "new_lows_52w", "n_52w_eligible", "vol_ratio", "up_turnover_pct",
                            "dispersion", "is_filled", "rs_ratio_mkt", "rs_momentum_mkt"):
                    row[col] = _clean(rec.get(col)) if col in rec.index else None
                for k in ("n_constituents", "n_core", "new_highs_52w", "new_lows_52w", "n_52w_eligible"):
                    if row[k] is not None:
                        row[k] = int(row[k])
                row["quadrant"] = quadrant(row["rs_ratio"], row["rs_momentum"])
                row["quadrant_mkt"] = quadrant(row["rs_ratio_mkt"], row["rs_momentum_mkt"])
                if kind == "index":
                    row["confidence"] = "reference"
                else:
                    row["confidence"] = confidence(int(row["n_constituents"] or 0), row["provider_share"])
                bench_lb = bench_ret_lookback[tf].get(idx)
                row["rel_ret_lookback"] = (
                    _clean(row["ret_lookback"] - bench_lb)
                    if row["ret_lookback"] is not None and bench_lb is not None else None
                )
                rows.append(row)

    bench_d = (bench / bench.shift(DAILY.lookback) - 1) * 100.0
    last_idx = pd.Series(calendar, index=calendar).groupby(weeks.values).max()
    bw = bench.loc[last_idx.values]
    bw.index = last_idx.index
    bench_w = (bw / bw.shift(WEEKLY.lookback) - 1) * 100.0
    bench_ret_lookback = {
        "D": {k: _clean(v) for k, v in bench_d.items()},
        "W": {k: _clean(v) for k, v in bench_w.items()},
    }

    groups = [(MARKET_ENTITY, "market", present)] + [(s, "sector", c) for s, c in by_sector.items()]
    mkt_level = None
    for entity, kind, cols in groups:
        core = [c for c in cols if members[c][1] in CORE_SOURCES]
        weak = [c for c in cols if members[c][1] not in CORE_SOURCES]
        daily = _sector_daily(pn, cols, core, weak, bench, mkt_level)
        weekly = _sector_weekly(pn, daily, cols, core, bench, weeks, mkt_level)
        if kind == "market":
            mkt_level = daily["level"]
        emit(entity, kind, daily, weekly)

    index_meta = {}
    for name in sorted(index_bars):
        if name == BENCHMARK or not index_bars[name]:
            continue
        raw = _frame({name: index_bars[name]}, "close", calendar)[name]
        filled = raw.isna() & raw.ffill().notna()
        level = raw.ffill()
        daily = _index_daily(level, filled, bench)
        weekly = _index_weekly(daily, bench, calendar, weeks)
        emit(name, "index", daily, weekly)
        index_meta[name] = int(filled.sum())

    meta = {
        "calc_version": CALC_VERSION,
        "calendar_start": calendar[0].date().isoformat(),
        "calendar_end": calendar[-1].date().isoformat(),
        "sessions": len(calendar),
        "members_given": len(members),
        "members_with_bars": len(present),
        "members_without_bars": len(members) - len(present),
        "sectors": {s: len(c) for s, c in by_sector.items()},
        "breaks_dropped": pn.breaks_dropped,
        "index_filled_days": index_meta,
        "rows": len(rows),
    }
    return {"rows": rows, "meta": meta}


def as_of_date(rows: list[dict]) -> date | None:
    days = [r["date"] for r in rows if r["timeframe"] == "D"]
    return date.fromisoformat(max(days)) if days else None
