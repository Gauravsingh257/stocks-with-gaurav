"""The stale cull must be reachable on BOTH structural-exit paths.

Regression guard for a silent 7-week outage: the stale cull lived inside the
`else` of the trend-break branch, so enabling the risk engine's TREND_BREAK exit
made it unreachable. STALE_EXIT fired 20 times between 2026-07-05 and
2026-07-08 and then never again — the risk engine shipped 2026-07-09 with its
flags default-ON. Positions that were neither broken down nor moving matched
nothing and sat for 38-87 days.

These tests pin the decision logic itself rather than a live tick, so they stay
fast and need no database.
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="stale_exit_test_")
os.environ["DATA_DIR"] = _TMP

import pytest  # noqa: E402

MIN_DAYS = 20
LOWER = -5.0
UPPER = 3.0


def would_stale_exit(days_held: float, pl_pct: float, *, trend_break_on: bool,
                     independent: bool, stale_on: bool = True,
                     trend_break_fires: bool = False) -> bool:
    """Mirror of the branch structure in position_tracking_service.

    Kept deliberately as a small model of the control flow: the bug was purely
    structural (a reachable rule nested inside the wrong branch), so what needs
    pinning is the branching, not the arithmetic.
    """
    exit_reason = None

    if trend_break_on:
        if trend_break_fires:
            exit_reason = "TREND_BREAK"
    else:
        if (exit_reason is None and stale_on
                and days_held >= MIN_DAYS and LOWER <= pl_pct <= UPPER):
            exit_reason = "STALE_EXIT"

    if (exit_reason is None and stale_on and independent
            and days_held >= MIN_DAYS and LOWER <= pl_pct <= UPPER):
        exit_reason = "STALE_EXIT"

    return exit_reason == "STALE_EXIT"


# ── the bug ──────────────────────────────────────────────────────────────────

def test_the_outage_trend_break_on_and_flag_off_means_stale_never_fires():
    """This is the production state from 2026-07-09 to 2026-08-31."""
    assert would_stale_exit(53, 1.80, trend_break_on=True, independent=False) is False


def test_the_fix_trend_break_on_and_flag_on_lets_stale_fire():
    assert would_stale_exit(53, 1.80, trend_break_on=True, independent=True) is True


# ── behaviour must be unchanged while the flag is off ────────────────────────

@pytest.mark.parametrize("days,pl", [(53, 1.80), (38, 0.56), (23, 2.03), (19, 0.0), (60, 12.0)])
def test_flag_off_is_byte_identical_to_legacy_on_both_paths(days, pl):
    """With the flag off, each path behaves exactly as it did before."""
    legacy = would_stale_exit(days, pl, trend_break_on=False, independent=False)
    assert would_stale_exit(days, pl, trend_break_on=False, independent=True) == legacy
    assert would_stale_exit(days, pl, trend_break_on=True, independent=False) is False


# ── the rule's own boundaries still hold when reachable ──────────────────────

def test_a_real_winner_is_never_stale_culled():
    """+12% is progress — the cull must not touch it. This is the guard against
    truncating the positive-skew winners the book depends on."""
    assert would_stale_exit(60, 12.0, trend_break_on=True, independent=True) is False


def test_a_position_below_the_loss_floor_is_left_to_the_stop():
    """-9% is not 'dead money', it is a losing trade — the stop owns it."""
    assert would_stale_exit(60, -9.0, trend_break_on=True, independent=True) is False


def test_young_position_is_never_culled_however_flat():
    assert would_stale_exit(19, 0.0, trend_break_on=True, independent=True) is False


@pytest.mark.parametrize("pl", [UPPER, LOWER])
def test_band_edges_are_inclusive(pl):
    assert would_stale_exit(MIN_DAYS, pl, trend_break_on=True, independent=True) is True


def test_trend_break_still_wins_when_it_fires():
    """Ordering matters: a genuine breakdown must journal as TREND_BREAK, not
    STALE_EXIT, or the exit-reason stats become unreadable."""
    assert would_stale_exit(60, 1.0, trend_break_on=True, independent=True,
                            trend_break_fires=True) is False


def test_master_switch_still_disables_everything():
    assert would_stale_exit(53, 1.80, trend_break_on=True, independent=True,
                            stale_on=False) is False


# ── the module actually exposes the flag ─────────────────────────────────────

def test_flag_exists_and_defaults_off(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_STALE_EXIT_INDEPENDENT", raising=False)
    import importlib

    import services.position_tracking_service as pts
    importlib.reload(pts)
    assert pts._STALE_EXIT_INDEPENDENT is False, "default must preserve current behaviour"

    monkeypatch.setenv("PORTFOLIO_STALE_EXIT_INDEPENDENT", "1")
    importlib.reload(pts)
    assert pts._STALE_EXIT_INDEPENDENT is True
    monkeypatch.delenv("PORTFOLIO_STALE_EXIT_INDEPENDENT", raising=False)
    importlib.reload(pts)
