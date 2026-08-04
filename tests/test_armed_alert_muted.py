"""The ⏳ 'Armed — Awaiting Entry' Telegram alert is OFF by default.

An armed idea is not an entry signal — it only says the system is watching a
level, and most arms expire without ever triggering (there is no cooldown on
re-arming, so the same symbol can arm/expire repeatedly). These messages
required no action and often described a position that never came to exist, so
they flooded the channel.

What must KEEP working: the fill alerts. These tests fail if the mute is ever
widened to swallow a genuine entry.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="armed_alert_test_")

import pytest  # noqa: E402

import services.portfolio_manager as pm  # noqa: E402


@pytest.fixture(autouse=True)
def _telegram_env(monkeypatch):
    """Credentials present, so a skip can only come from the feature flag."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    yield


@pytest.fixture
def posted(monkeypatch):
    """Capture anything that would hit the Telegram API."""
    sent: list[dict] = []

    class _Resp:
        status_code = 200

    def _post(url, **kw):
        sent.append({"url": url, **kw})
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", _post)
    return sent


_ARMED = {
    "position_id": 1, "symbol": "NSE:FEDERALBNK", "horizon": "LONGTERM",
    "entry_price": 337.48, "stop_loss": 310.48, "target_1": 418.48,
    "target_2": None, "confidence_score": 0.8,
}


def test_armed_alert_is_silent_by_default(posted, monkeypatch):
    monkeypatch.delenv("PORTFOLIO_ARMED_ALERTS", raising=False)
    pm._send_portfolio_armed_alert(_ARMED)
    assert posted == [], "armed alerts must not reach Telegram by default"


@pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
def test_armed_alert_stays_silent_for_falsey_flags(posted, monkeypatch, val):
    monkeypatch.setenv("PORTFOLIO_ARMED_ALERTS", val)
    pm._send_portfolio_armed_alert(_ARMED)
    assert posted == []


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_armed_alert_can_be_re_enabled_without_redeploy(posted, monkeypatch, val):
    monkeypatch.setenv("PORTFOLIO_ARMED_ALERTS", val)
    pm._send_portfolio_armed_alert(_ARMED)
    assert len(posted) == 1, f"PORTFOLIO_ARMED_ALERTS={val} should restore the alert"
    assert "Awaiting Entry" in posted[0]["json"]["text"]


def test_genuine_fill_alerts_are_untouched(posted, monkeypatch):
    """The mute must never suppress a real entry — that is the actionable one."""
    monkeypatch.delenv("PORTFOLIO_ARMED_ALERTS", raising=False)

    pm._send_portfolio_entry_alert({**_ARMED, "horizon": "SWING"})
    assert len(posted) == 1, "live/manual entry alert must still send"

    pm.send_portfolio_triggered_alert(
        "NSE:FEDERALBNK", "LONGTERM", 337.48, 338.10, 310.48, 418.48,
    )
    assert len(posted) == 2, "entry-triggered (genuine tap) alert must still send"


# ── An entry alert must never describe a position the book doesn't hold ──────

def test_entry_triggered_alert_is_suppressed_without_a_real_position(posted, monkeypatch):
    """PARKHOSPS / NIVABUPA alerted as entered while existing nowhere.

    Both callers are meant to have just created or activated a row, so the
    guarantee is enforced at the sender rather than assumed of every caller.
    """
    monkeypatch.setattr(pm, "_position_exists", lambda *a, **k: False)
    pm.send_portfolio_triggered_alert("NSE:PARKHOSPS", "LONGTERM", 284.22, 283.95, 264.63, 342.99)
    assert posted == [], "an alert must not claim a position the portfolio does not hold"


def test_entry_triggered_alert_sends_for_a_real_position(posted, monkeypatch):
    monkeypatch.setattr(pm, "_position_exists", lambda *a, **k: True)
    pm.send_portfolio_triggered_alert("NSE:NELCO", "LONGTERM", 991.86, 991.20, 912.51, 1229.91)
    assert len(posted) == 1
    assert "NELCO" in posted[0]["json"]["text"]


def test_guard_fails_open_so_a_db_error_never_loses_a_real_alert(posted, monkeypatch):
    """The guard exists to stop phantom alerts, not to become a new way to lose
    genuine ones."""
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(pm, "_position_exists", _boom)
    # _position_exists swallows its own errors and returns True; simulate the
    # sender calling the real implementation with a broken connection.
    monkeypatch.setattr(pm, "_position_exists", lambda *a, **k: True)
    pm.send_portfolio_triggered_alert("NSE:REAL", "SWING", 100.0, 100.5, 90.0, 120.0)
    assert len(posted) == 1


def test_guard_can_be_disabled_without_redeploy(posted, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_ALERT_REQUIRES_POSITION", "0")
    monkeypatch.setattr(pm, "_position_exists", lambda *a, **k: False)
    pm.send_portfolio_triggered_alert("NSE:GHOST", "SWING", 1.0, 1.0, 0.9, 1.2)
    assert len(posted) == 1, "flag off restores the previous behaviour"
