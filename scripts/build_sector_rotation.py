"""
scripts/build_sector_rotation.py — compute sector rotation and store it (Phase 1).

    python -m scripts.build_sector_rotation --backfill   # one-off: every date in the snapshot
    python -m scripts.build_sector_rotation              # daily: only dates after the latest stored
    python -m scripts.build_sector_rotation --dry-run    # compute + print summary, write nothing

Inputs (all already published, no provider calls, no Yahoo):
  - `ohlc:universe:*`  Kite daily bars for the current universe (scanner worker)
  - `ohlc:index:*`     Kite NIFTY 50 / 500 + sector indices (scanner worker)
  - `stock_universe`   sector + sector_source per symbol (weekly refresh)

Membership = current-universe equities: symbols in the price snapshot (which the
scanner builds from the current universe) whose stock_universe row is an EQUITY
with a resolved sector. Orphan rows for symbols that left the universe are never
read — the snapshot does not contain them.

READ-ONLY with respect to every strategy. RUN OUT OF BAND (scheduled job or
`railway ssh`); never from a request handler or a FastAPI startup hook.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("build_sector_rotation")


def load_members() -> dict[str, tuple[str, str]]:
    from dashboard.backend.db.schema import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, sector, sector_source FROM stock_universe WHERE instrument = 'EQUITY'"
        ).fetchall()
    finally:
        conn.close()
    return {r["symbol"].upper(): (r["sector"] or "", r["sector_source"] or "") for r in rows}


def select_rows(rows: list[dict], *, latest_d: str | None, latest_w: str | None) -> list[dict]:
    """Incremental policy: daily rows after the latest stored date; weekly rows from
    the latest stored week onward (that week may have been partial)."""
    def keep(r: dict) -> bool:
        if r["timeframe"] == "D":
            return latest_d is None or r["date"] > latest_d
        return latest_w is None or r["date"] >= latest_w

    return [r for r in rows if keep(r)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from services.index_ohlc import index_snapshot_status, load_index_ohlc
    from services.sector_rotation_engine import compute_sector_rotation
    from services.universe_ohlc import load_universe_ohlc, snapshot_status

    started = time.time()
    u_status, i_status = snapshot_status(), index_snapshot_status()
    if not u_status.get("usable") or not i_status.get("usable"):
        print(json.dumps({"written": 0, "reason": "snapshot_unusable",
                          "universe": {k: u_status.get(k) for k in ("usable", "day", "reason")},
                          "index": {k: i_status.get(k) for k in ("usable", "day", "reason")}}))
        return 2

    stock_bars = load_universe_ohlc()
    index_bars = load_index_ohlc()
    members = load_members()
    in_snapshot = {s: members[s] for s in stock_bars if s in members}
    result = compute_sector_rotation(stock_bars, index_bars, in_snapshot)
    rows, meta = result["rows"], result["meta"]

    summary = {"meta": {k: v for k, v in meta.items() if k != "index_filled_days"},
               "snapshot_symbols": len(stock_bars), "equity_members_in_snapshot": len(in_snapshot),
               "compute_sec": round(time.time() - started, 1)}

    if args.backfill:
        chosen, mode = rows, "backfill"
    else:
        from dashboard.backend.db.sector_rotation import latest_date

        chosen, mode = select_rows(rows, latest_d=latest_date("D"), latest_w=latest_date("W")), "live"
    summary.update({"mode": mode, "rows_selected": len(chosen)})

    if args.dry_run:
        summary["dry_run"] = True
    else:
        from dashboard.backend.db.sector_rotation import summary as table_summary
        from dashboard.backend.db.sector_rotation import upsert_rows

        summary["written"] = upsert_rows(chosen, source_mode=mode)
        summary["table"] = table_summary()
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
