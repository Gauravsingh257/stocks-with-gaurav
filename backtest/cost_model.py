"""
backtest/cost_model.py — Transaction Cost Model (F3.4)
======================================================
Models Zerodha brokerage, STT, exchange charges, GST, and slippage
for realistic P&L calculation.

Indian market cost structure (equity options — main use case):
  - Brokerage: ₹20 per executed order (buy + sell = ₹40 round trip)
  - STT: 0.0625% on sell side (options)
  - Exchange txn: 0.053% (NSE options)
  - GST: 18% on (brokerage + exchange txn)
  - SEBI charges: ₹10 per crore
  - Stamp duty: 0.003% on buy side (options)
  - Slippage: configurable points/percentage

For equity intraday:
  - Brokerage: ₹20 per order or 0.03% (whichever is lower)
  - STT: 0.025% on sell side
  - Exchange txn: 0.00345%
  - GST: 18% on (brokerage + exchange txn)
  - SEBI: ₹10 per crore
  - Stamp duty: 0.003% on buy side
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class CostConfig:
    """Transaction cost configuration."""
    # Brokerage
    brokerage_per_order: float = 20.0       # ₹20 flat per order
    max_brokerage_pct: float = 0.03         # 0.03% cap (equity intraday)

    # STT (Securities Transaction Tax)
    stt_sell_pct: float = 0.0625            # Options sell side
    stt_buy_pct: float = 0.0               # Options buy side (0 for options)

    # Exchange transaction charges
    exchange_txn_pct: float = 0.053         # NSE options

    # GST on brokerage + exchange charges
    gst_pct: float = 18.0                   # 18% GST

    # SEBI turnover charges
    sebi_per_crore: float = 10.0            # ₹10 per ₹1 crore turnover

    # Stamp duty
    stamp_duty_buy_pct: float = 0.003       # On buy side

    # Slippage
    slippage_index_pts: float = 3.0         # Points for NIFTY/BANKNIFTY
    slippage_stock_pct: float = 0.05        # 0.05% for stocks


# Preset configurations
OPTIONS_COST = CostConfig(
    stt_sell_pct=0.0625,
    stt_buy_pct=0.0,
    exchange_txn_pct=0.053,
    stamp_duty_buy_pct=0.003,
)

EQUITY_INTRADAY_COST = CostConfig(
    stt_sell_pct=0.025,
    stt_buy_pct=0.0,
    exchange_txn_pct=0.00345,
    stamp_duty_buy_pct=0.003,
)

INDEX_OPTIONS_COST = CostConfig(
    stt_sell_pct=0.0625,
    stt_buy_pct=0.0,
    exchange_txn_pct=0.053,
    stamp_duty_buy_pct=0.003,
    slippage_index_pts=5.0,  # Wider slippage for index options
)


def calculate_slippage(price: float, direction: str, is_index: bool,
                       config: CostConfig = OPTIONS_COST) -> float:
    """
    Calculate slippage-adjusted entry/exit price.

    For entries: slippage works AGAINST you (buy higher, sell lower).
    For exits: same principle.

    Returns the slippage amount (always positive).
    """
    if is_index:
        return config.slippage_index_pts
    else:
        return price * (config.slippage_stock_pct / 100.0)


def calculate_round_trip_cost(entry_price: float, exit_price: float,
                              quantity: int, is_index: bool = True,
                              config: Optional[CostConfig] = None) -> dict:
    """
    Calculate total round-trip transaction costs.

    Args:
        entry_price: Buy/sell entry price
        exit_price: Exit price
        quantity: Number of lots/shares
        is_index: Whether this is an index trade
        config: Cost configuration (default: OPTIONS_COST)

    Returns:
        dict with breakdown: {brokerage, stt, exchange, gst, sebi, stamp, slippage, total}
    """
    if config is None:
        config = INDEX_OPTIONS_COST if is_index else EQUITY_INTRADAY_COST

    buy_value = entry_price * quantity
    sell_value = exit_price * quantity
    turnover = buy_value + sell_value

    # Brokerage (buy + sell)
    brokerage_buy = min(config.brokerage_per_order,
                        buy_value * config.max_brokerage_pct / 100.0)
    brokerage_sell = min(config.brokerage_per_order,
                         sell_value * config.max_brokerage_pct / 100.0)
    brokerage = brokerage_buy + brokerage_sell

    # STT
    stt = (buy_value * config.stt_buy_pct / 100.0 +
           sell_value * config.stt_sell_pct / 100.0)

    # Exchange transaction charges
    exchange = turnover * config.exchange_txn_pct / 100.0

    # GST (on brokerage + exchange charges)
    gst = (brokerage + exchange) * config.gst_pct / 100.0

    # SEBI charges
    sebi = turnover * config.sebi_per_crore / 1e7  # per crore = per 10^7

    # Stamp duty (buy side only)
    stamp = buy_value * config.stamp_duty_buy_pct / 100.0

    # Slippage (entry + exit)
    slip_per_unit = calculate_slippage(entry_price, "LONG", is_index, config)
    slippage = slip_per_unit * quantity * 2  # round trip

    total = brokerage + stt + exchange + gst + sebi + stamp + slippage

    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange": round(exchange, 2),
        "gst": round(gst, 2),
        "sebi": round(sebi, 2),
        "stamp": round(stamp, 2),
        "slippage": round(slippage, 2),
        "total": round(total, 2),
    }


def cost_as_points(entry_price: float, exit_price: float,
                   is_index: bool = True,
                   config: Optional[CostConfig] = None) -> float:
    """
    Return total round-trip cost expressed as points (per unit).
    Useful for quick R-multiple adjustment.
    """
    costs = calculate_round_trip_cost(entry_price, exit_price, 1, is_index, config)
    return costs["total"]


def adjust_pnl_for_costs(gross_pnl_points: float, entry_price: float,
                         exit_price: float, is_index: bool = True,
                         config: Optional[CostConfig] = None) -> float:
    """Return net P&L after subtracting transaction costs (in points per unit)."""
    cost = cost_as_points(entry_price, exit_price, is_index, config)
    return gross_pnl_points - cost
