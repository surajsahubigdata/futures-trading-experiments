import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import asyncio
from typing import Dict, Any
from backend.config import settings
from backend.models.trading_models import TechnicalData, Signal
from typing import Optional

class GeminiService:
    def __init__(self):
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-pro')
        self.safety_settings = {
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

    async def generate_analysis(
        self,
        symbol: str,
        interval: str,
        technical_data: Dict[str, Any], # Changed from TechnicalData to Dict[str, Any] for flexibility
        signal_data: Dict[str, Any],    # Changed from Signal to Dict[str, Any] for flexibility
        override_prompt: Optional[str] = None ) -> str: 
            if override_prompt: # Use the custom prompt if provided
                prompt = override_prompt
            else:
                prompt = f"""
                **Advanced Crypto Futures Trading Analysis for {symbol} ({interval} Timeframe)**

                **Current Market Context:**
                - Current Price: ${technical_data.current_price:.2f}
                - 20-period Exponential Moving Average (EMA_20): {technical_data.ema_20:.2f}
                - 50-period Exponential Moving Average (EMA_50): {technical_data.ema_50:.2f}
                - Relative Strength Index (RSI_14): {technical_data.rsi:.2f} (Neutral: 30-70, Overbought: >70, Oversold: <30)
                - MACD Histogram (MACDh): {technical_data.macd_hist:.2f} (Positive: Bullish momentum, Negative: Bearish momentum)
                - Fibonacci 38.2% Retracement Level (potential support): {technical_data.fib_382_retracement:.2f}
                - Fibonacci 61.8% Retracement Level (potential resistance): {technical_data.fib_618_retracement:.2f}

                **Detected Signal:** {signal_data.action} (Confidence: {signal_data.strength*100:.0f}%)
                **Signal Reason:** {signal_data.reason}

                ---

                **Detailed Analysis and Futures Trading Guide:**

                Please provide a professional, in-depth analysis for {symbol} on the {interval} timeframe, incorporating all the provided technical indicators and the detected signal. Focus on futures trading implications, emphasizing risk management due to leverage.

                Your analysis should cover:
                1.  **Current Trend & Market Structure:** What is the prevailing trend (bullish, bearish, range-bound)? Are there any significant support/resistance zones from the Fibonacci levels or EMAs?
                2.  **Momentum Interpretation:** How do RSI and MACD support or contradict the trend and signal? Are there any divergences?
                3.  **Signal Context & Confidence:** Elaborate on why the `{signal_data.action}` signal was generated and discuss the confidence level.
                4.  **Potential Entry & Exit Points:** Based on the analysis, suggest realistic entry points if the signal is confirmed, and profit targets using Fibonacci extensions (if applicable for trend following) or key price levels.
                5.  **Risk Management (Crucial for Futures):** Propose concrete stop-loss levels. Explain why risk management is paramount, especially with leverage, and suggest appropriate position sizing strategies (e.g., risk only X% of capital per trade).
                6.  **"What If" Scenarios / Invalidations:** What would invalidate the current analysis or signal?
                7.  **Educational Tip:** Briefly explain one of the advanced concepts you've taught me previously (e.g., how to identify liquidity pools, the importance of CVD, or how to interpret funding rates in futures) in the context of this specific trade setup. Assume the user understands basics.
                8.  **Overall Recommendation:** A concise summary of the trading recommendation.

                Keep the tone professional and informative. Avoid making guarantees.
                """
        
            try:
                # Use asyncio.to_thread for blocking API calls in an async context
                response = await asyncio.to_thread(
                    self.model.generate_content,
                    prompt,
                    safety_settings=self.safety_settings
                )
                return response.text if response.text else "AI analysis could not be generated."
            except Exception as e:
                print(f"Error generating Gemini analysis: {e}")
                return f"Error generating AI analysis: {e}"

gemini_service = GeminiService()