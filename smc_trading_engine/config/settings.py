"""
Configuration Settings for SMC Trading Engine
Centralizes all system parameters and constants
"""

from dataclasses import dataclass
from typing import Dict, List
import os
from datetime import time


@dataclass
class AccountConfig:
    """Account and risk management settings"""
    account_size: float = 100000.0  # $100,000
    risk_per_trade: float = 0.01  # 1% per trade
    min_rr_ratio: float = 3.0  # Minimum 1:3 risk-reward
    min_win_rate: float = 0.55  # 55% minimum win rate
    max_daily_loss: float = 0.05  # 5% daily loss limit
    max_trades_per_day: int = 10
    
    @property
    def risk_amount(self) -> float:
        """Calculate risk amount per trade"""
        return self.account_size * self.risk_per_trade
    
    @property
    def max_daily_loss_amount(self) -> float:
        """Calculate max daily loss in currency"""
        return self.account_size * self.max_daily_loss


@dataclass
class MarketConfig:
    """Market timing and settings"""
    market_open: time = time(9, 15)  # 9:15 AM IST
    market_close: time = time(15, 30)  # 3:30 PM IST
    new_signals_cutoff: time = time(15, 15)  # 3:15 PM (15-min buffer before close)
    eod_report_time: time = time(16, 0)  # 4:00 PM
    
    # Session trading window (entry model filter)
    session_start: time = time(9, 30)   # Only enter after 9:30 AM
    session_end: time = time(14, 30)    # Stop entries at 2:30 PM
    
    # Killzone windows (highest probability)
    killzone_morning_start: time = time(9, 30)
    killzone_morning_end: time = time(11, 30)
    killzone_afternoon_start: time = time(13, 45)
    killzone_afternoon_end: time = time(14, 30)
    
    def is_market_open(self) -> bool:
        """Check if NSE market is open (for existing positions only after 3:15 PM)"""
        from datetime import datetime
        now = datetime.now().time()
        return self.market_open <= now <= self.market_close
    
    def can_send_new_signals(self) -> bool:
        """Check if new trade signals can be sent"""
        from datetime import datetime
        now = datetime.now().time()
        return self.market_open <= now <= self.new_signals_cutoff


@dataclass
class SMCConfig:
    """Smart Money Concepts parameters"""
    min_smc_score: int = 6  # Minimum confluence score (out of 10)
    enable_setup_a: bool = True
    enable_setup_b: bool = True
    enable_setup_c: bool = False  # Disabled - needs improvement
    enable_setup_d: bool = False  # Disabled - poor performance (23.3% WR)
    
    hl_lookback_bars: int = 50  # High-Low lookback for structure
    fvg_min_pips: int = 5
    ob_min_size: float = 0.015  # Minimum OB size as % of price
    liquidity_pool_size: float = 0.02  # 2% for liquidity pools


@dataclass
class DataConfig:
    """Data fetching and processing settings"""
    kite_api_key: str = ""
    kite_access_token: str = ""
    default_lookback_bars: int = 200
    timeframes: List[str] = None
    
    def __post_init__(self):
        if self.timeframes is None:
            self.timeframes = ["5minute", "15minute", "30minute", "60minute"]
        
        # Load from environment if not set
        if not self.kite_api_key:
            self.kite_api_key = os.getenv("KITE_API_KEY", "")
        if not self.kite_access_token:
            self.kite_access_token = os.getenv("KITE_ACCESS_TOKEN", "")


@dataclass
class NotificationConfig:
    """Telegram and notification settings"""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    enable_telegram: bool = True
    enable_price_alerts: bool = True
    enable_trade_alerts: bool = True
    
    def __post_init__(self):
        if not self.telegram_bot_token:
            self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self.telegram_chat_id:
            self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")


@dataclass
class RegimeConfig:
    """Pre-market regime classification settings"""
    # PCR thresholds
    pcr_bullish: float = 1.3
    pcr_bearish: float = 0.7

    # VIX thresholds
    vix_high: float = 20.0
    vix_low: float = 14.0
    vix_rising_pct: float = 10.0

    # Gap / ATR ratios
    gap_high_atr_ratio: float = 0.8
    gap_low_atr_ratio: float = 0.3
    gap_trend_atr_ratio: float = 0.5  # for TREND_UP / TREND_DOWN trigger

    # Component weights (must sum to 1.0)
    weight_global: float = 0.25
    weight_oi: float = 0.25
    weight_gap: float = 0.15
    weight_vol: float = 0.15
    weight_event: float = 0.20

    # HIGH_VOL_EVENT position size reduction
    high_vol_size_multiplier: float = 0.50

    # Enable/disable regime filter
    enable_regime_filter: bool = True


class Settings:
    """Global settings container"""
    
    def __init__(self):
        self.account = AccountConfig()
        self.market = MarketConfig()
        self.smc = SMCConfig()
        self.data = DataConfig()
        self.notifications = NotificationConfig()
        self.regime = RegimeConfig()
    
    @staticmethod
    def get_default() -> "Settings":
        """Get default settings instance"""
        return Settings()
    
    def to_dict(self) -> Dict:
        """Convert all settings to dictionary"""
        return {
            "account": self.account.__dict__,
            "market": self.market.__dict__,
            "smc": self.smc.__dict__,
            "data": self.data.__dict__,
            "notifications": self.notifications.__dict__,
        }
    
    def validate(self) -> bool:
        """Validate critical settings"""
        assert self.account.account_size > 0, "Account size must be positive"
        assert 0 < self.account.risk_per_trade < 0.1, "Risk per trade must be 0.1% to 10%"
        assert self.account.min_rr_ratio >= 1.0, "RR ratio must be >= 1.0"
        assert self.account.min_win_rate > 0 and self.account.min_win_rate <= 1, "Win rate must be 0-1"
        return True


# Global settings instance
settings = Settings()
