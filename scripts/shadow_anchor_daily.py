#!/usr/bin/env python3
"""
scripts/shadow_anchor_daily.py
──────────────────────────────
Daily shadow recorder for ENTRY_ANCHOR_MAX_GAP_PCT=10 (Anchor10), action #3.

Run ONCE per trading session (manually or via the scheduler) for 3-5 sessions.
Each run:
  1. pulls today's LIVE final book from the backend discovery API,
  2. computes the Config-A (current) vs Config-B (Anchor10) summary metrics,
  3. appends one dated row per config to signal_history/shadow_anchor10.csv
     (idempotent — re-running the same date overwrites that date's rows),
  4. prints today's A-vs-B line and the full running multi-session table.

It NEVER enables the flag and never touches the engine — it only reads the
served book and records what Anchor10 *would* have produced. After 3-5 stable
sessions, feed the CSV to the go/no-go check in
docs/PHASE2_ENABLEMENT_CRITERIA.md.

Usage:
  python scripts/shadow_anchor_daily.py                 # pull live API
  python scripts/shadow_anchor_daily.py --path _disc.json   # from a saved payload
  python scripts/shadow_anchor_daily.py --show          # just print the table
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))           # for sibling import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from shadow_diff_phase2 import Setup, _row_to_setup, levels_for, metrics  # noqa: E402

CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "signal_history", "shadow_anchor10.csv",
)
DEFAULT_BACKEND = os.getenv("DASHBOARD_URL") or os.getenv("BACKEND_URL") \
    or "https://web-production-2781a.up.railway.app"
ANCHOR_GAP = float(os.getenv("SHADOW_ANCHOR_GAP_PCT", "10"))

FIELDS = ["date", "config", "scan_id", "count", "actionable", "actionable_pct",
          "avg_dist_from_entry_pct", "median_remaining_rr", "avg_remaining_rr",
          "extended_gt10pct", "extended_pct", "quality_score", "recorded_at"]


def _fetch_live() -> tuple[list[Setup], str]:
    url = DEFAULT_BACKEND.rstrip("/") + "/api/research/discovery"
    with urllib.request.urlopen(url, timeout=30) as resp:        # noqa: S310 (trusted host)
        payload = json.load(resp)
    rows = payload.get("final_trades") or payload.get("items") or []
    setups = [s for r in rows if (s := _row_to_setup(r))]
    return setups, str(payload.get("scan_id") or payload.get("generated_at") or "?")


def _load_json(path: str) -> tuple[list[Setup], str]:
    payload = json.load(open(path, encoding="utf-8"))
    rows = payload.get("final_trades") or payload.get("items") or []
    setups = [s for r in rows if (s := _row_to_setup(r))]
    return setups, str(payload.get("scan_id") or payload.get("generated_at") or path)


def _read_rows() -> list[dict]:
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_rows(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def _metric_row(date: str, config: str, scan_id: str, m: dict) -> dict:
    return {
        "date": date, "config": config, "scan_id": scan_id,
        "count": m.get("count", 0), "actionable": m.get("actionable", 0),
        "actionable_pct": m.get("actionable_pct", 0),
        "avg_dist_from_entry_pct": m.get("avg_dist_from_entry_pct", 0),
        "median_remaining_rr": m.get("median_remaining_rr", ""),
        "avg_remaining_rr": m.get("avg_remaining_rr", ""),
        "extended_gt10pct": m.get("extended_gt10pct", 0),
        "extended_pct": m.get("extended_pct", 0),
        "quality_score": m.get("quality_score", 0),
        "recorded_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("  (no sessions recorded yet)")
        return
    hdr = (f"{'date':<12}{'cfg':<10}{'count':>7}{'action':>8}{'act%':>7}"
           f"{'avgDist%':>10}{'medRemRR':>10}{'ext>10':>8}{'ext%':>7}{'quality':>9}")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        print(f"{r['date']:<12}{r['config']:<10}{r['count']:>7}{r['actionable']:>8}"
              f"{r['actionable_pct']:>7}{r['avg_dist_from_entry_pct']:>10}"
              f"{str(r['median_remaining_rr']):>10}{r['extended_gt10pct']:>8}"
              f"{r['extended_pct']:>7}{r['quality_score']:>9}")


def main():
    ap = argparse.ArgumentParser(description="Daily Anchor10 shadow recorder (read-only)")
    ap.add_argument("--path", help="record from a saved discovery JSON instead of the live API")
    ap.add_argument("--date", help="override the session date (YYYY-MM-DD)")
    ap.add_argument("--show", action="store_true", help="print the running table and exit")
    args = ap.parse_args()

    existing = _read_rows()
    if args.show:
        _print_table(existing)
        return

    setups, scan_id = _load_json(args.path) if args.path else _fetch_live()
    date = args.date or _dt.date.today().isoformat()
    if not setups:
        print(f"[{date}] no final setups returned (scan_id={scan_id}); nothing recorded.")
        _print_table(existing)
        return

    m_a = metrics(levels_for(setups, "current", ANCHOR_GAP))
    m_b = metrics(levels_for(setups, "anchor", ANCHOR_GAP))

    # idempotent: drop any prior rows for this date, then append A and B
    kept = [r for r in existing if r.get("date") != date]
    kept.append(_metric_row(date, "A_current", scan_id, m_a))
    kept.append(_metric_row(date, f"B_anchor{int(ANCHOR_GAP)}", scan_id, m_b))
    kept.sort(key=lambda r: (r["date"], r["config"]))
    _write_rows(kept)

    print(f"\n[{date}] scan={scan_id}  (Anchor gap={ANCHOR_GAP}%)")
    print(f"  A current : count={m_a['count']:>3}  actionable={m_a['actionable']:>3} "
          f"({m_a['actionable_pct']}%)  avgDist={m_a['avg_dist_from_entry_pct']}%  "
          f"medRemRR={m_a['median_remaining_rr']}  extended={m_a['extended_gt10pct']}")
    print(f"  B anchor10: count={m_b['count']:>3}  actionable={m_b['actionable']:>3} "
          f"({m_b['actionable_pct']}%)  avgDist={m_b['avg_dist_from_entry_pct']}%  "
          f"medRemRR={m_b['median_remaining_rr']}  extended={m_b['extended_gt10pct']}")
    # count-collapse guard signal
    if m_a["count"]:
        drop = (m_a["count"] - m_b["count"]) / m_a["count"] * 100.0
        flag = "OK" if drop <= 20 else "WARN: signal count dropped >20%"
        print(f"  count delta under Anchor10: {m_b['count']-m_a['count']:+d} ({-drop:+.0f}%)  [{flag}]")

    print(f"\nRunning sessions ({CSV_PATH}):")
    _print_table(kept)
    print("\nAfter 3-5 stable sessions, apply docs/PHASE2_ENABLEMENT_CRITERIA.md.")


if __name__ == "__main__":
    main()
