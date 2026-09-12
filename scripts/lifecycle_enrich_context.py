#!/usr/bin/env python
"""
scripts/lifecycle_enrich_context.py — backfill sector + entry regime into the
lifecycle ledger's `context_json`.

The ONLY data-plumbing gap the 2026-09-13 audit hit: `sector` is not stored on
position rows and `context_json` carried only reasoning/horizon/confidence for
the SWING/LONGTERM path, so sector and regime attribution were impossible.

Both inputs already exist — nothing new is computed or collected:
  • sector — `services.portfolio_risk.get_sector`, a pure dict lookup (the same
    one the admission gate uses via `admission_gate.sector_for`). Deterministic,
    so it backfills historical rows correctly.
  • regime — `regime_history` (schema TABLE 4) is timestamped, so the regime in
    force when a position was created is recoverable. Where no row predates the
    position the field is left ABSENT rather than guessed — an empty field is
    honest, an invented one is not.

Runs entirely outside the trading path: it reads the ledger and rewrites one
JSON column. It never touches a position, a flag, an engine, or any selection,
ranking, sizing or exit logic. Idempotent — re-running changes nothing unless a
field was missing. Additive — existing context_json keys are preserved.

Usage:
    python -m scripts.lifecycle_enrich_context --dry-run    # report only
    python -m scripts.lifecycle_enrich_context              # write
    python -m scripts.lifecycle_enrich_context --limit 500
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from bisect import bisect_right

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sector(symbol: str) -> str | None:
    try:
        from services.portfolio_risk import get_sector
        s = get_sector(str(symbol or "").replace("NSE:", ""))
        return s or None
    except Exception:
        return None


def _regime_index(conn) -> list[tuple[str, str]]:
    """(timestamp, regime) ascending. Empty when the table has no rows yet."""
    try:
        rows = conn.execute(
            "SELECT timestamp, regime FROM regime_history ORDER BY timestamp ASC"
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows if r[0] and r[1]]
    except Exception:
        return []


def _regime_at(idx: list[tuple[str, str]], when: str | None) -> str | None:
    """Regime in force at `when` — the last logged change at or before it.
    Returns None when the log does not reach back that far, so a trade older
    than the regime log is left unattributed instead of mislabelled."""
    if not idx or not when:
        return None
    pos = bisect_right([t for t, _ in idx], str(when))
    return idx[pos - 1][1] if pos > 0 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0 = all rows")
    args = ap.parse_args()

    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        idx = _regime_index(conn)
        print(f"regime_history rows: {len(idx)}"
              + (f"  ({idx[0][0][:10]} .. {idx[-1][0][:10]})" if idx else "  — regime cannot be backfilled"))

        q = ("SELECT uuid, symbol, context_json, created_at, idea_at, entry_fill_at "
             "FROM trade_lifecycle ORDER BY created_at DESC")
        if args.limit:
            q += f" LIMIT {int(args.limit)}"
        rows = conn.execute(q).fetchall()
        print(f"ledger rows: {len(rows)}")

        stats = {"sector_added": 0, "regime_added": 0, "already": 0, "no_sector": 0, "no_regime": 0}
        writes: list[tuple[str, str]] = []

        for r in rows:
            d = dict(r)
            try:
                c = json.loads(d.get("context_json") or "{}")
                if not isinstance(c, dict):
                    c = {}
            except Exception:
                c = {}
            changed = False

            if not c.get("sector"):
                s = _sector(d.get("symbol"))
                if s:
                    c["sector"] = s
                    stats["sector_added"] += 1
                    changed = True
                else:
                    stats["no_sector"] += 1

            if not c.get("regime_at_entry"):
                when = d.get("entry_fill_at") or d.get("created_at") or d.get("idea_at")
                g = _regime_at(idx, when)
                if g:
                    c["regime_at_entry"] = g
                    stats["regime_added"] += 1
                    changed = True
                else:
                    stats["no_regime"] += 1

            if changed:
                writes.append((json.dumps(c, default=str), d["uuid"]))
            else:
                stats["already"] += 1

        print(f"\n  sector added   : {stats['sector_added']}   (unresolvable: {stats['no_sector']})")
        print(f"  regime added   : {stats['regime_added']}   (outside log range: {stats['no_regime']})")
        print(f"  already complete: {stats['already']}")
        print(f"  rows to write  : {len(writes)}")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0
        if writes:
            conn.executemany("UPDATE trade_lifecycle SET context_json = ? WHERE uuid = ?", writes)
            conn.commit()
            print(f"\nwrote {len(writes)} rows.")
        else:
            print("\nnothing to write.")
        return 0
    finally:
        with contextlib.suppress(Exception):
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
