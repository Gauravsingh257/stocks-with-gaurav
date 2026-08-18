"""
scripts/admission_shadow_report.py
==================================
Human-readable Shadow Report for the portfolio admission gate (Step 3).

Reads the decisions the gate recorded into Redis
(`admission_gate:decisions:{YYYY-MM-DD}`, 30-day TTL) and prints what policy
WOULD have done, without policy having done anything.

This is the artefact Step 4 is read from: run it after several trading sessions
to decide which thresholds are worth enabling and at what level.

USAGE
    python scripts/admission_shadow_report.py
    python scripts/admission_shadow_report.py --days 7
    python scripts/admission_shadow_report.py --json shadow.json

Read-only. Touches no position, no threshold, no production behaviour.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="IST days to read back")
    ap.add_argument("--json", default=None, help="also write the summary as JSON")
    ap.add_argument("--limit-rejected", type=int, default=40)
    args = ap.parse_args()

    from services.admission_gate import cfg, load_decisions, summarize

    c = cfg()
    decisions = load_decisions(args.days)
    s = summarize(decisions)

    W = 100
    print("=" * W)
    print(f"PORTFOLIO ADMISSION GATE — SHADOW REPORT  (last {args.days} days)")
    print("=" * W)

    print("\nCONFIGURATION")
    mode = "ENFORCING" if c["ADMISSION_GATE_ENFORCE"] else "SHADOW (advisory only)"
    print(f"  mode                       : {mode}")
    print(f"  gate enabled               : {c['ADMISSION_GATE_ENABLED']}")
    print(f"  policy version             : {c['POLICY_VERSION']}")
    print(f"  PROMOTE_MIN_PRICE          : {c['PROMOTE_MIN_PRICE']:g}"
          f"{'   (no-op)' if c['PROMOTE_MIN_PRICE'] <= 0 else ''}")
    print(f"  PROMOTE_MIN_TURNOVER_CR    : {c['PROMOTE_MIN_TURNOVER_CR']:g}"
          f"{'   (no-op)' if c['PROMOTE_MIN_TURNOVER_CR'] <= 0 else ''}")
    print(f"  PROMOTE_MAX_ATR_PCT        : {c['PROMOTE_MAX_ATR_PCT']:g}"
          f"{'   (no-op)' if c['PROMOTE_MAX_ATR_PCT'] >= 999 else ''}")
    print(f"  PROMOTE_MAX_STOP_WIDTH_PCT : {c['PROMOTE_MAX_STOP_WIDTH_PCT']:g}"
          f"{'   (no-op)' if c['PROMOTE_MAX_STOP_WIDTH_PCT'] >= 999 else ''}")
    print(f"  PROMOTE_MAX_SECTOR_EXPOSURE: {c['PROMOTE_MAX_SECTOR_EXPOSURE']:g}"
          f"{'   (no-op)' if c['PROMOTE_MAX_SECTOR_EXPOSURE'] <= 0 else ''}")

    if not decisions:
        print("\n" + "=" * W)
        print("NO DECISIONS RECORDED")
        print("=" * W)
        print("  Either no candidate has been evaluated yet, or Redis is unreachable")
        print("  from this machine. This is NOT evidence that nothing was admitted.")
        print("  The gate logs to Redis from the web service; run this where that")
        print("  Redis is reachable (REDIS_URL), or wait for the next scan cycle.")
        return 0

    print("\n" + "=" * W)
    print("TOTALS")
    print("=" * W)
    pct = (s["pass"] / s["total"] * 100) if s["total"] else 0.0
    print(f"  candidates evaluated : {s['total']}")
    print(f"  PASS                 : {s['pass']}  ({pct:.1f}%)")
    print(f"  REJECT               : {s['reject']}  ({100 - pct:.1f}%)")
    print(f"  invalid/missing data : {s['invalid_metric_count']}")
    print(f"  policy versions seen : {', '.join(s['policy_versions']) or '-'}")
    if not s["shadow_only"]:
        print("  *** WARNING: some decisions were recorded with shadow_mode=False")
        print("      — the gate was ENFORCING for part of this window.")

    print("\n" + "=" * W)
    print("BY PORTFOLIO (LT vs Swing)")
    print("=" * W)
    print(f"  {'book':<14}{'total':>8}{'PASS':>8}{'REJECT':>8}")
    for k, v in sorted(s["by_horizon"].items()):
        print(f"  {k:<14}{v['total']:>8}{v.get('PASS', 0):>8}{v.get('REJECT', 0):>8}")

    print("\n" + "=" * W)
    print("BY SOURCE DOOR")
    print("=" * W)
    print(f"  {'door':<34}{'total':>8}{'PASS':>8}{'REJECT':>8}")
    for k, v in sorted(s["by_door"].items(), key=lambda x: -x[1]["total"]):
        flag = ""
        if "unattributed" in k:
            flag = "   <-- BYPASS TRIPWIRE: a creation path did not identify itself"
        print(f"  {k:<34}{v['total']:>8}{v.get('PASS', 0):>8}{v.get('REJECT', 0):>8}{flag}")

    print("\n" + "=" * W)
    print("REJECTIONS BY REASON")
    print("=" * W)
    if not s["by_reason"]:
        print("  (none — expected while every threshold is still a no-op)")
    for reason, n in sorted(s["by_reason"].items(), key=lambda x: -x[1]):
        print(f"  {reason:<26}{n:>6}")

    print("\n" + "=" * W)
    print("DAILY HISTORY")
    print("=" * W)
    print(f"  {'day':<14}{'total':>8}{'PASS':>8}{'REJECT':>8}")
    for day, v in sorted(s["by_day"].items()):
        print(f"  {day:<14}{v['total']:>8}{v.get('PASS', 0):>8}{v.get('REJECT', 0):>8}")

    rows = s["rejected_rows"][: args.limit_rejected]
    if rows:
        print("\n" + "=" * W)
        print(f"REJECTED CANDIDATES  (showing {len(rows)} of {len(s['rejected_rows'])})")
        print("=" * W)
        print(f"  {'symbol':<14}{'book':<10}{'price':>9}{'turn':>8}{'atr%':>7}"
              f"{'stop%':>7}  {'sector':<10}{'door':<26} reasons")
        for d in rows:
            def g(k, fmt="{:.2f}"):
                v = d.get(k)
                return fmt.format(v) if isinstance(v, (int, float)) else "-"
            print(f"  {str(d.get('symbol'))[:13]:<14}{str(d.get('portfolio_type'))[:9]:<10}"
                  f"{g('price'):>9}{g('turnover_cr'):>8}{g('atr_pct'):>7}"
                  f"{g('stop_width_pct'):>7}  {str(d.get('sector'))[:9]:<10}"
                  f"{str(d.get('source_door'))[:25]:<26}"
                  f"{','.join(d.get('rejection_reasons') or [])}")

    print("\n" + "=" * W)
    print("HOW TO READ THIS")
    print("=" * W)
    print("  While every threshold is a no-op, REJECT should be 0 and this report")
    print("  is a census of what enters the book and through which door. That is")
    print("  itself the first result: it proves the gate sees every creation path.")
    print("  To calibrate (Step 4), set ONE threshold to a candidate value, leave")
    print("  ADMISSION_GATE_ENFORCE=0, and re-read this after several sessions to")
    print("  see exactly what it would have excluded before making it authoritative.")

    if args.json:
        payload = dict(s)
        payload["config"] = c
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
