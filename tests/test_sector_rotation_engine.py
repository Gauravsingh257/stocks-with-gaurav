"""Sector rotation engine (Phase 1) — synthetic data with known answers."""
from __future__ import annotations

import math
import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="sector_rot_"))

import pytest  # noqa: E402

from services import sector_rotation_engine as eng  # noqa: E402

VOL = 1_000_000  # x price 100 = ₹10 Cr turnover: eligible


def _dates(n, start=date(2025, 1, 6)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(dates, closes, vol=VOL):
    return [{"date": d, "open": c, "high": c, "low": c, "close": c, "volume": vol}
            for d, c in zip(dates, closes, strict=False) if c is not None]


def _geo(n, start, g):
    return [start * (1 + g) ** i for i in range(n)]


def _world(n=300):
    dates = _dates(n)
    idx = {"NIFTY 50": _bars(dates, [100.0] * n, 0),
           "NIFTY IT": _bars(dates, _geo(n, 100, 0.001), 0)}
    stocks = {
        "UP1": _bars(dates, _geo(n, 100, 0.002)), "UP2": _bars(dates, _geo(n, 100, 0.002)),
        "UP3": _bars(dates, _geo(n, 100, 0.002)),
        "DN1": _bars(dates, _geo(n, 100, -0.001)), "DN2": _bars(dates, _geo(n, 100, -0.001)),
        "DN3": _bars(dates, _geo(n, 100, -0.001)),
    }
    members = {"UP1": ("Up", "nse_official"), "UP2": ("Up", "nse_official"), "UP3": ("Up", "provider"),
               "DN1": ("Down", "manual"), "DN2": ("Down", "manual"), "DN3": ("Down", "manual")}
    return dates, stocks, idx, members


def _row(rows, entity, d, tf="D"):
    return next(r for r in rows if r["entity"] == entity and r["date"] == d and r["timeframe"] == tf)


def test_leading_and_lagging_sectors_and_breadth():
    dates, stocks, idx, members = _world()
    rows = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    up, dn = _row(rows, "Up", dates[-1]), _row(rows, "Down", dates[-1])
    assert up["pct_above_50"] == 100.0 and up["pct_above_200"] == 100.0
    assert dn["pct_above_50"] == 0.0
    assert up["ret_bar"] == pytest.approx(0.2) and dn["ret_bar"] == pytest.approx(-0.1)
    assert up["rs_ratio"] > 100 > dn["rs_ratio"]
    assert up["new_highs_52w"] == 3 and dn["new_lows_52w"] == 3
    assert up["provider_share"] == pytest.approx(1 / 3) and up["n_core"] == 2
    # constant-growth sectors: momentum of a steady ratio is ~flat
    assert up["rs_momentum"] == pytest.approx(100, abs=0.05)
    assert up["dispersion"] is None  # < 5 constituents
    assert up["vol_ratio"] == pytest.approx(1.0) and up["up_turnover_pct"] == 100.0


def test_equal_weight_return_is_mean_of_constituents():
    dates, stocks, idx, members = _world(120)
    stocks["UP3"] = _bars(dates, _geo(120, 100, 0.005))
    rows = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    assert _row(rows, "Up", dates[-1])["ret_bar"] == pytest.approx((0.2 + 0.2 + 0.5) / 3, rel=1e-6)


def test_illiquid_stock_is_not_eligible():
    dates, stocks, idx, members = _world(120)
    stocks["UP4"] = _bars(dates, _geo(120, 100, 0.05), vol=10)  # ₹1,000/day, rockets +5%/day
    members["UP4"] = ("Up", "nse_official")
    r = _row(eng.compute_sector_rotation(stocks, idx, members)["rows"], "Up", dates[-1])
    assert r["n_constituents"] == 3 and r["ret_bar"] == pytest.approx(0.2)
    # a sector that never reaches MIN_CONSTITUENTS emits no rows at all
    members = {k: v for k, v in members.items() if k in ("UP1", "UP2", "UP4")}
    rows = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    assert not any(x["entity"] == "Up" for x in rows)


def test_missing_bar_earns_no_fake_return():
    dates, stocks, idx, members = _world(120)
    closes = _geo(120, 100, 0.002)
    closes[-1] = None
    stocks["UP1"] = _bars(dates, closes)
    stocks["UP4"] = _bars(dates, _geo(120, 100, 0.002))
    members["UP4"] = ("Up", "nse_official")
    r = _row(eng.compute_sector_rotation(stocks, idx, members)["rows"], "Up", dates[-1])
    assert r["n_constituents"] == 3
    assert r["ret_bar"] == pytest.approx(0.2)
    # below MIN_CONSTITUENTS on a day: no return, level carried flat
    members.pop("UP4")
    rows = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    r2, prev = _row(rows, "Up", dates[-1]), _row(rows, "Up", dates[-2])
    assert r2["ret_bar"] is None and r2["level"] == prev["level"] and r2["confidence"] == "insufficient"


def test_unadjusted_break_drops_prior_history():
    dates, stocks, idx, members = _world(300)
    closes = _geo(300, 100, 0.002)
    closes = closes[:250] + [c * 0.4 for c in closes[250:]]  # 60% demerger gap at bar 250
    stocks["UP1"] = _bars(dates, closes)
    res = eng.compute_sector_rotation(stocks, idx, members)
    assert res["meta"]["breaks_dropped"] == 1
    r = _row(res["rows"], "Up", dates[-1])
    assert r["n_52w_eligible"] == 2  # UP1 has only 50 post-break bars
    assert r["pct_above_200"] == 100.0  # computed on UP2/UP3 only, not distorted by the gap


def test_index_aligned_to_benchmark_calendar_and_flagged():
    dates, stocks, idx, members = _world(120)
    idx["NIFTY IT"] = [b for b in idx["NIFTY IT"] if b["date"] != dates[100]]
    res = eng.compute_sector_rotation(stocks, idx, members)
    filled = _row(res["rows"], "NIFTY IT", dates[100])
    prev = _row(res["rows"], "NIFTY IT", dates[99])
    assert filled["is_filled"] is True and filled["level"] == prev["level"]
    assert res["meta"]["index_filled_days"]["NIFTY IT"] == 1
    assert filled["confidence"] == "reference"


def test_weekly_rows_keyed_by_friday_and_partial_flag():
    dates, stocks, idx, members = _world(123)  # ends mid-week
    rows = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    wk = [r for r in rows if r["entity"] == "Up" and r["timeframe"] == "W"]
    assert all(date.fromisoformat(r["date"]).weekday() == 4 for r in wk)
    last = max(wk, key=lambda r: r["date"])
    assert last["partial"] is True and last["as_of"] == dates[-1]
    assert sum(r["partial"] for r in wk) == 1
    assert _row(rows, "Up", last["as_of"])["level"] == last["level"]


def test_market_relative_rs_removes_common_trend():
    dates, stocks, idx, members = _world()
    rows = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    up = _row(rows, "Up", dates[-1])
    mkt = _row(rows, eng.MARKET_ENTITY, dates[-1])
    assert mkt["rs_ratio_mkt"] is None and up["rs_ratio_mkt"] > 100
    assert up["quadrant_mkt"] in ("leading", "weakening")


def test_no_nan_and_deterministic():
    dates, stocks, idx, members = _world(150)
    a = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    b = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    assert a == b
    assert not any(isinstance(v, float) and math.isnan(v) for r in a for v in r.values())


def test_unresolved_sectors_are_excluded():
    dates, stocks, idx, members = _world(120)
    members["UP3"] = ("Unknown", "")
    members["DN3"] = ("Unassigned", "")
    res = eng.compute_sector_rotation(stocks, idx, members)
    assert not any(r["entity"] in ("Unknown", "Unassigned") for r in res["rows"])
    assert res["meta"]["members_given"] == 4
    assert _row(res["rows"], eng.MARKET_ENTITY, dates[-1])["n_constituents"] == 4


def test_missing_benchmark_raises():
    dates, stocks, idx, members = _world(60)
    idx.pop("NIFTY 50")
    with pytest.raises(ValueError):
        eng.compute_sector_rotation(stocks, idx, members)


def test_quadrant_and_confidence_rules():
    assert eng.quadrant(101, 101) == "leading" and eng.quadrant(101, 99) == "weakening"
    assert eng.quadrant(99, 99) == "lagging" and eng.quadrant(99, 101) == "improving"
    assert eng.quadrant(None, 100) is None
    assert eng.confidence(20, 0.2) == "high" and eng.confidence(20, 0.6) == "medium"
    assert eng.confidence(10, 0.9) == "low" and eng.confidence(2, 0.0) == "insufficient"


def test_storage_incremental_policy(tmp_path, monkeypatch):
    import dashboard.backend.db.schema as schema

    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "t.db")
    from dashboard.backend.db import sector_rotation as db
    from scripts.build_sector_rotation import select_rows

    dates, stocks, idx, members = _world(120)
    rows = eng.compute_sector_rotation(stocks, idx, members)["rows"]
    older = [r for r in rows if r["date"] < dates[-5]]
    assert db.upsert_rows(older, source_mode="backfill") == len(older)
    new = select_rows(rows, latest_d=db.latest_date("D"), latest_w=db.latest_date("W"))
    assert {r["date"] for r in new if r["timeframe"] == "D"} == set(dates[-5:])
    assert min(r["date"] for r in new if r["timeframe"] == "W") == db.latest_date("W")
    db.upsert_rows(new, source_mode="live")
    s = db.summary()
    assert s["D:live"]["n"] == len([r for r in new if r["timeframe"] == "D"])
    back = db.read_rows(timeframe="D", date=dates[-1], entity="Up")[0]
    assert back["source_mode"] == "live" and back["calc_version"] == eng.CALC_VERSION
