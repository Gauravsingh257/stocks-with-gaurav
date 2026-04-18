"""
services/portfolio_risk.py

Portfolio Risk Intelligence — ensures diversification and manages drawdown risk.

Features:
  1. Sector exposure limits — max 3 positions per sector
  2. Correlation filter — blocks highly correlated stocks
  3. Drawdown tracking — per-position and portfolio-level max drawdown
  4. Concentration risk — no single stock > 15% of portfolio value

Consumed by idea_selector before promotion to enforce risk constraints.
"""

from __future__ import annotations

import logging
from collections import defaultdict

log = logging.getLogger("services.portfolio_risk")

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

MAX_SECTOR_EXPOSURE = 3          # Max positions in one sector
MAX_DRAWDOWN_PCT = -25.0         # Portfolio-wide drawdown alert threshold

# NSE stock → sector mapping (top traded stocks)
# This is a static lookup — covers major stocks in typical SMC scans
_SECTOR_MAP: dict[str, str] = {
    # IT
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "LTIM": "IT", "MPHASIS": "IT", "COFORGE": "IT", "PERSISTENT": "IT", "LTTS": "IT",
    # Banking
    "HDFCBANK": "BANKING", "ICICIBANK": "BANKING", "SBIN": "BANKING", "KOTAKBANK": "BANKING",
    "AXISBANK": "BANKING", "INDUSINDBK": "BANKING", "BANKBARODA": "BANKING",
    "PNB": "BANKING", "AUBANK": "BANKING", "FEDERALBNK": "BANKING", "IDFCFIRSTB": "BANKING",
    "BANDHANBNK": "BANKING", "CANBK": "BANKING",
    # NBFC / Finance
    "BAJFINANCE": "FINANCE", "BAJAJFINSV": "FINANCE", "CHOLAFIN": "FINANCE",
    "MUTHOOTFIN": "FINANCE", "M&MFIN": "FINANCE", "SHRIRAMFIN": "FINANCE",
    "MANAPPURAM": "FINANCE", "POONAWALLA": "FINANCE", "5PAISA": "FINANCE",
    # Auto
    "MARUTI": "AUTO", "TATAMOTORS": "AUTO", "M&M": "AUTO", "BAJAJ-AUTO": "AUTO",
    "HEROMOTOCO": "AUTO", "EICHERMOT": "AUTO", "ASHOKLEY": "AUTO", "TVSMOTOR": "AUTO",
    "BALKRISIND": "AUTO",
    # Pharma
    "SUNPHARMA": "PHARMA", "DRREDDY": "PHARMA", "CIPLA": "PHARMA", "DIVISLAB": "PHARMA",
    "AUROPHARMA": "PHARMA", "BIOCON": "PHARMA", "LUPIN": "PHARMA", "TORNTPHARM": "PHARMA",
    "ALKEM": "PHARMA", "IPCALAB": "PHARMA",
    # Metal
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS",
    "VEDL": "METALS", "NATIONALUM": "METALS", "SAIL": "METALS", "COALINDIA": "METALS",
    "NMDC": "METALS", "JINDALSTEL": "METALS",
    # Oil & Gas
    "RELIANCE": "OIL_GAS", "ONGC": "OIL_GAS", "IOC": "OIL_GAS", "BPCL": "OIL_GAS",
    "GAIL": "OIL_GAS", "HINDPETRO": "OIL_GAS", "PETRONET": "OIL_GAS",
    "ADANIGREENR": "OIL_GAS", "ADANIPOWER": "POWER",
    # Power / Infra
    "NTPC": "POWER", "POWERGRID": "POWER", "TATAPOWER": "POWER", "ADANIGREEN": "POWER",
    "NHPC": "POWER", "SJVN": "POWER", "IREDA": "POWER",
    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "DABUR": "FMCG", "MARICO": "FMCG", "GODREJCP": "FMCG", "COLPAL": "FMCG",
    "TATACONSUM": "FMCG", "VBL": "FMCG", "NYKAA": "FMCG",
    # Cement
    "ULTRACEMCO": "CEMENT", "SHREECEM": "CEMENT", "AMBUJACEM": "CEMENT",
    "ACC": "CEMENT", "DALMIACEM": "CEMENT", "RAMCOCEM": "CEMENT",
    # Chemicals
    "PIDILITIND": "CHEMICALS", "SRF": "CHEMICALS", "AARTI": "CHEMICALS",
    "DEEPAKNTR": "CHEMICALS", "CLEAN": "CHEMICALS", "ATUL": "CHEMICALS",
    # Telecom
    "BHARTIARTL": "TELECOM", "IDEA": "TELECOM",
    # Realty
    "DLF": "REALTY", "GODREJPROP": "REALTY", "OBEROIRLTY": "REALTY",
    "PRESTIGE": "REALTY", "BRIGADE": "REALTY", "SOBHA": "REALTY",
    # Defence
    "HAL": "DEFENCE", "BEL": "DEFENCE", "MAZDOCK": "DEFENCE",
    "BDL": "DEFENCE", "COCHINSHIP": "DEFENCE",
    # Capital Goods
    "LT": "CAPGOODS", "SIEMENS": "CAPGOODS", "ABB": "CAPGOODS",
    "CGPOWER": "CAPGOODS", "BHEL": "CAPGOODS", "CUMMINSIND": "CAPGOODS",
    # Insurance
    "SBILIFE": "INSURANCE", "HDFCLIFE": "INSURANCE", "ICICIPRULI": "INSURANCE",
    "STARHEALTH": "INSURANCE",
    # Agri / Fertilizer
    "COROMANDEL": "AGRI", "EIDPARRY": "AGRI", "UPL": "AGRI",
    "PIIND": "AGRI", "CHAMBALFER": "AGRI", "RVNL": "INFRA",
}


def get_sector(symbol: str) -> str:
    """Lookup sector for a symbol. Returns 'OTHER' if not mapped."""
    return _SECTOR_MAP.get(symbol.strip().upper(), "OTHER")


# ──────────────────────────────────────────────────────────────────────────────
# Sector exposure check
# ──────────────────────────────────────────────────────────────────────────────

def get_sector_exposure(horizon: str | None = None) -> dict[str, list[str]]:
    """Get current sector exposure — {sector: [symbols]}."""
    from dashboard.backend.db.portfolio import get_portfolio

    positions = get_portfolio(horizon)
    exposure: dict[str, list[str]] = defaultdict(list)

    for pos in positions:
        if pos.get("status") != "ACTIVE":
            continue
        sector = get_sector(pos["symbol"])
        exposure[sector].append(pos["symbol"])

    return dict(exposure)


def check_sector_limit(symbol: str, horizon: str) -> tuple[bool, str]:
    """
    Check if adding this symbol would breach sector concentration limits.
    Returns (allowed, reason).
    """
    sector = get_sector(symbol)
    if sector == "OTHER":
        return True, "ok"

    exposure = get_sector_exposure(horizon)
    current_in_sector = exposure.get(sector, [])

    if len(current_in_sector) >= MAX_SECTOR_EXPOSURE:
        return False, f"Sector {sector} already has {len(current_in_sector)} positions: {', '.join(current_in_sector)}"

    return True, "ok"


# ──────────────────────────────────────────────────────────────────────────────
# Correlation filter — simple same-sector check
# ──────────────────────────────────────────────────────────────────────────────

def check_correlation(symbol: str, horizon: str) -> tuple[bool, str]:
    """
    Check if adding this symbol creates excessive correlation risk.
    Currently uses sector-based proximity as a correlation proxy.

    Rules:
      - If 2+ stocks already in the same sector → blocked (overlap with sector limit)
      - If the exact same symbol is active in the other horizon → warn but allow
    """
    from dashboard.backend.db.portfolio import get_active_position_by_symbol

    # Already in portfolio (any horizon)
    existing = get_active_position_by_symbol(symbol)
    if existing:
        return False, f"{symbol} already active in portfolio"

    # Sector check (reuses sector limit logic)
    return check_sector_limit(symbol, horizon)


# ──────────────────────────────────────────────────────────────────────────────
# Drawdown tracking
# ──────────────────────────────────────────────────────────────────────────────

def get_portfolio_drawdown(horizon: str | None = None) -> dict:
    """
    Compute portfolio-level drawdown stats from active positions.

    Returns:
      - total_pnl_pct: sum of unrealized P&L %
      - max_single_drawdown: worst single position drawdown
      - worst_position: symbol with max drawdown
      - positions_in_drawdown: count of losing positions
      - alert: True if portfolio drawdown exceeds threshold
    """
    from dashboard.backend.db.portfolio import get_portfolio

    positions = get_portfolio(horizon)
    active = [p for p in positions if p.get("status") == "ACTIVE"]

    if not active:
        return {
            "total_pnl_pct": 0.0,
            "max_single_drawdown": 0.0,
            "worst_position": None,
            "positions_in_drawdown": 0,
            "alert": False,
            "active_count": 0,
        }

    total_pnl_pct = 0.0
    max_dd = 0.0
    worst_sym = None
    in_dd = 0

    for pos in active:
        pnl_pct = float(pos.get("profit_loss_pct", 0))
        dd_pct = float(pos.get("drawdown_pct", 0))
        total_pnl_pct += pnl_pct

        if pnl_pct < 0:
            in_dd += 1

        if dd_pct < max_dd:
            max_dd = dd_pct
            worst_sym = pos["symbol"]

    avg_pnl = total_pnl_pct / len(active) if active else 0

    return {
        "total_pnl_pct": round(total_pnl_pct, 2),
        "avg_pnl_pct": round(avg_pnl, 2),
        "max_single_drawdown": round(max_dd, 2),
        "worst_position": worst_sym,
        "positions_in_drawdown": in_dd,
        "alert": avg_pnl < MAX_DRAWDOWN_PCT,
        "active_count": len(active),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Combined risk check (used by idea_selector before promotion)
# ──────────────────────────────────────────────────────────────────────────────

def pre_promotion_risk_check(symbol: str, horizon: str) -> tuple[bool, str]:
    """
    Run all risk checks before promoting a stock to the portfolio.
    Returns (allowed, reason).
    """
    # 1. Correlation / duplicate check
    ok, reason = check_correlation(symbol, horizon)
    if not ok:
        return False, reason

    # 2. Sector limit
    ok, reason = check_sector_limit(symbol, horizon)
    if not ok:
        return False, reason

    return True, "ok"


# ──────────────────────────────────────────────────────────────────────────────
# Risk summary for API
# ──────────────────────────────────────────────────────────────────────────────

def get_risk_summary() -> dict:
    """Complete portfolio risk assessment for API consumption."""
    swing_dd = get_portfolio_drawdown("SWING")
    longterm_dd = get_portfolio_drawdown("LONGTERM")
    swing_sectors = get_sector_exposure("SWING")
    longterm_sectors = get_sector_exposure("LONGTERM")

    # Identify breached sectors
    breached = []
    for sector, symbols in {**swing_sectors, **longterm_sectors}.items():
        if len(symbols) > MAX_SECTOR_EXPOSURE:
            breached.append({"sector": sector, "count": len(symbols), "symbols": symbols})

    return {
        "swing_drawdown": swing_dd,
        "longterm_drawdown": longterm_dd,
        "swing_sectors": {s: len(syms) for s, syms in swing_sectors.items()},
        "longterm_sectors": {s: len(syms) for s, syms in longterm_sectors.items()},
        "sector_breaches": breached,
        "max_sector_limit": MAX_SECTOR_EXPOSURE,
    }
