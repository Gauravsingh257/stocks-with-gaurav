"""
Content Creation / main.py

Production entry point for the Instagram carousel automation pipeline.

Features:
  - Scheduler: 8:30 AM pre-market, 4:30 PM post-market (IST via `schedule`)
  - Structured logging: file + console
  - Error handling with retries
  - Local output saving (images + JSON summary)
  - Immediate run mode (--now) and dry-run mode (--dry-run)

Usage:
  python main.py                  # Start scheduler (runs at 8:30 AM / 4:30 PM)
  python main.py --now            # Run once immediately (pre_market)
  python main.py --now post       # Run once immediately (post_market)
  python main.py --dry-run        # Run pipeline without publishing
  python main.py --now --dry-run  # Immediate dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

# Fix Windows cp1252 terminal for Unicode
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Must be set before any local imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import LOGS_DIR, OUTPUT_DIR, load_settings
from models.contracts import PipelineMode
from pipeline.orchestrator import Orchestrator

# ── Logging Setup ─────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    """Configure logging to both console and rotating daily file."""
    log_fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"pipeline_{datetime.now().strftime('%Y-%m-%d')}.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear old handlers to avoid duplicates on re-init
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_fmt, datefmt=date_fmt))
    root.addHandler(console)

    # File handler (append, UTF-8)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(log_fmt, datefmt=date_fmt))
    root.addHandler(fh)


log = logging.getLogger("content_creation.main")


# ── Pipeline Runner ───────────────────────────────────────────────────────

def run_pipeline(mode: PipelineMode, publish: bool = True) -> None:
    """Execute the full carousel pipeline with error handling."""
    log.info("=" * 70)
    log.info("PIPELINE TRIGGERED: mode=%s publish=%s at %s",
             mode.value, publish, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 70)

    try:
        settings = load_settings()
        orch = Orchestrator(settings=settings)
        result = orch.execute(mode=mode, publish=publish, max_qa_retries=1)

        # Save pipeline result JSON alongside the images
        _save_run_summary(result, mode)

        if result.status == "OK":
            log.info("Pipeline completed successfully: %d slides in %.1fs",
                     result.slides_generated, result.duration_seconds)
        elif result.status == "PARTIAL":
            log.warning("Pipeline completed with warnings: %s", result.error or "QA issues")
        else:
            log.error("Pipeline FAILED: %s", result.error)

        # Print summary to console
        print(f"\n{'='*50}")
        print(f"  Pipeline: {mode.value.upper()}")
        print(f"  Status:   {result.status}")
        print(f"  Slides:   {result.slides_generated}")
        print(f"  Published:{result.published}")
        print(f"  Duration: {result.duration_seconds:.1f}s")
        if result.error:
            print(f"  Error:    {result.error}")
        print(f"{'='*50}\n")

    except Exception:
        log.exception("Unhandled error in pipeline run")


def _save_run_summary(result, mode: PipelineMode) -> None:
    """Save a JSON summary of the pipeline run to output/<date>/."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = OUTPUT_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"run_{mode.value}_{datetime.now().strftime('%H%M%S')}.json"

    summary = {
        "run_id": result.run_id,
        "mode": result.mode.value,
        "status": result.status,
        "duration_seconds": result.duration_seconds,
        "slides_generated": result.slides_generated,
        "published": result.published,
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat() if result.ended_at else None,
        "error": result.error,
        "agent_timings": [
            {
                "agent": t.agent_name,
                "duration": t.duration_seconds,
                "status": t.status,
            }
            for t in result.agent_timings
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("Run summary saved: %s", summary_path.name)


# ── Scheduled Jobs ────────────────────────────────────────────────────────

def schedule_pre_market() -> None:
    """Scheduled job: 8:30 AM IST pre-market carousel."""
    run_pipeline(PipelineMode.PRE_MARKET, publish=True)


def schedule_post_market() -> None:
    """Scheduled job: 4:30 PM IST post-market carousel."""
    run_pipeline(PipelineMode.POST_MARKET, publish=True)


def start_scheduler() -> None:
    """Start the daily scheduler loop using `schedule` library."""
    schedule.every().day.at("08:30").do(schedule_pre_market)
    schedule.every().day.at("16:30").do(schedule_post_market)

    log.info("Scheduler started:")
    log.info("  Pre-market:  08:30 daily")
    log.info("  Post-market: 16:30 daily")
    log.info("Next run: %s", schedule.next_run())
    log.info("Press Ctrl+C to stop")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Scheduler stopped by user")


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Instagram Carousel Pipeline - @StocksWithGaurav"
    )
    parser.add_argument(
        "--now",
        nargs="?",
        const="pre",
        choices=["pre", "post"],
        help="Run once immediately. 'pre' (default) or 'post' market mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without publishing to Instagram/Telegram.",
    )
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.log_level)

    log.info("Content Creation Pipeline v1.0 - @StocksWithGaurav")
    log.info("Output directory: %s", OUTPUT_DIR)
    log.info("Logs directory: %s", LOGS_DIR)

    if args.now:
        mode = PipelineMode.POST_MARKET if args.now == "post" else PipelineMode.PRE_MARKET
        publish = not args.dry_run
        run_pipeline(mode, publish=publish)
    else:
        start_scheduler()


if __name__ == "__main__":
    main()
