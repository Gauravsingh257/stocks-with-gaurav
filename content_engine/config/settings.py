"""
content_engine/config/settings.py

All configuration loaded from environment variables.
Never import from engine/ — this module is fully isolated.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ContentEngineSettings:
    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str = field(
        default_factory=lambda: os.environ["TELEGRAM_BOT_TOKEN"]
    )
    # Separate channel/group for content (NOT the intraday signal chat)
    telegram_content_chat_id: str = field(
        default_factory=lambda: os.environ["TELEGRAM_CONTENT_CHAT_ID"]
    )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = field(
        default_factory=lambda: os.environ.get("OPENAI_API_KEY", "")
    )
    openai_model: str = field(
        default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    )

    # ── News APIs ─────────────────────────────────────────────────────────────
    newsapi_key: str = field(
        default_factory=lambda: os.environ.get("NEWS_API_KEY", "")
    )
    gnews_api_key: str = field(
        default_factory=lambda: os.environ.get("GNEWS_API_KEY", "")
    )

    # ── Retry / timeout ───────────────────────────────────────────────────────
    http_timeout_seconds: int = 15
    max_retries: int = 3
    retry_backoff_seconds: int = 2

    # ── Content behaviour ─────────────────────────────────────────────────────
    # Market indices to include in strategy posts
    default_symbols: list = field(
        default_factory=lambda: ["NIFTY 50", "BANKNIFTY", "SENSEX"]
    )
    # Log level for the content engine
    log_level: str = field(
        default_factory=lambda: os.environ.get("CONTENT_LOG_LEVEL", "INFO")
    )
    log_file: str = "logs/content_engine.log"


def load_settings() -> ContentEngineSettings:
    """Return validated settings; raises KeyError for missing required vars."""
    return ContentEngineSettings()
