"""Journal statistics integrity — locks the two invariants that were violated.

The Swing Portfolio header once read "45.7% win rate · 70 completed · Total
return: -41.37%". Both halves were wrong, for two independent reasons:

  1. SPLIT POPULATION. The win rate was computed over a setup-collapsed
     population (46 rows) while the return summed ALL 70 rows including 24
     re-seed duplicates. The two figures in one sentence described different
     trade sets — on the clean population the same trades summed to +24.97%.

  2. SUM-OF-PERCENTAGES AS A RETURN. `SUM(profit_loss_pct)` adds percentages
     taken on different capital bases. On a 20-slot equal-weight book the real
     move was ~-2.07%, not -41.37% — a ~20x overstatement.

The duplicates themselves came from a re-seed loop: the engine held a trade the
portfolio had already exited, so every tracker cycle re-created and re-closed it
(CIPLA journaled 11 times inside 51 minutes).

These tests fail if any of that is reintroduced.
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="pf_stats_test_")
os.environ["DATA_DIR"] = _TMP

import pytest  # noqa: E402

from dashboard.backend.db.portfolio import (  # noqa: E402
    init_portfolio_db, add_position, close_position, get_journal,
    get_journal_stats, mark_journal_duplicates, MAX_SWING_POSITIONS,
)
from dashboard.backend.db.schema import get_connection  # noqa: E402


def _wipe():
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_journal")
        c.execute("DELETE FROM portfolio_positions")
        c.commit()
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _fresh_db():
    init_portfolio_db()
    _wipe()
    yield
    _wipe()


def _trade(symbol: str, entry: float, exit_price: float, reason: str = "CLOSED",
           origin: str | None = None) -> int:
    """Open and close one position. `origin` forces created_at (the re-seed key)."""
    pid = add_position({
        "symbol": symbol, "horizon": "SWING", "entry_price": entry,
        "stop_loss": entry * 0.9, "target_1": entry * 1.2, "status": "ACTIVE",
    })
    if origin is not None:
        c = get_connection()
        try:
            c.execute("UPDATE portfolio_positions SET created_at = ? WHERE id = ?", (origin, pid))
            c.commit()
        finally:
            c.close()
    close_position(pid, exit_price, reason)
    return pid


# ── Invariant 1: one population for every published figure ────────────────────

def test_duplicates_are_flagged_and_excluded_from_every_stat():
    """A setup journaled N times from ONE origin counts once, everywhere."""
    origin = "2026-06-24 03:01:09"
    # Same symbol, same entry, same origin, closed repeatedly — the CIPLA loop.
    for exit_px in (96.0, 96.5, 96.2, 95.8):
        _trade("NSE:LOOPCO", 100.0, exit_px, "TREND_BREAK", origin=origin)
    # One genuine, independent trade.
    _trade("NSE:REALCO", 200.0, 220.0, "TARGET_HIT")

    s = get_journal_stats("SWING")
    assert s["total_trades"] == 2, "duplicates must not inflate the trade count"
    assert s["duplicates_excluded"] == 3
    # Terminal outcome (last close, -4.2%) is the one kept, not the first.
    assert s["sum_trade_return_pct"] == pytest.approx(-4.2 + 10.0, abs=0.01)
    assert s["wins"] == 1
    assert s["hit_rate_pct"] == 50.0


def test_win_rate_and_return_share_one_population():
    """The headline pair must never again be computed over different row sets."""
    origin = "2026-06-16 07:03:39"
    for exit_px in (97.0, 96.0):
        _trade("NSE:DUPECO", 100.0, exit_px, "STRUCTURE_BREAK", origin=origin)
    _trade("NSE:WINCO", 50.0, 60.0, "TARGET_HIT")

    s = get_journal_stats("SWING")
    # The deprecated unique_* aliases must mirror the single basis — a divergent
    # second population here is exactly what produced the misleading header.
    assert s["unique_trades"] == s["total_trades"]
    assert s["unique_hit_rate_pct"] == s["hit_rate_pct"]
    assert s["repeat_reentries_collapsed"] == 0
    # Win rate denominator and return numerator cover the same rows.
    assert s["wins"] + s["losses"] == s["total_trades"]


def test_visible_history_matches_the_stats_population():
    """The closed-trade list must not show rows the stats exclude (or vice versa)."""
    origin = "2026-06-24 03:01:09"
    for exit_px in (96.0, 95.0):
        _trade("NSE:LOOPCO", 100.0, exit_px, "TREND_BREAK", origin=origin)

    s = get_journal_stats("SWING")
    assert len(get_journal("SWING", limit=100)) == s["total_trades"]
    # The raw audit view still exposes everything — history stays immutable.
    assert len(get_journal("SWING", limit=100, include_duplicates=True)) == 2


def test_genuine_reentry_at_same_price_is_not_collapsed():
    """Different origin = a real second trade. Dedupe must not eat it."""
    _trade("NSE:SAMEPX", 100.0, 95.0, "STOP_HIT", origin="2026-06-01 09:00:00")
    _trade("NSE:SAMEPX", 100.0, 110.0, "TARGET_HIT", origin="2026-07-01 09:00:00")

    s = get_journal_stats("SWING")
    assert s["total_trades"] == 2, "same symbol+price from different origins are two trades"
    assert s["duplicates_excluded"] == 0
    assert s["wins"] == 1


# ── Invariant 2: a sum of percentages is never presented as a return ──────────

def test_book_return_is_slot_weighted_not_a_raw_sum():
    _trade("NSE:AAA", 100.0, 110.0, "TARGET_HIT")   # +10%
    _trade("NSE:BBB", 100.0, 80.0, "STOP_HIT")      # -20%

    s = get_journal_stats("SWING")
    assert s["sum_trade_return_pct"] == pytest.approx(-10.0, abs=0.01)
    assert s["book_slots"] == MAX_SWING_POSITIONS
    # Each trade is 1/slots of capital — the book moved a fraction of the sum.
    assert s["book_return_pct"] == pytest.approx(-10.0 / MAX_SWING_POSITIONS, abs=0.01)
    assert abs(s["book_return_pct"]) < abs(s["sum_trade_return_pct"])
    assert s["book_return_basis"]


def test_total_pnl_pct_alias_is_the_sum_not_the_book_return():
    """Legacy key must stay the sum, so nothing silently reinterprets it."""
    _trade("NSE:AAA", 100.0, 110.0, "TARGET_HIT")
    s = get_journal_stats("SWING")
    assert s["total_pnl_pct"] == s["sum_trade_return_pct"]
    assert s["total_pnl_pct"] != s["book_return_pct"]


# ── Root cause: the re-seed loop can no longer create duplicates ──────────────

def test_close_position_demotes_an_already_journaled_origin():
    """Any future write path that re-journals one origin self-flags on insert."""
    origin = "2026-06-24 03:01:09"
    _trade("NSE:LOOPCO", 100.0, 96.0, "TREND_BREAK", origin=origin)
    _trade("NSE:LOOPCO", 100.0, 94.0, "STOP_HIT", origin=origin)

    c = get_connection()
    try:
        rows = c.execute(
            "SELECT exit_reason, is_duplicate FROM portfolio_journal "
            "WHERE symbol = 'NSE:LOOPCO' ORDER BY datetime(closed_at) ASC"
        ).fetchall()
    finally:
        c.close()
    assert [r["is_duplicate"] for r in rows] == [1, 0], "newest close stays canonical"
    assert rows[-1]["exit_reason"] == "STOP_HIT"


def test_mark_journal_duplicates_is_idempotent_and_reversible():
    origin = "2026-06-24 03:01:09"
    for exit_px in (96.0, 95.0, 94.0):
        _trade("NSE:LOOPCO", 100.0, exit_px, "TREND_BREAK", origin=origin)

    first = mark_journal_duplicates()
    second = mark_journal_duplicates()
    assert first["duplicates"] == second["duplicates"] == 2
    assert first["clean_rows"] == second["clean_rows"] == 1
    # dry_run reports without mutating.
    audit = mark_journal_duplicates(dry_run=True)
    assert audit["dry_run"] is True and audit["duplicates"] == 2
    assert get_journal_stats("SWING")["total_trades"] == 1


# ── One source of truth: every surface must agree ─────────────────────────────

def test_all_books_use_the_canonical_engine_contract():
    """Swing, Long-Term and Momentum must publish the same metric contract.

    If a book stops routing through db/perf_stats it will drop these keys, which
    is how the books silently grew different definitions of "win" before.
    """
    from dashboard.backend.db.momentum_portfolio import get_journal_stats as mom_stats
    from dashboard.backend.db.perf_stats import WIN_DEFINITION, RETURN_DEFINITION

    _trade("NSE:AAA", 100.0, 110.0, "TARGET_HIT")
    contract = ("population", "win_definition", "return_definition",
                "book_return_pct", "sum_trade_return_pct", "book_slots",
                "hit_rate_pct", "cut_early", "duplicates_excluded")
    for stats in (get_journal_stats("SWING"), get_journal_stats("LONGTERM"), mom_stats()):
        for key in contract:
            assert key in stats, f"{stats.get('book')} missing canonical key {key}"
        assert stats["win_definition"] == WIN_DEFINITION
        assert stats["return_definition"] == RETURN_DEFINITION


def test_published_stats_match_the_rows_the_ui_shows():
    """The consistency endpoint's core invariant: published == recomputed."""
    from dashboard.backend.db.perf_stats import compute_book_stats
    from dashboard.backend.db.portfolio import MAX_SWING_POSITIONS

    origin = "2026-06-24 03:01:09"
    for exit_px in (96.0, 95.0):          # a re-seed pair
        _trade("NSE:LOOPCO", 100.0, exit_px, "TREND_BREAK", origin=origin)
    _trade("NSE:AAA", 100.0, 112.0, "TARGET_HIT")
    _trade("NSE:BBB", 100.0, 88.0, "STOP_HIT")

    published = get_journal_stats("SWING")
    visible = get_journal("SWING", limit=10_000)
    recomputed = compute_book_stats(visible, slots=MAX_SWING_POSITIONS, book="SWING")
    for f in ("total_trades", "wins", "hit_rate_pct", "sum_trade_return_pct",
              "book_return_pct", "target_hits", "stop_hits", "cut_early"):
        assert published[f] == recomputed[f], f"{f} drifted: {published[f]} vs {recomputed[f]}"


def test_exit_buckets_sum_to_total():
    """target + stopped + cut early must always account for every trade."""
    _trade("NSE:AAA", 100.0, 112.0, "TARGET_HIT")
    _trade("NSE:BBB", 100.0, 88.0, "STOP_HIT")
    _trade("NSE:CCC", 100.0, 99.0, "STRUCTURE_BREAK")
    _trade("NSE:DDD", 100.0, 101.0, "STALE_EXIT")
    s = get_journal_stats("SWING")
    assert s["target_hits"] + s["stop_hits"] + s["cut_early"] == s["total_trades"] == 4


def test_track_record_declares_its_different_basis():
    """The research ledger differs by design — it must SAY so, not differ silently."""
    from dashboard.backend.db import track_record
    tr = track_record.stats()
    if not tr.get("available"):
        pytest.skip("track record ledger not initialised in this environment")
    assert tr["population"] != get_journal_stats("SWING")["population"]
    assert tr["win_definition"] and tr["differs_from_portfolio_books"]
