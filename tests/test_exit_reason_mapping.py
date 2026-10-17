"""Exit-reason → lifecycle status mapping.

`_exit_reason_to_status` is a whitelist ending in `return "MANUAL_CLOSED"`, and
that catch-all silently swallowed the momentum book's entire exit vocabulary:
72 `TRAIL_STOP` and 13 `REPLACED` of its 91 recorded exits were reported as
human closes. The book looked as though no exit rule had ever fired, when in
fact rules closed 93% of its positions.

These tests pin the vocabulary and the safeguard that makes the next unmapped
reason visible instead of silent.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="exit_reason_"))

import pytest  # noqa: E402

from dashboard.backend.db.trade_lifecycle import STATUSES  # noqa: E402
from dashboard.backend.db.trade_lifecycle_migrate import (  # noqa: E402
    _exit_reason_to_status,
)

# ── the momentum vocabulary that was being lost ──────────────────────────────

@pytest.mark.parametrize("reason", ["TRAIL_STOP", "trail_stop", " TRAILING_STOP ",
                                    "REPLACED", "ROTATED"])
def test_rule_driven_momentum_exits_are_not_manual_closes(reason):
    assert _exit_reason_to_status(reason) == "FORCED_EXIT"


def test_a_trailing_stop_is_not_counted_as_a_stop_loss():
    """It usually exits in profit; counting it as STOP_HIT distorts stop stats."""
    assert _exit_reason_to_status("TRAIL_STOP") != "STOP_HIT"


# ── the existing vocabulary must be untouched ────────────────────────────────

@pytest.mark.parametrize("reason,expected", [
    ("STOP_HIT", "STOP_HIT"),
    ("TARGET_HIT", "TARGET_HIT"),
    ("STALE_EXIT", "TIME_EXIT"),
    # NB: EXPIRED_TIMEOUT maps to EXPIRED, not TIME_EXIT -- `startswith("EXPIRED")`
    # is tested before the TIME_EXIT tuple, so the "EXPIRED_TIMEOUT" entry in that
    # tuple is unreachable. Production agrees: all 38 such rows are EXPIRED.
    # Pinned so reordering the checks cannot silently restatus them.
    ("EXPIRED_TIMEOUT", "EXPIRED"),
    ("TIME_EXIT", "TIME_EXIT"),
    ("EXPIRED_RUNAWAY", "EXPIRED"),
    ("EXPIRED_STALE_FILL", "EXPIRED"),
    ("CANCELLED", "CANCELLED"),
    ("CANCELED", "CANCELLED"),
    ("TREND_BREAK", "FORCED_EXIT"),
    ("STRUCTURE_BREAK", "FORCED_EXIT"),
    ("MANUAL: 8-week swing horizon cap (held 90d)", "FORCED_EXIT"),
])
def test_known_reasons_keep_their_existing_mapping(reason, expected):
    assert _exit_reason_to_status(reason) == expected


def test_a_genuine_human_close_is_still_manual():
    assert _exit_reason_to_status("closed by hand") == "MANUAL_CLOSED"
    assert _exit_reason_to_status(None) == "MANUAL_CLOSED"
    assert _exit_reason_to_status("") == "MANUAL_CLOSED"


# ── the safeguard ────────────────────────────────────────────────────────────

def test_an_unmapped_reason_is_logged_not_swallowed(caplog):
    """The catch-all is how 85 exits went unnoticed. It must now announce itself."""
    with caplog.at_level("WARNING"):
        assert _exit_reason_to_status("SOME_NEW_RULE_EXIT") == "MANUAL_CLOSED"
    assert "unmapped exit_reason" in caplog.text
    assert "SOME_NEW_RULE_EXIT" in caplog.text


def test_absent_reason_does_not_log(caplog):
    """A missing reason is normal for an open position — not worth a warning."""
    with caplog.at_level("WARNING"):
        _exit_reason_to_status(None)
        _exit_reason_to_status("")
    assert not [r for r in caplog.records if "unmapped exit_reason" in str(r.msg)]


# ── every mapping must return a declared status ──────────────────────────────

@pytest.mark.parametrize("reason", [
    "STOP_HIT", "TARGET_HIT", "STALE_EXIT", "EXPIRED_TIMEOUT", "TIME_EXIT",
    "EXPIRED_RUNAWAY", "CANCELLED", "TREND_BREAK", "STRUCTURE_BREAK",
    "TRAIL_STOP", "REPLACED", "ROTATED", "MANUAL: x", "anything else", None,
])
def test_mapping_only_emits_declared_statuses(reason):
    assert _exit_reason_to_status(reason) in STATUSES
