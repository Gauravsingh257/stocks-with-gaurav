"""
Content Creation / agents / logger_agent.py

LoggerAgent — persists pipeline run logs and performance metrics.

Storage:
  - JSON Lines file (logs/pipeline_runs.jsonl) — one line per run
  - Daily summary file (logs/daily_YYYY-MM-DD.json)
  - Performance CSV (logs/performance.csv) — for analytics
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from agents.base import BaseContentAgent
from config.settings import LOGS_DIR
from models.contracts import PipelineRun

log = logging.getLogger("content_creation.agents.logger")


class LoggerAgent(BaseContentAgent):
    name = "LoggerAgent"
    description = "Persists pipeline run logs and performance metrics"

    def run(self, *, pipeline_run: PipelineRun) -> dict:
        """Log the pipeline run to all storage targets."""
        results = {
            "jsonl_logged": False,
            "daily_logged": False,
            "csv_logged": False,
        }

        # ── 1. Append to JSONL ────────────────────────────────────────────
        try:
            jsonl_path = LOGS_DIR / "pipeline_runs.jsonl"
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(pipeline_run.model_dump_json() + "\n")
            results["jsonl_logged"] = True
        except Exception as e:
            log.error("JSONL write failed: %s", e)

        # ── 2. Daily summary ──────────────────────────────────────────────
        try:
            date_str = pipeline_run.started_at.strftime("%Y-%m-%d")
            daily_path = LOGS_DIR / f"daily_{date_str}.json"

            daily_data = {"date": date_str, "runs": []}
            if daily_path.exists():
                daily_data = json.loads(daily_path.read_text(encoding="utf-8"))

            daily_data["runs"].append({
                "run_id": pipeline_run.run_id,
                "mode": pipeline_run.mode.value,
                "status": pipeline_run.status,
                "duration_seconds": pipeline_run.duration_seconds,
                "slides_generated": pipeline_run.slides_generated,
                "published": pipeline_run.published,
                "timestamp": pipeline_run.started_at.isoformat(),
            })
            daily_path.write_text(
                json.dumps(daily_data, indent=2, default=str),
                encoding="utf-8",
            )
            results["daily_logged"] = True
        except Exception as e:
            log.error("Daily log write failed: %s", e)

        # ── 3. Performance CSV ────────────────────────────────────────────
        try:
            csv_path = LOGS_DIR / "performance.csv"
            write_header = not csv_path.exists()

            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow([
                        "run_id", "mode", "started_at", "duration_seconds",
                        "status", "slides_generated", "published",
                        "agent_count", "errors",
                    ])

                errors = sum(1 for t in pipeline_run.agent_timings if t.status == "ERROR")
                writer.writerow([
                    pipeline_run.run_id,
                    pipeline_run.mode.value,
                    pipeline_run.started_at.isoformat(),
                    pipeline_run.duration_seconds,
                    pipeline_run.status,
                    pipeline_run.slides_generated,
                    pipeline_run.published,
                    len(pipeline_run.agent_timings),
                    errors,
                ])
            results["csv_logged"] = True
        except Exception as e:
            log.error("CSV write failed: %s", e)

        # ── Summary log ───────────────────────────────────────────────────
        log.info(
            "Pipeline run logged: id=%s mode=%s status=%s duration=%.1fs slides=%d",
            pipeline_run.run_id,
            pipeline_run.mode.value,
            pipeline_run.status,
            pipeline_run.duration_seconds,
            pipeline_run.slides_generated,
        )

        return results

    def get_recent_runs(self, limit: int = 10) -> list[dict]:
        """Read recent pipeline runs from JSONL."""
        jsonl_path = LOGS_DIR / "pipeline_runs.jsonl"
        if not jsonl_path.exists():
            return []

        runs = []
        for line in jsonl_path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                runs.append(json.loads(line))

        return runs[-limit:]
