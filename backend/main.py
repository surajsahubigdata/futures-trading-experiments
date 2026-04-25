from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.models.trading_models import AnalysisResponse, TechnicalData, Signal, ErrorResponse, Kline
from backend.services.exchange_api import ExchangeAPI
from backend.services.ta_analyzer import TAAnalyzer
from backend.services.signal_generator import SignalGenerator
from backend.services.gemini_service import GeminiService
from backend.dependencies import get_exchange_api, get_ta_analyzer, get_signal_generator, get_gemini_service, get_cached_klines
from typing import List
import asyncio
from pydantic import BaseModel
import pandas as pd 

app = FastAPI(
    title="Crypto Futures AI Assistant Backend",
    description="Provides real-time crypto futures analysis and trading signals.",
    version="1.0.0",
)

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Adjust this to your frontend's URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
SUPPORTED_INTERVALS = ["15m", "1h", "4h", "1d"]

@app.get(
    "/analyze/{symbol}",
    response_model=AnalysisResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get comprehensive analysis and trading signals for a crypto pair."
)
async def get_comprehensive_analysis(
    symbol: str,
    interval: str = "1h",
    exchange_api: ExchangeAPI = Depends(get_exchange_api),
    ta_analyzer: TAAnalyzer = Depends(get_ta_analyzer),
    signal_generator: SignalGenerator = Depends(get_signal_generator),
    gemini_service: GeminiService = Depends(get_gemini_service),
    klines: List[Kline] = Depends(get_cached_klines) # Uses cached klines
) -> AnalysisResponse:
    
    if symbol.upper() not in SUPPORTED_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"Unsupported symbol. Only {', '.join(SUPPORTED_SYMBOLS)} are supported.")
    if interval not in SUPPORTED_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval. Only {', '.join(SUPPORTED_INTERVALS)} are supported.")

    try:
        if not klines:
            raise HTTPException(status_code=404, detail="No kline data available for analysis.")

        technical_data: TechnicalData = ta_analyzer.analyze(klines)
        if not technical_data.current_price:
            raise HTTPException(status_code=500, detail="Failed to retrieve current price for analysis.")

        signal_data: Signal = signal_generator.generate_signal(technical_data)
        ai_analysis_text: str = await gemini_service.generate_analysis(symbol.upper(), interval, technical_data, signal_data)

        return AnalysisResponse(
            symbol=symbol.upper(),
            interval=interval,
            technical_data=technical_data,
            signal=signal_data,
            ai_analysis=ai_analysis_text,
            chart_data=klines # Send raw klines, frontend will plot
        )
    except HTTPException as e:
        raise e # Re-raise HTTPExceptions
    except Exception as e:
        print(f"Error in get_comprehensive_analysis: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error during analysis: {e}")

# If you were to implement WebSockets for live updates directly from FastAPI
# @app.websocket("/ws/kline_updates/{symbol}/{interval}")
# async def websocket_endpoint(websocket: WebSocket, symbol: str, interval: str):
#     await websocket.accept()
#     # Logic to subscribe to exchange websocket
#     # And then push processed data/signals to the connected client
#     # This is a significant undertaking and would require a separate service
#     # to manage exchange websocket connections and broadcast to FastAPI clients.
#     try:
#         while True:
#             # Wait for updates from a shared queue managed by a background task
#             # For now, just keep connection alive or send dummy
#             await websocket.send_json({"status": "waiting for updates"})
#             await asyncio.sleep(5)
#     except Exception as e:
#         print(f"WebSocket error: {e}")
#     finally:
#         await websocket.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True, workers=1)