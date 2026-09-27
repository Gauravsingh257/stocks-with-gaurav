"""Sector rotation API (Phase 2): payload shaping, freshness, flags, routes."""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="sector_api_"))

import pytest  # noqa: E402

from services import sector_rotation_view as view  # noqa: E402


def _row(date, entity, kind="sector", **kw):
    base = {"date": date, "timeframe": "D", "entity": entity, "kind": kind, "as_of": date, "partial": 0,
            "n_constituents": 20, "n_core": 15, "provider_share": 0.25, "confidence": "high",
            "ret_bar": 0.1, "ret_lookback": 2.0, "rs_ratio": 101.0, "rs_momentum": 100.5, "rs_accel": 0.3,
            "quadrant": "leading", "rs_ratio_core": 100.8, "rs_ratio_mkt": 99.0, "rs_momentum_mkt": 100.0,
            "quadrant_mkt": "improving", "pct_above_20": 55.0, "pct_above_50": 60.0, "pct_above_200": 70.0,
            "new_highs_52w": 3, "new_lows_52w": 1, "n_52w_eligible": 18, "vol_ratio": 1.1,
            "up_turnover_pct": 58.0, "dispersion": 9.0, "calc_version": "sr-1.1", "source_mode": "backfill",
            "written_at": "2026-09-26 20:26:40"}
    base.update(kw)
    return base


DATES = ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22",
         "2026-09-23", "2026-09-24", "2026-09-25"]


def _rows():
    out = []
    for i, d in enumerate(DATES):
        out.append(_row(d, "IT", rs_ratio_mkt=98.0 + i * 0.1, rs_momentum_mkt=100.0 + i * 0.2))
        out.append(_row(d, "Tiny", confidence="insufficient", n_constituents=2))
        out.append(_row(d, "ALL EQUITIES", kind="market", ret_lookback=1.0, rs_ratio_mkt=None,
                        rs_momentum_mkt=None, quadrant_mkt=None))
    return out


NOW = datetime(2026, 9, 27, 2, 0, tzinfo=UTC)  # Sunday 07:30 IST


def test_market_benchmark_maps_mkt_columns_and_trail():
    p = view.build_rotation_payload(_rows(), tf="D", bench="market", trail=5, now=NOW)
    it = next(s for s in p["sectors"] if s["sector"] == "IT")
    assert p["benchmark"]["key"] == "market" and p["as_of"] == "2026-09-25"
    assert it["rs_ratio"] == pytest.approx(98.8) and it["quadrant"] == "improving"
    assert [t["date"] for t in it["trail"]] == DATES[-5:]
    assert it["trail"][-1]["rs_momentum"] == pytest.approx(101.6)
    # acceleration vs market = stored momentum_mkt[t] - momentum_mkt[t-5]
    assert it["rs_accel"] == pytest.approx(1.6 - 0.6)
    assert it["rs_ratio_core"] is None  # core RS exists only vs NIFTY 50
    assert it["ret_vs_market"] == pytest.approx(1.0)


def test_nifty_benchmark_uses_stored_columns_verbatim():
    p = view.build_rotation_payload(_rows(), tf="D", bench="nifty50", trail=10, now=NOW)
    it = next(s for s in p["sectors"] if s["sector"] == "IT")
    assert (it["rs_ratio"], it["rs_momentum"], it["rs_accel"], it["quadrant"]) == (101.0, 100.5, 0.3, "leading")
    assert it["rs_ratio_core"] == 100.8 and p["trail_length"] == len(DATES)


def test_insufficient_sectors_are_not_plottable_and_market_is_separate():
    p = view.build_rotation_payload(_rows(), tf="D", bench="market", trail=5, now=NOW)
    names = {s["sector"] for s in p["sectors"]}
    assert "ALL EQUITIES" not in names and p["market"]["sector"] == "ALL EQUITIES"
    tiny = next(s for s in p["sectors"] if s["sector"] == "Tiny")
    assert tiny["plottable"] is False
    assert sum(p["quadrant_counts"].values()) == 1


def test_unknown_benchmark_falls_back_to_market():
    p = view.build_rotation_payload(_rows(), tf="D", bench="bogus", trail=5, now=NOW)
    assert p["benchmark"]["key"] == "market"


@pytest.mark.parametrize("now,as_of,status,behind", [
    (datetime(2026, 9, 27, 2, 0, tzinfo=UTC), "2026-09-25", "fresh", 0),     # Sunday
    (datetime(2026, 9, 28, 6, 0, tzinfo=UTC), "2026-09-25", "fresh", 0),     # Mon 11:30 IST, before ready
    (datetime(2026, 9, 28, 11, 30, tzinfo=UTC), "2026-09-25", "stale", 1),   # Mon 17:00 IST, job missed
    (datetime(2026, 9, 30, 12, 0, tzinfo=UTC), "2026-09-25", "stale", 3),
])
def test_freshness(now, as_of, status, behind):
    f = view.freshness(as_of, now)
    assert f["status"] == status and f["sessions_behind"] == behind


def test_freshness_unknown_without_data():
    assert view.freshness(None, NOW)["status"] == "unknown"


def test_empty_rows_payload_is_valid():
    p = view.build_rotation_payload([], tf="W", bench="market", trail=10, now=NOW)
    assert p["sectors"] == [] and p["as_of"] is None and p["freshness"]["status"] == "unknown"


# ── routes ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import dashboard.backend.db.schema as schema

    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "t.db")
    schema.init_db()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dashboard.backend.db import sector_rotation as db
    from dashboard.backend.routes import sectors

    sectors._stamp_cache.update(at=0.0, value=None)
    store: dict = {}
    monkeypatch.setattr("dashboard.backend.cache.get", lambda k: store.get(k))
    monkeypatch.setattr("dashboard.backend.cache.set", lambda k, v, ttl_seconds=0: store.__setitem__(k, v) or True)
    db.upsert_rows(_rows(), source_mode="backfill")
    app = FastAPI()
    app.include_router(sectors.router)
    return TestClient(app), store


def test_routes_404_when_flag_off(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv("SECTOR_ROTATION_API_ENABLED", raising=False)
    assert c.get("/api/sectors/rotation").status_code == 404
    assert c.get("/api/sectors/constituents?sector=IT").status_code == 404
    monkeypatch.setenv("SECTOR_ROTATION_HOMEPAGE_ENABLED", "1")  # needs the API flag too
    st = c.get("/api/sectors/status").json()
    assert st == {"api_enabled": False, "page_public": False, "homepage_widget": False}


def test_rotation_route_serves_and_caches(client, monkeypatch):
    c, store = client
    monkeypatch.setenv("SECTOR_ROTATION_API_ENABLED", "1")
    a = c.get("/api/sectors/rotation?tf=D&benchmark=market&trail=5").json()
    b = c.get("/api/sectors/rotation?tf=D&benchmark=market&trail=5").json()
    assert a["cache"] == "miss" and b["cache"] == "hit" and len(store) == 1
    assert a["sectors"] == b["sectors"] and a["as_of"] == "2026-09-25"
    assert c.get("/api/sectors/rotation?benchmark=sp500").status_code == 422
    assert c.get("/api/sectors/rotation?trail=99").status_code == 422
    st = c.get("/api/sectors/status").json()
    assert st["api_enabled"] is True and st["latest"]["D"]["date"] == "2026-09-25"


def test_constituents_route_reads_stock_universe(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("SECTOR_ROTATION_API_ENABLED", "1")
    monkeypatch.setattr("services.universe_ohlc._get_redis", lambda: None)
    from dashboard.backend.db.universe import upsert_universe

    upsert_universe([
        {"symbol": "INFY", "company_name": "Infosys", "sector": "IT", "sector_source": "nse_official",
         "instrument": "EQUITY", "turnover_cr": 900.0},
        {"symbol": "TINYIT", "company_name": "Tiny IT", "sector": "IT", "sector_source": "provider",
         "instrument": "EQUITY", "turnover_cr": 0.2},
        {"symbol": "SUNPHARMA", "company_name": "Sun", "sector": "Pharma", "sector_source": "nse_official",
         "instrument": "EQUITY", "turnover_cr": 500.0},
    ])
    d = c.get("/api/sectors/constituents?sector=IT").json()
    assert [i["symbol"] for i in d["items"]] == ["INFY", "TINYIT"]
    assert [i["liquid"] for i in d["items"]] == [True, False]


def test_homepage_widget_flag(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("SECTOR_ROTATION_API_ENABLED", "1")
    monkeypatch.delenv("SECTOR_ROTATION_HOMEPAGE_ENABLED", raising=False)
    assert c.get("/api/sectors/status").json()["homepage_widget"] is False
    monkeypatch.setenv("SECTOR_ROTATION_HOMEPAGE_ENABLED", "1")
    assert c.get("/api/sectors/status").json()["homepage_widget"] is True
