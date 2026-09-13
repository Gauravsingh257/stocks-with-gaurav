"""scripts/scan_health.py — the checks that would have caught 2026-09-07.

Every fixture here mirrors a real row shape from `ranking_runs`. The two
properties that matter most: a manifest with no shards behind it is CRITICAL
(the exact 09-07 state), and ranking-engine rows never leak into the validation
Layer-1 baseline (the mixed-column trap that produced a misleading "median 75").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.scan_health import (
    CRITICAL,
    OK,
    UNKNOWN,
    classify_run,
    exit_code,
    l1_collapse_runs,
    next_scan_after,
    snapshot_verdict,
    zero_pass_runs,
)

IST = timezone(timedelta(hours=5, minutes=30))
RANK = "sources={'nse_dynamic_cache': 2583, 'stock_universe_500': 278}|ideas=0"
VAL = "research_feed_tick|scan_id=VAL-SWING-2026-09-07-51bc6a2b"


def run(t, notes, qpass, scanned=2190, horizon="SWING"):
    return {"run_time": t, "horizon": horizon, "notes": notes,
            "universe_scanned": scanned, "quality_passed": qpass}


def ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


# ── row classification ───────────────────────────────────────────────────────

def test_classifies_both_row_types_and_flags_the_rest():
    assert classify_run(run("x", RANK, 1)) == "ranking"
    assert classify_run(run("x", VAL, 1)) == "validation"
    assert classify_run(run("x", "something else", 1)) == "unknown"
    assert classify_run({"notes": None}) == "unknown"


# ── zero-pass ────────────────────────────────────────────────────────────────

def test_zero_pass_fires_on_the_0907_ranking_row():
    rows = [run("2026-09-07 03:02:54", RANK, 0, scanned=2189)]
    assert len(zero_pass_runs(rows)) == 1


def test_zero_pass_ignores_healthy_runs_tiny_universes_and_validation_rows():
    rows = [
        run("2026-09-08 03:02:21", RANK, 1483),
        run("2026-09-08 03:02:22", RANK, 0, scanned=12),   # tiny universe, not a blackout
        run("2026-08-18 03:02:57", VAL, 0),                # validation zero is the L1 check's job
    ]
    assert zero_pass_runs(rows) == []


# ── Layer-1 collapse ─────────────────────────────────────────────────────────

def _priors(level=410, notes=VAL, horizon="SWING"):
    return [run(f"2026-08-{d:02d} 03:03:00", notes, level, horizon=horizon) for d in range(20, 30)]


def test_l1_collapse_fires_on_the_degraded_first_scan():
    rows = _priors() + [run("2026-09-07 03:03:55", VAL, 150)]
    fires = l1_collapse_runs(rows)
    assert len(fires) == 1 and fires[0]["quality_passed"] == 150
    assert fires[0]["baseline_l1"] == 410


def test_same_day_degraded_scan_does_not_poison_a_later_healthy_scan():
    rows = _priors() + [run("2026-09-07 03:03:55", VAL, 150),
                        run("2026-09-07 04:03:23", VAL, 411)]
    fired = [r["run_time"] for r in l1_collapse_runs(rows)]
    assert fired == ["2026-09-07 03:03:55"]


def test_ranking_rows_never_enter_the_validation_baseline():
    """The mixed-column trap: ~1,500 ranking-engine passes would make a normal
    ~400 validation scan look like a collapse if pooled."""
    rows = _priors() + [run(f"2026-08-{d:02d} 03:02:00", RANK, 1500) for d in range(20, 30)]
    rows.append(run("2026-09-08 03:06:40", VAL, 400))
    assert l1_collapse_runs(rows) == []


def test_needs_enough_history_and_is_per_horizon():
    few = [run(f"2026-08-{d:02d} 03:03:00", VAL, 410) for d in range(20, 23)]
    assert l1_collapse_runs(few + [run("2026-09-07 03:03:55", VAL, 10)]) == []
    lt_priors = _priors(level=410, horizon="LONGTERM")
    swing_scan = run("2026-09-07 03:03:55", VAL, 150, horizon="SWING")
    assert l1_collapse_runs(lt_priors + [swing_scan]) == []


# ── next scan ────────────────────────────────────────────────────────────────

def test_next_scan_skips_the_weekend():
    assert next_scan_after(ist(2026, 9, 11, 17, 0)) == ist(2026, 9, 14, 8, 30)   # Fri -> Mon
    assert next_scan_after(ist(2026, 9, 13, 10, 30)) == ist(2026, 9, 14, 8, 30)  # Sun -> Mon
    assert next_scan_after(ist(2026, 9, 14, 7, 0)) == ist(2026, 9, 14, 8, 30)    # Mon before
    assert next_scan_after(ist(2026, 9, 14, 9, 0)) == ist(2026, 9, 15, 8, 30)    # Mon after


# ── snapshot verdict ─────────────────────────────────────────────────────────

def _usable(expires_at):
    return {"enabled": True, "available": True, "usable": True, "day": "2026-09-11",
            "shards": 9, "shards_alive": 9, "symbols": 2142, "expires_at": expires_at}


def test_manifest_without_shards_is_critical():
    """The exact 2026-09-07 state: manifest alive, every price shard expired."""
    status = {"enabled": True, "available": True, "usable": False, "day": "2026-09-04",
              "shards": 9, "shards_alive": 0}
    level, message = snapshot_verdict(status, ist(2026, 9, 7, 8, 0))
    assert level == CRITICAL and "0/9" in message


def test_friday_snapshot_with_50h_shards_cannot_reach_monday():
    friday_publish = ist(2026, 9, 11, 16, 0)
    expires = (friday_publish + timedelta(hours=50)).timestamp()      # Sun 18:00 IST
    level, _ = snapshot_verdict(_usable(expires), ist(2026, 9, 11, 17, 0))
    assert level == CRITICAL


def test_friday_snapshot_with_96h_shards_reaches_monday():
    friday_publish = ist(2026, 9, 11, 16, 0)
    expires = (friday_publish + timedelta(hours=96)).timestamp()      # Tue 16:00 IST
    level, _ = snapshot_verdict(_usable(expires), ist(2026, 9, 11, 17, 0))
    assert level == OK


def test_unseeable_or_legacy_snapshot_is_unknown_not_ok():
    assert snapshot_verdict(None, ist(2026, 9, 13, 10, 30))[0] == UNKNOWN
    legacy = {"available": True, "day": "2026-09-12", "shards": 9}   # endpoint without `usable`
    assert snapshot_verdict(legacy, ist(2026, 9, 13, 10, 30))[0] == UNKNOWN
    assert snapshot_verdict({"available": False, "reason": "no_snapshot"},
                            ist(2026, 9, 13, 10, 30))[0] == CRITICAL
    assert snapshot_verdict({"enabled": False}, ist(2026, 9, 13, 10, 30))[0] == OK


def test_exit_code_prefers_critical_then_unknown():
    assert exit_code([OK, OK]) == 0
    assert exit_code([OK, UNKNOWN]) == 2
    assert exit_code([UNKNOWN, CRITICAL]) == 1
