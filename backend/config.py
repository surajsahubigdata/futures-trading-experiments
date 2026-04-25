import os
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file

class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "YOUR_DEFAULT_GEMINI_KEY")
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
    # Add settings for other exchanges if you integrate them
    
    # Cache settings
    CACHE_MAXSIZE: int = 128
    CACHE_TTL: int = 300 # seconds (5 minutes)

settings = Settings()