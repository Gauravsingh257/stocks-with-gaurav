"""
Tests for engine/paper_mode.py — Paper Trading Mode (Phase 6).
"""

import os
import csv
import tempfile
import pytest
from unittest import mock

# Ensure PAPER_MODE can be toggled for testing
os.environ["PAPER_MODE"] = "1"

from engine import paper_mode
from engine.paper_mode import (
    log_paper_trade, log_paper_outcome, paper_prefix,
    paper_daily_summary, paper_mode_banner,
    _LOG_FIELDS, _OUTCOME_FIELDS, PASS_CRITERIA,
)


class TestPaperPrefix:
    def test_prefix_added(self):
        assert paper_prefix("Hello").startswith("[PAPER]")

    def test_already_prefixed(self):
        msg = "[PAPER] Hi"
        assert paper_prefix(msg) == msg

    def test_html_prefixed(self):
        msg = "<b>[PAPER]</b> Hi"
        assert paper_prefix(msg) == msg

    def test_empty_string(self):
        assert paper_prefix("") == "[PAPER] "


class TestLogPaperTrade:
    def test_creates_csv(self, tmp_path, monkeypatch):
        log_file = str(tmp_path / "test_log.csv")
        monkeypatch.setattr(paper_mode, "PAPER_TRADE_LOG", log_file)
        monkeypatch.setattr(paper_mode, "PAPER_MODE", True)

        signal = {
            "symbol": "NSE:NIFTY 50", "setup": "SETUP_C", "direction": "LONG",
            "entry": 22500, "sl": 22400, "target": 22700, "rr": 2.0,
        }
        log_paper_trade(signal)

        assert os.path.exists(log_file)
        with open(log_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "NSE:NIFTY 50"
        assert rows[0]["setup"] == "SETUP_C"
        assert rows[0]["direction"] == "LONG"
        assert float(rows[0]["entry"]) == 22500

    def test_appends_multiple(self, tmp_path, monkeypatch):
        log_file = str(tmp_path / "test_log.csv")
        monkeypatch.setattr(paper_mode, "PAPER_TRADE_LOG", log_file)
        monkeypatch.setattr(paper_mode, "PAPER_MODE", True)

        for i in range(3):
            log_paper_trade({"symbol": f"SYM{i}", "setup": "C", "direction": "LONG"})

        with open(log_file, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3

    def test_skips_if_not_paper_mode(self, tmp_path, monkeypatch):
        log_file = str(tmp_path / "test_log.csv")
        monkeypatch.setattr(paper_mode, "PAPER_TRADE_LOG", log_file)
        monkeypatch.setattr(paper_mode, "PAPER_MODE", False)

        log_paper_trade({"symbol": "TEST"})
        assert not os.path.exists(log_file)


class TestLogPaperOutcome:
    def test_logs_outcome(self, tmp_path, monkeypatch):
        out_file = str(tmp_path / "test_outcomes.csv")
        monkeypatch.setattr(paper_mode, "PAPER_OUTCOMES_LOG", out_file)
        monkeypatch.setattr(paper_mode, "PAPER_MODE", True)

        trade = {
            "symbol": "NSE:NIFTY BANK", "setup": "SETUP_C",
            "direction": "LONG", "entry": 50000, "sl": 49800, "target": 50400,
        }
        log_paper_outcome(trade, 50400, "WIN", 2.0)

        with open(out_file, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["result"] == "WIN"
        assert float(rows[0]["pnl_r"]) == 2.0

    def test_skips_if_not_paper_mode(self, tmp_path, monkeypatch):
        out_file = str(tmp_path / "test_outcomes.csv")
        monkeypatch.setattr(paper_mode, "PAPER_OUTCOMES_LOG", out_file)
        monkeypatch.setattr(paper_mode, "PAPER_MODE", False)

        log_paper_outcome({}, 0, "WIN", 1.0)
        assert not os.path.exists(out_file)


class TestPaperDailySummary:
    def test_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paper_mode, "PAPER_OUTCOMES_LOG", str(tmp_path / "missing.csv"))
        result = paper_daily_summary()
        assert "No trades" in result

    def test_summary_with_trades(self, tmp_path, monkeypatch):
        from datetime import datetime
        out_file = str(tmp_path / "outcomes.csv")
        monkeypatch.setattr(paper_mode, "PAPER_OUTCOMES_LOG", out_file)

        # Write some outcomes for today
        today = datetime.now().strftime("%Y-%m-%d")
        with open(out_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_OUTCOME_FIELDS)
            writer.writeheader()
            writer.writerow({
                "timestamp": f"{today} 10:00:00", "symbol": "NIFTY",
                "exit_time": f"{today} 11:00:00", "result": "WIN", "pnl_r": "2.0",
            })
            writer.writerow({
                "timestamp": f"{today} 11:00:00", "symbol": "BANK",
                "exit_time": f"{today} 12:00:00", "result": "LOSS", "pnl_r": "-1.0",
            })

        result = paper_daily_summary()
        assert "Trades: 2" in result
        assert "W: 1" in result
        assert "L: 1" in result
        assert "+1.0R" in result


class TestPaperModeBanner:
    def test_banner_content(self):
        banner = paper_mode_banner()
        assert "PAPER TRADING MODE" in banner
        assert "paper_trade_log.csv" in banner


class TestPassCriteria:
    def test_criteria_keys(self):
        required = {"min_days", "min_trades", "min_win_rate",
                     "min_profit_factor", "min_expectancy_r", "max_drawdown_r"}
        assert required.issubset(PASS_CRITERIA.keys())

    def test_reasonable_values(self):
        assert PASS_CRITERIA["min_trades"] >= 10
        assert PASS_CRITERIA["min_win_rate"] >= 40
        assert PASS_CRITERIA["min_profit_factor"] >= 1.0
