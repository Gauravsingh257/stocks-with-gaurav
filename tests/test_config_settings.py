"""
tests/test_config_settings.py — Tests for centralized configuration.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestSettings:
    def test_settings_import(self):
        from config.settings import settings
        assert settings is not None

    def test_default_values(self):
        from config.settings import settings
        assert settings.engine_mode in ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")
        assert settings.max_daily_signals > 0
        assert settings.max_daily_loss_r < 0

    def test_is_live_default_false(self):
        from config.settings import settings
        assert settings.is_live is False

    def test_project_root(self):
        from config.settings import PROJECT_ROOT
        assert PROJECT_ROOT.exists()
        assert (PROJECT_ROOT / "engine").exists()
