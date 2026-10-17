"""Stop/target event timing — derived only where the event is certain.

`time_to_stop_days`, `time_to_target_days` and `target_1_hit_at` were declared in
the schema and carried through every SELECT, but nothing ever wrote them: 0 of
165 closed SMC trades had them, including all 71 STOP_HITs. Without them MAE/MFE
is the only witness to an excursion, and MAE/MFE carries no ordering — so "was
the stop hit before the move developed?" was unanswerable.

The guard these tests exist to hold: timing is filled ONLY when the level
terminated the trade, and `holding_days` is never substituted for it.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="lifecycle_timing_"))

import pytest  # noqa: E402

from dashboard.backend.db.trade_lifecycle import (  # noqa: E402
    _days_between,
    derive_event_timing,
)

ENTRY = "2026-09-01T09:20:00+05:30"
EXIT = "2026-09-08T15:10:00+05:30"          # 7 days + 5h50m later


def _rec(**kw):
    base = {"status": "ACTIVE", "entry_fill_at": ENTRY, "exit_at": None,
            "target_1": 115.0, "entry_price": 100.0, "stop_loss": 95.0,
            "time_to_stop_days": None, "time_to_target_days": None,
            "target_1_hit_at": None, "holding_days": 7}
    base.update(kw)
    return base


# ── the two certain cases ────────────────────────────────────────────────────

def test_stop_hit_derives_time_to_stop_from_timestamps():
    r = derive_event_timing(_rec(status="STOP_HIT", exit_at=EXIT))
    assert r["time_to_stop_days"] == pytest.approx(7.243, abs=0.01)
    assert r["time_to_target_days"] is None       # it never reached target
    assert r["target_1_hit_at"] is None


def test_target_hit_derives_time_to_target_and_the_hit_timestamp():
    r = derive_event_timing(_rec(status="TARGET_HIT", exit_at=EXIT))
    assert r["time_to_target_days"] == pytest.approx(7.243, abs=0.01)
    assert r["target_1_hit_at"] == EXIT
    assert r["time_to_stop_days"] is None


def test_target_hit_without_a_published_target_has_nothing_to_timestamp():
    r = derive_event_timing(_rec(status="TARGET_HIT", exit_at=EXIT, target_1=None))
    assert r["time_to_target_days"] == pytest.approx(7.243, abs=0.01)
    assert r["target_1_hit_at"] is None


# ── the uncertain cases must stay NULL ───────────────────────────────────────

@pytest.mark.parametrize("status", ["TIME_EXIT", "MANUAL_CLOSED", "FORCED_EXIT",
                                    "STALE_EXIT", "TREND_BREAK", "EXPIRED", "ACTIVE"])
def test_other_statuses_never_get_event_timing(status):
    """A time-exit may have traded through its target intrabar — date unknown."""
    r = derive_event_timing(_rec(status=status, exit_at=EXIT))
    assert r["time_to_stop_days"] is None
    assert r["time_to_target_days"] is None
    assert r["target_1_hit_at"] is None


def test_holding_days_is_never_substituted():
    """The whole point. No timestamps means no value, even with holding_days set."""
    r = derive_event_timing(_rec(status="STOP_HIT", exit_at=None, holding_days=11))
    assert r["time_to_stop_days"] is None
    r2 = derive_event_timing(_rec(status="STOP_HIT", entry_fill_at=None, exit_at=EXIT,
                                  holding_days=11))
    assert r2["time_to_stop_days"] is None


def test_a_caller_supplied_value_is_not_overwritten():
    r = derive_event_timing(_rec(status="STOP_HIT", exit_at=EXIT, time_to_stop_days=2.5))
    assert r["time_to_stop_days"] == 2.5


def test_exit_before_entry_is_refused_rather_than_negated():
    r = derive_event_timing(_rec(status="STOP_HIT", entry_fill_at=EXIT, exit_at=ENTRY))
    assert r["time_to_stop_days"] is None


# ── the parser ───────────────────────────────────────────────────────────────

def test_days_between_tolerates_space_and_z_separators():
    assert _days_between("2026-09-01 00:00:00", "2026-09-03 00:00:00") == pytest.approx(2.0)
    assert _days_between("2026-09-01T00:00:00Z", "2026-09-02T12:00:00Z") == pytest.approx(1.5)


def test_days_between_refuses_mixed_naive_and_aware():
    """Subtracting those raises; returning None beats a 500 in the ledger."""
    assert _days_between("2026-09-01T00:00:00", EXIT) is None


@pytest.mark.parametrize("a,b", [(None, EXIT), (ENTRY, None), ("rubbish", EXIT)])
def test_days_between_returns_none_on_unusable_input(a, b):
    assert _days_between(a, b) is None


# ── it reaches the database through the real write path ──────────────────────

def test_upsert_persists_derived_timing():
    from dashboard.backend.db.schema import get_connection
    from dashboard.backend.db.trade_lifecycle import init_lifecycle_db, upsert

    init_lifecycle_db()
    lid = upsert({"source": "SWING", "portfolio": "SWING", "engine": "SMC",
                  "symbol": "NSE:TIMINGTEST", "status": "STOP_HIT", "stage": "POSITION",
                  "entry_price": 100.0, "stop_loss": 95.0, "target_1": 115.0,
                  "entry_fill_at": ENTRY, "exit_at": EXIT, "exit_price": 95.0,
                  "source_table": "test", "source_id": "timing-1"})
    c = get_connection()
    try:
        row = c.execute("SELECT time_to_stop_days, time_to_target_days, target_1_hit_at "
                        "FROM trade_lifecycle WHERE uuid = ?", (lid,)).fetchone()
    finally:
        c.close()
    assert row["time_to_stop_days"] == pytest.approx(7.243, abs=0.01)
    assert row["time_to_target_days"] is None
    assert row["target_1_hit_at"] is None
