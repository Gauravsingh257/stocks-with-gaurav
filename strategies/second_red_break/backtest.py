"""
Second Red Break Put Strategy — Backtest Engine.

Fetches historical 5-min data for NIFTY & BANKNIFTY, runs strategy day-by-day,
and produces trade log CSV + summary report.

Usage:
    # With Kite API (fetches live historical data):
    python -m strategies.second_red_break.backtest

    # With local CSV data (offline mode):
    python -m strategies.second_red_break.backtest --csv data/nifty_5m.csv data/bn_5m.csv

Environment:
    PYTHONPATH must include repo root.
    Kite credentials required for API mode (config/kite_auth.py).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .strategy import run_day
from .utils import (
    INDEX_CONFIG,
    RESULTS_DIR,
    Candle,
    TradeRecord,
    fetch_historical_5m,
    group_candles_by_day,
    load_candles_csv,
    print_summary_table,
    save_candles_csv,
    save_summary_json,
    save_trades_csv,
)


# ── Default backtest period: ~4 months back from today ─────────────────
DEFAULT_FROM = date(2025, 12, 1)
DEFAULT_TO = date(2026, 3, 28)


def _load_candles_from_csv(filepath: str) -> List[Candle]:
    """
    Load 5-min candles from a CSV file.
    Expected columns: date, open, high, low, close, volume (optional).
    """
    candles: List[Candle] = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dt = row.get("date") or row.get("datetime") or row.get("time")
            candles.append(Candle(
                date=datetime.fromisoformat(str(dt)),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)),
            ))
    candles.sort(key=lambda c: c.date)
    return candles


def _fetch_candles_kite(instrument: str, from_date: date,
                        to_date: date) -> List[Candle]:
    """Fetch candles via Kite API using existing auth infrastructure."""
    from kiteconnect import KiteConnect
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from config.kite_auth import get_api_key, get_access_token

    api_key = get_api_key()
    access_token = get_access_token()
    if not api_key or not access_token:
        print("ERROR: Kite credentials not available. Use --csv mode or set credentials.")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    # Resolve instrument token
    exchange_sym = INDEX_CONFIG[instrument]["exchange_symbol"]
    ltp_data = kite.ltp(exchange_sym)
    token = list(ltp_data.values())[0]["instrument_token"]

    print(f"  Fetching {instrument} 5m data ({from_date} → {to_date}) token={token}")
    return fetch_historical_5m(kite, token, from_date, to_date)


# ── Backtest runner ────────────────────────────────────────────────────
def run_backtest(
    instruments: List[str],
    candle_sources: Dict[str, List[Candle]],
    max_entry_hour: int = 23,
    max_sl: Optional[Dict[str, float]] = None,      # {inst: max_sl_pts}
    use_partial_exit: bool = False,
    use_full_trail: bool = True,
    brokerage: Optional[Dict[str, float]] = None,   # {inst: pts per trade}
) -> Tuple[List[TradeRecord], dict]:
    """
    Run the backtest across instruments & days.

    Args:
        instruments: e.g. ["NIFTY", "BANKNIFTY"]
        candle_sources: {instrument: [Candle, ...]}

    Returns:
        (all_trades, summary_dict)
    """
    all_trades: List[TradeRecord] = []
    instrument_stats: Dict[str, dict] = {}
    max_sl = max_sl or {}
    brokerage = brokerage or {}

    for inst in instruments:
        candles = candle_sources.get(inst, [])
        if not candles:
            print(f"  SKIP {inst}: no candle data")
            continue

        days = group_candles_by_day(candles)
        trades: List[TradeRecord] = []
        wins, losses, eod_exits, no_trades, breakevens = 0, 0, 0, 0, 0
        inst_sl = max_sl.get(inst, 99999.0)
        inst_brok = brokerage.get(inst, 0.0)

        print(f"\n  Processing {inst}: {len(days)} trading days")

        for trade_date in sorted(days.keys()):
            day_candles = days[trade_date]
            summary, trade_rec = run_day(
                inst, trade_date, day_candles,
                max_entry_hour=max_entry_hour,
                max_sl_pts=inst_sl,
                use_partial_exit=use_partial_exit,
                use_full_trail=use_full_trail,
            )

            if trade_rec:
                # Apply brokerage / slippage
                if inst_brok > 0:
                    trade_rec.pnl_points -= inst_brok
                    trade_rec.rr_achieved = (
                        trade_rec.pnl_points / trade_rec.risk_points
                        if trade_rec.risk_points else 0
                    )
                trades.append(trade_rec)
                if trade_rec.outcome == "WIN":
                    wins += 1
                elif trade_rec.outcome == "LOSS":
                    losses += 1
                elif trade_rec.outcome == "EOD_EXIT":
                    eod_exits += 1
                elif trade_rec.outcome == "BREAKEVEN":
                    breakevens += 1
            else:
                no_trades += 1

        all_trades.extend(trades)

        total_traded = wins + losses + eod_exits + breakevens
        total_pnl = sum(t.pnl_points for t in trades)
        win_rate = (wins / total_traded * 100) if total_traded else 0

        # Compute max drawdown (cumulative PnL curve)
        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        equity_curve = []
        for t in trades:
            cum_pnl += t.pnl_points
            equity_curve.append(cum_pnl)
            peak = max(peak, cum_pnl)
            dd = peak - cum_pnl
            max_dd = max(max_dd, dd)

        # Average RR achieved on wins
        avg_rr_wins = 0.0
        if wins:
            avg_rr_wins = sum(t.rr_achieved for t in trades if t.outcome == "WIN") / wins

        instrument_stats[inst] = {
            "total_days": len(days),
            "total_trades": total_traded,
            "no_trade_days": no_trades,
            "wins": wins,
            "losses": losses,
            "eod_exits": eod_exits,
            "breakevens": breakevens,
            "win_rate_%": round(win_rate, 1),
            "total_pnl_points": round(total_pnl, 2),
            "avg_rr_on_wins": round(avg_rr_wins, 2),
            "max_drawdown_points": round(max_dd, 2),
            "equity_curve": equity_curve,
        }

    # ── Aggregate summary ──────────────────────────────────────────
    total_trades = sum(s["total_trades"] for s in instrument_stats.values())
    total_wins = sum(s["wins"] for s in instrument_stats.values())
    total_pnl = sum(s["total_pnl_points"] for s in instrument_stats.values())

    summary = {
        "strategy": "Second Red Break Put",
        "period": f"{DEFAULT_FROM} to {DEFAULT_TO}",
        "instruments": instruments,
        "overall": {
            "total_trades": total_trades,
            "total_wins": total_wins,
            "win_rate_%": round(total_wins / total_trades * 100, 1) if total_trades else 0,
            "total_pnl_points": round(total_pnl, 2),
        },
    }
    # Per-instrument stats (strip equity curve for JSON)
    for inst, stats in instrument_stats.items():
        s = {k: v for k, v in stats.items() if k != "equity_curve"}
        summary[inst] = s

    return all_trades, summary


# ── Main entry point ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Backtest: Second Red Break Put Strategy"
    )
    parser.add_argument(
        "--csv", nargs="*", default=None,
        help="Path(s) to CSV files. Format: INSTRUMENT:path (e.g. NIFTY:data/nifty.csv)"
    )
    parser.add_argument(
        "--from-date", type=str, default=str(DEFAULT_FROM),
        help=f"Start date YYYY-MM-DD (default: {DEFAULT_FROM})"
    )
    parser.add_argument(
        "--to-date", type=str, default=str(DEFAULT_TO),
        help=f"End date YYYY-MM-DD (default: {DEFAULT_TO})"
    )
    parser.add_argument(
        "--instruments", nargs="*", default=["NIFTY", "BANKNIFTY"],
        help="Instruments to backtest (default: NIFTY BANKNIFTY)"
    )
    # ── Filter flags ──────────────────────────────────────────────
    parser.add_argument(
        "--filter", action="store_true", default=False,
        help="Enable time filter (no entry >=10:00) + SL filter (NF>70, BN>150)"
    )
    parser.add_argument(
        "--partial-exit", action="store_true", default=False,
        help="Partial exit at 1.5R, move SL to breakeven, trail rest to 3R"
    )
    parser.add_argument(
        '--trail-from-3r', dest='full_trail', action='store_true', default=True,
        help="Strategy 6: full trail from 3R with 1.5R gap (default: True, matches live engine)"
    )
    parser.add_argument(
        '--no-trail', dest='full_trail', action='store_false',
        help="Disable trailing (run pure fixed 3R target)"
    )
    parser.add_argument(
        "--brokerage-nf", type=float, default=0.0,
        help="Brokerage deduction per NIFTY trade in index points (default: 0)"
    )
    parser.add_argument(
        "--brokerage-bn", type=float, default=0.0,
        help="Brokerage deduction per BANKNIFTY trade in index points (default: 0)"
    )
    parser.add_argument(
        "--compare", action="store_true", default=False,
        help="Run baseline AND filtered backtest on same data and print comparison"
    )
    args = parser.parse_args()

    from_dt = date.fromisoformat(args.from_date)
    to_dt = date.fromisoformat(args.to_date)
    instruments = [i.upper() for i in args.instruments]

    print("=" * 60)
    print("  SECOND RED BREAK PUT STRATEGY — BACKTEST")
    print(f"  Period : {from_dt} → {to_dt}")
    print(f"  Instruments : {instruments}")
    if args.filter:
        print("  Filters     : time<=10:00 | SL<=70pts (NF) / <=150pts (BN)")
    if args.partial_exit:
        print("  Partial exit: 1.5R partial → trail to 3R")
    if args.full_trail:
        print("  Trail       : Strategy 6 — full trail from 3R, 1.5R gap (LIVE CONFIG)")
    if args.brokerage_nf or args.brokerage_bn:
        print(f"  Brokerage   : NF={args.brokerage_nf}pts  BN={args.brokerage_bn}pts")
    print("=" * 60)

    # ── Load candle data ───────────────────────────────────────────
    candle_sources: Dict[str, List[Candle]] = {}

    if args.csv:
        for spec in args.csv:
            if ":" in spec:
                inst, path = spec.split(":", 1)
            else:
                inst = "NIFTY" if "nifty" in spec.lower() and "bank" not in spec.lower() else "BANKNIFTY"
                path = spec
            inst = inst.upper()
            print(f"  Loading CSV for {inst}: {path}")
            candle_sources[inst] = _load_candles_from_csv(path)
    else:
        for inst in instruments:
            # Try cached data first
            cached = load_candles_csv(inst, from_dt, to_dt)
            if cached:
                candle_sources[inst] = cached
            else:
                candle_sources[inst] = _fetch_candles_kite(inst, from_dt, to_dt)
                # Auto-save for future runs
                if candle_sources[inst]:
                    save_candles_csv(candle_sources[inst], inst, from_dt, to_dt)

    # ── Shared filter config ───────────────────────────────────────
    brokerage = {"NIFTY": args.brokerage_nf, "BANKNIFTY": args.brokerage_bn}
    max_sl_filtered = {"NIFTY": 70.0, "BANKNIFTY": 150.0}

    if args.compare:
        # ── V1: BASELINE (no filters) ─────────────────────────────
        print("\n[1/3] Running V1: BASELINE (no filters, no brokerage)...")
        trades_v1, summary_v1 = run_backtest(instruments, candle_sources)

        # ── V2: FILTERED + partial exit 1.5R ──────────────────────
        print("\n[2/3] Running V2: FILTERED + partial exit 1.5R + brokerage...")
        trades_v2, summary_v2 = run_backtest(
            instruments, candle_sources,
            max_entry_hour=10,
            max_sl=max_sl_filtered,
            use_partial_exit=True,
            brokerage=brokerage,
        )

        # ── V3: FILTERED + full 3R target + Strategy 6 trail (LIVE CONFIG) ─────
        print("\n[3/3] Running V3: FILTERED + Strategy 6 trail from 3R (LIVE CONFIG)...")
        trades_v3, summary_v3 = run_backtest(
            instruments, candle_sources,
            max_entry_hour=10,
            max_sl=max_sl_filtered,
            use_partial_exit=False,
            use_full_trail=True,
            brokerage=brokerage,
        )

        # ── Save results ───────────────────────────────────────────
        save_trades_csv(trades_v2, "backtest_trades_v2_partial.csv")
        csv_v3 = save_trades_csv(trades_v3, "backtest_trades_v3_full3R.csv")
        json_v3 = save_summary_json(summary_v3, "backtest_summary_v3_full3R.json")

        # ── Print 3-way comparison ─────────────────────────────────
        _print_comparison_3way(summary_v1, summary_v2, summary_v3, instruments, brokerage)
        print(f"\n  V3 trade log : {csv_v3}")
        print(f"  V3 summary   : {json_v3}")

    else:
        # ── Single run ─────────────────────────────────────────────
        kwargs: dict = {}
        if args.filter:
            kwargs["max_entry_hour"] = 10
            kwargs["max_sl"] = max_sl_filtered
        if args.partial_exit:
            kwargs["use_partial_exit"] = True
        kwargs["use_full_trail"] = args.full_trail
        kwargs["brokerage"] = brokerage

        trades, summary = run_backtest(instruments, candle_sources, **kwargs)

        suffix = "_filtered" if (args.filter or args.partial_exit) else ""
        csv_path = save_trades_csv(trades, f"backtest_trades{suffix}.csv")
        json_path = save_summary_json(summary, f"backtest_summary{suffix}.json")
        print_summary_table(summary)
        print(f"  Trade log : {csv_path} ({len(trades)} trades)")
        print(f"  Summary   : {json_path}")


# ── 3-way comparison printer ───────────────────────────────────────────
def _print_comparison_3way(
    v1: dict, v2: dict, v3: dict,
    instruments: List[str],
    brokerage: Dict[str, float],
) -> None:
    """Print a side-by-side V1 vs V2 vs V3 comparison table."""
    print("\n" + "=" * 90)
    print("  COMPARISON:  V1 (Baseline)  vs  V2 (Partial 1.5R)  vs  V3 (Full 3R)")
    print("=" * 90)

    header = f"  {'Metric':<28s} {'V1 BASELINE':>18s} {'V2 PARTIAL':>18s} {'V3 FULL 3R':>18s}"
    print(header)
    print("  " + "-" * 84)

    def row(label, a, b, c):
        print(f"  {label:<28s} {str(a):>18s} {str(b):>18s} {str(c):>18s}")

    o1, o2, o3 = v1["overall"], v2["overall"], v3["overall"]
    row("Total trades", o1["total_trades"], o2["total_trades"], o3["total_trades"])
    row("Total wins", o1["total_wins"], o2["total_wins"], o3["total_wins"])
    row("Win rate %",
        f"{o1['win_rate_%']:.1f}%", f"{o2['win_rate_%']:.1f}%", f"{o3['win_rate_%']:.1f}%")
    row("Total PnL (pts)",
        f"{o1['total_pnl_points']:+.1f}", f"{o2['total_pnl_points']:+.1f}", f"{o3['total_pnl_points']:+.1f}")

    for inst in instruments:
        if inst not in v1 or inst not in v2 or inst not in v3:
            continue
        i1, i2, i3 = v1[inst], v2[inst], v3[inst]
        print(f"\n  [{inst}]")
        row("  Trades", i1["total_trades"], i2["total_trades"], i3["total_trades"])
        row("  Wins / Losses / EOD",
            f"{i1['wins']}W/{i1['losses']}L/{i1['eod_exits']}E",
            f"{i2['wins']}W/{i2['losses']}L/{i2['eod_exits']}E",
            f"{i3['wins']}W/{i3['losses']}L/{i3['eod_exits']}E")
        row("  Win rate %",
            f"{i1['win_rate_%']:.1f}%", f"{i2['win_rate_%']:.1f}%", f"{i3['win_rate_%']:.1f}%")
        row("  Total PnL (pts)",
            f"{i1['total_pnl_points']:+.1f}", f"{i2['total_pnl_points']:+.1f}", f"{i3['total_pnl_points']:+.1f}")
        row("  Max drawdown (pts)",
            f"{i1['max_drawdown_points']:.1f}", f"{i2['max_drawdown_points']:.1f}", f"{i3['max_drawdown_points']:.1f}")
        row("  Avg RR on wins",
            f"{i1['avg_rr_on_wins']:.2f}", f"{i2['avg_rr_on_wins']:.2f}", f"{i3['avg_rr_on_wins']:.2f}")
        brk = brokerage.get(inst, 0)
        if brk:
            brok2 = brk * i2["total_trades"]
            brok3 = brk * i3["total_trades"]
            row("  Brokerage deducted", "", f"-{brok2:.1f}pts", f"-{brok3:.1f}pts")

    print("=" * 90)


if __name__ == "__main__":
    main()
