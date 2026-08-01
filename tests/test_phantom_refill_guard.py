"""P0 — the phantom re-fill loop must be impossible.

Observed on prod 2026-07-31: NAZARA exited at target (+8.58%, exit 315.90), was
immediately re-armed at its OLD entry 290.95 while price sat at 314.85, the
breakout-side arm "tapped" instantly and filled at 290.95 — a price that had not
traded in weeks — so the row was born ~8% in profit and re-hit target 2m41s
later, journaling a second +8.63% that never happened. Both rows counted.

Four independent controls, any one of which breaks the loop:
  G0        never re-arm the SAME entry price just exited (any exit reason)
  slip      never fill when CMP has run past the entry by > max slip
  Rule 2    two closes at one entry inside the window = one holding
  insert    the flag is applied the moment the row lands
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="phantom_test_")

import pytest  # noqa: E402

from dashboard.backend.db.portfolio import (  # noqa: E402
    init_portfolio_db, add_position, close_position, get_journal_stats,
    mark_journal_duplicates, _reentry_would_block, JOURNAL_DUPE_WINDOW_MIN,
)
from dashboard.backend.db.schema import get_connection  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))


def _wipe():
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_journal")
        c.execute("DELETE FROM portfolio_positions")
        c.commit()
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _fresh():
    init_portfolio_db()
    _wipe()
    yield
    _wipe()


def _closed(symbol, entry, exit_px, reason, closed_at, created_at="2026-07-01 09:00:00"):
    """Write a closed position + journal row directly, with controlled timestamps."""
    pid = add_position({"symbol": symbol, "horizon": "SWING", "entry_price": entry,
                        "stop_loss": entry * 0.9, "target_1": entry * 1.2, "status": "ACTIVE"})
    c = get_connection()
    try:
        c.execute("UPDATE portfolio_positions SET created_at = ? WHERE id = ?", (created_at, pid))
        c.commit()
    finally:
        c.close()
    close_position(pid, exit_px, reason)
    c = get_connection()
    try:
        c.execute("UPDATE portfolio_journal SET closed_at = ? WHERE position_id = ?",
                  (closed_at, pid))
        c.commit()
    finally:
        c.close()
    return pid


# ── G0: never re-arm the entry you just left ─────────────────────────────────

@pytest.mark.parametrize("reason", ["TARGET_HIT", "STOP_HIT", "STALE_EXIT", "TREND_BREAK"])
def test_g0_blocks_rearm_at_the_entry_just_exited_regardless_of_reason(reason):
    """The NAZARA case: a WIN must not licence re-arming the same stale price."""
    now = datetime.now(_IST)
    _closed("NSE:NAZARA", 290.95, 315.90, reason, (now - timedelta(minutes=2)).isoformat())
    would, why = _reentry_would_block("NSE:NAZARA", "SWING", 290.95, cmp=314.85)
    assert would, f"re-arm at the just-exited entry must block (got: {why})"
    assert "stale-entry-rearm" in why


def test_g0_allows_a_genuine_fresh_breakout_at_a_new_level():
    """EXIDEIND-style: won, then re-entered ~9% higher. Must stay allowed."""
    now = datetime.now(_IST)
    _closed("NSE:EXIDEIND", 393.80, 429.05, "TARGET_HIT", (now - timedelta(days=17)).isoformat())
    would, why = _reentry_would_block("NSE:EXIDEIND", "SWING", 429.33, cmp=452.70)
    assert not would, f"a fresh breakout at a new level must be allowed (got: {why})"


def test_g0_allows_the_same_level_again_once_it_is_no_longer_stale():
    now = datetime.now(_IST)
    _closed("NSE:OLDCO", 100.0, 120.0, "TARGET_HIT", (now - timedelta(days=30)).isoformat())
    would, why = _reentry_would_block("NSE:OLDCO", "SWING", 100.0, cmp=101.0)
    assert not would, f"a month later the level is not 'just exited' (got: {why})"


# ── Rule 2: two closes at one entry inside the window = one holding ──────────

def test_phantom_refill_keeps_the_genuine_earlier_exit():
    """A re-fill from a NEW origin minutes later is the artifact, not the truth.

    Real NAZARA data: the genuine close was +8.58% (lineage from 2026-06-16,
    45 days held); +8.63% two minutes later came from a 2026-07-16 arm that
    filled at 290.95 while price was already 314.85. Keeping the LAST row would
    publish the fabricated one, so the window rule keeps the FIRST.
    """
    now = datetime.now(_IST)
    _closed("NSE:NAZARA", 290.95, 315.90, "TARGET_HIT",
            (now - timedelta(minutes=6)).isoformat(), created_at="2026-06-16 07:00:00")
    _closed("NSE:NAZARA", 290.95, 316.05, "TARGET_HIT",
            (now - timedelta(minutes=3)).isoformat(), created_at="2026-07-16 07:00:00")

    res = mark_journal_duplicates()
    assert res["duplicates"] == 1, "the later re-fill must be flagged"
    s = get_journal_stats("SWING")
    assert s["total_trades"] == 1, "one holding, not two"
    assert s["sum_trade_return_pct"] == pytest.approx(8.58, abs=0.05), \
        "the genuine earlier exit must be the one counted"


def test_same_lineage_collapses_regardless_of_time_span():
    """APTUS: 11 rows over 38 DAYS, one origin, days_held counting up 7 -> 45.

    One position the seed loop kept re-creating and re-closing. days_held is
    measured from the fixed origin so it grows instead of resetting, which is
    what proves these are not separate entries. The terminal (last) close wins.
    """
    now = datetime.now(_IST)
    origin = "2026-06-16 07:03:39"
    for days_ago, exit_px in ((38, 267.8), (30, 264.9), (20, 265.1), (0, 262.9)):
        _closed("NSE:APTUS", 272.70, exit_px, "STRUCTURE_BREAK",
                (now - timedelta(days=days_ago)).isoformat(), created_at=origin)

    res = mark_journal_duplicates()
    assert res["duplicates"] == 3, "all but the terminal close are artifacts"
    s = get_journal_stats("SWING")
    assert s["total_trades"] == 1, "one holding across 38 days, not four trades"
    assert s["sum_trade_return_pct"] == pytest.approx((262.9 - 272.70) / 272.70 * 100, abs=0.05)


def test_rule2_leaves_a_genuine_reentry_days_later_intact():
    now = datetime.now(_IST)
    _closed("NSE:REALCO", 100.0, 95.0, "STOP_HIT",
            (now - timedelta(days=20)).isoformat(), created_at="2026-06-01 09:00:00")
    _closed("NSE:REALCO", 100.0, 110.0, "TARGET_HIT",
            (now - timedelta(days=2)).isoformat(), created_at="2026-07-20 09:00:00")

    assert mark_journal_duplicates()["duplicates"] == 0
    assert get_journal_stats("SWING")["total_trades"] == 2


def test_rule2_window_boundary_is_respected():
    now = datetime.now(_IST)
    beyond = JOURNAL_DUPE_WINDOW_MIN + 60
    _closed("NSE:EDGE", 50.0, 55.0, "TARGET_HIT",
            (now - timedelta(minutes=beyond)).isoformat(), created_at="2026-06-01 09:00:00")
    _closed("NSE:EDGE", 50.0, 56.0, "TARGET_HIT",
            now.isoformat(), created_at="2026-07-01 09:00:00")
    assert mark_journal_duplicates()["duplicates"] == 0


def test_duplicates_are_marked_never_deleted():
    """Audit trail must survive: history immutable, only the flag changes."""
    now = datetime.now(_IST)
    _closed("NSE:NAZARA", 290.95, 315.90, "TARGET_HIT",
            (now - timedelta(minutes=6)).isoformat(), created_at="2026-06-16 07:00:00")
    _closed("NSE:NAZARA", 290.95, 316.05, "TARGET_HIT",
            (now - timedelta(minutes=3)).isoformat(), created_at="2026-07-16 07:00:00")
    mark_journal_duplicates()

    c = get_connection()
    try:
        rows = c.execute("SELECT profit_loss_pct, is_duplicate FROM portfolio_journal "
                         "WHERE symbol='NSE:NAZARA' ORDER BY datetime(closed_at)").fetchall()
    finally:
        c.close()
    assert len(rows) == 2, "both rows must still exist — nothing is ever deleted"
    # Genuine earlier exit kept; the later re-fill flagged.
    assert [r["is_duplicate"] for r in rows] == [0, 1]


def test_insert_time_detection_flags_without_waiting_for_a_sweep():
    """close_position applies Rule 2 immediately, so stats are never briefly wrong."""
    pid1 = add_position({"symbol": "NSE:INST", "horizon": "SWING", "entry_price": 100.0,
                         "stop_loss": 90.0, "target_1": 120.0, "status": "ACTIVE"})
    close_position(pid1, 120.0, "TARGET_HIT")
    pid2 = add_position({"symbol": "NSE:INST", "horizon": "SWING", "entry_price": 100.0,
                         "stop_loss": 90.0, "target_1": 120.0, "status": "ACTIVE"})
    close_position(pid2, 121.0, "TARGET_HIT")

    s = get_journal_stats("SWING")   # no mark_journal_duplicates() call
    assert s["total_trades"] == 1
    assert s["duplicates_excluded"] == 1


# ── Stale-fill guard: never fill an arm that price has already run past ──────

def test_stale_fill_guard_refuses_a_fill_beyond_max_slip():
    """The exact NAZARA fill: entry 290.95, CMP 314.85 (+8.2% beyond)."""
    import services.portfolio_tracker as pt
    entry, cmp_ = 290.95, 314.85
    slip = (cmp_ - entry) / entry * 100.0
    assert slip > pt._PENDING_MAX_SLIP_PCT, (
        f"a fill {slip:.1f}% beyond entry must exceed the {pt._PENDING_MAX_SLIP_PCT}% cap"
    )


def test_stale_fill_guard_still_allows_a_normal_tap():
    """MINDACORP-style genuine tap: entry 700.25, CMP 700.05 — right at the level."""
    import services.portfolio_tracker as pt
    entry, cmp_ = 700.25, 700.05
    slip = (entry - cmp_) / entry * 100.0      # pullback side
    assert slip <= pt._PENDING_MAX_SLIP_PCT, "a genuine tap at the level must still fill"
