import httpx
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any
from backend.config import settings
from backend.models.trading_models import Kline
from fastapi import HTTPException

# Binance specific client (if you use python-binance, it's easier)
# from binance.client import Client
# binance_client = Client(settings.BINANCE_API_KEY, settings.BINANCE_API_SECRET)

class ExchangeAPI:
    def __init__(self):
        self.base_url = "https://api.binance.com/api/v3"
        self.client = httpx.AsyncClient(base_url=self.base_url) # For direct HTTP requests

    async def fetch_klines(self, symbol: str, interval: str = '1h', limit: int = 100) -> List[Kline]:
        # --- Using direct HTTP (more universal, but requires manual parsing) ---
        endpoint = "/klines"
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        try:
            response = await self.client.get(endpoint, params=params)
            response.raise_for_status() # Raises HTTPStatusError for 4xx/5xx responses
            klines_data = response.json()
            
            klines: List[Kline] = []
            for kline in klines_data:
                klines.append(Kline(
                    open_time=datetime.fromtimestamp(kline[0] / 1000),
                    open=float(kline[1]),
                    high=float(kline[2]),
                    low=float(kline[3]),
                    close=float(kline[4]),
                    volume=float(kline[5])
                ))
            return klines
        except httpx.HTTPStatusError as e:
            print(f"HTTP error fetching klines for {symbol}: {e.response.status_code} - {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Exchange API error: {e.response.text}")
        except httpx.RequestError as e:
            print(f"Network error fetching klines for {symbol}: {e}")
            raise HTTPException(status_code=503, detail=f"Network error: Could not connect to exchange API. {e}")
        except Exception as e:
            print(f"Unexpected error fetching klines: {e}")
            raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

    # TODO: Add methods for fetching funding rates, open interest, order book data
    # async def fetch_funding_rate(self, symbol: str) -> float: ...
    # async def fetch_open_interest(self, symbol: str) -> Dict[str, Any]: ...

    # --- For WebSockets (Conceptual, would be in a separate service or background task) ---
    # This would involve an asyncio.Queue to push real-time data to a processing loop
    # async def start_kline_websocket(self, symbol: str, interval: str, callback: Callable):
    #     ws_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@kline_{interval}"
    #     async with websockets.connect(ws_url) as ws:
    #         while True:
    #             message = await ws.recv()
    #             data = json.loads(message)
    #             # Process data and call callback with new Kline
    #             # callback(Kline(...))

exchange_api = ExchangeAPI()