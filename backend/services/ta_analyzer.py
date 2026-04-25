import pandas as pd
import pandas_ta as ta
from typing import Dict, Any, List
from backend.models.trading_models import Kline, TechnicalData

class TAAnalyzer:
    def analyze(self, klines: List[Kline]) -> Dict[str, Any]:
        if not klines:
            return {}

        # Convert list of Kline models to DataFrame for pandas_ta
        df = pd.DataFrame([k.model_dump() for k in klines])
        df['open_time'] = pd.to_datetime(df['open_time'])
        df = df.set_index('open_time').sort_index()

        # Apply various technical indicators
        df.ta.ema(length=20, append=True)
        df.ta.ema(length=50, append=True)
        df.ta.rsi(length=14, append=True)
        df.ta.macd(append=True)
        # df.ta.adx(append=True) # Add ADX
        # df.ta.bbands(append=True) # Bollinger Bands

        # --- Fibonacci Retracement (Simplified) ---
        # A proper implementation would find a significant swing high/low
        if len(df) > 50: # Ensure enough data for a swing
            recent_high = df['high'].iloc[-50:].max()
            recent_low = df['low'].iloc[-50:].min()
            
            # Ensure high is above low to avoid division by zero or negative ranges
            if recent_high > recent_low:
                price_range = recent_high - recent_low
                fib_382 = recent_high - (price_range * 0.382)
                fib_618 = recent_high - (price_range * 0.618)
            else:
                fib_382 = None
                fib_618 = None
        else:
            fib_382 = None
            fib_618 = None

        latest_data = df.iloc[-1]
        
        return TechnicalData(
            current_price=latest_data['close'],
            ema_20=latest_data.get('EMA_20'),
            ema_50=latest_data.get('EMA_50'),
            rsi=latest_data.get('RSI_14'), # pandas_ta names columns like 'RSI_14'
            macd_hist=latest_data.get('MACDh_12_26_9'),
            fib_382_retracement=fib_382,
            fib_618_retracement=fib_618,
            # Populate other indicators
        )

ta_analyzer = TAAnalyzer()