"""
services/pil/reports.py
=======================
Automated reports (Parts 7 & 8).

  build_daily()   — an end-of-day structured report: regime, portfolio & engine
                    summary, entries/exits/pending, capital usage, sector
                    exposure, risk warnings, engine performance, cash, health.
  build_monthly() — a professional monthly report (structured + print-ready
                    standalone HTML with an inline SVG equity curve): performance,
                    engine comparison, risk, allocation, attribution, win/loss
                    distribution, auto-generated lessons + suggested improvements.

Both are pure aggregations over the PIL services (metrics/exposure/allocation/
health/scorecard) — they never change an engine. A compact text summary is
produced for Telegram delivery.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger("pil.reports")
_IST = timezone(timedelta(hours=5, minutes=30))


def _now() -> datetime:
    return datetime.now(_IST)


def _market_regime() -> str:
    """Best-effort latest market regime from regime_history (else n/a)."""
    try:
        from dashboard.backend.db.schema import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT regime FROM regime_history ORDER BY datetime(timestamp) DESC LIMIT 1"
            ).fetchone()
            return row["regime"] if row else "N/A"
        finally:
            conn.close()
    except Exception:
        return "N/A"


def _entries_exits(date_str: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Entries (entered today), exits (closed today), pending — across books."""
    from dashboard.backend.db.schema import get_connection
    entries, exits, pending = [], [], []
    conn = get_connection()
    try:
        for table, book_col in (("portfolio_positions", "horizon"), ("momentum_positions", None)):
            try:
                cols = "symbol, entry_price, current_price, entered_at" + (f", {book_col}" if book_col else "")
                for r in conn.execute(
                    f"SELECT {cols} FROM {table} WHERE status='ACTIVE' AND substr(entered_at,1,10)=?",
                    (date_str,),
                ).fetchall():
                    d = dict(r)
                    d["book"] = d.get(book_col) if book_col else "MOMENTUM"
                    entries.append(d)
                for r in conn.execute(
                    f"SELECT symbol, entry_price{',' + book_col if book_col else ''} FROM {table} WHERE status='PENDING'"
                ).fetchall():
                    d = dict(r); d["book"] = d.get(book_col) if book_col else "MOMENTUM"
                    pending.append(d)
            except Exception:
                continue
        for table, book_col in (("portfolio_journal", "horizon"), ("momentum_journal", None)):
            try:
                # Real exits only — a re-seed artifact would list the same
                # position as several separate exits in the daily report.
                # COALESCE keeps this working on momentum_journal, which has no
                # is_duplicate column (no re-seed path feeds it).
                dupe = " AND COALESCE(is_duplicate, 0) = 0" if table == "portfolio_journal" else ""
                for r in conn.execute(
                    f"SELECT symbol, profit_loss_pct, exit_reason{',' + book_col if book_col else ''} "
                    f"FROM {table} WHERE substr(closed_at,1,10)=?{dupe}",
                    (date_str,),
                ).fetchall():
                    d = dict(r); d["book"] = d.get(book_col) if book_col else "MOMENTUM"
                    exits.append(d)
            except Exception:
                continue
    finally:
        conn.close()
    return entries, exits, pending


def build_daily(period: str | None = None) -> dict[str, Any]:
    from services.pil import accounting, metrics, exposure, allocation, health
    date_str = period or _now().date().isoformat()
    books = accounting.reconstruct_all()
    met = metrics.metrics_all(books)
    exp = exposure.compute(books)
    alloc = allocation.compute(books)
    hlth = health.compute(books)
    entries, exits, pending = _entries_exits(date_str)

    combined = met["COMBINED"]
    return {
        "kind": "daily",
        "period": date_str,
        "generated_at": _now().isoformat(),
        "market_regime": _market_regime(),
        "portfolio_summary": {
            "portfolio_value": combined["portfolio_value"],
            "total_return_pct": combined["total_return_pct"],
            "today_return_pct": combined["today_return_pct"],
            "cash": combined["available_cash"],
            "invested": combined["invested_capital"],
            "open_positions": combined["open_positions"],
            "pending_positions": combined["pending_positions"],
        },
        "engine_summary": {
            b: {"portfolio_value": met[b]["portfolio_value"],
                "today_return_pct": met[b]["today_return_pct"],
                "total_return_pct": met[b]["total_return_pct"],
                "open_positions": met[b]["open_positions"]}
            for b in ("SWING", "LONGTERM", "MOMENTUM")
        },
        "new_entries": entries,
        "exits": exits,
        "pending": pending,
        "capital_usage": {b: {"invested": met[b]["invested_capital"], "cash": met[b]["available_cash"]}
                          for b in ("SWING", "LONGTERM", "MOMENTUM")},
        "sector_exposure": exp["by_sector"][:8],
        "risk_warnings": exp["warnings"] + alloc["warnings"],
        "engine_performance": {b: {"hit_rate_pct": met[b]["hit_rate_pct"],
                                   "expectancy": met[b]["expectancy"],
                                   "profit_factor": met[b]["profit_factor"]}
                               for b in ("SWING", "LONGTERM", "MOMENTUM")},
        "cash_position": {"combined_cash": combined["available_cash"],
                          "cash_pct": exp["cash_pct"]},
        "allocation": {"rebalance_needed": alloc["rebalance_needed"],
                       "rows": alloc["rows"]},
        "portfolio_health": {b: {"overall": hlth[b]["overall"], "status": hlth[b]["status"]}
                             for b in ("SWING", "LONGTERM", "MOMENTUM", "COMBINED")},
        "top_holdings": exp["top10"][:5],
    }


def build_monthly(period: str | None = None) -> dict[str, Any]:
    from services.pil import accounting, metrics, exposure, allocation, health, scorecard, analytics
    ym = period or _now().strftime("%Y-%m")
    books = accounting.reconstruct_all()
    met = metrics.metrics_all(books)
    exp = exposure.compute(books)
    alloc = allocation.compute(books)
    hlth = health.compute(books)
    an = analytics.compute(books)
    cards = scorecard.generate_all("monthly", ym)

    # win/loss distribution from combined closed trades
    closed = books.get("COMBINED", {}).get("closed_trades", [])
    wins = [t["pnl_pct"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl_pct"] for t in closed if t["pnl"] <= 0]
    dist_buckets = _distribution([t["pnl_pct"] for t in closed])

    report = {
        "kind": "monthly",
        "period": ym,
        "generated_at": _now().isoformat(),
        "performance": {b: met[b] for b in ("SWING", "LONGTERM", "MOMENTUM", "COMBINED")},
        "engine_comparison": an["per_engine"],
        "contribution": an["contribution"],
        "correlation": an["correlation"],
        "diversification": an["diversification"],
        "risk": {"warnings": exp["warnings"], "beta": exp["portfolio_beta"],
                 "diversification_score": exp["diversification_score"], "hhi": exp["hhi"]},
        "sector_exposure": exp["by_sector"],
        "allocation": alloc,
        "attribution": {b: cards[b].get("attribution") for b in ("SWING", "LONGTERM", "MOMENTUM")
                        if "attribution" in cards.get(b, {})},
        "win_distribution": {"wins": len(wins), "losses": len(losses), "buckets": dist_buckets},
        "health": hlth,
        "lessons": _lessons(met, exp, alloc, an),
        "suggested_improvements": _improvements(met, exp, alloc, an),
        "equity_curve": books.get("COMBINED", {}).get("equity_curve", []),
    }
    report["html"] = render_monthly_html(report)
    return report


def _distribution(pcts: list[float]) -> list[dict]:
    edges = [(-999, -10), (-10, -5), (-5, 0), (0, 5), (5, 10), (10, 20), (20, 999)]
    labels = ["<-10%", "-10..-5%", "-5..0%", "0..5%", "5..10%", "10..20%", ">20%"]
    out = []
    for (lo, hi), lab in zip(edges, labels):
        out.append({"bucket": lab, "count": sum(1 for p in pcts if lo <= p < hi)})
    return out


def _lessons(met, exp, alloc, an) -> list[str]:
    out = []
    top = an["contribution"].get("top_contributor")
    if top:
        out.append(f"{top} was the largest return contributor this month.")
    worst_dd = min(("SWING", "LONGTERM", "MOMENTUM"), key=lambda b: met[b]["max_drawdown_pct"])
    out.append(f"{worst_dd} had the deepest drawdown ({met[worst_dd]['max_drawdown_pct']}%).")
    if exp["warnings"]:
        out.append(f"{len(exp['warnings'])} concentration/risk threshold(s) were breached.")
    if an["diversification"]["diversification_benefit_pct"] > 0:
        out.append(f"The engine mix cut volatility by {an['diversification']['diversification_benefit_pct']:.1f} pts vs standalone.")
    return out


def _improvements(met, exp, alloc, an) -> list[str]:
    out = []
    if alloc["rebalance_needed"]:
        out.append("Allocation has drifted beyond target — consider rebalancing capital toward target weights.")
    for b in ("SWING", "LONGTERM", "MOMENTUM"):
        if met[b]["profit_factor"] and met[b]["profit_factor"] < 1.0:
            out.append(f"{b} profit factor < 1.0 — review recent losers for a common failure mode.")
    opt = an["optimal"].get("max_sharpe")
    if opt:
        w = opt["weights"]
        out.append(f"Max-Sharpe historical mix ≈ Swing {w['SWING']*100:.0f}% / LT {w['LONGTERM']*100:.0f}% / Momentum {w['MOMENTUM']*100:.0f}% (insight only).")
    if not out:
        out.append("No structural issues detected — maintain current configuration.")
    return out


# ── HTML rendering (print-ready, standalone) ─────────────────────────────────

def _svg_equity(curve: list[dict], w: int = 720, h: int = 200) -> str:
    if len(curve) < 2:
        return ""
    vals = [p["value"] for p in curve]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    n = len(vals)
    pts = " ".join(
        f"{i / (n - 1) * w:.1f},{h - (v - lo) / rng * h:.1f}" for i, v in enumerate(vals)
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" '
        f'style="background:#0b1220;border-radius:8px">'
        f'<polyline fill="none" stroke="#34d399" stroke-width="2" points="{pts}"/></svg>'
    )


def _row(cells: list[str], header: bool = False) -> str:
    tag = "th" if header else "td"
    style = "padding:6px 10px;border-bottom:1px solid #1e293b;text-align:right"
    first = "padding:6px 10px;border-bottom:1px solid #1e293b;text-align:left"
    tds = [f'<{tag} style="{first if i == 0 else style}">{html.escape(str(c))}</{tag}>'
           for i, c in enumerate(cells)]
    return "<tr>" + "".join(tds) + "</tr>"


def render_monthly_html(r: dict) -> str:
    perf = r["performance"]
    books = ["SWING", "LONGTERM", "MOMENTUM", "COMBINED"]
    labels = {"SWING": "Swing", "LONGTERM": "Long-Term", "MOMENTUM": "Momentum", "COMBINED": "Combined"}

    perf_rows = _row(["Metric", *[labels[b] for b in books]], header=True)
    for key, lab, fmt in [
        ("portfolio_value", "Portfolio Value", "inr"), ("total_return_pct", "Total Return", "pct"),
        ("cagr_pct", "CAGR", "pct"), ("max_drawdown_pct", "Max Drawdown", "pct"),
        ("sharpe", "Sharpe", "num"), ("sortino", "Sortino", "num"),
        ("hit_rate_pct", "Hit Rate", "pct"), ("profit_factor", "Profit Factor", "num"),
    ]:
        cells = [lab]
        for b in books:
            v = perf[b].get(key, 0)
            if fmt == "inr":
                cells.append(f"₹{v:,.0f}")
            elif fmt == "pct":
                cells.append(f"{v:+.2f}%")
            else:
                cells.append(f"{v:.2f}")
        perf_rows += _row(cells)

    health_badges = "".join(
        f'<span style="display:inline-block;margin:2px 6px;padding:3px 10px;border-radius:12px;'
        f'background:{"#052e16" if r["health"][b]["status"]=="GREEN" else "#422006" if r["health"][b]["status"]=="YELLOW" else "#450a0a"};'
        f'color:{"#4ade80" if r["health"][b]["status"]=="GREEN" else "#fbbf24" if r["health"][b]["status"]=="YELLOW" else "#f87171"}">'
        f'{labels.get(b, b)}: {r["health"][b]["overall"]} {r["health"][b]["status"]}</span>'
        for b in books
    )

    sector_rows = _row(["Sector", "Exposure %", "Names"], header=True)
    for s in r["sector_exposure"][:10]:
        sector_rows += _row([s["name"], f'{s["pct"]:.1f}%', s["count"]])

    dist_rows = _row(["P&L Bucket", "Trades"], header=True)
    for d in r["win_distribution"]["buckets"]:
        dist_rows += _row([d["bucket"], d["count"]])

    lessons = "".join(f"<li>{html.escape(x)}</li>" for x in r["lessons"])
    improvements = "".join(f"<li>{html.escape(x)}</li>" for x in r["suggested_improvements"])

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Portfolio Intelligence — Monthly Report {html.escape(r['period'])}</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b1220;color:#e2e8f0;margin:0;padding:32px;max-width:900px;margin:0 auto}}
  h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;margin:28px 0 8px;color:#67e8f9;border-bottom:1px solid #1e293b;padding-bottom:4px}}
  .sub{{color:#64748b;font-size:12px;margin-bottom:16px}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}}
  th{{color:#94a3b8;font-weight:600;font-size:11px;text-transform:uppercase}}
  ul{{font-size:13px;line-height:1.7;color:#cbd5e1}} @media print{{body{{background:#fff;color:#000}}}}
</style></head><body>
<h1>Portfolio Intelligence — Monthly Report</h1>
<div class="sub">{html.escape(r['period'])} · generated {html.escape(r['generated_at'][:16])} IST · multi-engine PMS</div>

<h2>Combined Equity Curve</h2>
{_svg_equity(r.get('equity_curve', []))}

<h2>Performance</h2>
<table>{perf_rows}</table>

<h2>Portfolio Health</h2>
<div>{health_badges}</div>

<h2>Sector Exposure</h2>
<table>{sector_rows}</table>

<h2>Win / Loss Distribution</h2>
<div class="sub">{r['win_distribution']['wins']} wins · {r['win_distribution']['losses']} losses</div>
<table>{dist_rows}</table>

<h2>Lessons Learned</h2>
<ul>{lessons}</ul>

<h2>Suggested Improvements</h2>
<ul>{improvements}</ul>

<div class="sub" style="margin-top:32px">Portfolio Intelligence Layer · observational PMS · does not influence engine decisions</div>
</body></html>"""


def summary_text(report: dict) -> str:
    """Compact Telegram summary for a daily/monthly report."""
    if report["kind"] == "daily":
        ps = report["portfolio_summary"]
        h = report["portfolio_health"]["COMBINED"]
        lines = [
            f"📊 <b>PIL Daily</b> — {report['period']}",
            f"Regime: {report['market_regime']}",
            f"Value: ₹{ps['portfolio_value']:,.0f} ({ps['today_return_pct']:+.2f}% today, {ps['total_return_pct']:+.2f}% total)",
            f"Open: {ps['open_positions']} · Pending: {ps['pending_positions']} · Cash: ₹{ps['cash']:,.0f}",
            f"Entries: {len(report['new_entries'])} · Exits: {len(report['exits'])}",
            f"Health: {h['overall']} {h['status']}",
        ]
        if report["risk_warnings"]:
            lines.append(f"⚠️ {len(report['risk_warnings'])} risk warning(s)")
        return "\n".join(lines)
    # monthly
    c = report["performance"]["COMBINED"]
    return (f"📈 <b>PIL Monthly</b> — {report['period']}\n"
            f"Return: {c['total_return_pct']:+.2f}% · Sharpe {c['sharpe']} · MaxDD {c['max_drawdown_pct']}%\n"
            f"Top contributor: {report['contribution'].get('top_contributor', 'n/a')}")


# ── generate + persist + notify ──────────────────────────────────────────────

def generate_and_store(kind: str = "daily", period: str | None = None,
                       notify: bool = True) -> dict[str, Any]:
    from dashboard.backend.db import pil as pildb
    report = build_monthly(period) if kind == "monthly" else build_daily(period)
    html_doc = report.get("html")
    pildb.save_report(kind, report["period"], {k: v for k, v in report.items() if k != "html"}, html_doc)
    if notify:
        _notify(report)
    return report


def _notify(report: dict) -> None:
    from services.pil import config as pil_config
    if not pil_config.telegram_enabled():
        return
    from services.pil.notify import send_telegram
    send_telegram(summary_text(report))
