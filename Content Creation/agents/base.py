"""
Content Creation / agents / base.py

BaseContentAgent — abstract base class for all pipeline agents.

Each agent:
  - Has a name and description
  - Receives typed input, returns typed output
  - Is independently testable
  - Logs timing and errors automatically
"""

from __future__ import annotations

import logging
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from models.contracts import AgentTiming

log = logging.getLogger("content_creation.agents")


class BaseContentAgent(ABC):
    """Abstract base for every agent in the carousel pipeline."""

    name: str = "BaseAgent"
    description: str = ""

    def __init__(self, settings: Any = None):
        self.settings = settings
        self._logger = logging.getLogger(f"content_creation.agents.{self.name}")

    def execute(self, **kwargs) -> tuple[Any, AgentTiming]:
        """
        Run the agent with timing + error handling.
        Returns (result, timing) — never raises.
        """
        started = datetime.now()
        t0 = time.perf_counter()
        timing = AgentTiming(
            agent_name=self.name,
            started_at=started,
            ended_at=started,
            duration_seconds=0.0,
        )

        try:
            self._logger.info("Starting %s", self.name)
            result = self.run(**kwargs)
            timing.status = "OK"
            self._logger.info("%s completed successfully", self.name)
        except Exception:
            tb = traceback.format_exc()
            self._logger.error("%s failed:\n%s", self.name, tb)
            timing.status = "ERROR"
            timing.error = tb
            result = None

        elapsed = time.perf_counter() - t0
        timing.ended_at = datetime.now()
        timing.duration_seconds = round(elapsed, 3)

        return result, timing

    @abstractmethod
    def run(self, **kwargs) -> Any:
        """Implement agent logic. Receives typed kwargs, returns typed output."""
        ...
