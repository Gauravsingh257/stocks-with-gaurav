#!/usr/bin/env python
"""Stagnation-rule SHADOW LOG — observes, never acts.

Records, once per trading day, what a stagnation exit rule WOULD have fired on
across all three books, plus whether a replacement candidate was actually
available that day. It changes no live behaviour: every call is a read.

WHY THIS EXISTS
Backtests on closed trades gave contradictory verdicts (Swing NO-GO, Momentum
no-op, Long-Term promising on only 19 trades). The blocker was that the benefit
the rule is FOR — recycling a freed slot into something better — could not be
measured from history: the point-in-time candidate ledger had zero candidates
with a knowable outcome, and 82% of LT candidates expire without ever
triggering. Only forward observation answers it, so this accumulates the
evidence live at zero risk.

Reads the public API only — it deliberately imports nothing from the engine or
services layer, so it cannot perturb production even by accident.

Usage:
    python -m scripts.stagnation_shadow                     # append today's row
    python -m scripts.stagnation_shadow --dry-run           # print, write nothing
    python -m scripts.stagnation_shadow --band 3            # different band
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_API = os.getenv("DASHBOARD_URL", "https://web-production-2781a.up.railway.app")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "validation", "stagnation_shadow_log.csv")

FIELDS = [
    "date", "book", "symbol", "days_held", "pl_pct", "would_fire",
    "book_used", "book_max", "book_full", "candidates_available",
    "best_candidate", "best_candidate_conf", "entry_price", "current_price",
    "source_door", "recommendation_id",
]


def _get(url: str):
    """GET returning parsed JSON, or None. Never raises — a logging job must not
    fail a workflow over a transient 502.

    Prefers `requests` for the same reason scripts/backtest_giveback.py does: it
    ships its own certifi bundle, whereas a stale system CA store makes urllib
    fail the TLS handshake on some dev machines. urllib is the no-dependency
    fallback so this still runs in a bare CI container."""
    try:
        import requests

        resp = requests.get(url, timeout=60, headers={"User-Agent": "stagnation-shadow/1"})
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  ! fetch failed {url}: {exc}", file=sys.stderr)
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stagnation-shadow/1"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! fetch failed {url}: {exc}", file=sys.stderr)
        return None


def collect(api: str, band: float, min_days: int) -> list[dict]:
    today = datetime.now(IST).date().isoformat()
    counts = _get(f"{api}/api/portfolio/counts") or {}

    books: dict[str, list] = {}
    for book, path in (("SWING", "/api/portfolio/swing?limit=40"),
                       ("LONGTERM", "/api/portfolio/longterm?limit=40")):
        d = _get(f"{api}{path}") or {}
        books[book] = d.get("items", [])
    mom = _get(f"{api}/api/momentum-portfolio/holdings") or {}
    books["MOMENTUM"] = mom.get("items", [])

    # Replacement candidates available TODAY, per horizon. A candidate only
    # counts if it is not already held — a name we own is not a replacement.
    cands: dict[str, list] = {}
    for book, path in (("SWING", "/api/research/swing?limit=25"),
                       ("LONGTERM", "/api/research/longterm?limit=25")):
        d = _get(f"{api}{path}") or {}
        cands[book] = d.get("items", [])
    cands["MOMENTUM"] = []  # momentum has no equivalent public candidate feed

    rows: list[dict] = []
    for book, positions in books.items():
        active = [p for p in positions if str(p.get("status")) == "ACTIVE"]
        held = {str(p.get("symbol", "")).replace("NSE:", "") for p in positions}
        pool = [c for c in cands.get(book, [])
                if str(c.get("symbol", "")).replace("NSE:", "") not in held]
        pool.sort(key=lambda c: float(c.get("confidence_score") or 0), reverse=True)
        best = pool[0] if pool else None

        lo = book.lower()
        used = counts.get(f"{lo}_used")
        cap = counts.get(f"{lo}_max")
        if book == "MOMENTUM":
            used, cap = len(active), None

        for p in active:
            try:
                days = int(p.get("days_held") or 0)
                pl = float(p.get("profit_loss_pct") or 0.0)
            except (TypeError, ValueError):
                continue
            fires = days > min_days and abs(pl) <= band
            if not fires:
                continue  # log only what the rule would touch — keep the file small
            rows.append(dict(
                date=today, book=book,
                symbol=str(p.get("symbol", "")).replace("NSE:", ""),
                days_held=days, pl_pct=round(pl, 2), would_fire=1,
                book_used=used, book_max=cap,
                book_full=int(used is not None and cap is not None and used >= cap),
                candidates_available=len(pool),
                best_candidate=(str(best.get("symbol", "")).replace("NSE:", "") if best else ""),
                best_candidate_conf=(round(float(best.get("confidence_score") or 0), 2) if best else ""),
                entry_price=p.get("entry_price"), current_price=p.get("current_price"),
                source_door=p.get("source_door") or "", recommendation_id=p.get("recommendation_id") or "",
            ))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--band", type=float, default=4.0, help="abs %% return band")
    ap.add_argument("--min-days", type=int, default=20)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = collect(a.api, a.band, a.min_days)
    today = datetime.now(IST).date().isoformat()

    print(f"[stagnation-shadow] {today}  band=+-{a.band}%  min_days={a.min_days}")
    if not rows:
        print("  no position would fire today")
    for r in rows:
        print("  {:<9} {:<12} {:>3}d {:+6.2f}%  book_full={} candidates={} best={}".format(
            r["book"], r["symbol"], r["days_held"], r["pl_pct"],
            bool(r["book_full"]), r["candidates_available"], r["best_candidate"] or "-"))

    if a.dry_run:
        print("  (dry-run — nothing written)")
        return 0

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    existing = set()
    if os.path.exists(a.out):
        try:
            with open(a.out, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    existing.add((r.get("date"), r.get("book"), r.get("symbol")))
        except Exception:
            pass

    new = [r for r in rows if (r["date"], r["book"], r["symbol"]) not in existing]
    if not new:
        print("  already logged for today — nothing appended")
        return 0

    write_header = not os.path.exists(a.out) or os.path.getsize(a.out) == 0
    with open(a.out, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new)
    print(f"  appended {len(new)} row(s) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
