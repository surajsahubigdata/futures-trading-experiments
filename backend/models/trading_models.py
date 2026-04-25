from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

class Kline(BaseModel):
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    # Add more fields if your exchange API returns them (e.g., quote_asset_volume, trades)

class TechnicalData(BaseModel):
    current_price: float
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    rsi: Optional[float] = None
    macd_hist: Optional[float] = None
    fib_382_retracement: Optional[float] = None
    fib_618_retracement: Optional[float] = None
    # Add more indicators here as you implement them

class Signal(BaseModel):
    action: str # e.g., "BUY", "SELL", "HOLD"
    reason: str
    strength: Optional[float] = None # e.g., confidence score

class AnalysisResponse(BaseModel):
    symbol: str
    interval: str
    technical_data: TechnicalData
    signal: Signal
    ai_analysis: str
    chart_data: List[Kline] # Send raw klines, Streamlit plots them
    timestamp: datetime = datetime.now()

class ErrorResponse(BaseModel):
    detail: str