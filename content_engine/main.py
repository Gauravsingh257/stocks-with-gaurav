"""
content_engine/main.py

CLI entry point for the content engine.

Usage:
    python main.py --mode strategy     # Generate strategy images → send to Telegram
    python main.py --mode news         # Generate news images → send to Telegram
    python main.py --mode schedule     # Start the daily scheduler (blocking)
    python main.py --mode all          # Run both agents once now

Flow (strategy / news):
    generate posts → render 1080×1080 PNG per post → sendPhoto to content channel

This file sets up logging and delegates to the appropriate agent or scheduler.
All logic lives in agents/ and services/.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# ── Logging setup ──────────────────────────────────────────────────────────────
# Must happen before any other content_engine import so all loggers inherit it.

def _setup_logging(log_file: str, level: str) -> None:
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "openai", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="content_engine",
        description="AI Content Engine — generates daily trading content via Telegram",
    )
    parser.add_argument(
        "--mode",
        choices=["strategy", "news", "schedule", "all"],
        required=True,
        help=(
            "strategy: run strategy agent once | "
            "news: run news agent once | "
            "schedule: start daily scheduler | "
            "all: run all agents once"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load settings early to get log config
    from content_engine.config.settings import load_settings
    try:
        settings = load_settings()
    except KeyError as exc:
        print(f"[ERROR] Missing required environment variable: {exc}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(settings.log_file, settings.log_level)
    log = logging.getLogger("content_engine.main")
    log.info("Content engine starting in mode: %s", args.mode)

    if args.mode == "strategy":
        from content_engine.agents import strategy_agent
        result = strategy_agent.run(settings)
        log.info(
            "strategy_agent done | images: %d | sent: %d | errors: %d",
            result.get("images_generated", 0),
            result.get("posts_sent", 0),
            len(result.get("errors", [])),
        )
        sys.exit(0 if result["status"] in ("ok", "partial") else 1)

    elif args.mode == "news":
        from content_engine.agents import news_agent
        result = news_agent.run(settings)
        log.info(
            "news_agent done | intel: %d | images: %d | sent: %d | errors: %d",
            result.get("intel_count", 0),
            result.get("images_generated", 0),
            result.get("posts_sent", 0),
            len(result.get("errors", [])),
        )
        sys.exit(0 if result["status"] in ("ok", "partial") else 1)

    elif args.mode == "all":
        from content_engine.agents import strategy_agent, news_agent
        r1 = strategy_agent.run(settings)
        r2 = news_agent.run(settings)
        log.info(
            "strategy_agent | images: %d | sent: %d | errors: %d",
            r1.get("images_generated", 0), r1.get("posts_sent", 0), len(r1.get("errors", [])),
        )
        log.info(
            "news_agent     | images: %d | sent: %d | errors: %d",
            r2.get("images_generated", 0), r2.get("posts_sent", 0), len(r2.get("errors", [])),
        )
        failed = any(r["status"] == "error" for r in [r1, r2])
        sys.exit(1 if failed else 0)

    elif args.mode == "schedule":
        from content_engine.scheduler import start_scheduler
        start_scheduler()  # blocking


if __name__ == "__main__":
    main()
