#!/usr/bin/env python
"""Which exit rules are enabled but have stopped firing?

THE FAILURE THIS CATCHES
The stale/dead-money cull sat inside the `else` of the trend-break branch. When
the risk engine shipped on 2026-07-09 with its flags default-ON, that branch
stopped being taken and the cull became unreachable. `PORTFOLIO_STALE_EXIT`
still read "1", so from the outside everything looked healthy — while positions
quietly sat for 38-87 days going nowhere. It was dead for ~7 weeks and nothing
surfaced it.

The general shape: a feature flag says ON, but the code path is unreachable.
A flag is a statement of INTENT; only the journal proves BEHAVIOUR. This script
compares the two by asking, for each exit reason, "when did you last fire?" A
rule believed to be enabled that has been silent for weeks is either genuinely
idle or broken — and both are worth a look.

Read-only. Prints a table and exits non-zero if anything looks silent, so it can
be used as a check in CI or a scheduled job.

Usage:
    python -m scripts.exit_rule_health
    python -m scripts.exit_rule_health --silent-days 21
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_API = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")

# exit_reason -> (what turns it on, what it means)
RULES = {
    "STOP_HIT": ("always on", "price hit the stop"),
    "TARGET_HIT": ("always on", "price hit the target"),
    "STALE_EXIT": ("PORTFOLIO_STALE_EXIT + PORTFOLIO_STALE_EXIT_INDEPENDENT",
                   "held long, went nowhere"),
    "TREND_BREAK": ("RISK_ENGINE_ENABLED + TREND_BREAK_EXIT_ENABLED",
                    "below 200-DMA with weak RS"),
    "STRUCTURE_BREAK": ("PORTFOLIO_STRUCTURE_EXIT (legacy path only)",
                        "underwater, long-held, below 200-DMA"),
    "TIME_EXIT": ("MOMENTUM_TIME_EXIT_DAYS", "momentum time stop"),
    "TRAIL_STOP": ("MOMENTUM_TRAIL_METHOD", "momentum trailing stop"),
    "MAX_LOSS": ("MOMENTUM_MAX_LOSS_PCT", "momentum hard loss backstop"),
    "REPLACED": ("momentum rotation", "swapped for a stronger candidate"),
    "MANUAL": ("operator", "closed by hand"),
}

# Rules that are EXPECTED to be silent, with the reason. Listing one here is a
# deliberate statement that its silence is understood — not an excuse to ignore
# it. STRUCTURE_BREAK is the honest case: TREND_BREAK (200-DMA + weak RS) is a
# strict upgrade of it (200-DMA + underwater), so the risk engine genuinely
# supersedes it. That is exactly why STALE_EXIT's silence was missed for weeks —
# it sat in the same branch and looked like the same intentional supersession,
# but it answers a different question ("gone nowhere", not "broken down") and
# nothing replaced it.
EXPECTED_SILENT = {
    "STRUCTURE_BREAK": "superseded by TREND_BREAK while the risk engine is on",
    "MAX_LOSS": "momentum backstop at -12%; the trailing stop normally fires first",
    "TIME_EXIT": "momentum time stop at 40d; positions rarely survive that long",
}


def _get(url: str):
    try:
        import requests

        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.json()
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--silent-days", type=int, default=21,
                    help="flag a rule that has not fired in this many days")
    a = ap.parse_args()

    rows = []
    j = _get(f"{a.api}/api/portfolio/journal/all?limit=200") or {}
    rows += [r for r in j.get("items", []) if not r.get("is_duplicate")]
    m = _get(f"{a.api}/api/momentum-portfolio/journal?limit=200") or {}
    rows += m.get("items", m if isinstance(m, list) else [])

    if not rows:
        print("no journal rows reachable — cannot assess", file=sys.stderr)
        return 0  # not a failure of the rules themselves

    last: dict[str, str] = {}
    count: dict[str, int] = {}
    for r in rows:
        reason = str(r.get("exit_reason") or "").split(":")[0].strip()
        if not reason:
            continue
        when = str(r.get("closed_at") or "")[:10]
        count[reason] = count.get(reason, 0) + 1
        if when and when > last.get(reason, ""):
            last[reason] = when

    today = datetime.now(IST).date()
    print(f"Exit-rule health — {today}   ({len(rows)} journal rows)")
    print(f"{'exit reason':<18}{'fired':>6}{'last seen':>13}{'silent':>9}   turned on by")
    print("-" * 100)

    silent = []
    for reason, (flag, _meaning) in RULES.items():
        n = count.get(reason, 0)
        when = last.get(reason, "")
        days = ""
        if when:
            try:
                days = (today - datetime.fromisoformat(when).date()).days
            except Exception:
                days = ""
        mark = ""
        is_silent = n == 0 or (isinstance(days, int) and days >= a.silent_days)
        if is_silent:
            why = EXPECTED_SILENT.get(reason)
            if why:
                mark = f"  (expected: {why})"
            else:
                mark = "  <-- NEVER fired" if n == 0 else f"  <-- SILENT {days}d"
                silent.append(reason)
        print(f"{reason:<18}{n:>6}{when or '-':>13}{(str(days) + 'd') if days != '' else '-':>9}   {flag}{mark}")

    # Anything the journal shows that this script does not know about — a new
    # rule shipped without being registered here.
    unknown = sorted(set(count) - set(RULES))
    if unknown:
        print()
        print("unregistered exit reasons seen in the journal (add them to RULES):")
        for u in unknown:
            print(f"  {u}  ({count[u]} rows, last {last.get(u, '-')})")

    if silent:
        print()
        print("CHECK THESE. A rule whose flag says ON but which has not fired is")
        print("either genuinely idle or unreachable. The stale cull was unreachable")
        print("for ~7 weeks in 2026 and nothing surfaced it — that is what this")
        print("script exists to prevent. Confirm the flag state in Railway, then")
        print("confirm the code path is actually taken.")
        return 1
    print("\nall registered rules have fired recently")
    return 0


if __name__ == "__main__":
    sys.exit(main())
