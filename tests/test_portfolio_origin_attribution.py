"""Durable origin attribution for portfolio positions.

`recommendation_id` is polysemous — door 1 (`promote_to_portfolio`) stores a
signals_log id and its path applies Phase 2, while door 2
(`seed_from_recommendations`) stores a stock_recommendations id and its path
does not. Until `source_door` existed, the only record of which door admitted a
position was the admission gate's Redis log, which expires after 30 days, so
origin became unanswerable for anything older than that.

These tests pin the two properties that make the column trustworthy: it records
the SAME string the gate is given (so the row and the shadow log cannot drift),
and adding it changed no behaviour.
"""

from __future__ import annotations

import ast
import os
import tempfile

# Throwaway SQLite dir BEFORE importing the db modules (same convention as
# tests/test_admission_gate.py) so the real dashboard.db is never touched.
_TMP = tempfile.mkdtemp(prefix="pf_origin_test_")
os.environ["DATA_DIR"] = _TMP

import pytest  # noqa: E402

from dashboard.backend.db.portfolio import (  # noqa: E402
    add_position,
    get_portfolio,
    init_portfolio_db,
    migrate_portfolio_origin_column,
)
from dashboard.backend.db.schema import get_connection  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("ADMISSION_GATE_PERSIST", "0")
    monkeypatch.delenv("ADMISSION_GATE_ENFORCE", raising=False)
    init_portfolio_db()
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_positions")
        c.execute("DELETE FROM portfolio_journal")
        c.commit()
    finally:
        c.close()


def _door_of(symbol: str) -> str | None:
    c = get_connection()
    try:
        row = c.execute(
            "SELECT source_door FROM portfolio_positions WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row[0] if row else None
    finally:
        c.close()


BASE = dict(entry_price=100.0, stop_loss=95.0, target_1=120.0, current_price=100.0)


# ── 1. the column exists and the migration is idempotent ─────────────────────

def test_source_door_column_exists_and_migration_is_idempotent():
    c = get_connection()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
    finally:
        c.close()
    assert "source_door" in cols

    # Running it again must not raise or duplicate the column.
    migrate_portfolio_origin_column()
    migrate_portfolio_origin_column()
    c = get_connection()
    try:
        names = [r[1] for r in c.execute("PRAGMA table_info(portfolio_positions)").fetchall()]
    finally:
        c.close()
    assert names.count("source_door") == 1


# ── 2. door 1 — origin persisted ─────────────────────────────────────────────

def test_door1_promote_to_portfolio_records_its_origin():
    add_position({"symbol": "DOOR1", "horizon": "SWING",
                  "source_door": "promote_to_portfolio", **BASE})
    assert _door_of("DOOR1") == "promote_to_portfolio"


def test_door1_without_an_explicit_door_is_marked_unattributed_not_null():
    """A caller that bypasses the promotion path must be visible, not silent —
    the same reasoning the admission gate uses for its own default."""
    add_position({"symbol": "NODOOR", "horizon": "SWING", **BASE})
    assert _door_of("NODOOR") == "add_position:unattributed"


# ── 3. door 2 — origin persisted by the seed path ────────────────────────────

def test_door2_seed_records_seed_from_recommendations():
    """Drive the real seed function through a running_trades row rather than
    asserting on a hand-written INSERT, so the test breaks if the seed path
    stops attributing itself."""
    from dashboard.backend.db.portfolio import seed_portfolio_from_recommendations
    from dashboard.backend.db.schema import init_db

    init_db()  # the seed joins running_trades -> stock_recommendations

    c = get_connection()
    try:
        c.execute(
            "INSERT INTO stock_recommendations (symbol, agent_type, entry_price, stop_loss,"
            " targets, confidence_score, reasoning) VALUES (?,?,?,?,?,?,?)",
            ("SEEDSYM", "SWING", 100.0, 95.0, "[120.0]", 70.0, "seeded"),
        )
        rec_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            "INSERT INTO running_trades (symbol, recommendation_id, entry_price, stop_loss,"
            " targets, current_price, status) VALUES (?,?,?,?,?,?, 'RUNNING')",
            ("SEEDSYM", rec_id, 100.0, 95.0, "[120.0]", 101.0),
        )
        c.commit()
    finally:
        c.close()

    seed_portfolio_from_recommendations()
    assert _door_of("SEEDSYM") == "seed_from_recommendations"


# ── 4. both doors are attributable and distinguishable ───────────────────────

def test_the_two_doors_are_distinguishable_in_the_durable_row():
    add_position({"symbol": "AAA", "horizon": "SWING",
                  "source_door": "promote_to_portfolio", **BASE})
    add_position({"symbol": "BBB", "horizon": "SWING",
                  "source_door": "seed_from_recommendations", **BASE})
    assert _door_of("AAA") != _door_of("BBB")
    assert {_door_of("AAA"), _door_of("BBB")} == {
        "promote_to_portfolio", "seed_from_recommendations"}


# ── 5. both doors call the SAME gate, with a source_door ──────────────────────

def test_both_doors_call_the_same_admission_gate_with_an_attributed_door():
    """Static assertion on the source: both insert paths must reach
    services.admission_gate.evaluate_safe and pass source_door. A second gate
    implementation, or an unattributed call, is the regression this catches."""
    src = open("dashboard/backend/db/portfolio.py", encoding="utf-8").read()
    tree = ast.parse(src)

    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", None)) == "evaluate_safe"
    ]
    assert len(calls) >= 2, "expected an admission-gate call on both doors"
    for c in calls:
        assert any(kw.arg == "source_door" for kw in c.keywords), \
            "every gate call must attribute its door"

    # Exactly one gate module is imported — not a second implementation.
    imports = {
        n.module for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and "admission_gate" in n.module
    }
    assert imports == {"services.admission_gate"}


# ── 6. shadow mode stays behaviour-neutral ───────────────────────────────────

def test_gate_verdict_is_not_consulted_position_is_created_regardless():
    """A candidate the gate would REJECT must still be admitted while shadowing.
    If this ever fails, the gate has started enforcing."""
    from services import admission_gate as ag

    decision = ag.evaluate(
        "SHADOWSYM", "SWING", 100.0, 20.0,          # 80% stop width — absurd
        source_door="promote_to_portfolio", direction="LONG",
        price=1.0, turnover_cr=0.01, atr_pct=99.0, position_size=None,
        sector_counts={}, book_used=0, book_max=20,
    )
    pos_id = add_position({"symbol": "SHADOWSYM", "horizon": "SWING",
                           "source_door": "promote_to_portfolio",
                           **{**BASE, "entry_price": 100.0, "stop_loss": 20.0}})
    assert pos_id > 0, "shadow mode must not block an insert"
    assert decision.shadow_mode is True
    assert any(p["symbol"] == "SHADOWSYM" for p in get_portfolio("SWING"))


# ── 7. existing rows are untouched ───────────────────────────────────────────

def test_pre_existing_rows_keep_null_origin_and_are_not_rewritten():
    """The migration is additive: a row written before the column existed stays
    exactly as it was, with NULL origin — honestly 'unknown', not back-filled
    with a guess."""
    c = get_connection()
    try:
        c.execute(
            "INSERT INTO portfolio_positions (symbol, horizon, entry_price, stop_loss,"
            " current_price, status) VALUES ('LEGACY','SWING',100.0,95.0,100.0,'ACTIVE')"
        )
        c.commit()
        before = dict(c.execute(
            "SELECT * FROM portfolio_positions WHERE symbol='LEGACY'").fetchone())
    finally:
        c.close()

    migrate_portfolio_origin_column()
    add_position({"symbol": "NEWROW", "horizon": "SWING",
                  "source_door": "promote_to_portfolio", **BASE})

    c = get_connection()
    try:
        after = dict(c.execute(
            "SELECT * FROM portfolio_positions WHERE symbol='LEGACY'").fetchone())
    finally:
        c.close()

    assert after == before
    assert after["source_door"] is None
