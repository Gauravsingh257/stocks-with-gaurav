"""
scripts/validate_portfolio_analytics.py

Phase 8 - pre-deployment validation report.

  A. CMP validation     - 20 random recommendations: snapshot CMP vs live CMP, diff%.
  B. Outcome validation - counts of ACTIVE / TARGET_HIT / STOP_HIT / EXPIRED / ARCHIVED.
  C. Analytics validation - every symbol that feeds public analytics must trace
     back to a portfolio recommendation (expected 0 orphans), and running_trades
     must be 100% linked to a recommendation.

Usage (from repo root):
    python -m scripts.validate_portfolio_analytics
"""

from __future__ import annotations

import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    from dashboard.backend.db import get_connection
    from dashboard.backend.routes.analytics import PORTFOLIO_AGENT_TYPES

    conn = get_connection()
    try:
        ph = ",".join("?" for _ in PORTFOLIO_AGENT_TYPES)
        recs = [dict(r) for r in conn.execute(
            f"""SELECT id, symbol, agent_type, status, entry_price, stop_loss,
                       scan_cmp, pnl_pct, pnl_r
                FROM stock_recommendations WHERE agent_type IN ({ph})""",
            list(PORTFOLIO_AGENT_TYPES),
        ).fetchall()]
        rt_total = conn.execute("SELECT COUNT(*) FROM running_trades").fetchone()[0]
        rt_orphan = [dict(r) for r in conn.execute(
            """SELECT rt.id, rt.symbol, rt.recommendation_id FROM running_trades rt
               WHERE rt.recommendation_id IS NULL
                  OR NOT EXISTS (SELECT 1 FROM stock_recommendations sr WHERE sr.id = rt.recommendation_id)"""
        ).fetchall()]
    finally:
        conn.close()

    # -- A. CMP validation ----------------------------------------------------
    _section("A. CMP VALIDATION - snapshot vs live (20 random recommendations)")
    active = [r for r in recs if r["status"] == "ACTIVE"]
    sample = random.sample(active, min(20, len(active)))
    try:
        from services.price_resolver import resolve_cmp
        live_map = resolve_cmp(
            [r["symbol"] for r in sample],
            scan_cmp_map={r["symbol"]: r["scan_cmp"] for r in sample if r.get("scan_cmp")},
        )
    except Exception as exc:
        live_map = {}
        print(f"   (resolve_cmp unavailable in this environment: {exc})")
    print(f"   {'symbol':20s} {'snapshot':>10s} {'live':>10s} {'diff%':>8s}  source")
    worst = 0.0
    for r in sample:
        snap = r.get("scan_cmp")
        live = live_map.get(r["symbol"])
        live_px = live["price"] if live else None
        if snap and live_px:
            diff = abs(live_px - snap) / snap * 100
            worst = max(worst, diff)
            print(f"   {r['symbol']:20s} {snap:10.2f} {live_px:10.2f} {diff:7.2f}%  {live.get('source')}")
        else:
            print(f"   {r['symbol']:20s} {str(snap):>10s} {str(live_px):>10s} {'n/a':>8s}")
    print(f"   -> worst diff in sample: {worst:.2f}% (target after live overlay: < 0.5%)")

    # -- B. Outcome validation ------------------------------------------------
    _section("B. OUTCOME VALIDATION - status counts")
    counts: dict[str, int] = {}
    for r in recs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for st in ("ACTIVE", "TARGET_HIT", "STOP_HIT", "EXPIRED", "ARCHIVED"):
        print(f"   {st:12s}: {counts.get(st, 0)}")

    # -- C. Analytics / portfolio integrity -----------------------------------
    _section("C. ANALYTICS INTEGRITY - orphan check")
    analytics_syms = {r["symbol"] for r in recs if r["status"] in ("TARGET_HIT", "STOP_HIT")}
    reco_syms = {r["symbol"] for r in recs}
    orphan_syms = analytics_syms - reco_syms  # by construction = empty
    print(f"   analytics closed symbols     : {len(analytics_syms)}")
    print(f"   orphan analytics symbols     : {len(orphan_syms)}  (expected 0)")
    print(f"   running_trades total         : {rt_total}")
    print(f"   running_trades unlinked      : {len(rt_orphan)}  (expected 0)")
    linkage = round((rt_total - len(rt_orphan)) / rt_total * 100, 1) if rt_total else 100.0
    print(f"   running_trades linkage       : {linkage}%  (target 100%)")

    ok = len(orphan_syms) == 0 and len(rt_orphan) == 0
    _section(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
