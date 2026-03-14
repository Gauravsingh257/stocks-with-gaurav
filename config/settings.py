"""
config/settings.py — Centralized configuration using pydantic-settings.

Loads from .env file and environment variables with validation.
Usage:
    from config.settings import settings
    print(settings.engine_mode)
"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TradingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Zerodha / Kite
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smc_pro_chat_id: str = ""

    # OpenAI
    openai_api_key: str = ""

    # Engine
    engine_mode: str = Field(default="AGGRESSIVE", description="CONSERVATIVE | BALANCED | AGGRESSIVE")
    backtest_mode: bool = False
    debug_mode: bool = True
    paper_trading: bool = True

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # Database
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'dashboard.db'}"
    backtest_db_url: str = f"sqlite:///{PROJECT_ROOT / 'backtest_data.db'}"

    # Logging
    log_level: str = "INFO"
    log_dir: Path = PROJECT_ROOT / "logs"

    # Risk
    max_daily_loss_r: float = -3.0
    max_daily_signals: int = 5
    cooldown_after_streak: int = 3

    @property
    def is_live(self) -> bool:
        return not self.paper_trading and not self.backtest_mode


settings = TradingSettings()
