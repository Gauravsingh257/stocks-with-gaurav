"""
scripts/telegram_health_check.py — fail loudly when Telegram delivery is broken.

Reads GET /api/system/telegram-health (built from the outcomes of real
sendMessage calls; see services/telegram_health.py) and exits non-zero when any
destination is FAILING. Run by .github/workflows/telegram-health.yml — a failed
run is the alert (GitHub emails the repo owner). Deliberately not Telegram.

Added after the 2026-09-29 → 10-08 outage: the bot was frozen by Telegram,
every channel send returned PEER_ID_INVALID, and nothing noticed for ten days.

    python -m scripts.telegram_health_check [--api URL]

Exit codes: 0 healthy (or no sends recorded yet), 1 a destination is FAILING,
2 the health endpoint itself is unreachable/unavailable.
"""

from __future__ import annotations

import argparse
import os
import sys

DEFAULT_API = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")


def verdict(health: dict) -> tuple[int, list[str]]:
    lines: list[str] = []
    if not isinstance(health, dict) or not health.get("available"):
        return 2, [f"telegram health unavailable: {(health or {}).get('reason')}"]
    for d in health.get("destinations", []):
        lines.append(
            f"{d.get('status'):8s} chat={d.get('chat')} consecutive_failures={d.get('consecutive_failures')} "
            f"last_ok={d.get('last_ok_age_h')}h ago last_fail={d.get('last_fail_age_h')}h ago "
            f"source={d.get('last_fail_source') or d.get('last_source')} error={d.get('last_error')}"
        )
    if not health.get("destinations"):
        lines.append("no Telegram sends recorded yet")
    return (1 if health.get("failing") else 0), lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Telegram delivery health (read-only)")
    ap.add_argument("--api", default=DEFAULT_API)
    args = ap.parse_args()
    import requests

    try:
        resp = requests.get(f"{args.api.rstrip('/')}/api/system/telegram-health", timeout=60,
                            headers={"User-Agent": "telegram-health/1"})
        resp.raise_for_status()
        health = resp.json()
    except Exception as exc:
        print(f"::error::telegram health endpoint unreachable: {exc}")
        return 2
    code, lines = verdict(health)
    for line in lines:
        print(line)
    if code == 1:
        print(f"::error::Telegram delivery FAILING for {health.get('failing')} — alerts are not reaching users")
    elif code == 2:
        print("::error::" + lines[0])
    return code


if __name__ == "__main__":
    sys.exit(main())
