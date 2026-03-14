"""
agents/runner.py
APScheduler-based agent runner.

Schedules:
  RiskSentinel      — every 1 min  (09:15–15:30, Mon–Fri)
  TradeManager      — every 5 min  (09:15–15:30, Mon–Fri)
  PreMarketBriefing — 08:45        (Mon–Fri)
  PostMarketAnalyst — 15:30        (Mon–Fri)

Public API (called from FastAPI lifespan):
  start_scheduler()
  stop_scheduler()
  run_agent_now(agent_name) -> dict
  get_agent_statuses() -> list[dict]
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.post_market  import PostMarketAnalyst
from agents.pre_market   import PreMarketBriefing
from agents.trade_manager import TradeManager
from agents.risk_sentinel import RiskSentinel
from agents.oi_intelligence_agent import generate_snapshot as _oi_generate_snapshot
from agents.swing_alpha_agent import SwingTradeAlphaAgent
from agents.longterm_investment_agent import LongTermInvestmentAgent

logger = logging.getLogger("agents.runner")

# ── Agent registry ───────────────────────────────────────────────────────────
AGENTS = {
    "PostMarketAnalyst": PostMarketAnalyst(),
    "PreMarketBriefing": PreMarketBriefing(),
    "TradeManager":      TradeManager(),
    "RiskSentinel":      RiskSentinel(),
    "SwingTradeAlphaAgent": SwingTradeAlphaAgent(),
    "LongTermInvestmentAgent": LongTermInvestmentAgent(),
}

_scheduler: BackgroundScheduler | None = None


def get_scheduler_running() -> bool:
    """Return True if the APScheduler is currently running."""
    return bool(_scheduler and _scheduler.running)

# ── Market hours guard (IST, UTC+5:30) ───────────────────────────────────────
def _market_hours() -> bool:
    """Return True during 09:15–15:31 IST Mon–Fri."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time
    return time(9, 15) <= t <= time(15, 31)


def _run_agent(agent_name: str) -> None:
    agent = AGENTS.get(agent_name)
    if agent is None:
        logger.error("Unknown agent: %s", agent_name)
        return
    try:
        result = agent.execute()
        level  = "INFO" if result.status == "OK" else "WARNING"
        logger.log(logging.getLevelName(level),
                   "[%s] %s | %s", agent_name, result.status, result.summary)
    except Exception:
        logger.exception("[%s] Unhandled exception", agent_name)


def _run_market_hours(agent_name: str) -> None:
    """Wrapper that skips execution outside market hours."""
    if _market_hours():
        _run_agent(agent_name)


def weekly_swing_scan() -> None:
    """Public wrapper for weekly swing scan."""
    _run_agent("SwingTradeAlphaAgent")


def weekly_longterm_scan() -> None:
    """Public wrapper for weekly long-term ranking scan."""
    _run_agent("LongTermInvestmentAgent")


# ── Lifecycle ────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        logger.info("Scheduler already running")
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

    # Pre-market briefing: 08:45 Mon–Fri
    _scheduler.add_job(
        lambda: _run_agent("PreMarketBriefing"),
        CronTrigger(hour=8, minute=45, day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="pre_market",
        name="Pre-Market Briefing",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Post-market analyst: 15:30 Mon–Fri
    _scheduler.add_job(
        lambda: _run_agent("PostMarketAnalyst"),
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="post_market",
        name="Post-Market Analyst",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Trade manager: every 5 min (market hours auto-gated inside wrapper)
    _scheduler.add_job(
        lambda: _run_market_hours("TradeManager"),
        CronTrigger(minute="*/5", day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="trade_manager",
        name="Trade Manager",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Risk sentinel: every 1 min (market hours gated)
    _scheduler.add_job(
        lambda: _run_market_hours("RiskSentinel"),
        CronTrigger(minute="*", day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="risk_sentinel",
        name="Risk Sentinel",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # OI Intelligence: every 60s during market hours (read-only, no AGENTS entry)
    def _run_oi_intelligence():
        if _market_hours():
            try:
                _oi_generate_snapshot()
                logger.debug("[OI Intelligence] snapshot generated")
            except Exception:
                logger.exception("[OI Intelligence] snapshot failed")

    _scheduler.add_job(
        _run_oi_intelligence,
        CronTrigger(minute="*", day_of_week="mon-fri", timezone="Asia/Kolkata"),
        id="oi_intelligence",
        name="OI Intelligence",
        replace_existing=True,
        misfire_grace_time=30,
    )

    # Swing alpha scan: weekly before market open
    _scheduler.add_job(
        weekly_swing_scan,
        CronTrigger(day_of_week="mon", hour=8, minute=30, timezone="Asia/Kolkata"),
        id="weekly_swing_scan",
        name="Weekly Swing Alpha Scan",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Long-term ranking scan: weekly before market open
    _scheduler.add_job(
        weekly_longterm_scan,
        CronTrigger(day_of_week="mon", hour=8, minute=40, timezone="Asia/Kolkata"),
        id="weekly_longterm_scan",
        name="Weekly Long-Term Ranking Scan",
        replace_existing=True,
        misfire_grace_time=600,
    )

    _scheduler.start()
    logger.info("Agent scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Agent scheduler stopped")
    _scheduler = None


# ── Public helpers ───────────────────────────────────────────────────────────

def run_agent_now(agent_name: str) -> dict:
    """Manually trigger an agent. Returns serialised AgentResult dict."""
    agent = AGENTS.get(agent_name)
    if agent is None:
        return {"error": f"Unknown agent: {agent_name}"}
    try:
        result = agent.execute()
        return result.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def get_agent_statuses() -> list[dict]:
    """Return last-run status + next scheduled run for each agent."""
    from dashboard.backend.db import get_connection

    statuses = []
    for name, agent in AGENTS.items():
        try:
            conn = get_connection()
            row  = conn.execute(
                """
                SELECT run_time, status, summary
                FROM agent_logs
                WHERE agent_name = ?
                ORDER BY run_time DESC
                LIMIT 1
                """,
                (name,),
            ).fetchone()
            conn.close()

            last_run    = dict(row) if row else None
            next_run    = None
            if _scheduler:
                job = _scheduler.get_job(name.lower().replace("analyst","_analyst")
                                                     .replace("briefing","_briefing")
                                                     .replace("manager","_manager")
                                                     .replace("sentinel","_sentinel"))
                # simpler: look up by agent display name
                for j in _scheduler.get_jobs():
                    if name.lower() in j.id:
                        next_run = j.next_run_time.isoformat() if j.next_run_time else None
                        break

            statuses.append({
                "name":        name,
                "description": agent.description,
                "schedule":    agent.schedule,
                "priority":    agent.priority,
                "last_run":    last_run,
                "next_run":    next_run,
            })
        except Exception as e:
            statuses.append({"name": name, "error": str(e)})

    return statuses
