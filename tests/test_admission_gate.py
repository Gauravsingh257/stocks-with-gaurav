"""Portfolio admission gate (Step 3) — shadow-mode contract.

The gate's whole value is that it sees EVERY automatic creation path while
changing nothing. These tests pin both halves of that: the checks themselves,
and the guarantee that production behaviour is byte-identical while shadowing.
"""

from __future__ import annotations

import ast
import os
import tempfile

# Throwaway SQLite dir BEFORE importing the db modules (same convention as
# tests/test_portfolio_arm_on_tap.py) so the real dashboard.db is never touched.
_TMP = tempfile.mkdtemp(prefix="pf_gate_test_")
os.environ["DATA_DIR"] = _TMP

import pytest  # noqa: E402

from services import admission_gate as ag  # noqa: E402
from dashboard.backend.db.portfolio import (  # noqa: E402
    init_portfolio_db, add_position, get_portfolio,
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Thresholds explicitly at their shipped no-op defaults, persistence off
    (no Redis in tests), and a clean book."""
    for k in ("PROMOTE_MIN_PRICE", "PROMOTE_MIN_TURNOVER_CR",
              "PROMOTE_MAX_ATR_PCT", "PROMOTE_MAX_STOP_WIDTH_PCT",
              "PROMOTE_MAX_SECTOR_EXPOSURE", "ADMISSION_GATE_ENFORCE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ADMISSION_GATE_PERSIST", "0")
    init_portfolio_db()
    from dashboard.backend.db.schema import get_connection
    c = get_connection()
    try:
        c.execute("DELETE FROM portfolio_positions")
        c.execute("DELETE FROM portfolio_journal")
        c.commit()
    finally:
        c.close()


HEALTHY = dict(price=500.0, turnover_cr=25.0, atr_pct=2.5, position_size=100000.0)


# ── 1. valid candidate passes ────────────────────────────────────────────────
def test_valid_candidate_passes():
    d = ag.evaluate("NSE:TCS", "SWING", 100.0, 95.0,
                    source_door="promote_to_portfolio", persist=False, **HEALTHY)
    assert d.decision == "PASS"
    assert d.rejection_reasons == []
    assert d.stop_width_pct == 5.0
    assert d.shadow_mode is True


def test_defaults_are_no_ops(monkeypatch):
    """Shipped defaults must not be able to reject anything — a Rs 1 stock with
    zero turnover, 40% ATR and a 60% stop still PASSes until thresholds are set."""
    d = ag.evaluate("NSE:JUNK", "LONGTERM", 1.0, 0.4, persist=False,
                    price=1.0, turnover_cr=0.0, atr_pct=40.0)
    assert d.decision == "PASS", d.rejection_reasons


# ── 2. zero turnover ─────────────────────────────────────────────────────────
def test_zero_turnover_rejected(monkeypatch):
    monkeypatch.setenv("PROMOTE_MIN_TURNOVER_CR", "1.0")
    d = ag.evaluate("NSE:SONAL", "SWING", 90.8, 84.9, persist=False,
                    price=90.8, turnover_cr=0.0, atr_pct=4.6)
    assert d.decision == "REJECT"
    assert ag.REASON_TURNOVER in d.rejection_reasons


# ── 3. excessive stop ────────────────────────────────────────────────────────
def test_stop_too_wide_rejected(monkeypatch):
    monkeypatch.setenv("PROMOTE_MAX_STOP_WIDTH_PCT", "15")
    d = ag.evaluate("NSE:VIPULLTD", "LONGTERM", 14.93, 8.93, persist=False,
                    price=14.93, turnover_cr=1.15, atr_pct=4.0)
    assert d.decision == "REJECT"
    assert ag.REASON_STOP in d.rejection_reasons
    assert d.stop_width_pct == pytest.approx(40.19, abs=0.05)


# ── 4. multiple reasons accumulate ───────────────────────────────────────────
def test_multiple_rejection_reasons(monkeypatch):
    monkeypatch.setenv("PROMOTE_MIN_PRICE", "50")
    monkeypatch.setenv("PROMOTE_MIN_TURNOVER_CR", "2")
    monkeypatch.setenv("PROMOTE_MAX_ATR_PCT", "4")
    monkeypatch.setenv("PROMOTE_MAX_STOP_WIDTH_PCT", "15")
    d = ag.evaluate("NSE:VIPULLTD", "LONGTERM", 14.93, 8.93, persist=False,
                    price=14.93, turnover_cr=0.34, atr_pct=7.0)
    assert d.decision == "REJECT"
    assert set(d.rejection_reasons) == {
        ag.REASON_PRICE, ag.REASON_TURNOVER, ag.REASON_ATR, ag.REASON_STOP}


def test_capacity_and_sector_reasons(monkeypatch):
    monkeypatch.setenv("PROMOTE_MAX_SECTOR_EXPOSURE", "3")
    d = ag.evaluate("NSE:SUNPHARMA", "SWING", 100.0, 95.0, persist=False,
                    sector_counts={"PHARMA": 3}, book_used=20, book_max=20, **HEALTHY)
    assert ag.REASON_SECTOR in d.rejection_reasons
    assert ag.REASON_CAPACITY in d.rejection_reasons


def test_missing_metrics_flagged_invalid(monkeypatch):
    monkeypatch.setenv("PROMOTE_MIN_TURNOVER_CR", "2")
    d = ag.evaluate("NSE:X", "SWING", 100.0, 95.0, persist=False, turnover_cr=None)
    assert ag.REASON_INVALID in d.rejection_reasons


def test_invalid_geometry_flagged():
    d = ag.evaluate("NSE:X", "SWING", None, None, persist=False)
    assert ag.REASON_INVALID in d.rejection_reasons


# ── 5 + 6. both doors are wired to the SAME gate ─────────────────────────────
def _capture(monkeypatch):
    seen: list = []
    real = ag.evaluate

    def spy(*a, **kw):
        kw["persist"] = False
        d = real(*a, **kw)
        seen.append(d)
        return d

    monkeypatch.setattr(ag, "evaluate", spy)
    return seen


def test_promote_to_portfolio_uses_the_gate(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setenv("RISK_ENGINE_ENABLED", "0")  # no network, legacy sizing
    monkeypatch.setattr("services.portfolio_manager._send_portfolio_entry_alert",
                        lambda *_a, **_k: None)
    monkeypatch.setattr("services.portfolio_manager._send_portfolio_armed_alert",
                        lambda *_a, **_k: None)
    from services.portfolio_manager import promote_to_portfolio

    promote_to_portfolio("NSE:TESTA", "SWING", 100.0, 95.0, target_1=120.0,
                         current_price=100.0, pending=False)
    assert len(seen) == 1
    assert seen[0].symbol == "NSE:TESTA"
    assert seen[0].source_door == "promote_to_portfolio"


def test_seed_from_recommendations_uses_the_same_gate(monkeypatch):
    """Door 2 is a raw INSERT that bypasses promote_to_portfolio entirely — it
    must still reach the identical gate, tagged as its own door."""
    seen = _capture(monkeypatch)
    from dashboard.backend.db.schema import get_connection, init_db
    from dashboard.backend.db.portfolio import seed_portfolio_from_recommendations

    # Use the REAL schema. A hand-rolled stub here would be created with
    # CREATE TABLE IF NOT EXISTS and then persist in the shared test DB
    # (DB_PATH is resolved once at import), shadowing the real definition for
    # every test module that runs afterwards.
    init_db()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO stock_recommendations (symbol, agent_type,"
                     " entry_price, stop_loss, confidence_score, reasoning, targets)"
                     " VALUES ('NSE:SEEDCO','SWING',100.0,95.0,80,'t','[120]')")
        rec_id = conn.execute("SELECT id FROM stock_recommendations WHERE"
                              " symbol='NSE:SEEDCO'").fetchone()[0]
        conn.execute("INSERT INTO running_trades (symbol, entry_price, stop_loss,"
                     " current_price, status, recommendation_id, created_at)"
                     " VALUES ('NSE:SEEDCO',100.0,95.0,100.0,'RUNNING',?,'2026-08-19')",
                     (rec_id,))
        conn.commit()

        seed_portfolio_from_recommendations()
        doors = {d.source_door for d in seen}
        assert "seed_from_recommendations" in doors, f"door 2 not gated; saw {doors}"
    finally:
        # Leave no trace: DB_PATH is process-global, so a stray RUNNING trade
        # here would be visible to every module that runs after this one.
        conn.execute("DELETE FROM running_trades WHERE symbol='NSE:SEEDCO'")
        conn.execute("DELETE FROM stock_recommendations WHERE symbol='NSE:SEEDCO'")
        conn.execute("DELETE FROM portfolio_positions WHERE symbol='NSE:SEEDCO'")
        conn.commit()
        conn.close()


# ── 7. no automatic creation path bypasses the gate ──────────────────────────
def test_no_ungated_insert_path_exists():
    """STATIC guard. Every function in db/portfolio.py that INSERTs a real row
    into portfolio_positions must call the admission gate. This fails loudly if
    someone adds a third door later — which is exactly how doors 1 and 2 came to
    diverge in the first place."""
    src = open("dashboard/backend/db/portfolio.py", encoding="utf-8").read()
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(src, node) or ""
        # Only real position creation — the schema-migration table copies
        # (portfolio_positions_new / _pend) are not admission paths.
        inserts = "INSERT INTO portfolio_positions" in body
        migration_only = ("portfolio_positions_new" in body
                          or "portfolio_positions_pend" in body)
        if inserts and not migration_only:
            if "evaluate_safe" not in body:
                offenders.append(node.name)

    assert not offenders, (
        f"ungated portfolio_positions INSERT in: {offenders}. "
        "Every automatic creation path must call admission_gate.evaluate_safe()."
    )


def test_gate_is_not_duplicated_across_callers():
    """The gate logic lives in ONE module. Callers may only invoke it."""
    for path in ("services/portfolio_manager.py", "dashboard/backend/db/portfolio.py"):
        body = open(path, encoding="utf-8").read()
        assert "REASON_TURNOVER" not in body and "PROMOTE_MIN_TURNOVER_CR" not in body, (
            f"{path} re-implements gate policy; it must only call the gate")


# ── 8 + 9. shadow mode changes nothing ───────────────────────────────────────
def test_shadow_mode_does_not_block_a_rejected_candidate(monkeypatch):
    """A candidate the gate REJECTS must still be created while shadowing."""
    monkeypatch.setenv("PROMOTE_MAX_STOP_WIDTH_PCT", "5")
    monkeypatch.setenv("PROMOTE_MIN_TURNOVER_CR", "10")
    pid = add_position({"symbol": "NSE:WIDE", "horizon": "SWING",
                        "entry_price": 100.0, "stop_loss": 60.0,
                        "status": "ACTIVE", "source_door": "promote_to_portfolio"})
    assert pid > 0
    book = get_portfolio("SWING", include_pending=True)
    assert any(p["symbol"] == "NSE:WIDE" for p in book), "shadow gate blocked an insert"


def test_gate_verdict_would_have_been_reject(monkeypatch):
    """Same candidate, evaluated directly: the gate DOES say REJECT — proving
    the previous test passed because of shadow mode, not a lenient policy."""
    monkeypatch.setenv("PROMOTE_MAX_STOP_WIDTH_PCT", "5")
    d = ag.evaluate("NSE:WIDE", "SWING", 100.0, 60.0, persist=False, **HEALTHY)
    assert d.decision == "REJECT" and ag.REASON_STOP in d.rejection_reasons


def test_existing_positions_untouched_by_evaluation(monkeypatch):
    """Evaluating candidates must not mutate the book in any way."""
    pid = add_position({"symbol": "NSE:HOLD", "horizon": "SWING",
                        "entry_price": 100.0, "stop_loss": 95.0, "status": "ACTIVE"})
    before = [dict(p) for p in get_portfolio("SWING", include_pending=True)]
    monkeypatch.setenv("PROMOTE_MIN_PRICE", "1000")
    for _ in range(5):
        ag.evaluate("NSE:HOLD", "SWING", 100.0, 95.0, persist=False, **HEALTHY)
    after = [dict(p) for p in get_portfolio("SWING", include_pending=True)]
    assert before == after
    assert any(p["id"] == pid for p in after)


def test_gate_never_raises_on_garbage():
    """The insert path must survive any input the gate is handed."""
    for args in [
        ("NSE:X", "SWING", "not-a-number", None),
        (None, None, None, None),
        ("NSE:X", "SWING", 0, 0),
        ("NSE:X", "SWING", 100, 200),   # stop above entry
    ]:
        d = ag.evaluate(*args, persist=False)
        assert d is not None and d.decision in ("PASS", "REJECT")


def test_evaluate_safe_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("ADMISSION_GATE_ENABLED", "0")
    assert ag.evaluate_safe("NSE:X", "SWING", 100.0, 95.0) is None


def test_enforce_flag_flips_shadow_marker(monkeypatch):
    """Shadow marker must reflect the flag — the report relies on it to warn
    that part of a window was recorded while enforcing."""
    monkeypatch.setenv("ADMISSION_GATE_ENFORCE", "1")
    d = ag.evaluate("NSE:X", "SWING", 100.0, 95.0, persist=False, **HEALTHY)
    assert d.shadow_mode is False


# ── report aggregation ───────────────────────────────────────────────────────
def test_summarize_shapes_the_report():
    decisions = [
        ag.evaluate("A", "SWING", 100, 95, source_door="promote_to_portfolio",
                    persist=False, **HEALTHY).to_dict(),
        ag.evaluate("B", "LONGTERM", 100, 95, source_door="seed_from_recommendations",
                    persist=False, **HEALTHY).to_dict(),
        ag.evaluate("C", "SWING", None, None, source_door="add_position:unattributed",
                    persist=False).to_dict(),
    ]
    s = ag.summarize(decisions)
    assert s["total"] == 3 and s["pass"] == 2 and s["reject"] == 1
    assert s["by_horizon"]["SWING"]["total"] == 2
    assert s["by_door"]["seed_from_recommendations"]["total"] == 1
    assert s["invalid_metric_count"] == 1
    assert s["unattributed_count"] == 0  # exact key, not a prefix match
    assert s["by_reason"][ag.REASON_INVALID] == 1
