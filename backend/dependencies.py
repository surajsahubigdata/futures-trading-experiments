from backend.services.exchange_api import ExchangeAPI
from backend.services.ta_analyzer import TAAnalyzer
from backend.services.signal_generator import SignalGenerator
from backend.services.gemini_service import GeminiService
from functools import lru_cache
from backend.config import settings
from cachetools import cached, TTLCache
from backend.models.trading_models import Kline
import asyncio
from typing import List
from fastapi import Depends
# --- Cached Services ---
# This ensures a single instance of each service is used and configured
@lru_cache()
def get_exchange_api() -> ExchangeAPI:
    return ExchangeAPI()

@lru_cache()
def get_ta_analyzer() -> TAAnalyzer:
    return TAAnalyzer()

@lru_cache()
def get_signal_generator() -> SignalGenerator:
    return SignalGenerator()

@lru_cache()
def get_gemini_service() -> GeminiService:
    return GeminiService()

# --- Caching for FastAPI endpoints ---
# Cache for fetch_klines to avoid hitting exchange rate limits too often
kline_cache = TTLCache(maxsize=settings.CACHE_MAXSIZE, ttl=settings.CACHE_TTL)

async def get_cached_klines(
    symbol: str,
    interval: str,
    exchange_api: ExchangeAPI = Depends(get_exchange_api)
) -> List[Kline]:
    cache_key = f"klines_{symbol}_{interval}"
    if cache_key in kline_cache:
        return kline_cache[cache_key]

    klines = await exchange_api.fetch_klines(symbol, interval)
    kline_cache[cache_key] = klines
    return klines