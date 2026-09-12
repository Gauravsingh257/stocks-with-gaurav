"""Write-time provenance capture.

These tests pin the two properties that make this table worth having: it is
captured at WRITE time (so it records the configuration actually in force, not
whatever a later backfill happens to see), and it can never break a position
write. The 2026-09-13 audit failed because both properties were absent.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from dashboard.backend.db.position_provenance import (
    _algorithm_hash,
    _flags,
    capture,
    init_provenance_db,
)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_provenance_db(c)
    yield c
    c.close()


PAYLOAD = {
    "symbol": "NSE:TESTCO", "horizon": "SWING",
    "entry_price": 100.0, "stop_loss": 95.0, "target_1": 115.0,
    "current_price": 101.0, "confidence_score": 77.5,
    "source_door": "promote_to_portfolio",
}


def test_capture_records_entry_geometry(conn):
    assert capture(1, PAYLOAD, conn=conn)
    r = dict(conn.execute("SELECT * FROM position_provenance").fetchone())
    assert r["stop_width_pct"] == pytest.approx(5.0)
    assert r["rr_at_entry"] == pytest.approx(3.0)
    assert r["entry_gap_pct"] == pytest.approx(1.0)
    assert r["source_door"] == "promote_to_portfolio"


def test_missing_evidence_is_recorded_not_silently_absent(conn):
    """An unresolvable field must be *named*. A later audit has to be able to
    tell 'we looked and it wasn't there' from 'nobody looked'."""
    capture(1, PAYLOAD, conn=conn)
    missing = json.loads(conn.execute(
        "SELECT missing FROM position_provenance").fetchone()["missing"])
    # No signals_log table exists in this fixture, so the selection evidence
    # genuinely cannot resolve — and that must be stated.
    assert "signals_log_row" in missing
    assert "smc_evidence" in missing


def test_capture_never_raises_on_garbage(conn):
    """A position must never fail to be created because provenance broke."""
    assert capture(2, {}, conn=conn) is not None or True
    assert capture(3, {"symbol": None, "entry_price": "not-a-number",
                       "stop_loss": None}, conn=conn) is not None or True


def test_algorithm_hash_tracks_configuration(monkeypatch):
    """Two positions differ here exactly when the rules that produced them
    differed — the property the backfilled hash lacked (it was one value across
    all 143 rows because it fingerprinted the backfill, not the trade)."""
    monkeypatch.setenv("ENTRY_ANCHOR_MAX_GAP_PCT", "30")
    a = _algorithm_hash(_flags(), "SMC v4.2.1")
    monkeypatch.setenv("ENTRY_ANCHOR_MAX_GAP_PCT", "10")
    b = _algorithm_hash(_flags(), "SMC v4.2.1")
    assert a != b, "flag change must produce a different configuration fingerprint"
    monkeypatch.setenv("ENTRY_ANCHOR_MAX_GAP_PCT", "30")
    assert _algorithm_hash(_flags(), "SMC v4.2.1") == a, "hash must be stable"


def test_recommendation_is_snapshotted_not_referenced(conn):
    """stock_recommendations rows recycle, so a stored id decays into a false
    link. The values must be copied at capture time."""
    capture(1, {**PAYLOAD, "recommendation_id": 999999}, conn=conn)
    snap = json.loads(conn.execute(
        "SELECT recommendation_snapshot FROM position_provenance").fetchone()[0])
    assert snap["confidence_score"] == 77.5
    assert snap["entry_price"] == 100.0


def test_scan_evidence_is_linked_when_available(conn):
    """With signals_log present, the scan and its candidate universe are
    recorded — this is what makes 'why this stock over the alternatives?'
    answerable later."""
    conn.executescript("""
        CREATE TABLE signals_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,
            horizon TEXT NOT NULL, symbol TEXT NOT NULL, date TEXT NOT NULL,
            cmp REAL, entry REAL, stop_loss REAL, target REAL,
            confidence REAL NOT NULL DEFAULT 0, layer1_pass INTEGER DEFAULT 0,
            layer2_pass INTEGER DEFAULT 0, layer3_pass INTEGER DEFAULT 0,
            final_selected INTEGER DEFAULT 0, rejection_reason TEXT DEFAULT '[]',
            layer_details TEXT DEFAULT '{}', coverage_report TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
    """)
    ld = json.dumps({"smc": {"entry_type": "LIMIT", "confirmation_score": 7.5, "tier": "A"}})
    conn.execute(
        "INSERT INTO signals_log (scan_id,horizon,symbol,date,cmp,entry,confidence,"
        "final_selected,layer_details) VALUES ('VAL-SWING-2026-09-14-abc','SWING',"
        "'TESTCO','2026-09-14',101.0,100.0,77.5,1,?)", (ld,))
    # two rivals that were scanned but not selected — the alternatives
    for sym in ("RIVAL1", "RIVAL2"):
        conn.execute(
            "INSERT INTO signals_log (scan_id,horizon,symbol,date,confidence,final_selected) "
            "VALUES ('VAL-SWING-2026-09-14-abc','SWING',?, '2026-09-14',60.0,0)", (sym,))

    capture(1, PAYLOAD, conn=conn)
    r = dict(conn.execute("SELECT * FROM position_provenance").fetchone())
    assert r["scan_id"] == "VAL-SWING-2026-09-14-abc"
    assert r["signals_log_id"] is not None
    assert r["entry_type"] == "LIMIT"
    assert json.loads(r["smc_evidence"])["tier"] == "A"
    # the denominator behind "chosen over N alternatives"
    assert r["universe_scanned"] == 3
    assert r["selected_count"] == 1
    assert "signals_log_row" not in json.loads(r["missing"])
