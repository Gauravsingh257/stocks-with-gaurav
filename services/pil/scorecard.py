"""
services/pil/scorecard.py
=========================
Engine scorecards (Part 3). For each engine (and combined) generate a daily or
monthly scorecard covering the candidate→trade funnel, realised performance,
attribution (best/worst sector / entry-model / regime), notable trades, and
composite quality scores.

Honest by construction: it computes what each engine's data actually supports.
Momentum's journal carries entry_model / regime / sector / quality_score, so it
gets full attribution + ranking quality; the Swing/LT journal carries sector (via
reference_data) + confidence_score, so those get sector attribution + ranking
quality and `null` where a dimension isn't recorded. Nothing here changes an
engine — it only reads journals/positions and writes to pil_scorecards.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from statistics import mean, pstdev
from typing import Any

from services.pil import reference_data as ref

log = logging.getLogger("pil.scorecard")
_IST = timezone(timedelta(hours=5, minutes=30))


def _today() -> datetime:
    return datetime.now(_IST)


# ── raw loaders (read-only) ──────────────────────────────────────────────────

def _load_journal(book: str) -> list[dict]:
    book = book.upper()
    if book in ("SWING", "LONGTERM"):
        from dashboard.backend.db.portfolio import get_journal
        return get_journal(book, limit=100_000)
    if book == "MOMENTUM":
        from dashboard.backend.db.momentum_portfolio import get_journal
        return get_journal(limit=100_000)
    return []


def _status_counts(book: str) -> dict[str, int]:
    """Point-in-time position status counts (active/pending/expired)."""
    from dashboard.backend.db.schema import get_connection
    book = book.upper()
    table = "momentum_positions" if book == "MOMENTUM" else "portfolio_positions"
    where = "" if book == "MOMENTUM" else f"WHERE horizon = '{book}'"
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT status, COUNT(*) c FROM {table} {where} GROUP BY status"
        ).fetchall()
        return {r["status"]: r["c"] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def _in_period(ts: str | None, start: str, end: str) -> bool:
    if not ts:
        return False
    d = str(ts)[:10]
    return start <= d <= end


def _pnl_pct(j: dict) -> float:
    return float(j.get("profit_loss_pct") or 0.0)


def _attr(journal: list[dict], key: str, symbol_sector: bool = False) -> dict[str, dict]:
    """Attribution by a metadata key → per-group {n, hit_rate, avg_pnl_pct, total_pnl_pct}."""
    groups: dict[str, list[float]] = defaultdict(list)
    for j in journal:
        if symbol_sector:
            k = ref.get_sector(j.get("symbol", ""), j.get("sector"))
        else:
            k = j.get(key)
            if not k:
                continue
            k = str(k)
        groups[k].append(_pnl_pct(j))
    out = {}
    for k, v in groups.items():
        wins = [x for x in v if x > 0]
        out[k] = {
            "n": len(v),
            "hit_rate": round(len(wins) / len(v) * 100, 1),
            "avg_pnl_pct": round(mean(v), 2),
            "total_pnl_pct": round(sum(v), 2),
        }
    return out


def _best_worst(attr: dict[str, dict], min_n: int = 2) -> tuple[dict | None, dict | None]:
    eligible = [{"name": k, **v} for k, v in attr.items() if v["n"] >= min_n]
    if not eligible:
        eligible = [{"name": k, **v} for k, v in attr.items()]
    if not eligible:
        return None, None
    best = max(eligible, key=lambda x: x["avg_pnl_pct"])
    worst = min(eligible, key=lambda x: x["avg_pnl_pct"])
    return best, worst


def _ranking_quality(journal: list[dict]) -> float | None:
    """Pearson(entry conviction, realised P&L%). Positive => the engine ranked
    winners higher at entry. Uses quality_score (Momentum) or confidence_score
    (Swing/LT)."""
    xs, ys = [], []
    for j in journal:
        conv = j.get("quality_score")
        if conv is None:
            conv = j.get("confidence_score")
        if conv is None:
            continue
        xs.append(float(conv)); ys.append(_pnl_pct(j))
    n = len(xs)
    if n < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sx, sy = pstdev(xs), pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / n
    return round(cov / (sx * sy), 3)


def _engine_quality_score(hit_rate: float, profit_factor: float, expectancy_pct: float,
                          ranking_q: float | None) -> float:
    """0..100 composite of realised edge quality."""
    hr = min(hit_rate / 60.0, 1.0)                     # 60% hit -> full
    pf = min(max(profit_factor, 0) / 2.5, 1.0)         # PF 2.5 -> full
    exp = min(max(expectancy_pct, 0) / 5.0, 1.0)       # +5% avg -> full
    rq = ((ranking_q or 0) + 1) / 2                     # -1..1 -> 0..1
    score = 100 * (0.3 * hr + 0.3 * pf + 0.25 * exp + 0.15 * rq)
    return round(score, 1)


def generate(book: str, scope: str = "daily", period: str | None = None) -> dict[str, Any]:
    """Build one scorecard. scope 'daily' => period=YYYY-MM-DD, 'monthly' => YYYY-MM."""
    book = book.upper()
    now = _today()
    if scope == "monthly":
        period = period or now.strftime("%Y-%m")
        start, end = f"{period}-01", f"{period}-31"
    else:
        period = period or now.date().isoformat()
        start = end = period

    journal_all = _load_journal(book)
    journal = [j for j in journal_all if _in_period(j.get("closed_at"), start, end)]
    counts = _status_counts(book)

    from services.pil import accounting, metrics
    ledger = accounting.reconstruct(book) if book != "COMBINED" else accounting.combine(
        [accounting.reconstruct(b) for b in ("SWING", "LONGTERM", "MOMENTUM")])
    met = metrics.metrics_for_book(ledger)

    # funnel
    triggered = len([j for j in journal]) + int(counts.get("ACTIVE", 0))
    funnel = {
        "closed": len(journal),
        "active": int(counts.get("ACTIVE", 0)),
        "pending": int(counts.get("PENDING", 0)),
        "expired": int(counts.get("EXPIRED", 0)),
        "triggered_lifetime": len(journal_all) + int(counts.get("ACTIVE", 0)),
        "accepted_lifetime": len(journal_all) + int(counts.get("ACTIVE", 0))
        + int(counts.get("PENDING", 0)) + int(counts.get("EXPIRED", 0)),
    }

    # attribution
    by_sector = _attr(journal_all, "", symbol_sector=True)
    by_entry_model = _attr(journal_all, "entry_model")
    by_regime = _attr(journal_all, "regime")
    best_sec, worst_sec = _best_worst(by_sector)
    best_em, worst_em = _best_worst(by_entry_model)
    best_rg, worst_rg = _best_worst(by_regime)

    # notable trades (period)
    ranked = sorted(journal, key=lambda j: _pnl_pct(j), reverse=True)
    top_winners = [{"symbol": j["symbol"], "pnl_pct": _pnl_pct(j),
                    "exit_reason": j.get("exit_reason")} for j in ranked[:5] if _pnl_pct(j) > 0]
    top_losers = [{"symbol": j["symbol"], "pnl_pct": _pnl_pct(j),
                   "exit_reason": j.get("exit_reason")} for j in ranked[-5:] if _pnl_pct(j) < 0]

    # potential missed opportunity / avoided loss from EXPIRED armed ideas (geometry)
    missed, avoided = _expired_potentials(book)

    ranking_q = _ranking_quality(journal_all)
    eq_score = _engine_quality_score(met["hit_rate_pct"], met["profit_factor"],
                                     met["expectancy_pct"], ranking_q)

    # portfolio quality: health of current book (avg unrealised %, breadth)
    positions = ledger.get("positions", [])
    avg_unreal = mean([p["unrealized_pnl_pct"] for p in positions]) if positions else 0.0
    pq_score = round(min(100, max(0, 50 + avg_unreal * 3)), 1)

    return {
        "book": book, "scope": scope, "period": period,
        "generated_at": now.isoformat(),
        "funnel": funnel,
        "performance": {
            "closed_trades": met["closed_trades"],
            "hit_rate_pct": met["hit_rate_pct"],
            "expectancy": met["expectancy"],
            "expectancy_pct": met["expectancy_pct"],
            "profit_factor": met["profit_factor"],
            "avg_hold_days": met["avg_hold_days"],
            "realized_pnl": ledger.get("realized_pnl"),
        },
        "attribution": {
            "best_sector": best_sec, "worst_sector": worst_sec,
            "best_entry_model": best_em, "worst_entry_model": worst_em,
            "best_regime": best_rg, "worst_regime": worst_rg,
            "by_sector": by_sector,
        },
        "notable": {
            "top_winners": top_winners, "top_losers": top_losers,
            "largest_missed_opportunity": missed, "largest_avoided_loss": avoided,
        },
        "quality": {
            "engine_quality_score": eq_score,
            "portfolio_quality_score": pq_score,
            "ranking_quality": ranking_q,
            "replacement_efficiency": _replacement_efficiency(journal_all),
        },
    }


def _expired_potentials(book: str) -> tuple[dict | None, dict | None]:
    """From armed ideas that EXPIRED without triggering, derive the largest
    *potential* upside foregone (target/entry) and *risk avoided* (entry/stop).
    These are geometry-based estimates (no fabricated outcomes)."""
    from dashboard.backend.db.schema import get_connection
    book = book.upper()
    if book == "COMBINED":
        return None, None
    table = "momentum_positions" if book == "MOMENTUM" else "portfolio_positions"
    where = "status='EXPIRED'" if book == "MOMENTUM" else f"status='EXPIRED' AND horizon='{book}'"
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            f"SELECT symbol, entry_price, stop_loss, target_1 FROM {table} WHERE {where}"
        ).fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    missed = avoided = None
    best_up = best_dn = 0.0
    for r in rows:
        e = float(r.get("entry_price") or 0)
        t = float(r.get("target_1") or 0)
        s = float(r.get("stop_loss") or 0)
        if e > 0 and t > 0:
            up = (t - e) / e * 100
            if up > best_up:
                best_up = up
                missed = {"symbol": r["symbol"], "potential_upside_pct": round(up, 2)}
        if e > 0 and s > 0:
            dn = (e - s) / e * 100
            if dn > best_dn:
                best_dn = dn
                avoided = {"symbol": r["symbol"], "risk_avoided_pct": round(dn, 2)}
    return missed, avoided


def _replacement_efficiency(journal: list[dict]) -> float | None:
    """Of trades exited to make room for a better idea (exit_reason ~ REPLACE),
    what was their average P&L%? Negative avg => replacement culled laggards well.
    None if the engine doesn't record replacement exits."""
    repl = [_pnl_pct(j) for j in journal
            if "REPLACE" in str(j.get("exit_reason", "")).upper()]
    if not repl:
        return None
    return round(mean(repl), 2)


def generate_all(scope: str = "daily", period: str | None = None) -> dict[str, dict]:
    out = {}
    for b in ("SWING", "LONGTERM", "MOMENTUM", "COMBINED"):
        try:
            out[b] = generate(b, scope, period)
        except Exception as exc:
            log.error("[PIL] scorecard(%s) failed: %s", b, exc)
            out[b] = {"book": b, "scope": scope, "error": str(exc)}
    return out


def generate_and_store(scope: str = "daily", period: str | None = None) -> dict[str, dict]:
    """Generate all scorecards and persist them to pil_scorecards."""
    from dashboard.backend.db import pil as pildb
    cards = generate_all(scope, period)
    for b, card in cards.items():
        if "error" not in card:
            pildb.save_scorecard(scope, b, card["period"], card)
    return cards
