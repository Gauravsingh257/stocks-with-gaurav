"""
content_engine/scheduler.py

APScheduler-based scheduler that runs agents on a daily cadence.

Default schedule (IST):
  - 08:30  → strategy_agent  (pre-market outlook, before 09:15 open)
  - 09:00  → news_agent      (morning news digest)
  - 15:45  → news_agent      (end-of-day wrap-up)

The scheduler runs as a blocking process and is designed to be deployed
as a separate Railway service so it never interferes with the engine.

Usage:
    python -m content_engine.scheduler
    # or from main.py:
    python main.py --mode schedule
"""

import logging
import sys
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from content_engine.config.settings import load_settings
from content_engine.agents import strategy_agent, news_agent

log = logging.getLogger("content_engine.scheduler")

IST = ZoneInfo("Asia/Kolkata")


def _run_strategy():
    settings = load_settings()
    log.info("Scheduler: running strategy_agent")
    result = strategy_agent.run(settings)
    log.info("strategy_agent result: %s", result)


def _run_news():
    settings = load_settings()
    log.info("Scheduler: running news_agent")
    result = news_agent.run(settings)
    log.info("news_agent result: %s", result)


def start_scheduler() -> None:
    """Start the blocking APScheduler with all content engine jobs."""
    scheduler = BlockingScheduler(timezone=IST)

    # Pre-market strategy post at 08:30 IST
    scheduler.add_job(
        _run_strategy,
        trigger=CronTrigger(hour=8, minute=30, timezone=IST),
        id="strategy_premarket",
        name="Pre-Market Strategy Post",
        replace_existing=True,
    )

    # Morning news digest at 09:00 IST
    scheduler.add_job(
        _run_news,
        trigger=CronTrigger(hour=9, minute=0, timezone=IST),
        id="news_morning",
        name="Morning News Digest",
        replace_existing=True,
    )

    # End-of-day news wrap at 15:45 IST (after market close)
    scheduler.add_job(
        _run_news,
        trigger=CronTrigger(hour=15, minute=45, timezone=IST),
        id="news_eod",
        name="End-of-Day News Wrap",
        replace_existing=True,
    )

    log.info("Content engine scheduler started. Jobs: %s", [j.name for j in scheduler.get_jobs()])
    log.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
        sys.exit(0)


if __name__ == "__main__":
    start_scheduler()
