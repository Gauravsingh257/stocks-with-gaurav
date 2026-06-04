#!/usr/bin/env python3
"""
scripts/shadow_anchor_daily.py
──────────────────────────────
Manual CLI for the Anchor10 shadow programme (action #3).

In production this runs AUTOMATICALLY via the scheduler job
`anchor_shadow_record` (agents/runner.py, 09:20 IST Mon-Fri) — you do not need
to run it by hand. This CLI exists for ad-hoc recording / inspection and uses
the SAME core (services/anchor_shadow.py) and the SAME Redis store
(shadow:anchor10:sessions), so manual and scheduled sessions never diverge.

Each run:
  1. pulls the final book (live discovery API, Redis snapshot, or a saved JSON),
  2. records one deduped session to Redis via services.anchor_shadow,
  3. mirrors the session to signal_history/shadow_anchor10.csv (local history),
  4. prints today's A-vs-Anchor10 line and the C1–C5 criteria status.

Usage:
  python scripts/shadow_anchor_daily.py                 # live discovery API
  python scripts/shadow_anchor_daily.py --path _disc.json   # from a saved payload
  python scripts/shadow_anchor_daily.py --show          # criteria status only
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from services.anchor_shadow import (  # noqa: E402
    ANCHOR_GAP_DEFAULT, REDIS_SESSIONS_KEY, book_from_payload, evaluate_criteria,
    load_sessions, record_session,
)

CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "signal_history", "shadow_anchor10.csv",
)
DEFAULT_BACKEND = os.getenv("DASHBOARD_URL") or os.getenv("BACKEND_URL") \
    or "https://web-production-2781a.up.railway.app"

CSV_FIELDS = ["date", "config", "scan_id", "count", "actionable", "actionable_pct",
              "avg_dist_from_entry_pct", "median_remaining_rr", "avg_remaining_rr",
              "extended_gt10pct", "extended_pct", "quality_score", "recorded_at"]


def _redis_client():
    try:
        import redis  # type: ignore
        url = os.getenv("REDIS_URL", "").strip()
        return redis.from_url(url, decode_responses=True, socket_timeout=4) if url else None
    except Exception:
        return None


def _fetch_payload(path: str | None) -> dict:
    if path:
        return json.load(open(path, encoding="utf-8"))
    url = DEFAULT_BACKEND.rstrip("/") + "/api/research/discovery"
    with urllib.request.urlopen(url, timeout=30) as resp:        # noqa: S310 (trusted host)
        return json.load(resp)


def _mirror_csv(session: dict) -> None:
    """Append the session's A and B metric rows to the local CSV (deduped by date)."""
    rows: list[dict] = []
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") != session["date"]]
    now = _dt.datetime.now().isoformat(timespec="seconds")
    for cfg, key in (("A_current", "A_current"), (f"B_anchor{int(session['anchor_gap_pct'])}", "B_anchor")):
        m = session[key]
        rows.append({
            "date": session["date"], "config": cfg, "scan_id": session["scan_id"],
            "count": m["count"], "actionable": m["actionable"],
            "actionable_pct": m["actionable_pct"],
            "avg_dist_from_entry_pct": m["avg_dist_from_entry_pct"],
            "median_remaining_rr": m["median_remaining_rr"],
            "avg_remaining_rr": m["avg_remaining_rr"],
            "extended_gt10pct": m["extended_gt10pct"], "extended_pct": m["extended_pct"],
            "quality_score": m["quality_score"], "recorded_at": now,
        })
    rows.sort(key=lambda r: (r["date"], r["config"]))
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def _print_status(sessions: list[dict]) -> None:
    ev = evaluate_criteria(sessions)
    print(f"\nAnchor10 status: {ev['overall']}  "
          f"({ev['session_count']}/{ev['sessions_required']} sessions)")
    for k, v in ev["criteria"].items():
        print(f"  {'PASS' if v['pass'] else 'FAIL'}  {k:<20} — {v['rule']}")
    print(f"  → {ev['recommendation']}")
    if sessions:
        hdr = (f"\n{'date':<12}{'A act%':>8}{'B act%':>8}{'B avgDist%':>12}"
               f"{'B medRR':>9}{'cntDrop%':>10}{'breaches':>10}")
        print(hdr)
        for s in sessions:
            a, b = s["A_current"], s["B_anchor"]
            print(f"{s['date']:<12}{a['actionable_pct']:>8}{b['actionable_pct']:>8}"
                  f"{b['avg_dist_from_entry_pct']:>12}{str(b['median_remaining_rr']):>9}"
                  f"{s['count_drop_pct']:>10}{len(s['breaches']):>10}")


def main():
    ap = argparse.ArgumentParser(description="Anchor10 daily shadow recorder (read-only)")
    ap.add_argument("--path", help="record from a saved discovery JSON instead of the live API")
    ap.add_argument("--date", help="override the session date (YYYY-MM-DD)")
    ap.add_argument("--show", action="store_true", help="print criteria status and exit")
    args = ap.parse_args()

    cli = _redis_client()

    if args.show:
        _print_status(load_sessions(cli))
        return

    payload = _fetch_payload(args.path)
    setups = book_from_payload(payload)
    date = args.date or _dt.date.today().isoformat()
    scan_id = str(payload.get("scan_id") or payload.get("generated_at") or "?")
    if not setups:
        print(f"[{date}] no final setups (scan_id={scan_id}); nothing recorded.")
        _print_status(load_sessions(cli))
        return

    session = record_session(setups, date, scan_id, cli, ANCHOR_GAP_DEFAULT)
    _mirror_csv(session)

    a, b = session["A_current"], session["B_anchor"]
    print(f"\n[{date}] scan={scan_id}  (Anchor gap={session['anchor_gap_pct']}%)  "
          f"redis={'on' if cli else 'OFF (csv only)'}")
    print(f"  A current : count={a['count']:>3}  actionable={a['actionable']:>3} "
          f"({a['actionable_pct']}%)  avgDist={a['avg_dist_from_entry_pct']}%  "
          f"medRemRR={a['median_remaining_rr']}  extended={a['extended_gt10pct']}")
    print(f"  B anchor10: count={b['count']:>3}  actionable={b['actionable']:>3} "
          f"({b['actionable_pct']}%)  avgDist={b['avg_dist_from_entry_pct']}%  "
          f"medRemRR={b['median_remaining_rr']}  extended={b['extended_gt10pct']}")
    print(f"  count delta under Anchor10: {b['count']-a['count']:+d} "
          f"({-session['count_drop_pct']:+.0f}%)  "
          f"breaches: {session['breaches'] or 'none'}")

    _print_status(load_sessions(cli) or [session])


if __name__ == "__main__":
    main()
