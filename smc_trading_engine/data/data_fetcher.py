"""
Data Fetcher - Fetch OHLC data from Kite API
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import time as _time
import pandas as pd
try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None  # Allow module to load without kiteconnect
import logging

logger = logging.getLogger(__name__)


class DataFetcher:
    """Fetch and manage market data with rate limiting"""
    
    RATE_LIMIT_INTERVAL = 0.35  # seconds between API calls
    
    def __init__(self, kite=None, lookback_bars: int = 200):
        """
        Initialize DataFetcher
        
        Args:
            kite: KiteConnect instance (optional for backtest mode)
            lookback_bars: Default number of bars to fetch
        """
        self.kite = kite
        self.lookback_bars = lookback_bars
        self._cache: Dict[str, Dict[str, pd.DataFrame]] = {}
        self._last_api_call: float = 0
        self._token_cache: Dict[str, int] = {}
    
    def fetch_ohlc(
        self,
        symbol: str,
        interval: str = "5minute",
        lookback: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLC data for a symbol
        
        Args:
            symbol: Trading symbol (e.g., "NSE:RELIANCE")
            interval: Timeframe ("5minute", "15minute", "60minute", "day")
            lookback: Number of bars to fetch (default: self.lookback_bars)
        
        Returns:
            DataFrame with OHLC data or None if fetch failed
        """
        try:
            lookback = lookback or self.lookback_bars
            
            # Try cache first
            if symbol in self._cache and interval in self._cache[symbol]:
                return self._cache[symbol][interval]
            
            # Fetch from Kite API
            data = self.kite.historical_data(
                instrument_token=self._get_token(symbol),
                from_date=datetime.now() - timedelta(days=lookback),
                to_date=datetime.now(),
                interval=interval
            )
            
            if not data:
                logger.warning(f"No data fetched for {symbol} {interval}")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            
            # Cache it
            if symbol not in self._cache:
                self._cache[symbol] = {}
            self._cache[symbol][interval] = df
            
            return df
        
        except Exception as e:
            logger.error(f"Error fetching {symbol} {interval}: {e}")
            return None
    
    def fetch_multitf(self, symbol: str, timeframes: List[str] = None) -> Dict[str, pd.DataFrame]:
        """
        Fetch data for multiple timeframes
        
        Args:
            symbol: Trading symbol
            timeframes: List of intervals to fetch
        
        Returns:
            Dictionary with timeframe: DataFrame pairs
        """
        timeframes = timeframes or ["5minute", "15minute", "60minute"]
        result = {}
        
        for tf in timeframes:
            data = self.fetch_ohlc(symbol, tf)
            if data is not None:
                result[tf] = data
        
        return result
    
    def _get_token(self, symbol: str) -> int:
        """Get instrument token from symbol"""
        try:
            instruments = self.kite.instruments()
            for ins in instruments:
                if ins['tradingsymbol'] == symbol.split(':')[1]:
                    return ins['instrument_token']
        except Exception as e:
            logger.error(f"Error getting token for {symbol}: {e}")
        return 0
    
    def clear_cache(self, symbol: Optional[str] = None) -> None:
        """Clear cached data"""
        if symbol:
            if symbol in self._cache:
                del self._cache[symbol]
        else:
            self._cache.clear()
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get latest closing price"""
        try:
            # If using kite (live), use LTP for speed
            if self.kite:
                return self.kite.ltp(symbol)[symbol]['last_price']
            
            # Backtest fallback
            data = self.fetch_ohlc(symbol, "5minute", lookback=1)
            if data is not None and len(data) > 0:
                return float(data['close'].iloc[-1])
        except Exception as e:
            logger.error(f"Error getting latest price for {symbol}: {e}")
        return None

    # ─── OPTION CHAIN SUPPORT ─────────────────────────
    
    def fetch_instruments(self):
        """Cache instruments for option chain lookup"""
        if not self.kite:
            return
        logger.info("Fetching instruments master list...")
        self.instruments = self.kite.instruments("NFO")
        self.instruments_df = pd.DataFrame(self.instruments)
        logger.info(f"Fetched {len(self.instruments)} NFO instruments")

    def fetch_option_chain_snapshot(
        self, 
        symbol: str = "NIFTY", 
        strikes: int = 10
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OI data for NIFTY/BANKNIFTY option chain.
        Returns DataFrame expected by oi_analyzer.py
        """
        if not self.kite:
            return None
            
        try:
            # 1. Get Spot Price
            spot_sym = "NSE:NIFTY 50" if "NIFTY" in symbol else "NSE:NIFTY BANK"
            spot_price = self.get_latest_price(spot_sym)
            if not spot_price:
                logger.warning(f"Failed to get spot price for {spot_sym}")
                return None
            logger.info(f"Spot {spot_sym}: {spot_price}")
                
            # 2. Get Nearest Expiry
            if not hasattr(self, 'instruments_df'):
                self.fetch_instruments()
                
            # Filter for symbol (NIFTY/BANKNIFTY)
            df = self.instruments_df
            sym_df = df[df['name'] == symbol]
            if sym_df.empty:
                logger.warning(f"No instruments found for {symbol}")
                return None
            
            # Find nearest expiry
            today = datetime.now().date()
            expiries = sorted([d for d in sym_df['expiry'].unique() if d >= today])
            if not expiries:
                logger.warning(f"No future expiries found for {symbol}")
                return None
            nearest_expiry = expiries[0]
            logger.info(f"Selected Expiry: {nearest_expiry}")
            
            # 3. Select Strikes around ATM
            step = 50 if "NIFTY" in symbol and "BANK" not in symbol else 100
            atm = round(spot_price / step) * step
            
            selected = sym_df[
                (sym_df['expiry'] == nearest_expiry) &
                (sym_df['strike'] >= atm - (strikes * step)) &
                (sym_df['strike'] <= atm + (strikes * step))
            ]
            
            if selected.empty:
                logger.warning(f"No strikes found around ATM {atm}")
                return None
            
            logger.info(f"Fetching quotes for {len(selected)} strikes around {atm}")
            
            # 4. Fetch Quotes for OI
            tokens = selected['instrument_token'].tolist()
            quotes = self.kite.quote(tokens)
            
            # 5. Build DataFrame
            chain_data = []
            
            # Group by strike
            for strike in selected['strike'].unique():
                ce_row = selected[(selected['strike'] == strike) & (selected['instrument_type'] == 'CE')]
                pe_row = selected[(selected['strike'] == strike) & (selected['instrument_type'] == 'PE')]
                
                if ce_row.empty or pe_row.empty:
                    continue
                    
                ce_token = ce_row.iloc[0]['instrument_token']
                pe_token = pe_row.iloc[0]['instrument_token']
                
                # quote keys are likely strings of token IDs? No, usually int. Let's check.
                if ce_token not in quotes or pe_token not in quotes:
                     continue
                    
                ce_q = quotes[ce_token]
                pe_q = quotes[pe_token]
                
                chain_data.append({
                    "strike": strike,
                    "call_oi": ce_q['oi'],
                    "put_oi": pe_q['oi'],
                    "call_change_oi": 0, 
                    "put_change_oi": 0
                })
            
            if not chain_data:
                logger.warning("Chain data empty after fetching quotes")
                # Return empty DF with correct columns
                return pd.DataFrame(columns=["strike", "call_oi", "put_oi", "call_change_oi", "put_change_oi"])
                
            return pd.DataFrame(chain_data)
            
        except Exception as e:
            logger.error(f"Error fetching option chain: {e}")
            return None

