from typing import Dict, Any
from backend.models.trading_models import TechnicalData, Signal

class SignalGenerator:
    def generate_signal(self, technical_data: TechnicalData) -> Signal:
        signal_action = "HOLD"
        reason = "Market is consolidating or no clear signal detected."
        strength = 0.5 # Default neutral strength

        # Ensure all required data points exist before evaluating
        if all(v is not None for v in [technical_data.ema_20, technical_data.ema_50, technical_data.rsi, technical_data.macd_hist]):
            # --- Trend Following (EMA Cross) ---
            if technical_data.ema_20 > technical_data.ema_50:
                if technical_data.rsi > 55 and technical_data.macd_hist > 0:
                    signal_action = "BUY"
                    reason = "Short-term EMA above long-term EMA with strong bullish momentum (RSI > 55, MACD rising)."
                    strength = 0.8
                elif technical_data.rsi > 50: # Mildly bullish
                    signal_action = "BUY"
                    reason = "Short-term EMA above long-term EMA, mild bullish momentum."
                    strength = 0.6
            elif technical_data.ema_20 < technical_data.ema_50:
                if technical_data.rsi < 45 and technical_data.macd_hist < 0:
                    signal_action = "SELL"
                    reason = "Short-term EMA below long-term EMA with strong bearish momentum (RSI < 45, MACD falling)."
                    strength = 0.8
                elif technical_data.rsi < 50: # Mildly bearish
                    signal_action = "SELL"
                    reason = "Short-term EMA below long-term EMA, mild bearish momentum."
                    strength = 0.6
            
            # --- Counter-Trend (RSI Divergence/Overbought/Oversold near Fib) ---
            # This would require more complex logic involving comparing RSI peaks/troughs with price peaks/troughs
            # For simplicity, let's add an overbought/oversold check near a Fib level
            if technical_data.rsi > 70 and technical_data.fib_618_retracement and abs(technical_data.current_price - technical_data.fib_618_retracement) / technical_data.current_price < 0.005:
                signal_action = "SELL"
                reason = f"Asset is overbought (RSI > 70) and near a strong Fibonacci resistance ({technical_data.fib_618_retracement:.2f}). Potential reversal."
                strength = 0.7
            elif technical_data.rsi < 30 and technical_data.fib_382_retracement and abs(technical_data.current_price - technical_data.fib_382_retracement) / technical_data.current_price < 0.005:
                signal_action = "BUY"
                reason = f"Asset is oversold (RSI < 30) and near a strong Fibonacci support ({technical_data.fib_382_retracement:.2f}). Potential bounce."
                strength = 0.7
            
            # Override to HOLD if conflicting signals or low conviction
            if strength < 0.6 and signal_action != "HOLD":
                signal_action = "HOLD"
                reason = "Conflicting signals or low conviction. Waiting for clearer direction."
                strength = 0.5


        return Signal(action=signal_action, reason=reason, strength=strength)

signal_generator = SignalGenerator()