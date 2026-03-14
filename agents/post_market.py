"""
agents/post_market.py
Post-Market Analyst — runs at 15:30 IST after market close.

UPGRADED: Now compares day's performance against morning tactical plan,
computes per-setup expectancy, generates tomorrow-prep insights,
and feeds data back to the Daily Tactical Controller.

Responsibilities:
  1. Compute final EOD P&L summary
  2. Compare actual results vs. morning tactical plan
  3. Per-setup expectancy (not just WR)
  4. Detect performance drift (rolling WR / PF degrading)
  5. Flag parameter anomalies (setups with 3+ consecutive losses)
  6. Generate "Tomorrow Prep" insights (what to adjust)
  7. Log parameter version snapshot for trend tracking
  8. Queue EOD Telegram report (auto-send, no approval needed)
"""

from datetime import datetime
from collections import defaultdict
import json

from agents.base import BaseAgent, AgentResult


class PostMarketAnalyst(BaseAgent):
    name        = "PostMarketAnalyst"
    description = "EOD analyst: tactical plan review, setup expectancy, performance drift, tomorrow prep."
    schedule    = "Daily 15:30"
    priority    = "high"

    def run(self, result: AgentResult) -> None:

        snap  = self.snapshot()
        today = datetime.now().date().isoformat()

        # ── 1. Today's closed trades ─────────────────────────────────────────
        today_trades = self.today_trades()
        total_today  = len(today_trades)
        wins_today   = sum(1 for t in today_trades if t["result"] == "WIN")
        losses_today = sum(1 for t in today_trades if t["result"] == "LOSS")
        pnl_today    = sum(t["pnl_r"] or 0 for t in today_trades)

        result.metrics.update({
            "date":         today,
            "trades_today": total_today,
            "wins_today":   wins_today,
            "losses_today": losses_today,
            "pnl_today_r":  round(pnl_today, 3),
            "daily_pnl_r":  snap.get("daily_pnl_r", 0),
        })

        # ── 2. Load morning tactical plan ────────────────────────────────────
        tactical_plan = None
        try:
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
                tactical_plan = json.loads(row["new_value"])
        except Exception:
            pass

        plan_review = {}
        if tactical_plan:
            planned_mode        = tactical_plan.get("mode", "UNKNOWN")
            planned_max_risk    = tactical_plan.get("max_daily_risk", 3.0)
            planned_stop_losses = tactical_plan.get("stop_after_losses", 3)
            planned_confidence  = tactical_plan.get("confidence", 50)

            # Did we stay within plan?
            within_risk   = abs(pnl_today) <= planned_max_risk
            within_losses = losses_today <= planned_stop_losses
            plan_followed = within_risk and within_losses

            plan_review = {
                "planned_mode":       planned_mode,
                "planned_max_risk":   planned_max_risk,
                "plan_followed":      plan_followed,
                "within_risk_limit":  within_risk,
                "within_loss_limit":  within_losses,
                "planned_confidence": planned_confidence,
            }
            result.metrics["plan_review"] = plan_review

            if not within_risk:
                result.add_finding("WARNING", "plan_risk_exceeded",
                    f"Daily P&L {pnl_today:+.2f}R exceeded plan max {planned_max_risk}R",
                    pnl_today)
            if not within_losses:
                result.add_finding("WARNING", "plan_losses_exceeded",
                    f"{losses_today} losses exceeded plan stop-after-{planned_stop_losses}",
                    losses_today)

        # ── 3. Rolling performance (20-trade window) ─────────────────────────
        rolling = self.rolling_stats(20)
        result.metrics.update({
            "rolling_wr_20":   round(rolling["win_rate"], 3),
            "rolling_pf_20":   rolling["profit_factor"],
            "rolling_total_r": rolling["total_r"],
        })

        # Drift detection
        if rolling["total"] >= 10:
            if rolling["win_rate"] < 0.35:
                result.add_finding("CRITICAL", "win_rate_low",
                    f"Rolling 20 WR dropped to {rolling['win_rate']*100:.1f}% (threshold: 35%)",
                    rolling["win_rate"])
            pf = rolling["profit_factor"]
            if pf != float("inf") and pf < 0.9:
                result.add_finding("CRITICAL", "pf_below_1",
                    f"Profit Factor {pf:.2f} — system not profitable on recent trades", pf)
            if rolling["total_r"] < -5.0:
                result.add_finding("WARNING", "rolling_dd",
                    f"Rolling 20-trade total R: {rolling['total_r']:.2f}R",
                    rolling["total_r"])

        # ── 4. Per-setup breakdown with expectancy ──────────────────────────
        setup_today: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "total_r": 0.0, "count": 0}
        )
        for t in today_trades:
            s = t.get("setup") or "UNKNOWN"
            setup_today[s]["count"] += 1
            if t["result"] == "WIN":    setup_today[s]["wins"]   += 1
            if t["result"] == "LOSS":   setup_today[s]["losses"] += 1
            setup_today[s]["total_r"] += t.get("pnl_r") or 0

        # Compute expectancy per setup
        setup_expectancy = {}
        for s, st in setup_today.items():
            if st["count"] > 0:
                st["expectancy_r"] = round(st["total_r"] / st["count"], 3)
                setup_expectancy[s] = st["expectancy_r"]

        result.metrics["setup_today"]      = dict(setup_today)
        result.metrics["setup_expectancy"] = setup_expectancy

        # Also compute rolling 20 per-setup expectancy
        last20 = self.recent_trades(20)
        setup_r20: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "total": 0, "total_r": 0.0}
        )
        for t in last20:
            s = t.get("setup") or "UNKNOWN"
            setup_r20[s]["total"] += 1
            setup_r20[s]["total_r"] += t.get("pnl_r") or 0
            if t["result"] == "WIN":
                setup_r20[s]["wins"] += 1

        setup_r20_summary = {}
        for s, st in setup_r20.items():
            wr = st["wins"] / st["total"] if st["total"] else 0
            exp = st["total_r"] / st["total"] if st["total"] else 0
            setup_r20_summary[s] = {
                "win_rate": round(wr, 3),
                "expectancy_r": round(exp, 3),
                "total_trades": st["total"],
                "total_r": round(st["total_r"], 3),
            }
        result.metrics["setup_rolling20"] = setup_r20_summary

        # ── 5. Consecutive setup losses (last 30 trades) ────────────────────
        last30 = self.recent_trades(30)
        setup_streak: dict[str, int] = {}
        for t in reversed(last30):
            s = t.get("setup") or "UNKNOWN"
            if t["result"] == "LOSS":
                setup_streak[s] = setup_streak.get(s, 0) + 1
            else:
                setup_streak[s] = 0

        for setup, streak in setup_streak.items():
            if streak >= 3:
                result.add_finding("WARNING", f"setup_streak_{setup}",
                    f"{setup} has {streak} consecutive losses — suggest disabling",
                    streak)

        # ── 6. Tomorrow Prep Insights ────────────────────────────────────────
        tomorrow_prep = []

        # Suggest mode for tomorrow based on today's result
        if pnl_today < -2.0:
            tomorrow_prep.append("Consider CONTROLLED or DEFENSIVE mode tomorrow — red day.")
        elif pnl_today > 2.0:
            tomorrow_prep.append("Good day. NORMAL or AGGRESSIVE+ may be appropriate if streak holds.")

        # Setup recommendations
        for s, st in setup_r20_summary.items():
            if st["total_trades"] >= 3 and st["expectancy_r"] < -0.2:
                tomorrow_prep.append(f"Consider disabling {s} — negative expectancy ({st['expectancy_r']}R).")
            elif st["total_trades"] >= 3 and st["expectancy_r"] > 0.5:
                tomorrow_prep.append(f"Focus on {s} — strong expectancy ({st['expectancy_r']}R).")

        result.metrics["tomorrow_prep"] = tomorrow_prep

        for tip in tomorrow_prep:
            result.add_finding("INFO", "tomorrow_prep", tip)

        # ── 7. Circuit breaker status ────────────────────────────────────────
        cb = snap.get("circuit_breaker_active", False)
        if cb:
            result.add_finding("CRITICAL", "circuit_breaker",
                "Circuit breaker was active today — review risk parameters", True)

        # ── 8. Snapshot parameter version ───────────────────────────────────
        try:
            snapshot_data = json.dumps({
                "engine_mode":     snap.get("engine_mode", "UNKNOWN"),
                "pnl_today_r":     round(pnl_today, 3),
                "trades_today":    total_today,
                "rolling_wr":      round(rolling["win_rate"], 3),
                "rolling_pf":      rolling["profit_factor"],
                "plan_followed":   plan_review.get("plan_followed"),
                "setup_expectancy": setup_expectancy,
                "tomorrow_prep":   tomorrow_prep,
            })
            conn = self.db()
            conn.execute(
                """INSERT INTO parameter_versions
                   (parameter, old_value, new_value, changed_by, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    "eod_snapshot",
                    None,
                    snapshot_data,
                    "PostMarketAnalyst",
                    f"EOD {today} — {total_today} trades, {pnl_today:+.2f}R",
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            result.add_finding("INFO", "param_snapshot_error",
                               f"Could not snapshot params: {e}")

        # ── 9. Build summary ─────────────────────────────────────────────────
        wr_pct = f"{wins_today/total_today*100:.0f}%" if total_today else "N/A"
        plan_tag = ""
        if tactical_plan:
            plan_tag = (" | Plan: " +
                        ("FOLLOWED" if plan_review.get("plan_followed") else "BREACHED"))

        result.summary = (
            f"EOD {today}: {total_today} trades, {wins_today}W "
            f"({wr_pct} WR), {pnl_today:+.2f}R | "
            f"Rolling20: WR={rolling['win_rate']*100:.1f}% PF={rolling['profit_factor']:.2f}"
            f"{plan_tag}"
        )

        # ── 10. Queue Telegram report ────────────────────────────────────────
        alerts = [f for f in result.findings
                  if f["level"] in ("WARNING", "CRITICAL")]
        alert_text = ""
        if alerts:
            alert_text = "\n\n\u26A0\uFE0F <b>Alerts:</b>\n" + "\n".join(
                f"\u2022 {a['message']}" for a in alerts)

        # Setup table
        setup_lines = []
        for s, st in setup_today.items():
            wr_s = f"{st['wins']/st['count']*100:.0f}%" if st['count'] else "N/A"
            setup_lines.append(
                f"  {s}: {st['count']} trades, {wr_s} WR, {st['total_r']:+.2f}R "
                f"(Exp: {st.get('expectancy_r', 0):+.2f}R)")
        setup_block = "\n".join(setup_lines) if setup_lines else "  No trades"

        plan_block = ""
        if tactical_plan:
            status = "\u2705 FOLLOWED" if plan_review.get("plan_followed") else "\u274C BREACHED"
            plan_block = (
                f"\n\n\U0001F4CB <b>Plan Review:</b>\n"
                f"  Mode: {plan_review.get('planned_mode')} | Status: {status}\n"
                f"  Risk limit: {'OK' if plan_review.get('within_risk_limit') else 'EXCEEDED'} | "
                f"Loss limit: {'OK' if plan_review.get('within_loss_limit') else 'EXCEEDED'}"
            )

        prep_block = ""
        if tomorrow_prep:
            prep_block = "\n\n\U0001F4A1 <b>Tomorrow Prep:</b>\n" + "\n".join(
                f"\u2022 {tip}" for tip in tomorrow_prep[:4])

        cb_text = "\u26D4 YES" if cb else "\u2705 OFF"

        result.queue_action(
            action_type="SEND_TELEGRAM",
            payload={
                "message": (
                    f"\U0001F4CA <b>POST-MARKET REPORT \u2014 {today}</b>\n\n"
                    f"Trades: {total_today} | Wins: {wins_today} ({wr_pct})\n"
                    f"Daily P&L: <b>{pnl_today:+.2f}R</b>\n"
                    f"Rolling 20 WR: {rolling['win_rate']*100:.1f}%\n"
                    f"Rolling 20 PF: {rolling['profit_factor']:.2f}\n"
                    f"CB: {cb_text}\n\n"
                    f"\U0001F4CA <b>Setup Breakdown:</b>\n{setup_block}"
                    f"{plan_block}"
                    f"{prep_block}"
                    f"{alert_text}"
                )
            },
            requires_approval=False,
        )
