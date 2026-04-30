"""tests/test_signal_delivery.py — Pure helpers for signal delivery."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.signal_delivery import format_no_setup_report_message


def test_format_no_setup_report_message():
    msg = format_no_setup_report_message(
        scanned=12,
        data_ok=11,
        reason="no_setup_condition_met",
    )
    assert "Scan Complete" in msg
    assert "Scanned: 12 symbols" in msg
    assert "Data OK: 11" in msg
    assert "no_setup_condition_met" in msg


def test_format_no_setup_report_empty_reason_defaults():
    msg = format_no_setup_report_message(scanned=0, data_ok=0, reason="")
    assert "Reason: —" in msg
