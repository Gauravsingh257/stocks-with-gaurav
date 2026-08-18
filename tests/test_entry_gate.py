"""The Portfolio-admission invariant.

Forensics proved rejected `signals_log` candidates (final_selected=0) reached
entry monitoring and fired "Entry Triggered" alerts for symbols the book never
held — TARSONS, HFCL, JSFB and five others.

The invariant these tests defend:

    Nothing may be armed, monitored for entry, or alerted on unless a REAL
    Portfolio row exists for that symbol + book.
"""

from __future__ import annotations

import os

# Deliberately does NOT set its own DATA_DIR. Every test module here assigns one
# at import time, and pytest imports them all before running any, so the last
# assignment wins for the whole session — a module adding another one silently
# changes which database the OTHER modules talk to. This file only needs a clean
# table, which the fixture guarantees.
import pytest  # noqa: E402

from dashboard.backend.db.schema import get_connection  # noqa: E402
from dashboard.backend.db.portfolio import init_portfolio_db, add_position, close_position  # noqa: E402
from services import entry_gate as gate  # noqa: E402


def _wipe():
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_journal")
        c.execute("DELETE FROM portfolio_positions")
        c.commit()
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    try:
        from dashboard.backend.db.schema import init_db
        init_db()
    except Exception:
        pass
    init_portfolio_db()
    _wipe()
    monkeypatch.delenv("ENTRY_GATE_ENFORCE", raising=False)
    yield
    _wipe()


def _armed(symbol, horizon="LONGTERM", status="PENDING"):
    return add_position({"symbol": symbol, "horizon": horizon, "entry_price": 100.0,
                         "stop_loss": 90.0, "target_1": 120.0, "status": status})


# ── The phantom symbols must be refused ──────────────────────────────────────

@pytest.mark.parametrize("symbol,book", [
    ("NSE:TARSONS", "LONGTERM"), ("NSE:HFCL", "SWING"), ("NSE:JSFB", "LONGTERM"),
    ("NSE:APOLLOHOSP", "LONGTERM"), ("NSE:ARVIND", "LONGTERM"),
    ("NSE:PARKHOSPS", "LONGTERM"), ("NSE:NIVABUPA", "LONGTERM"),
    ("NSE:MINDACORP", "SWING"),
])
def test_symbol_with_no_portfolio_row_is_never_admitted(symbol, book):
    chk = gate.is_portfolio_admitted(symbol, book)
    assert not chk.admitted
    assert chk.reason == gate.REASON_NO_ROW
    assert gate.can_monitor_entry(symbol, book, source="test") is False


def test_a_genuinely_admitted_position_passes():
    _armed("NSE:NELCO", "LONGTERM", "PENDING")
    chk = gate.is_portfolio_admitted("NSE:NELCO", "LONGTERM")
    assert chk.admitted and chk.status == "PENDING" and chk.position_id
    assert gate.can_monitor_entry("NELCO", "LONGTERM", source="test") is True


def test_symbol_normalisation_both_spellings():
    _armed("NSE:TITAN", "SWING", "ACTIVE")
    assert gate.can_monitor_entry("TITAN", "SWING", source="t")
    assert gate.can_monitor_entry("NSE:TITAN", "SWING", source="t")


def test_admission_is_per_book_not_per_symbol():
    """A LONGTERM holding does not license a SWING alert."""
    _armed("NSE:CROSS", "LONGTERM", "ACTIVE")
    assert gate.can_monitor_entry("NSE:CROSS", "LONGTERM", source="t") is True
    assert gate.can_monitor_entry("NSE:CROSS", "SWING", source="t") is False


def test_a_closed_position_is_no_longer_monitorable():
    pid = _armed("NSE:DONE", "SWING", "ACTIVE")
    close_position(pid, 120.0, "TARGET_HIT")
    assert gate.can_monitor_entry("NSE:DONE", "SWING", source="t") is False


def test_expired_arm_is_not_monitorable():
    from dashboard.backend.db.portfolio import expire_pending_position
    pid = _armed("NSE:GONE", "SWING", "PENDING")
    expire_pending_position(pid, "EXPIRED_TIMEOUT")
    assert gate.can_monitor_entry("NSE:GONE", "SWING", source="t") is False


# ── Evidence that must NOT count as admission ────────────────────────────────

def test_signals_log_presence_is_not_admission():
    """The exact defect: a rejected scan candidate must never be executable."""
    c = get_connection()
    try:
        c.execute(
            "INSERT INTO signals_log (scan_id, horizon, symbol, date, cmp, entry, stop_loss, "
            "target, confidence, layer1_pass, layer2_pass, layer3_pass, final_selected) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("VAL-SWING-2026-08-17-617a6b10", "SWING", "NSE:HFCL", "2026-08-17",
             219.5, 219.21, 208.25, 252.09, 61.49, 1, 1, 1, 0),
        )
        c.commit()
    finally:
        c.close()
    assert gate.can_monitor_entry("NSE:HFCL", "SWING", source="test") is False, \
        "a signals_log row — selected or not — is not Portfolio admission"


def test_invalid_input_is_refused():
    assert not gate.is_portfolio_admitted("", "SWING").admitted
    assert not gate.is_portfolio_admitted("NSE:X", "NOT_A_BOOK").admitted


def test_lookup_failure_fails_closed(monkeypatch):
    """A DB error must never be read as permission."""
    import dashboard.backend.db.schema as sch
    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(sch, "get_connection", _boom)
    chk = gate.is_portfolio_admitted("NSE:ANY", "SWING")
    assert not chk.admitted and chk.reason == gate.REASON_LOOKUP_FAILED


# ── Escape hatch ─────────────────────────────────────────────────────────────

def test_enforcement_can_be_downgraded_to_log_only(monkeypatch):
    monkeypatch.setenv("ENTRY_GATE_ENFORCE", "0")
    assert gate.enforcing() is False
    assert gate.can_monitor_entry("NSE:NOPE", "SWING", source="t") is True
    assert gate.is_portfolio_admitted("NSE:NOPE", "SWING").admitted is False, \
        "the verdict stays honest even when enforcement is off"


def test_enforcement_is_on_by_default():
    assert gate.enforcing() is True


# ── The alert sender delegates to the same gate ──────────────────────────────

def test_alert_sender_blocks_unadmitted_symbol(monkeypatch):
    import services.portfolio_manager as pm
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    sent: list = []
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: sent.append(k) or type("R", (), {"status_code": 200})())

    pm.send_portfolio_triggered_alert("NSE:TARSONS", "LONGTERM", 339.12, 336.45, 311.99, 420.51)
    assert sent == [], "phantom alert must be blocked by the shared gate"

    _armed("NSE:REALONE", "LONGTERM", "ACTIVE")
    pm.send_portfolio_triggered_alert("NSE:REALONE", "LONGTERM", 100.0, 100.5, 90.0, 120.0)
    assert len(sent) == 1, "a genuinely admitted position must still alert"
