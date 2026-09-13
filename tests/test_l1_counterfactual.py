"""The Layer-1 counterfactual — the only honest test of "is L1 trimming winners?"

After 4fe80ae an L1-failed stock is never selected, so its outcome never reaches
the portfolio journal. This metric measures it from scan-level forward returns
instead. The tests pin the rules that stop it lying: one observation per stock
per day, "selected in any scan" beats "excluded in one", no outcome from too few
labels, and no pooling across the fix date.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import scripts.phase1_calibration as pc

SCHEMA = """
CREATE TABLE signals_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT, horizon TEXT, symbol TEXT,
    date TEXT, entry REAL, layer1_pass INTEGER, final_selected INTEGER,
    layer_details TEXT DEFAULT '{}');
CREATE TABLE forward_returns (
    symbol TEXT, date TEXT, fwd_10d_pct REAL, fwd_20d_pct REAL, excess_20d_pct REAL);
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "calib.db"
    with sqlite3.connect(path) as c:
        c.executescript(SCHEMA)

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    import dashboard.backend.db.schema as schema

    monkeypatch.setattr(schema, "get_connection", connect)
    return path


def scan(path, sym, date, *, l1, selected, qualifies=None, entry=100.0, times=1, raw_details=None):
    details = raw_details if raw_details is not None else json.dumps(
        {"exceptionalism": {"qualifies": qualifies}} if qualifies is not None else {})
    with sqlite3.connect(path) as c:
        for _ in range(times):
            c.execute(
                "INSERT INTO signals_log (scan_id, horizon, symbol, date, entry, layer1_pass, "
                "final_selected, layer_details) VALUES ('S', 'SWING', ?, ?, ?, ?, ?, ?)",
                (f"NSE:{sym}", date, entry, l1, selected, details))


def label(path, sym, date, fwd20):
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO forward_returns VALUES (?, ?, ?, ?, ?)",
                  (f"NSE:{sym}", date, fwd20 / 2, fwd20, fwd20 - 1))


def run(**kw):
    return pc.l1_counterfactual(None, 3650, **kw)


def test_one_observation_per_stock_per_day(db):
    scan(db, "AAA", "2026-09-01", l1=1, selected=1, times=6)
    assert run()["post_fix"]["selected"]["n"] == 1


def test_excluded_needs_exceptionalism_a_tradable_entry_and_no_selection(db):
    scan(db, "EXC", "2026-09-01", l1=0, selected=0, qualifies=True)            # counts
    scan(db, "NOQ", "2026-09-01", l1=0, selected=0, qualifies=False)           # not exceptional
    scan(db, "NOE", "2026-09-01", l1=0, selected=0, qualifies=True, entry=None)  # no tradable entry
    # excluded by a degraded early scan, selected by the later healthy one
    scan(db, "BOTH", "2026-09-01", l1=0, selected=0, qualifies=True)
    scan(db, "BOTH", "2026-09-01", l1=1, selected=1)
    r = run()["post_fix"]
    assert r["excluded_l1_fail_exceptional"]["n"] == 1
    assert r["selected"]["n"] == 1


def test_outcome_withheld_until_enough_labels(db):
    for sym in ("A1", "A2"):
        scan(db, sym, "2026-09-01", l1=0, selected=0, qualifies=True)
        label(db, sym, "2026-09-01", 4.0)
    excluded = run(min_labelled=3)["post_fix"]["excluded_l1_fail_exceptional"]
    assert excluded["outcome"] is None and "2 labelled" in excluded["withheld"]
    mature = run(min_labelled=2)["post_fix"]["excluded_l1_fail_exceptional"]
    assert mature["outcome"]["median_fwd_20d_pct"] == 4.0
    assert mature["outcome"]["win_rate_20d_pct"] == 100.0


def test_never_pools_across_the_fix_date(db):
    scan(db, "OLD", "2026-08-20", l1=0, selected=1)                  # readmitted pre-fix
    scan(db, "NEW", "2026-09-01", l1=0, selected=0, qualifies=True)  # excluded post-fix
    r = run()
    assert r["pre_fix"]["selected_l1_fail_readmitted"]["n"] == 1
    assert r["post_fix"]["excluded_l1_fail_exceptional"]["n"] == 1
    assert r["post_fix"]["selected"]["n"] == 0


def test_reports_label_freshness_and_survives_malformed_details(db):
    scan(db, "BAD", "2026-09-01", l1=0, selected=0, raw_details="{not json")
    scan(db, "OK", "2026-08-25", l1=1, selected=1)
    label(db, "OK", "2026-08-25", 2.0)
    r = run()
    assert r["available"] is True
    assert r["forward_returns_labelled_through"] == "2026-08-25"
    assert r["post_fix"]["excluded_l1_fail_exceptional"]["n"] == 0


def test_empty_window_is_explicit(db):
    assert run() == {"available": False, "reason": "no signals_log rows in window"}
