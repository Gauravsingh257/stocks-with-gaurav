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
import services.entry_gate as eg  # noqa: E402


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

    # The fill alert is gated by PORTFOLIO_ALERT_REQUIRES_POSITION, so the book
    # must genuinely hold this position for the alert to be legitimate. Create
    # it explicitly: this test previously relied on _position_exists failing
    # OPEN because the portfolio table did not exist in the temp DB, which made
    # it pass for the wrong reason and depend on test-module import order.
    from dashboard.backend.db.portfolio import init_portfolio_db, add_position
    init_portfolio_db()
    add_position({"symbol": "NSE:FEDERALBNK", "horizon": "LONGTERM",
                  "entry_price": 337.48, "stop_loss": 310.48,
                  "target_1": 418.48, "status": "ACTIVE"})

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
    monkeypatch.setattr(eg, "can_monitor_entry", lambda *a, **k: False)
    pm.send_portfolio_triggered_alert("NSE:PARKHOSPS", "LONGTERM", 284.22, 283.95, 264.63, 342.99)
    assert posted == [], "an alert must not claim a position the portfolio does not hold"


def test_entry_triggered_alert_sends_for_a_real_position(posted, monkeypatch):
    monkeypatch.setattr(eg, "can_monitor_entry", lambda *a, **k: True)
    pm.send_portfolio_triggered_alert("NSE:NELCO", "LONGTERM", 991.86, 991.20, 912.51, 1229.91)
    assert len(posted) == 1
    assert "NELCO" in posted[0]["json"]["text"]


def test_alert_fails_CLOSED_when_admission_cannot_be_established(posted, monkeypatch):
    """DELIBERATE CONTRACT CHANGE.

    The old private guard returned True on a database error, so a lookup
    failure could still emit a phantom alert. The shared entry gate fails
    CLOSED: an unsent alert is a minor loss; an alert describing a trade the
    book never held is a correctness failure the user cannot audit.
    """
    import dashboard.backend.db.schema as sch
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(sch, "get_connection", _boom)
    pm.send_portfolio_triggered_alert("NSE:REAL", "SWING", 100.0, 100.5, 90.0, 120.0)
    assert posted == [], "a failed admission lookup must not be read as permission"


def test_guard_can_be_disabled_without_redeploy(posted, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_ALERT_REQUIRES_POSITION", "0")
    monkeypatch.setattr(eg, "can_monitor_entry", lambda *a, **k: False)
    pm.send_portfolio_triggered_alert("NSE:GHOST", "SWING", 1.0, 1.0, 0.9, 1.2)
    assert len(posted) == 1, "flag off restores the previous behaviour"
