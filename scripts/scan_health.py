#!/usr/bin/env python
"""Would we notice if the research scans went blind?

THE FAILURE THIS CATCHES (2026-09-07)
Both research engines read one universe OHLC snapshot that the scanner worker
publishes to Redis. Its price shards live 50h; its manifest lives 7 days; the
scanner republishes only on deploy and at weekday post-close. After the first
weekend since Phase 0 with no deploy, Monday's scans found a manifest with no
bars behind it. The ranking engine has no fallback, so every run that day
passed 0 of ~2,190 stocks. The validation scan fell back to per-symbol fetches
and its first run of the day collapsed (Layer 1: 150 vs ~410). Three positions
were admitted from those degraded scans. Nothing alerted.

Three checks. Read-only, public API only — it imports nothing from services, so
it cannot perturb production:

  1. SNAPSHOT (predictive) — /api/research/data-health. CRITICAL if the snapshot
     is not usable now, or its shards expire before the next weekday morning
     scan. This is the one that fires BEFORE the damage.
  2. ZERO-PASS (detective) — a ranking-engine run that scanned >= 500 stocks and
     passed none. Replayed over 101 runs, 2026-08-11..09-12: fires on 09-07 only.
  3. L1 COLLAPSE (detective) — a validation scan whose Layer-1 count is below 70%
     of the median of the prior 10 same-horizon scans from EARLIER days.
     Replayed: 6 fires, every one a real first-scan-of-day data collapse
     (08-14, 08-17, 08-18 x2, 09-07 x2); none on a healthy scan.

THE TRAP IT AVOIDS
`ranking_runs.quality_passed` holds two different quantities. Rows whose notes
start `sources=` are the ranking engine (universe -> quality gate, ~1,500).
Rows whose notes carry `scan_id=` are the validation scan, where
swing_alpha_agent logs the funnel's Layer-1 count in the same column (~400).
Pooling them turned a day of zeros into a misleading "median 75". Every row is
classified from its notes before any threshold applies, and an unrecognised row
type is reported rather than silently folded in.

Exit code: 0 healthy, 1 on any CRITICAL, 2 if something could not be seen — a
monitor that cannot see is not a passing monitor.

Usage:
    python -m scripts.scan_health
    python -m scripts.scan_health --replay      # the control: which past days fire
    python -m scripts.scan_health --hours 36
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_API = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")

ZERO_PASS_MIN_SCANNED = 500
L1_COLLAPSE_RATIO = 0.70
L1_TRAILING = 10
L1_MIN_PRIOR = 5
MORNING_SCAN_IST = (8, 30)

OK, WARN, UNKNOWN, CRITICAL = "OK", "WARN", "UNKNOWN", "CRITICAL"


def _get(url: str):
    """GET -> parsed JSON, or None. Never raises: an unreachable API is reported
    as UNKNOWN by the caller, not as a crash."""
    try:
        import requests

        resp = requests.get(url, timeout=60, headers={"User-Agent": "scan-health/1"})
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        pass
    except Exception as exc:
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None


def parse_run_time(value) -> datetime | None:
    """`ranking_runs.run_time` is SQLite datetime('now') — UTC, no offset."""
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def classify_run(run: dict) -> str:
    notes = str(run.get("notes") or "")
    if notes.startswith("sources="):
        return "ranking"
    if "scan_id=" in notes:
        return "validation"
    return "unknown"


def zero_pass_runs(runs: list[dict], min_scanned: int = ZERO_PASS_MIN_SCANNED) -> list[dict]:
    return [
        r for r in runs
        if classify_run(r) == "ranking"
        and int(r.get("universe_scanned") or 0) >= min_scanned
        and int(r.get("quality_passed") or 0) == 0
    ]


def l1_collapse_runs(runs: list[dict], ratio: float = L1_COLLAPSE_RATIO,
                     trailing: int = L1_TRAILING, min_prior: int = L1_MIN_PRIOR) -> list[dict]:
    """Validation scans whose Layer-1 count fell below `ratio` x baseline.

    The baseline uses EARLIER days only: a degraded first scan must not drag
    down the baseline for a healthy scan later the same morning."""
    fires: list[dict] = []
    history: dict[str, list[tuple[str, int]]] = defaultdict(list)
    validation = sorted((r for r in runs if classify_run(r) == "validation"),
                        key=lambda r: str(r.get("run_time") or ""))
    for r in validation:
        horizon = str(r.get("horizon") or "")
        day = str(r.get("run_time") or "")[:10]
        l1 = int(r.get("quality_passed") or 0)
        prior = [v for d, v in history[horizon] if d < day][-trailing:]
        if len(prior) >= min_prior:
            baseline = st.median(prior)
            if baseline > 0 and l1 < ratio * baseline:
                fires.append({**r, "baseline_l1": baseline, "ratio": round(l1 / baseline, 2)})
        history[horizon].append((day, l1))
    return fires


def next_scan_after(now: datetime) -> datetime:
    """The next weekday morning scan (08:30 IST) strictly after `now`, in UTC.
    Weekends are skipped. NSE holidays are not modelled."""
    local = now.astimezone(IST)
    candidate = local.replace(hour=MORNING_SCAN_IST[0], minute=MORNING_SCAN_IST[1],
                              second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), UTC).astimezone(IST).strftime("%a %d %b %H:%M IST")


def snapshot_verdict(status: dict | None, now: datetime) -> tuple[str, str]:
    if not status:
        return UNKNOWN, "data-health endpoint unreachable — cannot see the snapshot"
    if status.get("enabled") is False:
        return OK, "PHASE0_KITE_OHLC is off — the snapshot is not in the scan path"
    if not status.get("available"):
        return CRITICAL, (f"no universe OHLC snapshot ({status.get('reason', 'unknown')}) — "
                          "the ranking engine will pass 0 stocks")
    if "usable" not in status:
        return UNKNOWN, "endpoint predates the shard check (deploy pending) — cannot tell if bars exist"
    if not status.get("usable"):
        return CRITICAL, (f"manifest for {status.get('day')} exists but only "
                          f"{status.get('shards_alive')}/{status.get('shards')} price shards are alive — "
                          "scans will see no bars (the 2026-09-07 failure)")
    nxt = next_scan_after(now)
    expires = status.get("expires_at")
    nxt_txt = nxt.astimezone(IST).strftime("%a %d %b %H:%M IST")
    if expires is not None and float(expires) <= nxt.timestamp():
        return CRITICAL, (f"shards expire {_ist(expires)}, before the next scan {nxt_txt} — "
                          "republish (redeploy the scanner) or raise UNIVERSE_OHLC_TTL_SEC on the scanner")
    exp_txt = _ist(expires) if expires is not None else "never"
    return OK, (f"snapshot {status.get('day')} usable ({status.get('shards_alive')}/{status.get('shards')} "
                f"shards, {status.get('symbols')} symbols); expires {exp_txt}, after next scan {nxt_txt}")


def exit_code(levels) -> int:
    levels = list(levels)
    if CRITICAL in levels:
        return 1
    if UNKNOWN in levels:
        return 2
    return 0


def _describe(run: dict) -> str:
    t = parse_run_time(run.get("run_time"))
    when = t.astimezone(IST).strftime("%a %d %b %H:%M IST") if t else "?"
    line = (f"{run.get('run_time')} UTC ({when}) {run.get('horizon')} "
            f"scanned={run.get('universe_scanned')} passed={run.get('quality_passed')}")
    if "baseline_l1" in run:
        line += f" baseline={run['baseline_l1']:g} ({run['ratio']:.0%})"
    return line


def main() -> int:
    ap = argparse.ArgumentParser(description="Research scan data health (read-only)")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--hours", type=float, default=30.0,
                    help="how far back the detective checks look (default 30)")
    ap.add_argument("--replay", action="store_true",
                    help="evaluate the whole ranking_runs history — the control")
    args = ap.parse_args()
    now = datetime.now(UTC)
    api = args.api.rstrip("/")
    results: list[tuple[str, str, str]] = []

    if not args.replay:
        health = _get(f"{api}/api/research/data-health")
        status = health.get("universe_ohlc") if isinstance(health, dict) else None
        level, message = snapshot_verdict(status, now)
        results.append((level, "snapshot", message))

    payload = _get(f"{api}/api/research/ranking-runs?limit=200")
    runs = payload.get("items") if isinstance(payload, dict) else None
    zp: list[dict] = []
    lc: list[dict] = []
    if runs is None:
        results.append((UNKNOWN, "scans", "ranking-runs API unreachable — cannot see the scans"))
    else:
        cutoff = None if args.replay else now - timedelta(hours=args.hours)

        def in_window(r: dict) -> bool:
            t = parse_run_time(r.get("run_time"))
            return t is not None and (cutoff is None or t >= cutoff)

        scope = "in history" if args.replay else f"in the last {args.hours:g}h"
        window = [r for r in runs if in_window(r)]
        zp = [r for r in zero_pass_runs(runs) if in_window(r)]
        lc = [r for r in l1_collapse_runs(runs) if in_window(r)]
        n_rank = sum(1 for r in window if classify_run(r) == "ranking")
        n_val = sum(1 for r in window if classify_run(r) == "validation")
        unknown = [r for r in window if classify_run(r) == "unknown"]

        results.append((CRITICAL, "zero-pass", f"{len(zp)} ranking-engine run(s) passed 0 stocks {scope}")
                       if zp else
                       (OK, "zero-pass", f"no zero-pass ranking runs {scope} ({n_rank} checked)"))
        results.append((CRITICAL, "l1-collapse", f"{len(lc)} validation scan(s) below "
                        f"{L1_COLLAPSE_RATIO:.0%} of their Layer-1 baseline {scope}")
                       if lc else
                       (OK, "l1-collapse", f"no Layer-1 collapse {scope} ({n_val} checked)"))
        if unknown:
            results.append((WARN, "row-types", f"{len(unknown)} run(s) with unrecognised notes — "
                            "not classified, not checked"))

    width = 78
    print("=" * width)
    print(f"SCAN HEALTH  {now.astimezone(IST):%a %d %b %Y %H:%M} IST  api={api}")
    print("=" * width)
    for level, name, message in results:
        print(f"[{level:8s}] {name:12s} {message}")
    for label, fires in (("zero-pass ranking runs", zp), ("Layer-1 collapse scans", lc)):
        if fires:
            print(f"\n{label}:")
            for r in fires:
                print(f"  - {_describe(r)}")

    if args.replay:
        days = sorted({str(r.get("run_time"))[:10] for r in zp + lc})
        print(f"\nreplay: fires on {len(days)} day(s): {days}")
        return 0
    code = exit_code(level for level, _, _ in results)
    print(f"\nexit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
