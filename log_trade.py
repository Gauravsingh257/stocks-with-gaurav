"""
log_trade.py
============
Logs a single manually-analyzed trade into the AI training store.
Called programmatically after screenshot analysis.

Usage (from code):
    from log_trade import log_single_trade
    log_single_trade({...})
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_learning.data.trade_store import TradeStore
from ai_learning.data.schemas import ManualTrade


def log_single_trade(data: dict) -> dict:
    """
    Log one trade into the training store.

    Required keys:
        symbol, direction, entry_price, sl_price, target_price,
        result, timeframe, date

    Optional keys:
        pnl_r, setup_type, session, notes, htf_bias, ltf_trigger,
        ob_present, fvg_present, bos, choch, liq_sweep,
        displacement, in_ote, confluence_notes, chart_image
    """
    store = TradeStore()

    # Compute pnl_r if not provided
    entry = float(data["entry_price"])
    sl = float(data["sl_price"])
    target = float(data["target_price"])
    result = data["result"].upper()
    direction = data["direction"].upper()

    risk = abs(entry - sl)
    reward = abs(target - entry)
    rr = round(reward / risk, 2) if risk > 0 else 2.0

    if "pnl_r" not in data or data["pnl_r"] is None:
        if result == "WIN":
            data["pnl_r"] = rr
        elif result == "LOSS":
            data["pnl_r"] = -1.0
        else:
            data["pnl_r"] = 0.0

    # Build notes from all SMC fields
    smc_notes = []
    if data.get("htf_bias"):
        smc_notes.append(f"HTF: {data['htf_bias']}")
    if data.get("ltf_trigger"):
        smc_notes.append(f"Trigger: {data['ltf_trigger']}")
    if data.get("ob_present"):
        smc_notes.append("OB present")
    if data.get("fvg_present"):
        smc_notes.append("FVG present")
    if data.get("bos"):
        smc_notes.append("BOS confirmed")
    if data.get("choch"):
        smc_notes.append("CHoCH confirmed")
    if data.get("liq_sweep"):
        smc_notes.append("Liquidity sweep")
    if data.get("displacement"):
        smc_notes.append("Displacement candle")
    if data.get("in_ote"):
        smc_notes.append("Entry in OTE zone")
    if data.get("confluence_notes"):
        smc_notes.append(data["confluence_notes"])
    if data.get("notes"):
        smc_notes.append(data["notes"])

    # Generate trade_id
    existing = store.get_all_trades()
    ss_trades = [t for t in existing if t.trade_id.startswith("SS-")]
    next_num = len(ss_trades) + 1
    trade_id = f"SS-{next_num:04d}"

    trade = ManualTrade(
        trade_id=trade_id,
        symbol=data["symbol"].strip(),
        timeframe=data.get("timeframe", "5minute"),
        direction=direction,
        entry=entry,
        stop_loss=sl,
        target=target,
        result=result,
        pnl_r=float(data["pnl_r"]),
        notes=" | ".join(smc_notes) if smc_notes else data.get("notes", ""),
        timestamp=data.get("date", datetime.now().strftime("%Y-%m-%d")),
        chart_image=data.get("chart_image", ""),
        setup_type=data.get("setup_type", ""),
        session=data.get("session", ""),
        # Raw SMC fields stored in notes & extra dict
        extra={
            "htf_bias": data.get("htf_bias", ""),
            "ltf_trigger": data.get("ltf_trigger", ""),
            "ob_present": data.get("ob_present", False),
            "fvg_present": data.get("fvg_present", False),
            "bos": data.get("bos", False),
            "choch": data.get("choch", False),
            "liq_sweep": data.get("liq_sweep", False),
            "displacement": data.get("displacement", False),
            "in_ote": data.get("in_ote", False),
            "multi_tf_confluence": data.get("multi_tf_confluence", False),
            "pattern": data.get("pattern", ""),
        }
    )

    stored_id = store.add_trade(trade)

    # Count totals
    all_trades = store.get_all_trades()
    ss_count = len([t for t in all_trades if t.trade_id.startswith("SS-")])
    total_count = len(all_trades)

    result_summary = {
        "trade_id": stored_id,
        "symbol": trade.symbol,
        "direction": direction,
        "result": result,
        "pnl_r": trade.pnl_r,
        "rr_ratio": rr,
        "ss_trades_logged": ss_count,
        "total_trades_in_db": total_count,
        "ready_for_training": total_count >= 15,
    }

    print(json.dumps(result_summary, indent=2))
    return result_summary


def get_training_status() -> dict:
    """Show current training database status."""
    store = TradeStore()
    all_trades = store.get_all_trades()

    ss_trades = [t for t in all_trades if t.trade_id.startswith("SS-")]
    wins = sum(1 for t in all_trades if t.result == "WIN")
    losses = sum(1 for t in all_trades if t.result == "LOSS")

    setups = {}
    for t in all_trades:
        k = t.setup_type or "unknown"
        setups[k] = setups.get(k, 0) + 1

    ss_count = len(ss_trades)
    return {
        "screenshot_trades": ss_count,
        "total_trades": len(all_trades),
        "win_rate": round(wins / len(all_trades) * 100, 1) if all_trades else 0,
        "wins": wins,
        "losses": losses,
        "setups": setups,
        "ready_for_training": len(all_trades) >= 15,
        "recent_ss_trades": [
            {
                "id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "result": t.result,
                "pnl_r": t.pnl_r,
                "date": t.timestamp,
            }
            for t in ss_trades[-5:]
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(get_training_status(), indent=2))
    elif len(sys.argv) > 1:
        try:
            data = json.loads(sys.argv[1])
            log_single_trade(data)
        except json.JSONDecodeError:
            print("Pass trade data as JSON string or 'status'")
