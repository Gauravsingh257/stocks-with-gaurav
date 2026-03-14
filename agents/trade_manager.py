"""
agents/trade_manager.py
Trade Manager — runs every 5 minutes during market hours.

UPGRADED: Now reads the morning tactical plan and enforces guardrails:
  - stop_after_losses: suggest closing all if daily loss count exceeds plan
  - disable_setups: flag trades in disabled setups
  - SL proximity trailing, stale trade detection, position count
  - Partial profit suggestion at 1.5R unrealized
  - Unrealized P&L tracking across all positions
"""

from datetime import datetime, timezone, timedelta
import json

from agents.base import BaseAgent, AgentResult

# Thresholds
SL_PROXIMITY_PCT   = 0.20   # within 20% of (entry→SL) range = trail_stop alert
STALE_HOURS        = 2.0    # flag trades older than 2 hours
MAX_SIMULTANEOUS   = 3      # alert if more than this many concurrent positions
PARTIAL_PROFIT_R   = 1.5    # suggest partial book at 1.5R unrealized


class TradeManager(BaseAgent):
    name        = "TradeManager"
    description = "Monitors trades: SL proximity, stale, position count, tactical plan guardrails."
    schedule    = "Every 5 min (09:15\u201315:30)"
    priority    = "normal"

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

        snap          = self.snapshot()
        active_trades = snap.get("active_trades", [])
        now_utc       = datetime.now(timezone.utc)

        # Load tactical plan for guardrail enforcement
        plan = self._load_tactical_plan()
        plan_stop_losses   = plan.get("stop_after_losses", 3) if plan else 3
        plan_disable       = plan.get("disable_setups", []) if plan else []
        plan_mode          = plan.get("mode", "NORMAL") if plan else "NORMAL"
        plan_risk_mult     = plan.get("risk_multiplier", 1.0) if plan else 1.0

        result.metrics["tactical_plan_loaded"] = plan is not None
        result.metrics["plan_mode"]            = plan_mode

        if not active_trades:
            result.summary = f"No active trades | Plan: {plan_mode}"
            result.metrics["active_count"] = 0
            return

        trail_alerts  = 0
        stale_alerts  = 0
        close_alerts  = 0
        total_unrealized_r = 0.0
        partial_candidates = []

        for idx, trade in enumerate(active_trades):
            entry  = trade.get("entry")
            sl     = trade.get("sl") or trade.get("stop_loss")
            target = trade.get("target") or trade.get("tp")
            ltp    = trade.get("ltp") or trade.get("last_price")
            dirn   = (trade.get("direction") or "BUY").upper()
            symbol = trade.get("symbol", f"trade_{idx}")
            setup  = trade.get("setup", "UNKNOWN")
            trade_id = trade.get("trade_id") or trade.get("symbol", str(idx))

            # ── Guardrail: trade in disabled setup ──────────────────────────
            if setup in plan_disable:
                result.add_finding("WARNING", f"disabled_setup_{symbol}",
                    f"{symbol}: Setup {setup} is DISABLED in today's plan — consider closing",
                    setup)

            # ── SL proximity ────────────────────────────────────────────────
            if entry is not None and sl is not None and ltp is not None:
                try:
                    trade_range = abs(float(entry) - float(sl))
                    if trade_range > 0:
                        dist_to_sl = abs(float(ltp) - float(sl))
                        proximity  = dist_to_sl / trade_range

                        if proximity <= SL_PROXIMITY_PCT:
                            result.add_finding("WARNING", f"sl_proximity_{symbol}",
                                f"{symbol}: Price within {proximity*100:.0f}% of SL "
                                f"(LTP={ltp}, SL={sl})",
                                proximity)
                            result.queue_action(
                                action_type="TRAIL_STOP",
                                payload={"trade_id": trade_id, "symbol": symbol,
                                         "current_ltp": ltp, "sl": sl,
                                         "proximity_pct": round(proximity, 3)},
                                requires_approval=True,
                            )
                            trail_alerts += 1

                        # ── Unrealized R calculation ────────────────────────
                        if dirn == "BUY":
                            unrealized_r = (float(ltp) - float(entry)) / trade_range
                        else:
                            unrealized_r = (float(entry) - float(ltp)) / trade_range

                        total_unrealized_r += unrealized_r

                        # Partial profit suggestion
                        if unrealized_r >= PARTIAL_PROFIT_R:
                            partial_candidates.append({
                                "symbol": symbol,
                                "unrealized_r": round(unrealized_r, 2),
                                "ltp": ltp,
                            })
                except (TypeError, ValueError):
                    pass

            # ── Stale trade ──────────────────────────────────────────────────
            opened_str = (trade.get("time") or trade.get("entry_time")
                          or trade.get("timestamp"))
            if opened_str:
                try:
                    opened_dt = datetime.fromisoformat(str(opened_str))
                    if opened_dt.tzinfo is None:
                        opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                    age_hours = (now_utc - opened_dt).total_seconds() / 3600
                    if age_hours >= STALE_HOURS:
                        result.add_finding("WARNING", f"stale_trade_{symbol}",
                            f"{symbol}: Open for {age_hours:.1f}h — consider closing",
                            age_hours)
                        result.queue_action(
                            action_type="CLOSE_TRADE",
                            payload={"trade_id": trade_id, "symbol": symbol,
                                     "reason": f"Stale: {age_hours:.1f}h old",
                                     "ltp": ltp},
                            requires_approval=True,
                        )
                        stale_alerts += 1
                except (ValueError, TypeError):
                    pass

        # ── Partial profit suggestions ────────────────────────────────────────
        for pc in partial_candidates:
            result.add_finding("INFO", f"partial_profit_{pc['symbol']}",
                f"{pc['symbol']}: Unrealized {pc['unrealized_r']}R — consider partial booking",
                pc["unrealized_r"])
            result.queue_action(
                action_type="PARTIAL_BOOK",
                payload=pc,
                requires_approval=True,
            )

        # ── Simultaneous position count ───────────────────────────────────────
        n_active = len(active_trades)
        # Adjust max simultaneous based on plan mode
        effective_max = MAX_SIMULTANEOUS
        if plan_mode == "DEFENSIVE":
            effective_max = 1
        elif plan_mode == "CONTROLLED":
            effective_max = 2

        if n_active > effective_max:
            result.add_finding("WARNING", "too_many_positions",
                f"{n_active} positions (max for {plan_mode}: {effective_max})",
                n_active)

        # ── Guardrail: daily loss count check ─────────────────────────────────
        today_trades = self.today_trades()
        today_losses = sum(1 for t in today_trades if t["result"] == "LOSS")
        if today_losses >= plan_stop_losses:
            result.add_finding("CRITICAL", "stop_after_losses",
                f"{today_losses} losses today — plan says stop after {plan_stop_losses}. "
                f"Consider closing all active trades.",
                today_losses)
            result.queue_action(
                action_type="SEND_TELEGRAM",
                payload={
                    "message": (
                        f"\u26D4 <b>TRADE MANAGER \u2014 LOSS LIMIT REACHED</b>\n"
                        f"{today_losses} losses today (plan limit: {plan_stop_losses})\n"
                        f"Mode: {plan_mode} | Active trades: {n_active}\n"
                        f"Recommend: Close all positions and stop trading."
                    )
                },
                requires_approval=False,
            )

        result.metrics.update({
            "active_count":      n_active,
            "trail_alerts":      trail_alerts,
            "stale_alerts":      stale_alerts,
            "close_alerts":      close_alerts,
            "unrealized_r":      round(total_unrealized_r, 3),
            "partial_candidates": len(partial_candidates),
            "today_losses":      today_losses,
            "plan_loss_limit":   plan_stop_losses,
        })

        if trail_alerts or stale_alerts or partial_candidates:
            result.queue_action(
                action_type="SEND_TELEGRAM",
                payload={
                    "message": (
                        f"\U0001F4CD <b>TRADE MANAGER</b> | {plan_mode} mode\n"
                        f"Active: {n_active} | Unrealized: {total_unrealized_r:+.2f}R\n"
                        + (f"\u26A0\uFE0F SL proximity: {trail_alerts}\n"
                           if trail_alerts else "")
                        + (f"\u23F0 Stale trades: {stale_alerts}\n"
                           if stale_alerts else "")
                        + (f"\U0001F4B0 Partial book candidates: {len(partial_candidates)}\n"
                           if partial_candidates else "")
                        + "\nCheck dashboard \u2192 Actions for approval."
                    )
                },
                requires_approval=False,
            )

        result.summary = (
            f"{plan_mode} | {n_active} active | "
            f"Unrealized: {total_unrealized_r:+.2f}R | "
            f"SL: {trail_alerts} | Stale: {stale_alerts} | "
            f"Losses: {today_losses}/{plan_stop_losses}"
        )
