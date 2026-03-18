"""
engine/paper_mode.py — Paper Trading Mode
==========================================
Phase 6: Structured paper trade logging and validation.

When PAPER_MODE=1 env var is set:
  - All Telegram alerts are prefixed with [PAPER]
  - Every signal is logged to paper_trade_log.csv
  - Trade outcomes (SL/TP hit) are tracked in paper_trade_outcomes.csv
  - Daily PnL summary sent at EOD
"""

import os
import csv
import logging
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore
_IST = ZoneInfo("Asia/Kolkata")

# Hardcoded ON — safe default until live trading is validated
# To switch to LIVE, change to: PAPER_MODE = False
PAPER_MODE = True

# File paths
PAPER_TRADE_LOG = "paper_trade_log.csv"
PAPER_OUTCOMES_LOG = "paper_trade_outcomes.csv"

_LOG_FIELDS = [
    "timestamp", "symbol", "setup", "direction", "entry", "sl", "target",
    "rr", "risk_mult", "smc_score", "grade", "atr", "killzone_conf",
]

_OUTCOME_FIELDS = [
    "timestamp", "symbol", "setup", "direction", "entry", "sl", "target",
    "exit_price", "exit_time", "result", "pnl_r", "bars_held",
]

logger = logging.getLogger("paper_mode")


def _ensure_csv(path: str, fields: list):
    """Create CSV with headers if it doesn't exist."""
    if not Path(path).exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()


def log_paper_trade(signal: dict):
    """Log a new paper trade signal to CSV."""
    if not PAPER_MODE:
        return

    _ensure_csv(PAPER_TRADE_LOG, _LOG_FIELDS)

    row = {
        "timestamp": datetime.now(_IST).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": signal.get("symbol", ""),
        "setup": signal.get("setup", ""),
        "direction": signal.get("direction", ""),
        "entry": signal.get("entry", ""),
        "sl": signal.get("sl", ""),
        "target": signal.get("target", ""),
        "rr": signal.get("rr", ""),
        "risk_mult": signal.get("risk_mult", 1.0),
        "smc_score": signal.get("smc_score", ""),
        "grade": signal.get("grade", ""),
        "atr": signal.get("atr", ""),
        "killzone_conf": signal.get("killzone_conf", ""),
    }

    try:
        with open(PAPER_TRADE_LOG, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_LOG_FIELDS).writerow(row)
        logger.info(f"[PAPER] Logged: {row['symbol']} {row['setup']} {row['direction']}")
    except Exception as e:
        logger.error(f"[PAPER] CSV write failed: {e}")


def log_paper_outcome(trade: dict, exit_price: float, result: str, pnl_r: float):
    """Log a paper trade outcome (SL/TP hit or EOD close)."""
    if not PAPER_MODE:
        return

    _ensure_csv(PAPER_OUTCOMES_LOG, _OUTCOME_FIELDS)

    row = {
        "timestamp": trade.get("start_time", datetime.now(_IST).replace(tzinfo=None)).strftime("%Y-%m-%d %H:%M:%S")
            if hasattr(trade.get("start_time", ""), "strftime")
            else str(trade.get("start_time", "")),
        "symbol": trade.get("symbol", ""),
        "setup": trade.get("setup", ""),
        "direction": trade.get("direction", ""),
        "entry": trade.get("entry", ""),
        "sl": trade.get("sl", ""),
        "target": trade.get("target", ""),
        "exit_price": exit_price,
        "exit_time": datetime.now(_IST).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        "result": result,
        "pnl_r": round(pnl_r, 2),
        "bars_held": trade.get("bars_held", ""),
    }

    try:
        with open(PAPER_OUTCOMES_LOG, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_OUTCOME_FIELDS).writerow(row)
        logger.info(f"[PAPER] Outcome: {row['symbol']} {result} {pnl_r:+.2f}R")
    except Exception as e:
        logger.error(f"[PAPER] Outcome write failed: {e}")


def paper_prefix(msg: str) -> str:
    """Prefix a Telegram message with [PAPER] tag if in paper mode."""
    if not PAPER_MODE:
        return msg
    if msg.startswith("[PAPER]") or msg.startswith("<b>[PAPER]"):
        return msg  # Already prefixed
    return f"[PAPER] {msg}"


def paper_daily_summary() -> str:
    """Generate a daily paper trading summary from today's outcomes CSV."""
    if not Path(PAPER_OUTCOMES_LOG).exists():
        return "[PAPER] No trades recorded today."

    today = datetime.now(_IST).replace(tzinfo=None).strftime("%Y-%m-%d")
    wins, losses, total_r = 0, 0, 0.0

    try:
        with open(PAPER_OUTCOMES_LOG, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get("exit_time", "").startswith(today):
                    continue
                pnl_r = float(row.get("pnl_r", 0))
                total_r += pnl_r
                if pnl_r > 0:
                    wins += 1
                elif pnl_r < 0:
                    losses += 1
    except Exception as e:
        return f"[PAPER] Summary error: {e}"

    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0

    return (
        f"<b>[PAPER] Daily Summary</b>\n"
        f"Date: {today}\n"
        f"Trades: {total} (W: {wins} / L: {losses})\n"
        f"Win Rate: {wr:.0f}%\n"
        f"Daily PnL: {total_r:+.1f}R\n"
        f"---\n"
        f"{'PASSING' if total_r > 0 and total >= 2 else 'UNDER OBSERVATION'}"
    )


def paper_mode_banner() -> str:
    """Return the startup banner for paper mode."""
    sep = "=" * 50
    return (
        f"\n{sep}\n"
        f"  PAPER TRADING MODE\n"
        f"  No real orders will be placed.\n"
        f"  Signals logged to: paper_trade_log.csv\n"
        f"  Outcomes logged to: paper_trade_outcomes.csv\n"
        f"{sep}\n"
    )


# Validation criteria for paper trading pass
PASS_CRITERIA = {
    "min_days": 10,       # Minimum trading days
    "min_trades": 20,     # Minimum total trades
    "min_win_rate": 45,   # Minimum win rate %
    "min_profit_factor": 1.2,
    "min_expectancy_r": 0.05,
    "max_drawdown_r": -5.0,
}
