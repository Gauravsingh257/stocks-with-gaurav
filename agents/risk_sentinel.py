"""
agents/risk_sentinel.py
Risk Sentinel — runs every 1 minute during market hours.

UPGRADED: Now reads the morning tactical plan to dynamically adjust
thresholds based on the current mode (DEFENSIVE → tighter limits).

Responsibilities:
  1. Monitor daily_pnl_r vs. dynamic limits (from tactical plan or defaults)
  2. Monitor consecutive_losses vs. plan stop_after_losses
  3. Check multi-day drawdown from agent_logs history
  4. Intraday loss velocity check (losing too fast)
  5. If hard limits breached → queue HALT_TRADING (auto-approve)
  6. If soft limits breached → queue SEND_TELEGRAM warning (no approval)
"""

from datetime import datetime, timedelta
import json

from agents.base import BaseAgent, AgentResult

# Default risk thresholds (overridden by tactical plan if available)
DAILY_PNL_WARN       = -2.0   # R
DAILY_PNL_HALT       = -3.0   # R
CONSEC_LOSS_WARN     = 3
CONSEC_LOSS_HALT     = 5
MULTIDAY_DD_WARN     = -6.0   # R over rolling 3 days
MULTIDAY_DD_HALT     = -9.0   # R over rolling 3 days
VELOCITY_WINDOW_MIN  = 30     # check P&L velocity over last 30 min
VELOCITY_WARN_R      = -1.5   # losing 1.5R in 30 min = too fast


class RiskSentinel(BaseAgent):
    name        = "RiskSentinel"
    description = "Real-time risk: dynamic limits from tactical plan, drawdown, velocity, multi-day DD."
    schedule    = "Every 1 min (09:15\u201315:30)"
    priority    = "critical"

    def _load_tactical_plan(self) -> dict | None:
        """Load today's tactical plan from parameter_versions."""
        try:
            today = datetime.now().date().isoformat()
            conn = self.db()
            row = conn.execute(
                """SELECT new_value FROM parameter_versions
                   WHERE parameter = 'tactical_plan'
                     AND date(timestamp) = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (today,),
            ).fetchone()
            conn.close()
            if row:
                return json.loads(row["new_value"])
        except Exception:
            pass
        return None

    def run(self, result: AgentResult) -> None:

        snap           = self.snapshot()
        daily_pnl_r    = float(snap.get("daily_pnl_r") or 0)
        consec_losses  = int(snap.get("consecutive_losses") or 0)
        cb_active      = bool(snap.get("circuit_breaker_active", False))
        engine_mode    = snap.get("engine_mode", "UNKNOWN")

        # Load tactical plan for dynamic thresholds
        plan = self._load_tactical_plan()
        plan_mode = "NORMAL"

        # Dynamic thresholds — override defaults if plan exists
        pnl_halt     = DAILY_PNL_HALT
        pnl_warn     = DAILY_PNL_WARN
        cl_halt      = CONSEC_LOSS_HALT
        cl_warn      = CONSEC_LOSS_WARN

        if plan:
            plan_mode    = plan.get("mode", "NORMAL")
            max_risk     = plan.get("max_daily_risk", 3.0)
            stop_losses  = plan.get("stop_after_losses", 3)

            # Set dynamic PNL limits from plan
            pnl_halt = -max_risk
            pnl_warn = -max_risk * 0.67  # warn at 67% of limit

            # Set dynamic loss limits from plan
            cl_halt  = stop_losses + 2   # hard halt 2 beyond plan
            cl_warn  = stop_losses        # warn at plan limit

        result.metrics.update({
            "daily_pnl_r":        daily_pnl_r,
            "consecutive_losses": consec_losses,
            "circuit_breaker":    cb_active,
            "engine_mode":        engine_mode,
            "plan_mode":          plan_mode,
            "dynamic_pnl_halt":   pnl_halt,
            "dynamic_cl_halt":    cl_halt,
        })

        halt_queued = False

        # ── Already halted ───────────────────────────────────────────────────
        if cb_active:
            result.add_finding("CRITICAL", "circuit_breaker_on",
                "Circuit breaker is ACTIVE \u2014 trading blocked", True)
            result.summary = f"Circuit breaker ACTIVE | {plan_mode} mode"
            return

        # ── Daily P&L hard limit (dynamic) ───────────────────────────────────
        if daily_pnl_r <= pnl_halt:
            result.add_finding("CRITICAL", "daily_pnl_halt",
                f"Daily P&L hit hard stop: {daily_pnl_r:.2f}R (limit: {pnl_halt:.1f}R)",
                daily_pnl_r)
            result.queue_action(
                action_type="HALT_TRADING",
                payload={"reason": f"Daily P&L limit: {daily_pnl_r:.2f}R",
                         "threshold": pnl_halt,
                         "plan_mode": plan_mode},
                requires_approval=False,
            )
            result.queue_action(
                action_type="SEND_TELEGRAM",
                payload={"message":
                    f"\u26D4 <b>RISK SENTINEL \u2014 HALT ({plan_mode})</b>\n"
                    f"Daily P&L: <b>{daily_pnl_r:.2f}R</b> hit limit ({pnl_halt:.1f}R)\n"
                    f"Trading halted."},
                requires_approval=False,
            )
            halt_queued = True

        elif daily_pnl_r <= pnl_warn:
            result.add_finding("WARNING", "daily_pnl_warn",
                f"Daily P&L warning: {daily_pnl_r:.2f}R (warn: {pnl_warn:.1f}R, halt: {pnl_halt:.1f}R)",
                daily_pnl_r)
            result.queue_action(
                action_type="SEND_TELEGRAM",
                payload={"message":
                    f"\u26A0\uFE0F <b>RISK WARNING ({plan_mode})</b>\n"
                    f"Daily P&L: {daily_pnl_r:.2f}R \u2014 approaching halt ({pnl_halt:.1f}R)\n"
                    f"Trade with caution."},
                requires_approval=False,
            )

        # ── Consecutive losses (dynamic) ─────────────────────────────────────
        if consec_losses >= cl_halt and not halt_queued:
            result.add_finding("CRITICAL", "consec_loss_halt",
                f"{consec_losses} consecutive losses \u2014 hard stop triggered (limit: {cl_halt})",
                consec_losses)
            result.queue_action(
                action_type="HALT_TRADING",
                payload={"reason": f"Consecutive losses: {consec_losses}",
                         "threshold": cl_halt,
                         "plan_mode": plan_mode},
                requires_approval=False,
            )
            result.queue_action(
                action_type="SEND_TELEGRAM",
                payload={"message":
                    f"\u26D4 <b>RISK SENTINEL \u2014 HALT ({plan_mode})</b>\n"
                    f"{consec_losses} consecutive losses (limit: {cl_halt})\n"
                    f"Trading halted."},
                requires_approval=False,
            )
            halt_queued = True

        elif consec_losses >= cl_warn and not halt_queued:
            result.add_finding("WARNING", "consec_loss_warn",
                f"{consec_losses} consecutive losses \u2014 approaching halt ({cl_halt})",
                consec_losses)

        # ── Multi-day drawdown (3-day rolling) ───────────────────────────────
        if not halt_queued:
            try:
                conn = self.db()
                cutoff = (datetime.now().date() - timedelta(days=3)).isoformat()
                rows = conn.execute(
                    """SELECT metrics_json FROM agent_logs
                       WHERE agent_name = 'PostMarketAnalyst'
                         AND date(run_time) >= ?
                       ORDER BY run_time DESC LIMIT 3""",
                    (cutoff,),
                ).fetchall()
                conn.close()

                rolling_dd = 0.0
                for row in rows:
                    try:
                        m = json.loads(row["metrics_json"] or "{}")
                        rolling_dd += float(m.get("pnl_today_r", 0))
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass

                result.metrics["rolling_3day_r"] = round(rolling_dd, 3)

                if rolling_dd <= MULTIDAY_DD_HALT:
                    result.add_finding("CRITICAL", "multiday_dd_halt",
                        f"3-day rolling P&L: {rolling_dd:.2f}R \u2014 halt ({MULTIDAY_DD_HALT}R)",
                        rolling_dd)
                    result.queue_action(
                        action_type="HALT_TRADING",
                        payload={"reason": f"Multi-day DD: {rolling_dd:.2f}R",
                                 "threshold": MULTIDAY_DD_HALT},
                        requires_approval=True,
                    )
                elif rolling_dd <= MULTIDAY_DD_WARN:
                    result.add_finding("WARNING", "multiday_dd_warn",
                        f"3-day rolling P&L: {rolling_dd:.2f}R (warn: {MULTIDAY_DD_WARN}R)",
                        rolling_dd)

            except Exception as e:
                result.add_finding("INFO", "multiday_dd_error",
                    f"Could not compute multi-day DD: {e}")

        # ── Intraday loss velocity check ─────────────────────────────────────
        if not halt_queued:
            try:
                today_trades = self.today_trades()
                now = datetime.now()
                recent_trades = []
                for t in today_trades:
                    t_time = t.get("time") or t.get("exit_time") or t.get("timestamp")
                    if t_time:
                        try:
                            dt = datetime.fromisoformat(str(t_time))
                            age_min = (now - dt).total_seconds() / 60
                            if age_min <= VELOCITY_WINDOW_MIN:
                                recent_trades.append(t)
                        except (ValueError, TypeError):
                            pass

                if recent_trades:
                    velocity_r = sum(t.get("pnl_r") or 0 for t in recent_trades)
                    result.metrics["velocity_30min_r"] = round(velocity_r, 3)

                    if velocity_r <= VELOCITY_WARN_R:
                        result.add_finding("WARNING", "loss_velocity",
                            f"Losing {velocity_r:.2f}R in last {VELOCITY_WINDOW_MIN} min \u2014 slow down",
                            velocity_r)
                        result.queue_action(
                            action_type="SEND_TELEGRAM",
                            payload={"message":
                                f"\u26A0\uFE0F <b>RISK \u2014 VELOCITY WARNING</b>\n"
                                f"Lost {velocity_r:.2f}R in {VELOCITY_WINDOW_MIN} min\n"
                                f"Consider pausing for 30 min."},
                            requires_approval=False,
                        )
            except Exception:
                pass

        # ── Final summary ────────────────────────────────────────────────────
        n_warn = sum(1 for f in result.findings if f["level"] == "WARNING")
        n_crit = sum(1 for f in result.findings if f["level"] == "CRITICAL")

        cb_icon = "\u26D4" if cb_active else "\u2705"
        result.summary = (
            f"{plan_mode} | Daily: {daily_pnl_r:.2f}R/{pnl_halt:.1f}R | "
            f"CL: {consec_losses}/{cl_halt} | "
            f"CB: {cb_icon} | "
            f"{n_crit}C {n_warn}W"
        )

        if n_crit == 0 and n_warn == 0:
            result.add_finding("INFO", "all_clear",
                f"All risk checks passed | Mode: {plan_mode}")
