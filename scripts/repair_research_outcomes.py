"""
scripts/repair_research_outcomes.py

Phase 7 - one-time data repair.

  1. Ensure outcome-tracker P&L columns exist (pnl_pct, pnl_r) via migration.
  2. Evaluate EVERY currently-ACTIVE SWING/LONGTERM recommendation against live
     CMP and flip ACTIVE -> TARGET_HIT / STOP_HIT where SL/target is already
     breached (force=True so it runs regardless of market hours).
  3. Rebuild the swing/longterm Redis snapshots so the site reflects corrected
     statuses immediately.

Idempotent: re-running only affects rows still ACTIVE. Safe to run repeatedly.

Usage (from repo root):
    python -m scripts.repair_research_outcomes            # apply repair
    python -m scripts.repair_research_outcomes --dry-run  # report only, no writes
"""

from __future__ import annotations

import argparse
import os
import sys

# Bootstrap repo-root imports when run as a file.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair research recommendation outcomes.")
    parser.add_argument("--dry-run", action="store_true", help="evaluate only; do not write status changes")
    args = parser.parse_args()

    from dashboard.backend.db.schema import init_db, migrate_stock_recommendations
    from dashboard.backend.db import get_recommendations_for_tracking

    print("[repair] running schema migration (adds pnl_pct/pnl_r if missing)…")
    init_db()
    migrate_stock_recommendations()

    active = get_recommendations_for_tracking()
    print(f"[repair] {len(active)} ACTIVE SWING/LONGTERM recommendation(s) to evaluate")

    if args.dry_run:
        from services.price_resolver import resolve_cmp
        from services.research_outcome_tracker import _evaluate_outcome
        syms = [r["symbol"] for r in active]
        cmp_map = resolve_cmp(syms, scan_cmp_map={r["symbol"]: r.get("scan_cmp") for r in active if r.get("scan_cmp")})
        would_close = 0
        for r in active:
            live = cmp_map.get(r["symbol"])
            if not live or live.get("source") == "scan_snapshot":
                continue
            try:
                entry = float(r["entry_price"]); stop = float(r["stop_loss"] or 0)
                targets = [float(t) for t in (r.get("targets") or []) if t is not None]
            except (TypeError, ValueError):
                continue
            status, _ = _evaluate_outcome(entry, stop, targets, float(live["price"]))
            if status:
                would_close += 1
                print(f"   WOULD {status:10s} {r['symbol']:18s} cmp={live['price']:.2f} "
                      f"entry={entry:.2f} sl={stop:.2f} src={live.get('source')}")
        print(f"[repair][dry-run] would close {would_close} recommendation(s). No writes performed.")
        return 0

    from services.research_outcome_tracker import run_outcome_tracker_tick
    result = run_outcome_tracker_tick(force=True, rebuild_snapshot=True)
    print(f"[repair] done: {result}")
    for d in result.get("details", []):
        print(f"   {d['status']:10s} {d['symbol']:18s} exit={d['exit']:.2f} "
              f"pnl%={d['pnl_pct']} R={d['pnl_r']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
