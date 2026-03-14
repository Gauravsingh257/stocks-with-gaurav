"""
prepare_training.py
===================
Cleans and enriches trade_ledger_2026.csv for PRISM training.

Fixes:
  1. Deduplicates identical rows (UNIVERSAL signals logged 4x)
  2. Filters out options trades (NFO:...)
  3. Reconstructs sl_price and target_price from entry + exit_price + pnl_r
  4. Tags session from trade timestamp
  5. Outputs ai_learning/training_trades.csv

Run:
    python prepare_training.py
"""

import csv
import sys
from pathlib import Path
from datetime import datetime, time

LEDGER = Path("trade_ledger_2026.csv")
OUTPUT = Path("ai_learning/training_trades.csv")

# Session boundaries (IST)
KILLZONE_AM_START = time(9, 15)
KILLZONE_AM_END   = time(10, 30)
INDIA_MID_START   = time(10, 30)
INDIA_MID_END     = time(13, 30)
LONDON_START      = time(13, 30)
LONDON_END        = time(15, 30)


def get_session(ts_str: str) -> str:
    try:
        if " " in ts_str:
            t = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").time()
        else:
            return "INDIA_MID"  # Daily setups assumed mid-session
        if KILLZONE_AM_START <= t <= KILLZONE_AM_END:
            return "KILLZONE_AM"
        elif INDIA_MID_START < t <= INDIA_MID_END:
            return "INDIA_MID"
        elif LONDON_START < t <= LONDON_END:
            return "LONDON_OVERLAP"
        return "INDIA_MID"
    except Exception:
        return "INDIA_MID"


def reconstruct_sl_target(entry: float, exit_price: float, pnl_r: float,
                           direction: str, setup: str) -> tuple:
    """
    Reconstruct SL and Target from known trade data.

    If pnl_r == -1.0: exit_price IS the SL (full stop hit).
    If pnl_r > 0:     we know exit was a win, risk = (exit - entry) / pnl_r (LONG).
    """
    try:
        risk_per_r = abs(exit_price - entry) / max(abs(pnl_r), 0.01)

        if direction == "LONG":
            sl = entry - risk_per_r
            # Target based on typical RR from setup
            if "SETUP-D" in setup:
                target = entry + 2.0 * risk_per_r
            else:
                target = entry + 2.0 * risk_per_r
        else:  # SHORT
            sl = entry + risk_per_r
            if "SETUP-D" in setup:
                target = entry - 2.0 * risk_per_r
            else:
                target = entry - 2.0 * risk_per_r

        return round(sl, 2), round(target, 2)
    except Exception:
        offset = entry * 0.01
        if direction == "LONG":
            return round(entry - offset, 2), round(entry + offset * 2, 2)
        else:
            return round(entry + offset, 2), round(entry - offset * 2, 2)


def classify_setup(setup: str) -> str:
    """Map internal setup names to SMC concepts."""
    s = setup.upper()
    if "SETUP-D" in s:
        return "OB_BOS"
    elif "FVG" in s:
        return "FVG_SWEEP"
    elif "UNIVERSAL" in s:
        return "OB_CONTINUATION"
    elif s in ("A", "B", "C"):
        return f"SETUP_{s}"
    elif "OI-SC" in s:
        return "OI_STRUCTURE_CHANGE"
    return "OB_BOS"


def main():
    if not LEDGER.exists():
        print(f"ERROR: {LEDGER} not found")
        sys.exit(1)

    rows = []
    with open(LEDGER, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Raw rows: {len(rows)}")

    # Step 1: Filter options trades
    rows = [r for r in rows if not r["symbol"].startswith("NFO:")]
    print(f"After filtering options: {len(rows)}")

    # Step 2: Deduplicate (same date+symbol+direction+entry+exit)
    seen = set()
    deduped = []
    for r in rows:
        key = (r["date"], r["symbol"], r["direction"], r["entry"], r["exit_price"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    print(f"After deduplication: {len(deduped)}")

    # Step 3: Skip rows with missing essential data
    valid = []
    for r in deduped:
        try:
            entry = float(r["entry"])
            exit_price = float(r["exit_price"]) if r.get("exit_price") else None
            pnl_r = float(r["pnl_r"]) if r.get("pnl_r") else None
            direction = r["direction"].strip().upper()
            result = r["result"].strip().upper()
            if direction not in ("LONG", "SHORT"):
                continue
            if result not in ("WIN", "LOSS", "BE"):
                continue
            if entry <= 0:
                continue
            valid.append((r, entry, exit_price, pnl_r, direction, result))
        except (ValueError, KeyError):
            continue

    print(f"After validation: {len(valid)}")

    # Step 4: Build enriched rows
    output_rows = []
    for i, (r, entry, exit_price, pnl_r, direction, result) in enumerate(valid):
        sl, target = reconstruct_sl_target(
            entry, exit_price or entry, pnl_r or -1.0, direction, r["setup"]
        )
        session = get_session(r["date"].strip())
        setup_type = classify_setup(r["setup"])

        output_rows.append({
            "trade_id": f"T{i+1:04d}",
            "symbol": r["symbol"].strip(),
            "timeframe": "5minute",
            "direction": direction,
            "entry_price": entry,
            "sl_price": sl,
            "target_price": target,
            "result": result,
            "pnl_r": pnl_r if pnl_r is not None else (-1.0 if result == "LOSS" else 2.0),
            "setup_type": setup_type,
            "session": session,
            "notes": r["setup"].strip(),
            "date": r["date"].strip().split(" ")[0],
            "chart_image": "",
        })

    # Write output
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["trade_id", "symbol", "timeframe", "direction", "entry_price",
              "sl_price", "target_price", "result", "pnl_r", "setup_type",
              "session", "notes", "date", "chart_image"]

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nDONE: Training CSV written: {OUTPUT}")
    print(f"   Total clean trades: {len(output_rows)}")

    # Summary
    wins = sum(1 for r in output_rows if r["result"] == "WIN")
    losses = sum(1 for r in output_rows if r["result"] == "LOSS")
    setups = {}
    for r in output_rows:
        setups[r["setup_type"]] = setups.get(r["setup_type"], 0) + 1

    print(f"\n   Wins:   {wins}  ({wins/len(output_rows)*100:.1f}%)")
    print(f"   Losses: {losses}  ({losses/len(output_rows)*100:.1f}%)")
    print(f"\n   By setup:")
    for k, v in sorted(setups.items(), key=lambda x: -x[1]):
        print(f"     {k:<30} {v}")

    print(f"\nNext step: python -m ai_learning.cli ingest --source csv --file ai_learning/training_trades.csv")
    print(f"Then:      python -m ai_learning.cli learn")


if __name__ == "__main__":
    main()
