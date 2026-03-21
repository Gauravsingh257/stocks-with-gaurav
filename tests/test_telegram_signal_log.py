"""Tests for utils.telegram_signal_log — signal_log row building."""
import json

from utils.telegram_signal_log import build_signal_record, persist_telegram_signal


def test_build_signal_record_merges_meta():
    rec = build_signal_record(
        "<b>NSE:FOO</b> LONG",
        "sig-1",
        {
            "signal_kind": "ENTRY",
            "symbol": "NSE:FOO",
            "direction": "long",
            "strategy_name": "SETUP_A",
            "entry": 100.0,
            "stop_loss": 99.0,
            "target1": 102.0,
            "smc_score": 7,
        },
    )
    assert rec["signal_id"] == "sig-1"
    assert rec["direction"] == "LONG"
    assert rec["strategy_name"] == "SETUP_A"
    assert rec["score"] == 7.0
    assert "telegram_html" in rec
    assert "SETUP_A" in json.dumps(rec)


def test_persist_telegram_signal_no_crash(tmp_path, monkeypatch):
    """Smoke: persist does not raise when DB path is valid."""
    from ai_learning import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    persist_telegram_signal("hello", "sid-x", {"signal_kind": "TEST", "symbol": "NSE:X"})

    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    n = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
    conn.close()
    assert n == 1
