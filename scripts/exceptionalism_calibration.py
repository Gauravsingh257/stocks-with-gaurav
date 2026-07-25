"""
scripts/exceptionalism_calibration.py — Phase-2 validation harness (offline).

Proves (or disproves) that the new Exceptionalism architecture selects BETTER
stocks than the previous engine — using REAL forward returns, not intuition.

It is READ-ONLY analysis: it never touches the engine, the recommendation
tables, or any feature flag. It consumes the shadow dataset that
`EXCEPTIONALISM_SHADOW` (default ON) writes for every scanned stock into
`signals_log.layer_details.exceptionalism`, joins each row to its forward
returns (1/3/5/10/20 trading days from the scan CMP), and reports:

  * hit rate, avg/median forward return, avg max-drawdown (MAE), per
    exceptionalism band × market-health band × decision tier;
  * precision + false-positive rate of the exceptionalism gate;
  * a THRESHOLD SWEEP → the exceptionalism cutoff that maximises forward
    performance at each market-health level (the empirical
    `required_exceptionalism(health)` curve);
  * a LEGACY-vs-EXCEPTIONALISM comparison (old `final_selected` set vs the new
    `qualifies` set) — the "did it actually improve?" verdict.

Run only AFTER the shadow flag has collected several live sessions AND at least
~20 trading days have elapsed so the 20D forward window exists:

    python -m scripts.exceptionalism_calibration --from 2026-07-25 --to 2026-08-15 \
        --horizon SWING --out reports/exc_calibration.md

Nothing here fabricates data: symbols/dates without enough forward bars are
excluded from the horizon they can't cover (and counted as "pending").
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field

HORIZONS = (1, 3, 5, 10, 20)
EXC_BANDS = [(0, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
HEALTH_BANDS = [(0, 30, "BEAR"), (30, 45, "CORRECTION"), (45, 60, "SIDEWAYS"),
                (60, 75, "WEAK_BULL"), (75, 101, "STRONG_BULL")]


# ── shadow-row loading ────────────────────────────────────────────────────────

@dataclass(slots=True)
class ShadowRow:
    scan_id: str
    symbol: str
    date: str
    cmp: float | None
    final_selected: bool
    exceptionalism: float | None
    threshold: float | None
    qualifies: bool | None
    market_health: float | None
    sector_band: str | None
    forward: dict = field(default_factory=dict)   # {horizon: {ret, mae, mfe}}


def _parse_layer_details(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def load_shadow_rows(conn, *, date_from: str, date_to: str, horizon: str | None = None) -> list[ShadowRow]:
    """Read signals_log in [date_from, date_to] and extract the exceptionalism
    verdict from each row's layer_details JSON. `conn` is a sqlite connection."""
    q = ("SELECT scan_id, symbol, date, cmp, final_selected, layer_details "
         "FROM signals_log WHERE date >= ? AND date <= ?")
    params: list = [date_from, date_to]
    if horizon:
        q += " AND horizon = ?"
        params.append(horizon.upper())
    rows: list[ShadowRow] = []
    for r in conn.execute(q, params).fetchall():
        r = dict(r)
        ld = _parse_layer_details(r.get("layer_details"))
        exc = ld.get("exceptionalism") or {}
        if not isinstance(exc, dict):
            exc = {}
        rows.append(ShadowRow(
            scan_id=r.get("scan_id") or "",
            symbol=r.get("symbol") or "",
            date=str(r.get("date") or "")[:10],
            cmp=_f(r.get("cmp")),
            final_selected=bool(r.get("final_selected")),
            exceptionalism=_f(exc.get("exceptionalism")),
            threshold=_f(exc.get("threshold")),
            qualifies=exc.get("qualifies"),
            market_health=_f(exc.get("market_health")),
            sector_band=exc.get("sector_band"),
        ))
    return rows


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── forward returns ───────────────────────────────────────────────────────────

def attach_forward_returns(rows: list[ShadowRow], fetch_bars, *, horizons=HORIZONS) -> None:
    """Populate row.forward[h] = {ret, mae, mfe} for each horizon.

    `fetch_bars(symbol, from_date) -> list[dict(date, high, low, close)]` returns
    the trading bars ON/AFTER `from_date`, ascending. Bar 0 is the scan day. The
    N-day forward return uses the CLOSE of the Nth bar after the scan day; MAE/MFE
    are the worst/best intraday excursions over that window. A horizon with fewer
    than N+1 bars is left absent (pending), never guessed.
    """
    cache: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if not row.symbol or not row.cmp or row.cmp <= 0 or not row.date:
            continue
        key = (row.symbol, row.date)
        bars = cache.get(key)
        if bars is None:
            try:
                bars = fetch_bars(row.symbol, row.date) or []
            except Exception:
                bars = []
            cache[key] = bars
        if not bars:
            continue
        base = row.cmp
        for h in horizons:
            if len(bars) <= h:      # need bar[h] (h trading days AFTER the scan day)
                continue
            window = bars[1:h + 1]
            close_h = _f(bars[h].get("close"))
            if close_h is None:
                continue
            ret = (close_h - base) / base * 100.0
            lows = [_f(b.get("low")) for b in window if _f(b.get("low")) is not None]
            highs = [_f(b.get("high")) for b in window if _f(b.get("high")) is not None]
            mae = ((min(lows) - base) / base * 100.0) if lows else None
            mfe = ((max(highs) - base) / base * 100.0) if highs else None
            row.forward[h] = {"ret": round(ret, 2),
                              "mae": round(mae, 2) if mae is not None else None,
                              "mfe": round(mfe, 2) if mfe is not None else None}


# ── metrics ───────────────────────────────────────────────────────────────────

def metrics_for(returns: list[float], maes: list[float] | None = None) -> dict:
    """Core performance metrics for a set of forward returns."""
    n = len(returns)
    if n == 0:
        return {"n": 0}
    wins = [r for r in returns if r > 0]
    out = {
        "n": n,
        "hit_rate": round(len(wins) / n * 100, 1),          # % positive
        "avg_return": round(statistics.mean(returns), 2),
        "median_return": round(statistics.median(returns), 2),
        "avg_win": round(statistics.mean(wins), 2) if wins else None,
        "avg_loss": round(statistics.mean([r for r in returns if r <= 0]), 2) if len(wins) < n else None,
    }
    if maes:
        out["avg_max_drawdown"] = round(statistics.mean(maes), 2)
        out["worst_drawdown"] = round(min(maes), 2)
    return out


def _returns_at(rows: list[ShadowRow], horizon: int) -> tuple[list[float], list[float]]:
    rets, maes = [], []
    for r in rows:
        fwd = r.forward.get(horizon)
        if fwd and fwd.get("ret") is not None:
            rets.append(fwd["ret"])
            if fwd.get("mae") is not None:
                maes.append(fwd["mae"])
    return rets, maes


def bucket_by_exceptionalism(rows: list[ShadowRow], horizon: int) -> list[dict]:
    out = []
    for lo, hi in EXC_BANDS:
        band = [r for r in rows if r.exceptionalism is not None and lo <= r.exceptionalism < hi]
        rets, maes = _returns_at(band, horizon)
        out.append({"band": f"{lo}-{hi - 1}", **metrics_for(rets, maes)})
    return out


def bucket_by_health(rows: list[ShadowRow], horizon: int) -> list[dict]:
    out = []
    for lo, hi, label in HEALTH_BANDS:
        band = [r for r in rows if r.market_health is not None and lo <= r.market_health < hi]
        rets, maes = _returns_at(band, horizon)
        out.append({"health_band": f"{label} ({lo}-{hi - 1})", **metrics_for(rets, maes)})
    return out


def threshold_sweep(rows: list[ShadowRow], horizon: int, *, min_n: int = 20,
                    lo: int = 50, hi: int = 96, step: int = 2) -> list[dict]:
    """For each candidate exceptionalism cutoff, the forward performance of the
    stocks that would clear it — the empirical basis for the threshold."""
    scored = [r for r in rows if r.exceptionalism is not None]
    out = []
    for t in range(lo, hi, step):
        picks = [r for r in scored if r.exceptionalism >= t]
        rets, maes = _returns_at(picks, horizon)
        m = metrics_for(rets, maes)
        out.append({"threshold": t, "selected": m.get("n", 0),
                    "hit_rate": m.get("hit_rate"), "avg_return": m.get("avg_return"),
                    "avg_max_drawdown": m.get("avg_max_drawdown"),
                    "enough_sample": m.get("n", 0) >= min_n})
    return out


def optimal_threshold(sweep: list[dict], *, min_n: int = 20) -> dict | None:
    """Pick the cutoff maximising avg forward return among rows with a usable
    sample. Returns the row (or None if no bucket has enough data)."""
    usable = [s for s in sweep if s.get("enough_sample") and s.get("avg_return") is not None]
    if not usable:
        return None
    return max(usable, key=lambda s: (s["avg_return"], s.get("hit_rate") or 0))


def health_curve(rows: list[ShadowRow], horizon: int, *, min_n: int = 15) -> list[dict]:
    """Empirical required_exceptionalism(health): within each health band, the
    exceptionalism cutoff that best separates winners from losers. This is the
    data-driven replacement for the assumed threshold curve."""
    out = []
    for lo, hi, label in HEALTH_BANDS:
        band = [r for r in rows if r.market_health is not None and lo <= r.market_health < hi]
        sweep = threshold_sweep(band, horizon, min_n=min_n)
        best = optimal_threshold(sweep, min_n=min_n)
        out.append({
            "health_band": label,
            "n": len(band),
            "suggested_threshold": best["threshold"] if best else None,
            "at_threshold_hit_rate": best.get("hit_rate") if best else None,
            "at_threshold_avg_return": best.get("avg_return") if best else None,
        })
    return out


def legacy_vs_exceptionalism(rows: list[ShadowRow], horizon: int) -> dict:
    """The verdict: does the exceptionalism gate beat the previous engine's
    final-selected set on real forward returns?"""
    legacy = [r for r in rows if r.final_selected]
    exc = [r for r in rows if r.qualifies is True]
    lr, lm = _returns_at(legacy, horizon)
    er, em = _returns_at(exc, horizon)
    legacy_m = metrics_for(lr, lm)
    exc_m = metrics_for(er, em)
    improved = None
    if legacy_m.get("n") and exc_m.get("n"):
        improved = (exc_m.get("avg_return", 0) > legacy_m.get("avg_return", 0)
                    and (exc_m.get("hit_rate") or 0) >= (legacy_m.get("hit_rate") or 0))
    return {"legacy_final_selected": legacy_m, "exceptionalism_qualified": exc_m, "exceptionalism_better": improved}


def false_positive_rate(rows: list[ShadowRow], horizon: int) -> dict:
    """Of stocks the gate QUALIFIED, how many were forward losers (false positives)."""
    picks = [r for r in rows if r.qualifies is True and horizon in r.forward]
    if not picks:
        return {"n": 0}
    fp = [r for r in picks if (r.forward[horizon].get("ret") or 0) <= 0]
    return {"n": len(picks), "false_positives": len(fp),
            "false_positive_rate": round(len(fp) / len(picks) * 100, 1),
            "precision": round((len(picks) - len(fp)) / len(picks) * 100, 1)}


def build_report(rows: list[ShadowRow]) -> dict:
    covered = {h: sum(1 for r in rows if h in r.forward) for h in HORIZONS}
    return {
        "total_rows": len(rows),
        "with_exceptionalism": sum(1 for r in rows if r.exceptionalism is not None),
        "forward_coverage": covered,
        "by_horizon": {
            h: {
                "by_exceptionalism": bucket_by_exceptionalism(rows, h),
                "by_health": bucket_by_health(rows, h),
                "threshold_sweep": threshold_sweep(rows, h),
                "optimal_threshold": optimal_threshold(threshold_sweep(rows, h)),
                "health_curve": health_curve(rows, h),
                "false_positive": false_positive_rate(rows, h),
                "legacy_vs_exceptionalism": legacy_vs_exceptionalism(rows, h),
            }
            for h in HORIZONS
        },
    }


# ── markdown rendering ────────────────────────────────────────────────────────

def _tbl(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    head = "| " + " | ".join(c[1] for c in cols) + " |\n| " + " | ".join("---" for _ in cols) + " |\n"
    body = ""
    for r in rows:
        body += "| " + " | ".join(str(r.get(c[0], "—") if r.get(c[0]) is not None else "—") for c in cols) + " |\n"
    return head + body


def render_markdown(report: dict) -> str:
    md = ["# Exceptionalism Calibration Report", ""]
    md.append(f"- Rows analysed: **{report['total_rows']}** "
              f"(with exceptionalism verdict: {report['with_exceptionalism']})")
    md.append(f"- Forward-return coverage by horizon: {report['forward_coverage']}")
    md.append("")
    if report["with_exceptionalism"] == 0:
        md.append("> ⚠️ **No exceptionalism data yet.** Let `EXCEPTIONALISM_SHADOW` run across "
                  "several live sessions and allow ~20 trading days for the forward windows, then re-run.")
        return "\n".join(md)

    for h in HORIZONS:
        b = report["by_horizon"][h]
        md.append(f"## {h}-Day Forward Horizon\n")
        lv = b["legacy_vs_exceptionalism"]
        verdict = lv.get("exceptionalism_better")
        badge = "✅ improved" if verdict else ("❌ not yet" if verdict is False else "⚠️ insufficient data")
        md.append(f"**Legacy vs Exceptionalism: {badge}**\n")
        md.append(_tbl(
            [{"set": "Legacy (final_selected)", **lv["legacy_final_selected"]},
             {"set": "Exceptionalism (qualified)", **lv["exceptionalism_qualified"]}],
            [("set", "Selection set"), ("n", "N"), ("hit_rate", "Hit %"),
             ("avg_return", "Avg %"), ("avg_max_drawdown", "Avg MaxDD %")]))
        md.append("")
        fp = b["false_positive"]
        if fp.get("n"):
            md.append(f"Gate precision **{fp.get('precision')}%** · false-positive rate "
                      f"**{fp.get('false_positive_rate')}%** (n={fp['n']}).\n")
        md.append("**Forward return by exceptionalism band:**\n")
        md.append(_tbl(b["by_exceptionalism"],
                       [("band", "EXC band"), ("n", "N"), ("hit_rate", "Hit %"),
                        ("avg_return", "Avg %"), ("avg_max_drawdown", "Avg MaxDD %")]))
        md.append("")
        ot = b["optimal_threshold"]
        if ot:
            md.append(f"**Optimal cutoff (this horizon): EXC ≥ {ot['threshold']}** → "
                      f"hit {ot.get('hit_rate')}%, avg {ot.get('avg_return')}%, n={ot.get('selected')}.\n")
        md.append("**Empirical threshold-by-health curve** (data-driven `required_exceptionalism`):\n")
        md.append(_tbl(b["health_curve"],
                       [("health_band", "Health"), ("n", "N"), ("suggested_threshold", "Suggested EXC"),
                        ("at_threshold_hit_rate", "Hit %"), ("at_threshold_avg_return", "Avg %")]))
        md.append("")
    md.append("---\n_Read-only analysis. No engine or flag was modified. "
              "Enable production flags only after these tables confirm a real improvement._")
    return "\n".join(md)


# ── real forward-bar fetcher (production) ─────────────────────────────────────

def _kite_or_yf_fetch(symbol: str, from_date: str) -> list[dict]:
    """Fetch daily bars on/after from_date via the project's DataIngestion."""
    from data.ingestion import DataIngestion
    import pandas as pd
    src = __import__("os").getenv("RESEARCH_DATA_SOURCE", "yfinance")
    df = DataIngestion(source=src).fetch_historical(symbol, interval="day", days=60)
    if df is None or df.empty:
        return []
    frame = df.copy()
    if "date" not in frame.columns and isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.reset_index().rename(columns={frame.reset_index().columns[0]: "date"})
    frame.columns = [str(c).lower() for c in frame.columns]
    cutoff = pd.Timestamp(from_date)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    frame = frame[frame["date"] >= cutoff]
    return [{"date": str(r["date"])[:10], "high": r.get("high"), "low": r.get("low"), "close": r.get("close")}
            for _, r in frame.iterrows()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Exceptionalism calibration report (offline, read-only).")
    ap.add_argument("--from", dest="date_from", required=True, help="scan date lower bound YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="scan date upper bound YYYY-MM-DD")
    ap.add_argument("--horizon", default=None, help="SWING or LONGTERM (default: both)")
    ap.add_argument("--out", default=None, help="write markdown here (else stdout)")
    ap.add_argument("--json-out", default=None, help="also write the raw report JSON here")
    args = ap.parse_args()

    from dashboard.backend.db.schema import get_connection
    conn = get_connection()
    try:
        rows = load_shadow_rows(conn, date_from=args.date_from, date_to=args.date_to, horizon=args.horizon)
    finally:
        conn.close()

    attach_forward_returns(rows, _kite_or_yf_fetch)
    report = build_report(rows)
    md = render_markdown(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"Wrote report to {args.out} ({len(rows)} rows).")
    else:
        print(md)


if __name__ == "__main__":
    main()
