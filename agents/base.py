"""
agents/base.py
BaseAgent — shared infrastructure for all AI agents.

Each agent:
  • Has a unique name, schedule, and priority
  • Reads state via dashboard.backend.state_bridge (read-only)
  • Reads/writes dashboard.db via dashboard.backend.db
  • Never directly calls kite or mutates engine globals
  • Queues actions requiring human approval via agent_action_queue table
  • Logs every run to agent_logs table
"""

from abc import ABC, abstractmethod
from datetime import datetime
import logging
import traceback

from dashboard.backend.db import (
    get_connection, log_agent_action, log_regime_change, DB_PATH
)
from dashboard.backend.state_bridge import get_engine_snapshot

log = logging.getLogger("agents")


class AgentResult:
    """Structured result returned by every agent run."""

    def __init__(self, agent_name: str):
        self.agent_name   = agent_name
        self.run_time     = datetime.now()
        self.status       = "OK"          # OK | WARNING | ERROR
        self.summary      = ""
        self.findings:  list[dict] = []   # structured findings
        self.actions:   list[dict] = []   # actions queued for approval
        self.metrics:   dict       = {}   # any numeric outputs
        self.raw_log:   str        = ""

    def add_finding(self, level: str, key: str, message: str, value=None):
        """level: INFO | WARNING | CRITICAL"""
        self.findings.append({"level": level, "key": key, "message": message, "value": value})
        if level == "CRITICAL":
            self.status = "WARNING"

    def queue_action(self, action_type: str, payload: dict, requires_approval: bool = True):
        """
        Queue an agent action for human review.
        action_type: SEND_TELEGRAM | ADJUST_PARAM | HALT_TRADING | etc.
        """
        self.actions.append({
            "action_type":       action_type,
            "payload":           payload,
            "requires_approval": requires_approval,
            "queued_at":         datetime.now().isoformat(),
        })

    def to_dict(self) -> dict:
        return {
            "agent":    self.agent_name,
            "run_time": self.run_time.isoformat(),
            "status":   self.status,
            "summary":  self.summary,
            "findings": self.findings,
            "actions":  self.actions,
            "metrics":  self.metrics,
        }


class BaseAgent(ABC):
    """
    Abstract base agent. Subclasses implement `run()`.
    """

    name:        str = "BaseAgent"
    description: str = ""
    schedule:    str = "manual"      # cron-like description for display
    priority:    str = "normal"      # low | normal | high | critical

    def execute(self) -> AgentResult:
        """
        Public entry-point. Wraps run() with error handling + logging.
        Returns AgentResult always — never raises.
        """
        result = AgentResult(self.name)
        log.info("[%s] Starting run…", self.name)

        try:
            self.run(result)
            log.info("[%s] Run complete — status=%s summary=%s", self.name, result.status, result.summary[:80])
        except Exception:
            result.status  = "ERROR"
            result.summary = "Unhandled exception during agent run"
            result.raw_log = traceback.format_exc()
            log.error("[%s] Unhandled exception:\n%s", self.name, result.raw_log)

        # ── Persist to agent_logs ────────────────────────────────────────────
        try:
            import json
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO agent_logs (agent_name, run_time, status, summary, findings_json, actions_json, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.name,
                    result.run_time.isoformat(),
                    result.status,
                    result.summary,
                    json.dumps(result.findings),
                    json.dumps(result.actions),
                    json.dumps(result.metrics),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("[%s] Failed to persist log: %s", self.name, e)

        # ── Flush queued actions to agent_action_queue ────────────────────────
        for action in result.actions:
            try:
                import json as _json
                conn = get_connection()
                conn.execute(
                    """
                    INSERT INTO agent_action_queue (agent, action_type, symbol, payload, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self.name,
                        action["action_type"],
                        action.get("payload", {}).get("symbol"),
                        _json.dumps(action.get("payload", {})),
                        "PENDING" if action.get("requires_approval", True) else "APPROVED",
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                log.error("[%s] Failed to queue action %s: %s", self.name, action["action_type"], e)

        return result

    @abstractmethod
    def run(self, result: AgentResult) -> None:
        """
        Implement agent logic here.
        Write findings / queue actions via `result`.
        Do NOT raise — handle exceptions internally.
        """
        ...

    # ── Helpers ───────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return current engine snapshot (read-only)."""
        return get_engine_snapshot()

    def db(self):
        """Return an open SQLite connection. Caller must close it."""
        return get_connection()

    def recent_trades(self, n: int = 20) -> list[dict]:
        """Last N completed trades from dashboard.db."""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, date, symbol, direction, setup, entry, exit_price, result, pnl_r
                FROM trades
                WHERE result IN ('WIN','LOSS')
                ORDER BY date DESC
                LIMIT ?
                """,
                (n,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def today_trades(self) -> list[dict]:
        """Today's completed trades."""
        today = datetime.now().date().isoformat()
        conn  = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE date >= ? AND result IN ('WIN','LOSS')",
                (today,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def rolling_stats(self, n: int = 20) -> dict:
        """Win rate, PF, and total R for last N trades."""
        trades = self.recent_trades(n)
        if not trades:
            return {"total": 0, "win_rate": 0, "profit_factor": 0, "total_r": 0}

        wins   = [t for t in trades if t["result"] == "WIN"]
        losses = [t for t in trades if t["result"] == "LOSS"]
        gross_profit = sum(t["pnl_r"] for t in wins  if t["pnl_r"])
        gross_loss   = abs(sum(t["pnl_r"] for t in losses if t["pnl_r"]))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "total":         len(trades),
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      len(wins) / len(trades),
            "profit_factor": round(pf, 3),
            "total_r":       round(sum(t["pnl_r"] for t in trades if t["pnl_r"]), 3),
        }
