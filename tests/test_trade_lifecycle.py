"""Canonical lifecycle ledger — the guarantees Track Record depends on.

Track Record used to read `stock_recommendations`, which records IDEAS. On a
public page that meant names never taken into any book (TIL, STALLION, PNGJL,
SENCO…) were displayed as successful "Target Hit" trades, while real portfolio
trades — SCANSTL closed at +51.18% — were absent, and the page's win rate could
never agree with the books'.

These tests fail if any of that can happen again.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="lifecycle_test_")

import pytest  # noqa: E402

from dashboard.backend.db.schema import get_connection  # noqa: E402
from dashboard.backend.db.portfolio import (  # noqa: E402
    init_portfolio_db, add_position, close_position,
)
from dashboard.backend.db.trade_lifecycle import (  # noqa: E402
    init_lifecycle_db, make_uuid, upsert,
)
from dashboard.backend.db.trade_lifecycle_migrate import backfill  # noqa: E402
from dashboard.backend.db.trade_lifecycle_query import query, stats, timeline, facets  # noqa: E402


def _wipe():
    c = get_connection()
    try:
        for t in ("trade_lifecycle", "trade_lifecycle_events",
                  "portfolio_journal", "portfolio_positions", "stock_recommendations"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        c.commit()
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _fresh():
    # The research table lives in the core schema, which these tests need too.
    try:
        from dashboard.backend.db.schema import init_db
        init_db()
    except Exception:
        pass
    init_portfolio_db()
    init_lifecycle_db()
    _wipe()
    yield
    _wipe()


def _closed_trade(symbol, entry, exit_px, reason="TARGET_HIT", horizon="SWING"):
    pid = add_position({"symbol": symbol, "horizon": horizon, "entry_price": entry,
                        "stop_loss": entry * 0.9, "target_1": entry * 1.2, "status": "ACTIVE"})
    close_position(pid, exit_px, reason)
    return pid


def _research_idea(symbol, status="ACTIVE", agent="LONGTERM"):
    c = get_connection()
    try:
        c.execute(
            "INSERT INTO stock_recommendations (symbol, agent_type, status, entry_price, "
            "stop_loss, targets, confidence_score, created_at) "
            "VALUES (?,?,?,?,?,?,?,datetime('now'))",
            (symbol, agent, status, 100.0, 90.0, "[130.0]", 70.0),
        )
        c.commit()
    finally:
        c.close()


# ── A real portfolio trade must appear ───────────────────────────────────────

def test_portfolio_trade_appears_in_the_ledger():
    """The SCANSTL case: a real book trade must never be missing."""
    _closed_trade("NSE:SCANSTL", 38.16, 57.69, "TARGET_HIT")
    backfill()

    res = query(limit=50, symbol="SCANSTL")
    assert res["total"] == 1, "a closed portfolio trade must be in the ledger"
    row = res["items"][0]
    assert row["status"] == "TARGET_HIT"
    assert row["portfolio"] == "SWING"
    assert row["executed"] == 1
    assert row["pnl_pct"] == pytest.approx(51.18, abs=0.1)


def test_every_book_trade_is_represented():
    for sym, hz in (("NSE:A", "SWING"), ("NSE:B", "SWING"), ("NSE:C", "LONGTERM")):
        _closed_trade(sym, 100.0, 110.0, "TARGET_HIT", horizon=hz)
    backfill()
    assert stats(portfolio="SWING")["closed_trades"] == 2
    assert stats(portfolio="LONGTERM")["closed_trades"] == 1


# ── An idea that never traded must never look like a win ─────────────────────

def test_research_idea_never_taken_is_never_executed():
    """TIL / STALLION / PNGJL / SENCO were shown as Target Hit. Never again."""
    for s in ("NSE:TIL", "NSE:PNGJL", "NSE:SENCO"):
        _research_idea(s, status="TARGET_HIT")
    backfill()

    for s in ("TIL", "PNGJL", "SENCO"):
        rows = query(limit=10, symbol=s)["items"]
        assert rows, f"{s} should exist as an idea"
        assert all(r["status"] == "NEVER_EXECUTED" for r in rows), \
            f"{s} was never traded — it must not be reported as an outcome"
        assert all(r["executed"] == 0 for r in rows)
        assert all(r["pnl_pct"] is None for r in rows), \
            "an unexecuted idea must carry no P&L"


def test_no_research_row_can_be_reported_as_a_completed_trade():
    for s in ("NSE:X1", "NSE:X2"):
        _research_idea(s, status="TARGET_HIT")
    backfill()
    assert query(limit=100, portfolio="RESEARCH", status="TARGET_HIT")["total"] == 0
    assert query(limit=100, portfolio="RESEARCH", status="STOP_HIT")["total"] == 0


def test_unexecuted_ideas_do_not_move_win_rate():
    _closed_trade("NSE:WIN", 100.0, 120.0, "TARGET_HIT")
    for i in range(20):
        _research_idea(f"NSE:IDEA{i}", status="TARGET_HIT")
    backfill()

    s = stats()
    assert s["closed_trades"] == 1, "only the real trade is a closed trade"
    assert s["win_rate_pct"] == 100.0, "20 unexecuted ideas must not dilute it"
    assert s["signals_generated"] == 21, "but they are still counted as signals"
    assert s["never_executed"] == 20


# ── Stats basis ──────────────────────────────────────────────────────────────

def test_stats_cover_the_whole_filtered_set_not_a_page():
    for i in range(12):
        _closed_trade(f"NSE:S{i}", 100.0, 110.0 if i % 2 == 0 else 95.0,
                      "TARGET_HIT" if i % 2 == 0 else "STOP_HIT")
    backfill()

    page = query(limit=3, offset=0)
    assert len(page["items"]) == 3 and page["total"] == 12
    s = stats()
    assert s["closed_trades"] == 12, "cards describe every match, not the page"
    assert s["win_rate_pct"] == 50.0


def test_status_filter_narrows_rows_without_changing_the_denominator():
    """Selecting 'Target Hit' must not make the win rate 100%."""
    for i in range(10):
        _closed_trade(f"NSE:T{i}", 100.0, 110.0 if i < 4 else 95.0,
                      "TARGET_HIT" if i < 4 else "STOP_HIT")
    backfill()

    assert query(limit=50, status="TARGET_HIT")["total"] == 4
    assert stats()["win_rate_pct"] == 40.0, \
        "the unfiltered headline stays 40% however the table is filtered"


# ── Integrity ────────────────────────────────────────────────────────────────

def test_backfill_is_idempotent():
    _closed_trade("NSE:IDEM", 100.0, 120.0, "TARGET_HIT")
    _research_idea("NSE:IDEA1")
    a = backfill()
    b = backfill()
    assert a["ledger_rows"] == b["ledger_rows"], "re-running must not duplicate rows"
    assert query(limit=50, symbol="IDEM")["total"] == 1


def test_deterministic_uuid():
    assert make_uuid("SWING", "portfolio_journal", "42") == \
           make_uuid("SWING", "portfolio_journal", "42")
    assert make_uuid("SWING", "portfolio_journal", "42") != \
           make_uuid("SWING", "portfolio_journal", "43")


def test_history_is_append_only():
    """A status change must add an event, never overwrite the previous one."""
    lid = upsert({"source": "MANUAL", "symbol": "NSE:HIST", "status": "AWAITING_ENTRY",
                  "source_table": "manual", "source_id": "1"})
    upsert({"source": "MANUAL", "symbol": "NSE:HIST", "status": "ACTIVE",
            "source_table": "manual", "source_id": "1"})
    upsert({"source": "MANUAL", "symbol": "NSE:HIST", "status": "TARGET_HIT",
            "source_table": "manual", "source_id": "1", "pnl_pct": 12.0})

    t = timeline(lid)
    assert t["found"] and t["trade"]["status"] == "TARGET_HIT"
    seq = [e["to_status"] for e in t["events"]]
    assert seq == ["AWAITING_ENTRY", "ACTIVE", "TARGET_HIT"], \
        "the full transition history must survive"


def test_duplicate_journal_rows_stay_out_of_stats():
    """The ledger inherits the books' duplicate exclusion."""
    _closed_trade("NSE:DUP", 100.0, 110.0, "TARGET_HIT")
    _closed_trade("NSE:DUP", 100.0, 111.0, "TARGET_HIT")  # phantom re-fill
    backfill()
    assert stats(portfolio="SWING")["closed_trades"] == 1


def test_filters_and_facets():
    _closed_trade("NSE:F1", 100.0, 120.0, "TARGET_HIT")
    _closed_trade("NSE:F2", 100.0, 90.0, "STOP_HIT")
    _research_idea("NSE:F3")
    backfill()

    assert query(limit=50, execution="EXECUTED")["total"] == 2
    assert query(limit=50, outcome="WINNER")["total"] == 1
    assert query(limit=50, outcome="LOSER")["total"] == 1
    f = facets()
    assert "SWING" in f["portfolios"]
    assert "TARGET_HIT" in f["statuses"]
