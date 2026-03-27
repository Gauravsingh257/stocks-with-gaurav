"""
dashboard/backend/routes/market_intelligence.py
Market Intelligence API — holidays, FX, FRED macro, MF flows, QuickChart.

GET /api/market-intelligence/snapshot   — Full composite snapshot
GET /api/market-intelligence/holidays   — Indian public holidays for current year
GET /api/market-intelligence/macro      — FRED macro indicators
GET /api/market-intelligence/fx         — USD/INR exchange rate
GET /api/market-intelligence/mf-flows   — Indian MF NAV data (flow proxy)
GET /api/market-intelligence/chart-url  — Generate QuickChart image URL
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/market-intelligence", tags=["market-intelligence"])
log = logging.getLogger("dashboard.market_intelligence")


@router.get("/snapshot")
def get_snapshot():
    """Full Market Intelligence snapshot — holidays, FX, macro, MF flows."""
    from services.market_intelligence import get_market_intel_snapshot
    snap = get_market_intel_snapshot()
    return snap.as_dict()


@router.get("/holidays")
def get_holidays(year: Optional[int] = Query(None, description="Year (default: current)")):
    """Indian public holidays from Nager.Date API."""
    from services.market_intelligence import fetch_holidays, is_holiday_today, next_holiday
    holidays = fetch_holidays(year)
    nxt = next_holiday()
    return {
        "holidays": [h.as_dict() for h in holidays],
        "is_holiday_today": is_holiday_today(),
        "next_holiday": nxt.as_dict() if nxt else None,
        "count": len(holidays),
    }


@router.get("/macro")
def get_macro():
    """US macro indicators from FRED (Fed Funds, 10Y, DXY, CPI)."""
    from services.market_intelligence import fetch_fred_macro
    macro = fetch_fred_macro()
    return macro.as_dict()


@router.get("/fx")
def get_fx():
    """USD/INR exchange rate from Frankfurter API."""
    from services.market_intelligence import fetch_usd_inr
    fx = fetch_usd_inr()
    return fx.as_dict()


@router.get("/mf-flows")
def get_mf_flows():
    """Indian Mutual Fund NAV data as flow proxy."""
    from services.market_intelligence import fetch_mf_flows
    mf = fetch_mf_flows()
    return mf.as_dict()


@router.get("/chart-url")
def get_chart_url(
    chart_type: str = Query("line", description="Chart type: line, bar, pie, doughnut"),
    title: str = Query("", description="Chart title"),
    width: int = Query(600, ge=100, le=1200),
    height: int = Query(300, ge=100, le=800),
):
    """
    Generate a QuickChart image URL.
    Pass chart data as query params; returns a URL to embed.
    For full chart configs, use the frontend QuickChart integration directly.
    """
    from services.market_intelligence import generate_chart_url
    url = generate_chart_url(
        chart_type=chart_type,
        labels=["Sample"],
        datasets=[{"label": "Data", "data": [0]}],
        title=title,
        width=width,
        height=height,
    )
    return {"chart_url": url, "note": "Pass full chart config from frontend for real charts"}
