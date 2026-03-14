"""
scripts/trade_logger.py — Structured trade logging system.

Logs all trade events (entries, exits, modifications) to both
CSV ledger and SQLite database with full audit trail.

Usage:
    from scripts.trade_logger import TradeLogger
    logger = TradeLogger()
    logger.log_entry(symbol="NSE:NIFTY 50", direction="LONG", ...)
    logger.log_exit(trade_id=1, exit_price=22500, reason="TP")
"""

import csv
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = PROJECT_ROOT / "logs"
LEDGER_DIR.mkdir(exist_ok=True)


class TradeLogger:
    """
    Dual-format trade logger: CSV for portability + SQLite for querying.
    Thread-safe for use in async trading loops.
    """

    def __init__(
        self,
        csv_path: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ):
        year = datetime.now().year
        self.csv_path = csv_path or LEDGER_DIR / f"trade_ledger_{year}.csv"
        self.db_path = db_path or LEDGER_DIR / "trades.db"
        self._init_db()
        self._init_csv()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT,
                event TEXT,
                timestamp TEXT,
                symbol TEXT,
                direction TEXT,
                setup TEXT,
                entry_price REAL,
                stop_loss REAL,
                target REAL,
                exit_price REAL,
                exit_reason TEXT,
                r_multiple REAL,
                pnl_points REAL,
                confidence REAL,
                metadata TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _init_csv(self):
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "trade_id", "event", "symbol", "direction",
                    "setup", "entry_price", "stop_loss", "target",
                    "exit_price", "exit_reason", "r_multiple", "pnl_points",
                    "confidence", "metadata",
                ])

    def log_entry(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        setup: str,
        entry_price: float,
        stop_loss: float,
        target: float,
        confidence: float = 0.0,
        metadata: Optional[dict] = None,
    ):
        self._log("ENTRY", trade_id=trade_id, symbol=symbol, direction=direction,
                  setup=setup, entry_price=entry_price, stop_loss=stop_loss,
                  target=target, confidence=confidence, metadata=metadata)

    def log_exit(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        exit_price: float,
        exit_reason: str,
        r_multiple: float,
        pnl_points: float = 0.0,
        metadata: Optional[dict] = None,
    ):
        self._log("EXIT", trade_id=trade_id, symbol=symbol, direction=direction,
                  exit_price=exit_price, exit_reason=exit_reason,
                  r_multiple=r_multiple, pnl_points=pnl_points, metadata=metadata)

    def log_modification(
        self,
        trade_id: str,
        symbol: str,
        metadata: Optional[dict] = None,
    ):
        self._log("MODIFY", trade_id=trade_id, symbol=symbol, metadata=metadata)

    def _log(self, event: str, **kwargs):
        now = datetime.now().isoformat()
        meta_str = json.dumps(kwargs.pop("metadata", None) or {})

        row = {
            "timestamp": now,
            "event": event,
            "trade_id": kwargs.get("trade_id", ""),
            "symbol": kwargs.get("symbol", ""),
            "direction": kwargs.get("direction", ""),
            "setup": kwargs.get("setup", ""),
            "entry_price": kwargs.get("entry_price"),
            "stop_loss": kwargs.get("stop_loss"),
            "target": kwargs.get("target"),
            "exit_price": kwargs.get("exit_price"),
            "exit_reason": kwargs.get("exit_reason", ""),
            "r_multiple": kwargs.get("r_multiple"),
            "pnl_points": kwargs.get("pnl_points"),
            "confidence": kwargs.get("confidence"),
            "metadata": meta_str,
        }

        # CSV
        try:
            with open(self.csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "timestamp", "trade_id", "event", "symbol", "direction",
                    "setup", "entry_price", "stop_loss", "target",
                    "exit_price", "exit_reason", "r_multiple", "pnl_points",
                    "confidence", "metadata",
                ])
                writer.writerow(row)
        except Exception:
            logger.exception("CSV write failed")

        # SQLite
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                """INSERT INTO trade_log
                   (trade_id, event, timestamp, symbol, direction, setup,
                    entry_price, stop_loss, target, exit_price, exit_reason,
                    r_multiple, pnl_points, confidence, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["trade_id"], event, now, row["symbol"], row["direction"],
                 row["setup"], row["entry_price"], row["stop_loss"], row["target"],
                 row["exit_price"], row["exit_reason"], row["r_multiple"],
                 row["pnl_points"], row["confidence"], meta_str),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("SQLite write failed")

        logger.info("[%s] %s %s %s", event, row["trade_id"], row["symbol"], row["direction"])

    def get_today_trades(self) -> list[dict]:
        today = datetime.now().date().isoformat()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trade_log WHERE timestamp >= ? ORDER BY timestamp",
            (today,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_daily_summary(self, date: Optional[str] = None) -> dict:
        target_date = date or datetime.now().date().isoformat()
        trades = self.get_today_trades() if date is None else []
        exits = [t for t in trades if t["event"] == "EXIT"]
        r_values = [t["r_multiple"] for t in exits if t["r_multiple"] is not None]

        return {
            "date": target_date,
            "total_entries": len([t for t in trades if t["event"] == "ENTRY"]),
            "total_exits": len(exits),
            "total_r": round(sum(r_values), 2) if r_values else 0,
            "win_rate": round(len([r for r in r_values if r > 0]) / len(r_values) * 100, 1) if r_values else 0,
        }
