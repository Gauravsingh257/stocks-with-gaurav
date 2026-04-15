"""
archive_signals.py
==================
Pull trade signals from the Railway backend API and archive them permanently
to signal_history/signals_YYYY.csv in this repo.

Deduplication is done by signal_id — running this multiple times is safe.
Designed to be run from the local machine (not Railway).

Usage:
    python scripts/archive_signals.py                  # archive past 30 days
    python scripts/archive_signals.py --days 7         # archive past 7 days
    python scripts/archive_signals.py --date 2026-04-15  # archive one specific day
    python scripts/archive_signals.py --all            # archive all dates in signal_history CSVs
                                                       # (refresh/fill gaps — slow)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────
BACKEND = "https://web-production-2781a.up.railway.app"
SIGNAL_HISTORY_DIR = Path(__file__).resolve().parents[1] / "signal_history"
TIMEOUT = 15

# Canonical column order for CSV files
CSV_COLUMNS = [
    "signal_id",
    "date",        # derived from timestamp for easy filtering
    "timestamp",
    "symbol",
    "direction",
    "strategy_name",
    "signal_kind",
    "entry",
    "stop_loss",
    "target1",
    "target2",
    "score",
    "confidence",
    "result",
    "pnl_r",
    "delivery_channel",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _csv_path(year: int) -> Path:
    return SIGNAL_HISTORY_DIR / f"signals_{year}.csv"


def _load_existing_ids(year: int) -> set[str]:
    """Return set of signal_ids already in the year's CSV."""
    path = _csv_path(year)
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["signal_id"] for row in reader if row.get("signal_id")}


def _append_signals(signals: list[dict], existing_ids: set[str]) -> tuple[int, set[str]]:
    """
    Append new signals (not already in existing_ids) to the appropriate yearly CSV.
    Returns (count_added, updated_existing_ids).
    """
    added = 0
    by_year: dict[int, list[dict]] = {}

    for sig in signals:
        sid = sig.get("signal_id", "")
        if not sid or sid in existing_ids:
            continue
        ts = sig.get("timestamp", "")
        try:
            year = int(ts[:4])
        except (ValueError, TypeError):
            year = date.today().year
        by_year.setdefault(year, []).append(sig)
        existing_ids.add(sid)
        added += 1

    for year, year_sigs in by_year.items():
        path = _csv_path(year)
        write_header = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            for sig in sorted(year_sigs, key=lambda s: s.get("timestamp", "")):
                # Derive the date field
                ts = sig.get("timestamp", "")
                sig["date"] = ts[:10] if ts else ""
                writer.writerow(sig)

    return added, existing_ids


def _fetch_signals_for_date(query_date: str) -> list[dict]:
    """Fetch signals for a given date from Railway backend."""
    url = f"{BACKEND}/api/journal/signals-today?date={query_date}"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("signals", [])
    except requests.exceptions.ConnectionError:
        print(f"  [ERROR] Cannot reach backend — check internet/VPN. URL: {url}")
        return []
    except requests.exceptions.Timeout:
        print(f"  [WARN]  Timeout fetching {query_date} — skipped")
        return []
    except Exception as exc:
        print(f"  [WARN]  Failed to fetch {query_date}: {exc}")
        return []


def _date_range(start: date, end: date) -> list[date]:
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur)
        cur += timedelta(days=1)
    return dates


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Archive trade signals from Railway to signal_history/")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=30,
                       help="Number of past days to archive (default: 30)")
    group.add_argument("--date", type=str,
                       help="Archive a single specific date (YYYY-MM-DD)")
    args = parser.parse_args()

    SIGNAL_HISTORY_DIR.mkdir(exist_ok=True)

    # Build list of dates to fetch
    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            print(f"[ERROR] Invalid date format: {args.date}. Use YYYY-MM-DD.")
            sys.exit(1)
        dates_to_fetch = [target]
    else:
        end = date.today()
        start = end - timedelta(days=args.days - 1)
        dates_to_fetch = _date_range(start, end)

    print(f"\n=== Signal Archiver ===")
    print(f"Backend : {BACKEND}")
    print(f"Dates   : {dates_to_fetch[0]} → {dates_to_fetch[-1]} ({len(dates_to_fetch)} days)")
    print(f"Output  : {SIGNAL_HISTORY_DIR}\n")

    # Load all existing signal IDs from all yearly CSVs upfront
    years_needed = {d.year for d in dates_to_fetch}
    existing_ids: set[str] = set()
    for year in years_needed:
        existing_ids |= _load_existing_ids(year)
    print(f"Existing signals in CSVs: {len(existing_ids)}\n")

    total_fetched = 0
    total_added = 0

    for d in dates_to_fetch:
        d_str = d.isoformat()
        signals = _fetch_signals_for_date(d_str)
        total_fetched += len(signals)
        added, existing_ids = _append_signals(signals, existing_ids)
        total_added += added
        status = f"+{added} new" if added else "no new"
        print(f"  {d_str}: {len(signals):2d} fetched  → {status}")

    print(f"\n{'─'*40}")
    print(f"Total fetched : {total_fetched}")
    print(f"Total added   : {total_added} new signals archived")
    print(f"{'─'*40}")

    if total_added > 0:
        print(f"\nFiles updated:")
        for p in sorted(SIGNAL_HISTORY_DIR.glob("signals_*.csv")):
            lines = sum(1 for _ in open(p, encoding="utf-8")) - 1  # minus header
            print(f"  {p.name}  ({lines} total signals)")
        print("\nRun ARCHIVE_SIGNALS.bat to commit + push to GitHub.")
    else:
        print("\nNothing new to commit.")


if __name__ == "__main__":
    main()
