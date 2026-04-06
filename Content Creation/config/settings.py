"""
Content Creation / config / settings.py

All configuration loaded from environment variables.
Isolated from the main trading engine — no cross-imports.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
LOGS_DIR = ROOT_DIR / "logs"
TEMPLATES_DIR = ROOT_DIR / "templates"

OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    """Validated settings with env-var loading."""

    # ── Instagram / Meta Graph API ────────────────────────────────────────
    instagram_access_token: str = Field(default="", alias="INSTAGRAM_ACCESS_TOKEN")
    instagram_business_id: str = Field(default="", alias="INSTAGRAM_BUSINESS_ID")
    instagram_username: str = Field(default="", alias="INSTAGRAM_USERNAME")
    instagram_password: str = Field(default="", alias="INSTAGRAM_PASSWORD")

    # ── Telegram (for cross-posting) ──────────────────────────────────────
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_content_chat_id: str = Field(default="", alias="TELEGRAM_CONTENT_CHAT_ID")

    # ── OpenAI (optional — for content enrichment) ────────────────────────
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    # ── News / Data APIs ──────────────────────────────────────────────────
    newsapi_key: str = Field(default="", alias="NEWS_API_KEY")
    gnews_api_key: str = Field(default="", alias="GNEWS_API_KEY")

    # ── Branding ──────────────────────────────────────────────────────────
    brand_handle: str = "@StocksWithGaurav"
    brand_name: str = "StocksWithGaurav"

    # ── Pipeline ──────────────────────────────────────────────────────────
    max_retries: int = 3
    http_timeout: int = 15
    slide_width: int = 1080
    slide_height: int = 1080
    max_slides: int = 10
    default_indices: list[str] = ["NIFTY 50", "BANKNIFTY", "SENSEX"]

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", alias="CONTENT_LOG_LEVEL")

    class Config:
        env_file = ".env"
        extra = "ignore"


def load_settings() -> Settings:
    """Return validated settings. Raises ValidationError for bad config."""
    return Settings()
