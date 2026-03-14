"""
Trade Store — SQLite persistence for manual trades and extracted features.
==========================================================================
Stores manual trade records, extracted SMC features, learned profiles,
generated strategies, and optimization results.
"""

import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from ai_learning.config import DB_PATH
from ai_learning.data.schemas import (
    ManualTrade, SMCFeatures, TradingStyleProfile,
    StrategyRule, OptimizationResult
)


class TradeStore:
    """SQLite-backed store for the AI learning pipeline."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _migrate_db(self, conn):
        """Add new columns to existing DB without breaking old data."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(manual_trades)").fetchall()}
        migrations = [
            ("setup_type", "ALTER TABLE manual_trades ADD COLUMN setup_type TEXT DEFAULT ''"),
            ("session",    "ALTER TABLE manual_trades ADD COLUMN session    TEXT DEFAULT ''"),
            ("extra_json", "ALTER TABLE manual_trades ADD COLUMN extra_json TEXT DEFAULT '{}'"),
        ]
        for col, sql in migrations:
            if col not in existing:
                conn.execute(sql)

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS manual_trades (
                    trade_id     TEXT PRIMARY KEY,
                    symbol       TEXT NOT NULL,
                    timeframe    TEXT NOT NULL,
                    direction    TEXT NOT NULL,
                    entry        REAL NOT NULL,
                    stop_loss    REAL NOT NULL,
                    target       REAL NOT NULL,
                    result       TEXT,
                    pnl_r        REAL,
                    chart_image  TEXT,
                    notes        TEXT DEFAULT '',
                    timestamp    TEXT,
                    exit_price   REAL,
                    exit_time    TEXT,
                    setup_type   TEXT DEFAULT '',
                    session      TEXT DEFAULT '',
                    extra_json   TEXT DEFAULT '{}',
                    created_at   TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS smc_features (
                    trade_id             TEXT PRIMARY KEY,
                    htf_trend            TEXT,
                    ltf_trend            TEXT,
                    trend_aligned        INTEGER DEFAULT 0,
                    order_block_present  INTEGER DEFAULT 0,
                    ob_zone_json         TEXT,
                    ob_distance_atr      REAL DEFAULT 0,
                    entry_inside_ob      INTEGER DEFAULT 0,
                    fvg_present          INTEGER DEFAULT 0,
                    fvg_zone_json        TEXT,
                    fvg_distance_atr     REAL DEFAULT 0,
                    fvg_quality          REAL DEFAULT 0,
                    liquidity_sweep      INTEGER DEFAULT 0,
                    equal_highs_nearby   INTEGER DEFAULT 0,
                    equal_lows_nearby    INTEGER DEFAULT 0,
                    sweep_type           TEXT DEFAULT 'NONE',
                    bos_detected         INTEGER DEFAULT 0,
                    choch_detected       INTEGER DEFAULT 0,
                    displacement_detected INTEGER DEFAULT 0,
                    displacement_strength REAL DEFAULT 0,
                    in_discount          INTEGER DEFAULT 0,
                    in_premium           INTEGER DEFAULT 0,
                    in_ote               INTEGER DEFAULT 0,
                    zone_detail          TEXT DEFAULT 'UNKNOWN',
                    session              TEXT DEFAULT 'UNKNOWN',
                    minutes_from_open    INTEGER DEFAULT 0,
                    is_killzone          INTEGER DEFAULT 0,
                    atr                  REAL DEFAULT 0,
                    atr_percentile       REAL DEFAULT 0,
                    range_expansion      INTEGER DEFAULT 0,
                    volatility_regime    TEXT DEFAULT 'NORMAL',
                    rejection_candle     INTEGER DEFAULT 0,
                    engulfing_candle     INTEGER DEFAULT 0,
                    pin_bar              INTEGER DEFAULT 0,
                    confluence_score     INTEGER DEFAULT 0,
                    feature_vector_json  TEXT,
                    FOREIGN KEY (trade_id) REFERENCES manual_trades(trade_id)
                );

                CREATE TABLE IF NOT EXISTS style_profiles (
                    profile_id   TEXT PRIMARY KEY,
                    trader_id    TEXT DEFAULT 'default',
                    created_at   TEXT DEFAULT (datetime('now')),
                    profile_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_rules (
                    rule_id        TEXT PRIMARY KEY,
                    strategy_name  TEXT NOT NULL,
                    direction      TEXT,
                    rule_json      TEXT NOT NULL,
                    confidence     REAL DEFAULT 0,
                    source_cluster INTEGER DEFAULT -1,
                    created_at     TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS optimization_results (
                    opt_id          TEXT PRIMARY KEY,
                    strategy_name   TEXT NOT NULL,
                    result_json     TEXT NOT NULL,
                    is_robust       INTEGER DEFAULT 0,
                    robustness_score REAL DEFAULT 0,
                    created_at      TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS signal_log (
                    signal_id       TEXT PRIMARY KEY,
                    timestamp       TEXT,
                    symbol          TEXT,
                    direction       TEXT,
                    strategy_name   TEXT,
                    entry           REAL,
                    stop_loss       REAL,
                    target1         REAL,
                    target2         REAL,
                    score           REAL,
                    confidence      REAL,
                    result          TEXT,
                    pnl_r           REAL,
                    signal_json     TEXT,
                    created_at      TEXT DEFAULT (datetime('now'))
                );
            """)
            self._migrate_db(conn)

    # ─── Manual Trades CRUD ───────────────────────────────────────────

    def add_trade(self, trade: ManualTrade) -> str:
        """Insert a manual trade. Returns trade_id."""
        if not trade.trade_id:
            trade.trade_id = f"MT-{uuid.uuid4().hex[:8]}"
        with self._conn() as conn:
            self._migrate_db(conn)
            conn.execute("""
                INSERT OR REPLACE INTO manual_trades
                (trade_id, symbol, timeframe, direction, entry, stop_loss,
                 target, result, pnl_r, chart_image, notes, timestamp,
                 exit_price, exit_time, setup_type, session, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id, trade.symbol, trade.timeframe, trade.direction,
                trade.entry, trade.stop_loss, trade.target, trade.result,
                trade.pnl_r, trade.chart_image, trade.notes, trade.timestamp,
                trade.exit_price, trade.exit_time,
                trade.setup_type, trade.session,
                json.dumps(trade.extra) if trade.extra else "{}"
            ))
        return trade.trade_id

    def add_trades_bulk(self, trades: List[ManualTrade]) -> int:
        """Bulk insert trades. Returns count inserted."""
        count = 0
        with self._conn() as conn:
            self._migrate_db(conn)
            for t in trades:
                if not t.trade_id:
                    t.trade_id = f"MT-{uuid.uuid4().hex[:8]}"
                conn.execute("""
                    INSERT OR REPLACE INTO manual_trades
                    (trade_id, symbol, timeframe, direction, entry, stop_loss,
                     target, result, pnl_r, chart_image, notes, timestamp,
                     exit_price, exit_time, setup_type, session, extra_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    t.trade_id, t.symbol, t.timeframe, t.direction,
                    t.entry, t.stop_loss, t.target, t.result,
                    t.pnl_r, t.chart_image, t.notes, t.timestamp,
                    t.exit_price, t.exit_time,
                    t.setup_type, t.session,
                    json.dumps(t.extra) if t.extra else "{}"
                ))
                count += 1
        return count

    def get_trade(self, trade_id: str) -> Optional[ManualTrade]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM manual_trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_trade(row)

    def get_all_trades(self) -> List[ManualTrade]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM manual_trades ORDER BY timestamp"
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_trades_by_symbol(self, symbol: str) -> List[ManualTrade]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM manual_trades WHERE symbol = ? ORDER BY timestamp",
                (symbol,)
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def trade_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM manual_trades").fetchone()
        return row[0]

    # ─── SMC Features CRUD ────────────────────────────────────────────

    def save_features(self, features: SMCFeatures):
        """Save extracted SMC features for a trade."""
        vec = features.to_feature_vector()
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO smc_features
                (trade_id, htf_trend, ltf_trend, trend_aligned,
                 order_block_present, ob_zone_json, ob_distance_atr, entry_inside_ob,
                 fvg_present, fvg_zone_json, fvg_distance_atr, fvg_quality,
                 liquidity_sweep, equal_highs_nearby, equal_lows_nearby, sweep_type,
                 bos_detected, choch_detected, displacement_detected, displacement_strength,
                 in_discount, in_premium, in_ote, zone_detail,
                 session, minutes_from_open, is_killzone,
                 atr, atr_percentile, range_expansion, volatility_regime,
                 rejection_candle, engulfing_candle, pin_bar,
                 confluence_score, feature_vector_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                features.trade_id, features.htf_trend, features.ltf_trend,
                int(features.trend_aligned),
                int(features.order_block_present),
                json.dumps(features.ob_zone) if features.ob_zone else None,
                features.ob_distance_atr, int(features.entry_inside_ob),
                int(features.fvg_present),
                json.dumps(features.fvg_zone) if features.fvg_zone else None,
                features.fvg_distance_atr, features.fvg_quality,
                int(features.liquidity_sweep), int(features.equal_highs_nearby),
                int(features.equal_lows_nearby), features.sweep_type,
                int(features.bos_detected), int(features.choch_detected),
                int(features.displacement_detected), features.displacement_strength,
                int(features.in_discount), int(features.in_premium), int(features.in_ote),
                features.zone_detail,
                features.session, features.minutes_from_open, int(features.is_killzone),
                features.atr, features.atr_percentile, int(features.range_expansion),
                features.volatility_regime,
                int(features.rejection_candle), int(features.engulfing_candle),
                int(features.pin_bar),
                features.confluence_score, json.dumps(vec)
            ))

    def get_features(self, trade_id: str) -> Optional[SMCFeatures]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM smc_features WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_features(row)

    def get_all_features(self) -> List[SMCFeatures]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM smc_features").fetchall()
        return [self._row_to_features(r) for r in rows]

    def get_all_feature_vectors(self) -> List[tuple]:
        """Returns list of (trade_id, feature_vector)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT trade_id, feature_vector_json FROM smc_features "
                "WHERE feature_vector_json IS NOT NULL"
            ).fetchall()
        return [(r["trade_id"], json.loads(r["feature_vector_json"])) for r in rows]

    # ─── Profiles & Strategies ────────────────────────────────────────

    def save_profile(self, profile: TradingStyleProfile) -> str:
        pid = f"PROF-{uuid.uuid4().hex[:8]}"
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO style_profiles (profile_id, trader_id, profile_json)
                VALUES (?, ?, ?)
            """, (pid, profile.trader_id, profile.to_json()))
        return pid

    def get_latest_profile(self, trader_id: str = "default") -> Optional[TradingStyleProfile]:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT profile_json FROM style_profiles
                WHERE trader_id = ? ORDER BY created_at DESC LIMIT 1
            """, (trader_id,)).fetchone()
        if not row:
            return None
        d = json.loads(row["profile_json"])
        return self._dict_to_profile(d)

    def save_strategy_rule(self, rule: StrategyRule):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO strategy_rules
                (rule_id, strategy_name, direction, rule_json, confidence, source_cluster)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                rule.rule_id, rule.strategy_name, rule.direction,
                json.dumps(rule.to_dict()), rule.confidence, rule.source_cluster
            ))

    def get_all_strategy_rules(self) -> List[StrategyRule]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT rule_json FROM strategy_rules ORDER BY confidence DESC"
            ).fetchall()
        return [self._dict_to_rule(json.loads(r["rule_json"])) for r in rows]

    def save_optimization_result(self, opt: OptimizationResult):
        oid = f"OPT-{uuid.uuid4().hex[:8]}"
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO optimization_results
                (opt_id, strategy_name, result_json, is_robust, robustness_score)
                VALUES (?, ?, ?, ?, ?)
            """, (oid, opt.strategy_name, json.dumps(opt.to_dict()),
                  int(opt.is_robust), opt.robustness_score))

    # ─── Signal Log ───────────────────────────────────────────────────

    def log_signal(self, signal_dict: dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO signal_log
                (signal_id, timestamp, symbol, direction, strategy_name,
                 entry, stop_loss, target1, target2, score, confidence,
                 signal_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_dict.get("signal_id", f"SIG-{uuid.uuid4().hex[:8]}"),
                signal_dict.get("timestamp"), signal_dict.get("symbol"),
                signal_dict.get("direction"), signal_dict.get("strategy_name"),
                signal_dict.get("entry"), signal_dict.get("stop_loss"),
                signal_dict.get("target1"), signal_dict.get("target2"),
                signal_dict.get("score"), signal_dict.get("confidence"),
                json.dumps(signal_dict)
            ))

    def get_signal_history(self, limit: int = 100) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT signal_json FROM signal_log ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [json.loads(r["signal_json"]) for r in rows]

    # ─── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_trade(row) -> ManualTrade:
        keys = row.keys()
        return ManualTrade(
            trade_id=row["trade_id"], symbol=row["symbol"],
            timeframe=row["timeframe"], direction=row["direction"],
            entry=row["entry"], stop_loss=row["stop_loss"],
            target=row["target"], result=row["result"],
            pnl_r=row["pnl_r"], chart_image=row["chart_image"],
            notes=row["notes"] or "", timestamp=row["timestamp"],
            exit_price=row["exit_price"], exit_time=row["exit_time"],
            setup_type=row["setup_type"] if "setup_type" in keys else "",
            session=row["session"] if "session" in keys else "",
            extra=json.loads(row["extra_json"]) if ("extra_json" in keys and row["extra_json"]) else {},
        )

    @staticmethod
    def _row_to_features(row) -> SMCFeatures:
        return SMCFeatures(
            trade_id=row["trade_id"],
            htf_trend=row["htf_trend"] or "UNKNOWN",
            ltf_trend=row["ltf_trend"] or "UNKNOWN",
            trend_aligned=bool(row["trend_aligned"]),
            order_block_present=bool(row["order_block_present"]),
            ob_zone=json.loads(row["ob_zone_json"]) if row["ob_zone_json"] else None,
            ob_distance_atr=row["ob_distance_atr"] or 0,
            entry_inside_ob=bool(row["entry_inside_ob"]),
            fvg_present=bool(row["fvg_present"]),
            fvg_zone=json.loads(row["fvg_zone_json"]) if row["fvg_zone_json"] else None,
            fvg_distance_atr=row["fvg_distance_atr"] or 0,
            fvg_quality=row["fvg_quality"] or 0,
            liquidity_sweep=bool(row["liquidity_sweep"]),
            equal_highs_nearby=bool(row["equal_highs_nearby"]),
            equal_lows_nearby=bool(row["equal_lows_nearby"]),
            sweep_type=row["sweep_type"] or "NONE",
            bos_detected=bool(row["bos_detected"]),
            choch_detected=bool(row["choch_detected"]),
            displacement_detected=bool(row["displacement_detected"]),
            displacement_strength=row["displacement_strength"] or 0,
            in_discount=bool(row["in_discount"]),
            in_premium=bool(row["in_premium"]),
            in_ote=bool(row["in_ote"]),
            zone_detail=row["zone_detail"] or "UNKNOWN",
            session=row["session"] or "UNKNOWN",
            minutes_from_open=row["minutes_from_open"] or 0,
            is_killzone=bool(row["is_killzone"]),
            atr=row["atr"] or 0,
            atr_percentile=row["atr_percentile"] or 0,
            range_expansion=bool(row["range_expansion"]),
            volatility_regime=row["volatility_regime"] or "NORMAL",
            rejection_candle=bool(row["rejection_candle"]),
            engulfing_candle=bool(row["engulfing_candle"]),
            pin_bar=bool(row["pin_bar"]),
            confluence_score=row["confluence_score"] or 0,
        )

    @staticmethod
    def _dict_to_profile(d: dict) -> TradingStyleProfile:
        profile = TradingStyleProfile()
        for k, v in d.items():
            if k == "strategies":
                profile.strategies = [StrategyCluster(**s) for s in v]
            elif hasattr(profile, k):
                setattr(profile, k, v)
        return profile

    @staticmethod
    def _dict_to_rule(d: dict) -> StrategyRule:
        return StrategyRule(**{k: v for k, v in d.items()
                              if k in StrategyRule.__dataclass_fields__})


# Re-import for convenience
from ai_learning.data.schemas import StrategyCluster  # noqa: E402
