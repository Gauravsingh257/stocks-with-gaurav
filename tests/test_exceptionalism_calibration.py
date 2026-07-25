"""Tests for the offline exceptionalism calibration harness (metric math + loader)."""

from __future__ import annotations

import json
import sqlite3

from scripts.exceptionalism_calibration import (
    ShadowRow, attach_forward_returns, bucket_by_exceptionalism, build_report,
    false_positive_rate, legacy_vs_exceptionalism, load_shadow_rows,
    metrics_for, optimal_threshold, render_markdown, threshold_sweep,
)


# ── metrics math ──────────────────────────────────────────────────────────────

def test_metrics_for_basic():
    m = metrics_for([10, -5, 20, -2, 8], maes=[-3, -6, -1, -4, -2])
    assert m["n"] == 5
    assert m["hit_rate"] == 60.0                 # 3 of 5 positive
    assert m["avg_return"] == round((10 - 5 + 20 - 2 + 8) / 5, 2)
    assert m["median_return"] == 8
    assert m["avg_max_drawdown"] == round((-3 - 6 - 1 - 4 - 2) / 5, 2)
    assert m["worst_drawdown"] == -6


def test_metrics_empty():
    assert metrics_for([])["n"] == 0


# ── forward returns from synthetic bars ───────────────────────────────────────

def _row(sym="AAA", cmp=100.0, exc=85.0, health=40.0, qualifies=True, final=True, band="leading"):
    return ShadowRow(scan_id="S", symbol=sym, date="2026-07-25", cmp=cmp, final_selected=final,
                     exceptionalism=exc, threshold=78.0, qualifies=qualifies,
                     market_health=health, sector_band=band)


def test_attach_forward_returns_and_pending_horizon():
    # 6 bars: scan day + 5 forward. So 1/3/5 covered, 10/20 pending.
    bars = [{"date": f"d{i}", "high": 100 + i + 0.5, "low": 100 + i - 0.5, "close": 100.0 + i} for i in range(6)]
    rows = [_row()]
    attach_forward_returns(rows, lambda s, d: bars)
    fwd = rows[0].forward
    assert fwd[5]["ret"] == 5.0            # close 105 vs cmp 100
    assert 10 not in fwd and 20 not in fwd  # not enough bars → pending, not guessed
    assert fwd[3]["mfe"] is not None and fwd[3]["mae"] is not None


def test_forward_returns_skip_bad_cmp():
    rows = [ShadowRow("S", "AAA", "2026-07-25", None, True, 85, 78, True, 40, "leading")]
    attach_forward_returns(rows, lambda s, d: [{"date": "d", "high": 1, "low": 1, "close": 1}])
    assert rows[0].forward == {}


# ── bucketing / sweep / verdict ───────────────────────────────────────────────

def _rows_with_returns(specs, horizon=5):
    """specs: list of (exc, health, qualifies, final, ret) → ShadowRows with forward set."""
    rows = []
    for exc, health, q, f, ret in specs:
        r = _row(exc=exc, health=health, qualifies=q, final=f)
        r.forward[horizon] = {"ret": ret, "mae": min(0.0, ret) - 1, "mfe": max(0.0, ret) + 1}
        rows.append(r)
    return rows


def test_bucket_by_exceptionalism_groups_correctly():
    rows = _rows_with_returns([(95, 40, True, True, 12), (92, 40, True, True, 8),
                               (65, 60, False, False, -3)])
    buckets = {b["band"]: b for b in bucket_by_exceptionalism(rows, 5)}
    assert buckets["90-100"]["n"] == 2 and buckets["90-100"]["hit_rate"] == 100.0
    assert buckets["60-69"]["n"] == 1 and buckets["60-69"]["hit_rate"] == 0.0


def test_threshold_sweep_monotonic_selection():
    rows = _rows_with_returns([(95, 40, True, True, 10), (85, 40, True, True, 5),
                               (72, 40, False, False, -1), (60, 40, False, False, -4)])
    sweep = threshold_sweep(rows, 5, min_n=1)
    by_t = {s["threshold"]: s["selected"] for s in sweep}
    assert by_t[60] >= by_t[80] >= by_t[94]        # higher cutoff selects fewer
    best = optimal_threshold(sweep, min_n=1)
    assert best["avg_return"] >= sweep[0]["avg_return"]  # optimal not worse than the loosest


def test_legacy_vs_exceptionalism_detects_improvement():
    # Legacy final set is mediocre; exceptionalism-qualified set is stronger.
    rows = _rows_with_returns([
        (95, 40, True, True, 14),    # qualified + final, strong
        (91, 40, True, False, 10),   # qualified only, strong
        (55, 40, False, True, -6),   # legacy final only, weak (a false pick)
    ])
    v = legacy_vs_exceptionalism(rows, 5)
    assert v["exceptionalism_qualified"]["avg_return"] > v["legacy_final_selected"]["avg_return"]
    assert v["exceptionalism_better"] is True


def test_false_positive_rate():
    rows = _rows_with_returns([(95, 40, True, True, 10), (92, 40, True, True, -3),
                               (90, 40, True, True, 5)])
    fp = false_positive_rate(rows, 5)
    assert fp["n"] == 3 and fp["false_positives"] == 1
    assert fp["precision"] == round(2 / 3 * 100, 1)


# ── loader parses the real signals_log JSON shape ─────────────────────────────

def test_load_shadow_rows_parses_layer_details():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE signals_log (scan_id TEXT, horizon TEXT, symbol TEXT, date TEXT, "
                 "cmp REAL, final_selected INTEGER, layer_details TEXT)")
    ld = {"exceptionalism": {"exceptionalism": 88.0, "threshold": 80.0, "qualifies": True,
                             "market_health": 42.0, "sector_band": "leading"}}
    conn.execute("INSERT INTO signals_log VALUES (?,?,?,?,?,?,?)",
                 ("SC1", "SWING", "NSE:AAA", "2026-07-25", 100.0, 1, json.dumps(ld)))
    conn.commit()
    rows = load_shadow_rows(conn, date_from="2026-07-01", date_to="2026-08-01", horizon="SWING")
    assert len(rows) == 1
    r = rows[0]
    assert r.exceptionalism == 88.0 and r.threshold == 80.0
    assert r.qualifies is True and r.market_health == 42.0 and r.sector_band == "leading"


def test_build_report_empty_data_message():
    report = build_report([])
    md = render_markdown(report)
    assert report["with_exceptionalism"] == 0
    assert "No exceptionalism data yet" in md


def test_build_report_renders_with_data():
    rows = _rows_with_returns([(95, 40, True, True, 12), (91, 40, True, True, 8),
                               (60, 40, False, True, -4)], horizon=5)
    md = render_markdown(build_report(rows))
    assert "Exceptionalism Calibration Report" in md
    assert "5-Day Forward Horizon" in md
