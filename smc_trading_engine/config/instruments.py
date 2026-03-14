"""
Instruments Configuration
Defines trading universe and instrument properties
"""

from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class Instrument:
    """Instrument definition"""
    symbol: str
    exchange: str
    instrument_type: str  # "NSE", "NFO", "MCX", etc.
    lot_size: int = 1
    tick_size: float = 0.05
    category: str = "stock"  # "stock", "index", "option", "future"
    
    @property
    def full_symbol(self) -> str:
        return f"{self.exchange}:{self.symbol}"


# Core Trading Universe (NSE stocks used for testing)
INSTRUMENTS: Dict[str, Instrument] = {
    # Indices
    "NIFTY 50": Instrument("NIFTY 50", "NSE", "INDEX", tick_size=0.05, category="index"),
    "NIFTY BANK": Instrument("NIFTY BANK", "NSE", "INDEX", tick_size=0.05, category="index"),
    "NIFTY FIN SERVICE": Instrument("NIFTY FIN SERVICE", "NSE", "INDEX", tick_size=0.05, category="index"),
    
    # Liquid Stocks
    "RELIANCE": Instrument("RELIANCE", "NSE", "STOCK", lot_size=1, tick_size=0.05),
    "HDFC": Instrument("HDFC", "NSE", "STOCK", lot_size=1, tick_size=1.0),
    "INFY": Instrument("INFY", "NSE", "STOCK", lot_size=1, tick_size=0.05),
    "TCS": Instrument("TCS", "NSE", "STOCK", lot_size=1, tick_size=0.05),
    "ICICIBANK": Instrument("ICICIBANK", "NSE", "STOCK", lot_size=1, tick_size=0.05),
    
    # Mid-Cap
    "ASHOKLEY": Instrument("ASHOKLEY", "NSE", "STOCK", lot_size=1, tick_size=0.05),
    "EQUITASBNK": Instrument("EQUITASBNK", "NSE", "STOCK", lot_size=1, tick_size=0.05),
    "BECTORFOOD": Instrument("BECTORFOOD", "NSE", "STOCK", lot_size=1, tick_size=0.05),
}


def get_instrument(symbol: str, exchange: str = "NSE") -> Optional[Instrument]:
    """
    Get instrument definition by symbol
    
    Args:
        symbol: Symbol name (e.g., "RELIANCE")
        exchange: Exchange (default: "NSE")
    
    Returns:
        Instrument object or None if not found
    """
    key = symbol.upper()
    if key in INSTRUMENTS:
        return INSTRUMENTS[key]
    return None


def get_instruments_by_category(category: str) -> Dict[str, Instrument]:
    """
    Get all instruments of a specific category
    
    Args:
        category: Category name ("stock", "index", "option", "future")
    
    Returns:
        Dictionary of instruments matching the category
    """
    return {k: v for k, v in INSTRUMENTS.items() if v.category == category}


def add_instrument(symbol: str, instrument: Instrument) -> None:
    """Add a new instrument to the universe"""
    INSTRUMENTS[symbol.upper()] = instrument


def remove_instrument(symbol: str) -> bool:
    """Remove an instrument from the universe"""
    symbol = symbol.upper()
    if symbol in INSTRUMENTS:
        del INSTRUMENTS[symbol]
        return True
    return False
