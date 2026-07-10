"""
services/pil/exposure.py
========================
Cross-portfolio exposure & concentration analytics (Part 2). Given the
reconstructed ledgers (services/pil/accounting) it aggregates every OPEN holding
across all three books into risk dimensions and concentration measures:

  * Exposure by sector / industry / market-cap tier / theme (via reference_data)
  * Portfolio beta (market-value-weighted) and liquidity profile
  * Concentration: Herfindahl (HHI), top holdings, top-10 share, single-name max
  * Diversification score + effective-holdings
  * Engine correlation matrix (from the daily equity-curve returns)
  * Sector × engine heatmap
  * Threshold breaches (drives the Part-10 alerts)

Everything is descriptive — it never changes an engine decision. Exposure is
expressed as a share of *deployed* capital (Σ market value) so concentration is
measured among what is actually held, independent of the cash level.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, pstdev
from typing import Any

from services.pil import config as pil_config
from services.pil import reference_data as ref


def _bucket(positions: list[dict], keyfn) -> list[dict]:
    """Group positions by a dimension → sorted [{name, value, count, books}]."""
    agg: dict[str, dict] = defaultdict(lambda: {"value": 0.0, "count": 0, "books": set()})
    for p in positions:
        k = keyfn(p)
        agg[k]["value"] += p.get("market_value", 0.0)
        agg[k]["count"] += 1
        if p.get("book"):
            agg[k]["books"].add(p["book"])
    total = sum(a["value"] for a in agg.values()) or 1.0
    out = [
        {"name": k, "value": round(a["value"], 2),
         "pct": round(a["value"] / total * 100, 2), "count": a["count"],
         "books": sorted(a["books"])}
        for k, a in agg.items()
    ]
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


def _herfindahl(positions: list[dict]) -> float:
    total = sum(p.get("market_value", 0.0) for p in positions) or 1.0
    return round(sum((p.get("market_value", 0.0) / total) ** 2 for p in positions), 4)


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = mean(a), mean(b)
    sa, sb = pstdev(a), pstdev(b)
    if sa == 0 or sb == 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    return round(cov / (sa * sb), 3)


def _returns(curve: list[dict]) -> tuple[list[str], list[float]]:
    dates, vals = [], []
    for p in curve:
        dates.append(p["date"]); vals.append(p["value"])
    rets, rdates = [], []
    for i in range(1, len(vals)):
        if vals[i - 1]:
            rets.append(vals[i] / vals[i - 1] - 1.0)
            rdates.append(dates[i])
    return rdates, rets


def correlation_matrix(books: dict[str, dict]) -> dict[str, Any]:
    """Pearson correlation of daily returns between the three engines, aligned on
    common dates."""
    engines = ["SWING", "LONGTERM", "MOMENTUM"]
    series: dict[str, dict[str, float]] = {}
    for b in engines:
        curve = books.get(b, {}).get("equity_curve", [])
        rd, rr = _returns(curve)
        series[b] = dict(zip(rd, rr))

    matrix: dict[str, dict[str, float | None]] = {}
    for a in engines:
        matrix[a] = {}
        for c in engines:
            if a == c:
                matrix[a][c] = 1.0
                continue
            common = sorted(set(series[a]) & set(series[c]))
            va = [series[a][d] for d in common]
            vc = [series[c][d] for d in common]
            matrix[a][c] = _pearson(va, vc)
    return {"engines": engines, "matrix": matrix}


def compute(books: dict[str, dict]) -> dict[str, Any]:
    """Full exposure/risk payload from the reconstructed ledger map."""
    combined = books.get("COMBINED", {})
    positions: list[dict] = list(combined.get("positions", []))
    th = pil_config.thresholds()

    nav = combined.get("portfolio_value", 0.0) or 1.0
    deployed = sum(p.get("market_value", 0.0) for p in positions)

    # dimensions
    by_sector = _bucket(positions, lambda p: p.get("sector") or ref.get_sector(p["symbol"]))
    by_industry = _bucket(positions, lambda p: ref.get_industry(p["symbol"]))
    by_mcap = _bucket(positions, lambda p: ref.get_market_cap_tier(p["symbol"]))
    by_theme = _bucket(positions, lambda p: ref.get_theme(p["symbol"]))
    by_book = _bucket(positions, lambda p: p.get("book", "?"))

    # holdings (aggregate a symbol across books)
    hold: dict[str, dict] = defaultdict(lambda: {"value": 0.0, "books": set(), "sector": ""})
    for p in positions:
        s = p["symbol"]
        hold[s]["value"] += p.get("market_value", 0.0)
        hold[s]["books"].add(p.get("book", "?"))
        hold[s]["sector"] = p.get("sector") or ref.get_sector(s)
    holdings = [
        {"symbol": s, "value": round(h["value"], 2),
         "pct": round(h["value"] / (deployed or 1.0) * 100, 2),
         "pct_nav": round(h["value"] / nav * 100, 2),
         "sector": h["sector"], "books": sorted(h["books"])}
        for s, h in hold.items()
    ]
    holdings.sort(key=lambda x: x["value"], reverse=True)

    hhi = _herfindahl(positions)
    eff_holdings = round(1 / hhi, 1) if hhi else 0.0
    top10 = holdings[:10]
    top10_pct = round(sum(h["pct"] for h in top10), 2)
    single_max = holdings[0] if holdings else None

    # weighted beta + liquidity
    beta = 0.0
    liq_known = 0.0
    liq_flagged = []
    for p in positions:
        w = p.get("market_value", 0.0) / (deployed or 1.0)
        beta += w * ref.get_beta(p["symbol"])
        lc = ref.get_liquidity_cr(p["symbol"])
        if lc is not None:
            liq_known += w
            if lc < th["min_liquidity_cr"]:
                liq_flagged.append({"symbol": p["symbol"], "liquidity_cr": lc})

    # diversification score (0..1): blends 1-HHI with effective-holdings breadth
    n = len(positions) or 1
    div_score = round(min(1.0, (1 - hhi) * 0.6 + min(eff_holdings / max(n, 1), 1.0) * 0.4), 3)

    # sector × engine heatmap
    heat: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for p in positions:
        sec = p.get("sector") or ref.get_sector(p["symbol"])
        heat[sec][p.get("book", "?")] += p.get("market_value", 0.0)
    heatmap = [
        {"sector": sec, **{bk: round(v, 2) for bk, v in bks.items()}}
        for sec, bks in sorted(heat.items(), key=lambda kv: -sum(kv[1].values()))
    ]

    corr = correlation_matrix(books)

    # warnings
    warnings = []
    for s in by_sector:
        if s["pct"] / 100 > th["max_sector_share"]:
            warnings.append({"type": "SECTOR_OVERWEIGHT", "severity": "WARN",
                             "message": f"{s['name']} exposure = {s['pct']:.0f}% of deployed capital",
                             "value": round(s["pct"] / 100, 4), "threshold": th["max_sector_share"]})
    if single_max and single_max["pct"] / 100 > th["max_single_stock"]:
        warnings.append({"type": "SINGLE_STOCK_CONCENTRATION", "severity": "WARN",
                         "message": f"{single_max['symbol']} = {single_max['pct']:.0f}% of deployed capital",
                         "value": round(single_max["pct"] / 100, 4), "threshold": th["max_single_stock"]})
    if top10_pct / 100 > th["max_top10_share"]:
        warnings.append({"type": "TOP10_CONCENTRATION", "severity": "INFO",
                         "message": f"Top-10 holdings = {top10_pct:.0f}% of deployed capital",
                         "value": round(top10_pct / 100, 4), "threshold": th["max_top10_share"]})
    if div_score < th["min_diversification"]:
        warnings.append({"type": "LOW_DIVERSIFICATION", "severity": "INFO",
                         "message": f"Diversification score {div_score:.2f} below target {th['min_diversification']:.2f}",
                         "value": div_score, "threshold": th["min_diversification"]})
    # engine correlation spike
    for a in corr["engines"]:
        for c in corr["engines"]:
            if a < c and (corr["matrix"][a][c] or 0) > th["max_correlation"]:
                warnings.append({"type": "CORRELATION_SPIKE", "severity": "INFO",
                                 "message": f"{a}/{c} return correlation {corr['matrix'][a][c]:.2f}",
                                 "value": corr["matrix"][a][c], "threshold": th["max_correlation"]})
    for lf in liq_flagged:
        warnings.append({"type": "LIQUIDITY_WARNING", "severity": "WARN",
                         "message": f"{lf['symbol']} liquidity {lf['liquidity_cr']}₹Cr/day below floor",
                         "value": lf["liquidity_cr"], "threshold": th["min_liquidity_cr"]})

    return {
        "nav": round(nav, 2),
        "deployed": round(deployed, 2),
        "cash_pct": round((nav - deployed) / nav * 100, 2) if nav else 0.0,
        "by_sector": by_sector,
        "by_industry": by_industry,
        "by_market_cap": by_mcap,
        "by_theme": by_theme,
        "by_book": by_book,
        "holdings": holdings,
        "top10": top10,
        "top10_pct": top10_pct,
        "largest_holding": single_max,
        "hhi": hhi,
        "effective_holdings": eff_holdings,
        "diversification_score": div_score,
        "portfolio_beta": round(beta, 3),
        "liquidity_coverage_pct": round(liq_known * 100, 1),
        "heatmap": heatmap,
        "correlation": corr,
        "warnings": warnings,
        "thresholds": th,
    }
