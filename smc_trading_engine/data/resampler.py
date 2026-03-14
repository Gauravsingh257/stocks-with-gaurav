"""
Resampler - Convert OHLC data between timeframes
"""

import pandas as pd
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class Resampler:
    """Resample and aggregate OHLC data between timeframes"""
    
    TIMEFRAME_MINUTES = {
        "1minute": 1,
        "5minute": 5,
        "15minute": 15,
        "30minute": 30,
        "60minute": 60,
        "day": 1440,
    }
    
    @staticmethod
    def resample(
        df: pd.DataFrame,
        from_tf: str,
        to_tf: str
    ) -> Optional[pd.DataFrame]:
        """
        Resample OHLC data from one timeframe to another
        
        Args:
            df: DataFrame with OHLC data (index must be datetime)
            from_tf: Source timeframe
            to_tf: Target timeframe
        
        Returns:
            Resampled DataFrame or None if invalid
        """
        try:
            if from_tf == to_tf:
                return df.copy()
            
            # Validate timeframes
            if from_tf not in Resampler.TIMEFRAME_MINUTES:
                raise ValueError(f"Unknown timeframe: {from_tf}")
            if to_tf not in Resampler.TIMEFRAME_MINUTES:
                raise ValueError(f"Unknown timeframe: {to_tf}")
            
            from_mins = Resampler.TIMEFRAME_MINUTES[from_tf]
            to_mins = Resampler.TIMEFRAME_MINUTES[to_tf]
            
            # Only allow upsampling (shorter to longer timeframes)
            if from_mins >= to_mins:
                logger.warning(f"Cannot resample {from_tf} to {to_tf} (only upsampling allowed)")
                return None
            
            # Resample using OHLC rules
            agg_rules = {
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum',
            }
            
            # Create resample period
            if to_tf == "day":
                period = "D"
            else:
                period = f"{to_mins}T"
            
            resampled = df.resample(period).agg(agg_rules)
            
            # Drop rows with no data
            resampled = resampled.dropna(subset=['open'])
            
            return resampled
        
        except Exception as e:
            logger.error(f"Resampling error {from_tf}->{to_tf}: {e}")
            return None
    
    @staticmethod
    def get_higher_timeframe(tf: str) -> Optional[str]:
        """
        Get the next higher timeframe
        
        Args:
            tf: Current timeframe
        
        Returns:
            Next higher timeframe or None
        """
        tf_order = ["1minute", "5minute", "15minute", "30minute", "60minute", "day"]
        if tf in tf_order:
            idx = tf_order.index(tf)
            if idx < len(tf_order) - 1:
                return tf_order[idx + 1]
        return None
    
    @staticmethod
    def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        Add commonly used indicators to DataFrame
        
        Args:
            df: OHLC DataFrame
        
        Returns:
            DataFrame with added indicators
        """
        df = df.copy()
        
        try:
            # Moving Averages
            df['sma_20'] = df['close'].rolling(window=20).mean()
            df['sma_50'] = df['close'].rolling(window=50).mean()
            df['sma_200'] = df['close'].rolling(window=200).mean()
            
            # ATR (for volatility)
            df['tr'] = df[['high', 'low', 'close']].apply(
                lambda x: max(x['high'] - x['low'], 
                            abs(x['high'] - x['close']),
                            abs(x['low'] - x['close'])),
                axis=1
            )
            df['atr'] = df['tr'].rolling(window=14).mean()
            
            # Volume
            df['sma_volume'] = df['volume'].rolling(window=20).mean()
            
        except Exception as e:
            logger.error(f"Error adding indicators: {e}")
        
        return df
