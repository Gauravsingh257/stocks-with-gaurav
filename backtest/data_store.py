"""
backtest/data_store.py — SQLite Historical Data Store
=====================================================
Stores and retrieves OHLCV candles for backtesting.
Supports multiple symbols and timeframes.
Data can be populated via data_fetcher.py (Kite API) or CSV import.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "backtest_data.db")


class DataStore:
    """SQLite-backed historical candle store."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = os.path.abspath(db_path)
        self._conn = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol     TEXT NOT NULL,
                timeframe  TEXT NOT NULL,
                dt         TEXT NOT NULL,
                open       REAL NOT NULL,
                high       REAL NOT NULL,
                low        REAL NOT NULL,
                close      REAL NOT NULL,
                volume     INTEGER DEFAULT 0,
                PRIMARY KEY (symbol, timeframe, dt)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_candles_sym_tf
            ON candles (symbol, timeframe, dt)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS data_meta (
                symbol     TEXT NOT NULL,
                timeframe  TEXT NOT NULL,
                first_date TEXT,
                last_date  TEXT,
                row_count  INTEGER DEFAULT 0,
                updated_at TEXT,
                PRIMARY KEY (symbol, timeframe)
            )
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # INSERT
    # ------------------------------------------------------------------
    def insert_candles(self, symbol: str, timeframe: str,
                       candles: List[Dict], replace: bool = True):
        """
        Insert candles into the store.

        Each candle dict must have: date (datetime or str), open, high, low, close.
        Optional: volume (defaults to 0).
        """
        if not candles:
            return 0

        conn = self._get_conn()
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        rows = []
        for c in candles:
            dt = c.get("date") or c.get("dt") or c.get("datetime")
            if isinstance(dt, datetime):
                dt = dt.isoformat()
            elif isinstance(dt, str):
                pass  # keep as-is
            else:
                continue
            rows.append((
                symbol, timeframe, dt,
                float(c["open"]), float(c["high"]),
                float(c["low"]), float(c["close"]),
                int(c.get("volume", 0))
            ))

        conn.executemany(
            f"{verb} INTO candles (symbol, timeframe, dt, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows
        )
        # Update metadata
        if rows:
            dates = [r[2] for r in rows]
            conn.execute(
                "INSERT OR REPLACE INTO data_meta (symbol, timeframe, first_date, last_date, row_count, updated_at) "
                "VALUES (?, ?, "
                "  COALESCE((SELECT MIN(first_date, ?) FROM data_meta WHERE symbol=? AND timeframe=?), ?), "
                "  COALESCE((SELECT MAX(last_date, ?) FROM data_meta WHERE symbol=? AND timeframe=?), ?), "
                "  (SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=?), "
                "  ?)",
                (symbol, timeframe,
                 min(dates), symbol, timeframe, min(dates),
                 max(dates), symbol, timeframe, max(dates),
                 symbol, timeframe,
                 datetime.now().isoformat())
            )
        conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # QUERY
    # ------------------------------------------------------------------
    def get_candles(self, symbol: str, timeframe: str,
                    start: Optional[str] = None, end: Optional[str] = None,
                    as_dicts: bool = True) -> List[Dict]:
        """
        Retrieve candles, optionally filtered by date range.

        Returns list of dicts: {date, open, high, low, close, volume}
        sorted by date ascending.
        """
        conn = self._get_conn()
        query = "SELECT dt, open, high, low, close, volume FROM candles WHERE symbol=? AND timeframe=?"
        params: list = [symbol, timeframe]

        if start:
            query += " AND dt >= ?"
            params.append(start)
        if end:
            query += " AND dt <= ?"
            params.append(end)

        query += " ORDER BY dt ASC"
        rows = conn.execute(query, params).fetchall()

        if as_dicts:
            return [
                {"date": r[0], "open": r[1], "high": r[2],
                 "low": r[3], "close": r[4], "volume": r[5]}
                for r in rows
            ]
        return rows

    def get_symbols(self, timeframe: Optional[str] = None) -> List[str]:
        """List all symbols in the store (optionally filtered by timeframe)."""
        conn = self._get_conn()
        if timeframe:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM data_meta WHERE timeframe=?",
                (timeframe,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT symbol FROM data_meta").fetchall()
        return [r[0] for r in rows]

    def get_date_range(self, symbol: str, timeframe: str) -> Tuple[Optional[str], Optional[str]]:
        """Return (first_date, last_date) for a symbol/timeframe, or (None, None)."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT first_date, last_date FROM data_meta WHERE symbol=? AND timeframe=?",
            (symbol, timeframe)
        ).fetchone()
        if row:
            return row[0], row[1]
        return None, None

    def get_candle_count(self, symbol: str, timeframe: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe)
        ).fetchone()
        return row[0] if row else 0

    def summary(self) -> List[Dict]:
        """Return summary of all data in the store."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT symbol, timeframe, first_date, last_date, row_count FROM data_meta "
            "ORDER BY symbol, timeframe"
        ).fetchall()
        return [
            {"symbol": r[0], "timeframe": r[1],
             "first_date": r[2], "last_date": r[3], "row_count": r[4]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # CSV IMPORT
    # ------------------------------------------------------------------
    def import_csv(self, filepath: str, symbol: str, timeframe: str,
                   date_col: str = "date", date_format: str = "%Y-%m-%d %H:%M:%S"):
        """
        Import candles from a CSV file.

        Expected columns: date (or datetime), open, high, low, close, volume (optional).
        """
        import csv

        candles = []
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Normalize column names to lowercase
            for row in reader:
                row_lc = {k.strip().lower(): v for k, v in row.items()}
                dt_str = row_lc.get(date_col.lower()) or row_lc.get("datetime") or row_lc.get("date")
                if not dt_str:
                    continue
                try:
                    candles.append({
                        "date": dt_str.strip(),
                        "open": float(row_lc["open"]),
                        "high": float(row_lc["high"]),
                        "low": float(row_lc["low"]),
                        "close": float(row_lc["close"]),
                        "volume": int(float(row_lc.get("volume", 0)))
                    })
                except (ValueError, KeyError) as e:
                    continue  # skip malformed rows

        count = self.insert_candles(symbol, timeframe, candles)
        logger.info(f"Imported {count} candles for {symbol}/{timeframe} from {filepath}")
        return count

    # ------------------------------------------------------------------
    # CLEANUP
    # ------------------------------------------------------------------
    def delete_symbol(self, symbol: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM candles WHERE symbol=?", (symbol,))
        conn.execute("DELETE FROM data_meta WHERE symbol=?", (symbol,))
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
