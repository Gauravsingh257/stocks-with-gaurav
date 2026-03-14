"""
agents/pre_market.py
Daily Tactical Controller Agent — runs at 08:45 IST (or "Run Now").

UPGRADED from simple briefing → full 7-step decision engine.

STEP 1 — Pull inputs from snapshot + DB
STEP 2 — Classify performance (WR health, drawdown, consecutive losses)
STEP 3 — Classify market condition (regime alignment, expiry proximity)
STEP 4 — Decision engine: compute mode, risk multiplier, setup focus
STEP 5 — Output structured tactical plan
STEP 6 — Persist plan to DB (parameter_versions) + queue Telegram
STEP 7 — Plan is exposed via GET /api/tactical-plan for dashboard widget

This agent NEVER mutates engine globals.  It writes recommendations to
parameter_versions table.  The engine reads them via the approval gate.
"""

from datetime import datetime, timedelta
from collections import defaultdict
import json
import logging

from agents.base import BaseAgent, AgentResult

log = logging.getLogger("agents.pre_market")

# ─────────────────────────────────────────────────────────────
# THRESHOLDS & CLASSIFICATION TABLES
# ─────────────────────────────────────────────────────────────

WR_THRESHOLDS = [
    (0.55, "STRONG"),
    (0.45, "HEALTHY"),
    (0.35, "WARNING"),
    (0.00, "CRITICAL"),
]

DD_THRESHOLDS = [
    (-3.0, "NORMAL"),
    (-6.0, "ELEVATED"),
    (-999, "DANGER"),
]

CONSEC_LOSS_MAP = {
    0: "NORMAL",
    1: "NORMAL",
    2: "REDUCE_RISK",
}
# 3+ → CONSERVATIVE

# ─────────────────────────────────────────────────────────────
# TACTICAL MODE DEFINITIONS
# ─────────────────────────────────────────────────────────────

MODES = {
    "DEFENSIVE": {
        "risk_multiplier": 0.4,
        "max_daily_risk":  1.5,
        "score_threshold": 7,
        "stop_after_losses": 1,
        "disable_setups": ["SETUP_B"],
        "description": "Defensive mode: risk reduced to 0.4x, only high-score trades",
    },
    "CONTROLLED": {
        "risk_multiplier": 0.7,
        "max_daily_risk":  2.0,
        "score_threshold": 6,
        "stop_after_losses": 2,
        "disable_setups": [],
        "description": "Controlled mode: risk at 0.7x, SMC score >= 6 required",
    },
    "NORMAL": {
        "risk_multiplier": 1.0,
        "max_daily_risk":  3.0,
        "score_threshold": 5,
        "stop_after_losses": 3,
        "disable_setups": [],
        "description": "Normal mode: standard risk, all setups active",
    },
    "AGGRESSIVE_PLUS": {
        "risk_multiplier": 1.2,
        "max_daily_risk":  4.0,
        "score_threshold": 5,
        "stop_after_losses": 3,
        "disable_setups": [],
        "description": "Aggressive+ mode: riding the streak, 1.2x risk allowed",
    },
}


class PreMarketBriefing(BaseAgent):
    """Daily Tactical Controller — computes the day's trading plan."""

    name        = "PreMarketBriefing"
    description = "Daily Tactical Controller: risk classification, mode decision, trading plan."
    schedule    = "Daily 08:45"
    priority    = "high"

    def run(self, result: AgentResult) -> None:

        snap  = self.snapshot()
        today = datetime.now().date().isoformat()
        yest  = (datetime.now().date() - timedelta(days=1)).isoformat()

        # ================================================================
        # STEP 1 — GATHER ALL INPUT DATA
        # ================================================================

        # 1a. Yesterday's P&L
        conn  = self.db()
        rows  = conn.execute(
            "SELECT result, pnl_r, setup FROM trades WHERE date(date) = ?",
            (yest,),
        ).fetchall()
        conn.close()

        yest_total = len(rows)
        yest_wins  = sum(1 for r in rows if r["result"] == "WIN")
        yest_pnl   = sum((r["pnl_r"] or 0) for r in rows)

        # 1b. Rolling 20 stats
        rolling = self.rolling_stats(20)
        win_rate_20  = rolling["win_rate"]
        total_r_20   = rolling["total_r"]
        pf_20        = rolling["profit_factor"]

        # 1c. Consecutive losses from snapshot
        consec_losses = int(snap.get("consecutive_losses") or 0)

        # 1d. Current drawdown (rolling 20-trade total R as proxy)
        drawdown_r = total_r_20 if total_r_20 < 0 else 0

        # 1e. Multi-day drawdown (last 3 EOD reports)
        multiday_r = 0.0
        try:
            conn2 = self.db()
            cutoff = (datetime.now().date() - timedelta(days=3)).isoformat()
            eod_rows = conn2.execute(
                """
                SELECT metrics_json FROM agent_logs
                WHERE agent_name = 'PostMarketAnalyst'
                  AND date(run_time) >= ?
                ORDER BY run_time DESC LIMIT 3
                """,
                (cutoff,),
            ).fetchall()
            conn2.close()
            for r in eod_rows:
                try:
                    m = json.loads(r["metrics_json"] or "{}")
                    multiday_r += float(m.get("pnl_today_r", 0))
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
        except Exception:
            pass

        # 1f. Engine state
        engine_mode  = snap.get("engine_mode", "UNKNOWN")
        cb_active    = snap.get("circuit_breaker_active", False)
        paper_mode   = snap.get("paper_mode", False)
        index_only   = snap.get("index_only", False)
        regime       = snap.get("market_regime", "NEUTRAL")

        # 1g. Active zones
        zone_state   = snap.get("zone_state", {})
        n_zones = 0
        for sym, zones in (zone_state or {}).items():
            if isinstance(zones, list):
                n_zones += len(zones)
            elif isinstance(zones, dict):
                n_zones += sum(1 for v in zones.values() if v)

        # 1h. Setup performance analysis (last 20 trades)
        last20 = self.recent_trades(20)
        setup_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "total": 0, "r": 0.0})
        for t in last20:
            s = t.get("setup") or "UNKNOWN"
            setup_stats[s]["total"] += 1
            setup_stats[s]["r"]     += t.get("pnl_r") or 0
            if t["result"] == "WIN":
                setup_stats[s]["wins"] += 1

        best_setup  = None
        best_wr     = -1.0
        worst_setup = None
        worst_wr    = 2.0
        for s, st in setup_stats.items():
            if st["total"] < 3:
                continue
            wr = st["wins"] / st["total"]
            if wr > best_wr:
                best_wr    = wr
                best_setup = s
            if wr < worst_wr:
                worst_wr    = wr
                worst_setup = s

        # 1i. Expiry proximity check
        is_expiry_day = False
        days_to_exp   = 99
        try:
            from engine.expiry_manager import get_next_expiry
            nifty_exp = get_next_expiry("NIFTY")
            if nifty_exp:
                days_to_exp   = (nifty_exp - datetime.now().date()).days
                is_expiry_day = days_to_exp <= 1
        except Exception:
            pass

        # ================================================================
        # STEP 2 — PERFORMANCE CLASSIFICATION
        # ================================================================

        # 2a. Win Rate Health
        wr_state = "CRITICAL"
        for threshold, state in WR_THRESHOLDS:
            if win_rate_20 >= threshold:
                wr_state = state
                break

        # 2b. Drawdown Condition
        dd_state = "DANGER"
        for threshold, state in DD_THRESHOLDS:
            if drawdown_r >= threshold:
                dd_state = state
                break

        # 2c. Consecutive Losses
        cl_state = CONSEC_LOSS_MAP.get(consec_losses, "CONSERVATIVE")

        # ================================================================
        # STEP 3 — MARKET CONDITION CLASSIFICATION
        # ================================================================

        market_condition = "RANGE"  # default
        if regime in ("BULLISH", "BEARISH"):
            market_condition = "TRENDING"
        if is_expiry_day:
            market_condition = "EXPIRY_MANIPULATION"
        # Volatile override — if CB was active, market was volatile
        if cb_active:
            market_condition = "VOLATILE"

        # ================================================================
        # STEP 4 — DECISION ENGINE
        # ================================================================

        # 4a. Trading Mode Decision
        if win_rate_20 < 0.35 or drawdown_r < -6.0:
            mode = "DEFENSIVE"
        elif win_rate_20 < 0.45 or cl_state == "CONSERVATIVE":
            mode = "CONTROLLED"
        elif win_rate_20 > 0.55 and drawdown_r >= -3.0:
            mode = "AGGRESSIVE_PLUS"
        else:
            mode = "NORMAL"

        mode_cfg = dict(MODES[mode])  # copy so we can modify

        # 4b. Setup Focus Decision
        focus_setups:   list[str] = []
        disable_setups: list[str] = list(mode_cfg["disable_setups"])

        if market_condition == "TRENDING":
            focus_setups = ["SETUP_D", "UNIVERSAL-SHORT", "UNIVERSAL-LONG"]
        elif market_condition == "RANGE":
            focus_setups = ["SETUP_B"]
        elif market_condition == "VOLATILE":
            mode_cfg["score_threshold"] = max(mode_cfg["score_threshold"], 7)
        elif market_condition == "EXPIRY_MANIPULATION":
            disable_setups.append("OTM_STRIKES")

        # Add best-performing setup to focus
        if best_setup and best_setup not in focus_setups:
            focus_setups.append(best_setup)

        # Disable worst-performing setup if WR < 25%
        if worst_setup and worst_wr < 0.25 and worst_setup not in disable_setups:
            disable_setups.append(worst_setup)

        # 4c. Confidence Score (0-100)
        confidence = 50
        if wr_state == "STRONG":       confidence += 20
        elif wr_state == "HEALTHY":    confidence += 10
        elif wr_state == "WARNING":    confidence -= 10
        elif wr_state == "CRITICAL":   confidence -= 25

        if dd_state == "NORMAL":       confidence += 10
        elif dd_state == "ELEVATED":   confidence -= 10
        elif dd_state == "DANGER":     confidence -= 20

        if market_condition == "TRENDING":             confidence += 10
        elif market_condition == "RANGE":               confidence -= 5
        elif market_condition == "VOLATILE":            confidence -= 15
        elif market_condition == "EXPIRY_MANIPULATION": confidence -= 10

        if pf_20 != float("inf"):
            if pf_20 >= 1.5:   confidence += 10
            elif pf_20 < 1.0:  confidence -= 10

        confidence = max(10, min(100, confidence))

        # ================================================================
        # STEP 5 — BUILD TACTICAL PLAN
        # ================================================================

        tactical_plan = {
            "date":              today,
            "generated_at":      datetime.now().isoformat(),
            "mode":              mode,
            "mode_description":  mode_cfg["description"],
            "risk_multiplier":   mode_cfg["risk_multiplier"],
            "max_daily_risk":    mode_cfg["max_daily_risk"],
            "score_threshold":   mode_cfg["score_threshold"],
            "stop_after_losses": mode_cfg["stop_after_losses"],
            "focus_setups":      focus_setups,
            "disable_setups":    disable_setups,
            "market_condition":  market_condition,
            "market_regime":     regime,
            "confidence":        confidence,
            # Classification details
            "wr_state":          wr_state,
            "dd_state":          dd_state,
            "cl_state":          cl_state,
            # Raw inputs for transparency
            "inputs": {
                "win_rate_20":        round(win_rate_20, 3),
                "profit_factor":      pf_20 if pf_20 != float("inf") else 999,
                "total_r_20":         round(total_r_20, 3),
                "drawdown_r":         round(drawdown_r, 3),
                "multiday_r_3d":      round(multiday_r, 3),
                "consecutive_losses": consec_losses,
                "yesterday_trades":   yest_total,
                "yesterday_pnl_r":    round(yest_pnl, 3),
                "best_setup":         best_setup,
                "best_setup_wr":      round(best_wr, 3) if best_wr >= 0 else None,
                "worst_setup":        worst_setup,
                "worst_setup_wr":     round(worst_wr, 3) if worst_wr < 2 else None,
                "active_zones":       n_zones,
                "is_expiry_day":      is_expiry_day,
                "engine_mode":        engine_mode,
                "circuit_breaker":    cb_active,
                "paper_mode":         paper_mode,
            },
        }

        # ================================================================
        # STEP 6 — PERSIST + LOG + ACTIONS
        # ================================================================

        # 6a. Store in metrics for immediate API access
        result.metrics = tactical_plan

        # 6b. Persist to parameter_versions for historical tracking
        try:
            conn3 = self.db()
            conn3.execute(
                """INSERT INTO parameter_versions
                   (parameter, old_value, new_value, changed_by, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    "tactical_plan",
                    None,
                    json.dumps(tactical_plan),
                    "PreMarketBriefing",
                    f"Tactical plan for {today} | Mode: {mode} | Conf: {confidence}%",
                ),
            )
            conn3.commit()
            conn3.close()
        except Exception as e:
            result.add_finding("INFO", "persist_error",
                               f"Could not persist plan: {e}")

        # 6c. Findings based on classifications
        if wr_state == "CRITICAL":
            result.add_finding(
                "CRITICAL", "wr_critical",
                f"Rolling WR {win_rate_20*100:.1f}% — CRITICAL. Defensive mode activated.",
                win_rate_20)
        elif wr_state == "WARNING":
            result.add_finding(
                "WARNING", "wr_warning",
                f"Rolling WR {win_rate_20*100:.1f}% — below healthy. Controlled mode.",
                win_rate_20)

        if dd_state == "DANGER":
            result.add_finding(
                "CRITICAL", "dd_danger",
                f"Drawdown {drawdown_r:.2f}R — DANGER zone. Risk halved.",
                drawdown_r)
        elif dd_state == "ELEVATED":
            result.add_finding(
                "WARNING", "dd_elevated",
                f"Drawdown {drawdown_r:.2f}R — elevated. Trade cautiously.",
                drawdown_r)

        if cl_state == "CONSERVATIVE":
            result.add_finding(
                "WARNING", "consec_losses_high",
                f"{consec_losses} consecutive losses — conservative mode.",
                consec_losses)

        if is_expiry_day:
            result.add_finding(
                "WARNING", "expiry_day",
                "Expiry day — manipulation risk high. OTM trades restricted.")

        if cb_active:
            result.add_finding(
                "CRITICAL", "cb_active",
                "Circuit breaker ACTIVE. Trading blocked until reset.", True)

        if disable_setups:
            result.add_finding(
                "INFO", "disabled_setups",
                f"Disabled setups today: {', '.join(disable_setups)}")

        # 6d. Queue tactical plan action (Advisory Mode — requires approval)
        result.queue_action(
            action_type="SET_TACTICAL_PLAN",
            payload=tactical_plan,
            requires_approval=True,
        )

        # 6e. Queue Telegram notification
        alerts = [f for f in result.findings
                  if f["level"] in ("WARNING", "CRITICAL")]
        alert_block = ""
        if alerts:
            alert_block = "\n\n\u26A0\uFE0F <b>Alerts:</b>\n" + "\n".join(
                f"\u2022 {a['message']}" for a in alerts)

        focus_line = (f"Focus: {', '.join(focus_setups)}"
                      if focus_setups else "Focus: All active")
        disable_line = (f"\nDisabled: {', '.join(disable_setups)}"
                        if disable_setups else "")

        pf_display = f"{pf_20:.2f}" if pf_20 != float("inf") else "INF"

        result.queue_action(
            action_type="SEND_TELEGRAM",
            payload={
                "message": (
                    f"\U0001F3AF <b>DAILY TACTICAL PLAN \u2014 {today}</b>\n\n"
                    f"\u25CF Mode: <b>{mode}</b> (Confidence: {confidence}%)\n"
                    f"\u25CF Risk: {mode_cfg['risk_multiplier']}x | Max: {mode_cfg['max_daily_risk']}R/day\n"
                    f"\u25CF Score \u2265 {mode_cfg['score_threshold']} | "
                    f"Stop after {mode_cfg['stop_after_losses']} losses\n"
                    f"\u25CF Market: {market_condition} | Regime: {regime}\n"
                    f"\u25CF {focus_line}{disable_line}\n\n"
                    f"Yesterday: {yest_total} trades, {yest_pnl:+.2f}R\n"
                    f"Rolling 20: WR={win_rate_20*100:.1f}% PF={pf_display}"
                    f"{alert_block}"
                )
            },
            requires_approval=False,
        )

        # 6f. Summary line
        result.summary = (
            f"{mode} mode | Risk {mode_cfg['risk_multiplier']}x | "
            f"Max {mode_cfg['max_daily_risk']}R | "
            f"Score\u2265{mode_cfg['score_threshold']} | "
            f"Confidence {confidence}% | "
            f"Market: {market_condition}"
        )
