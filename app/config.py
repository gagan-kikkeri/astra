"""
Configuration and settings management for ASTRA Sentinel.
Handles environment variables via python-dotenv, network timeouts,
default database paths, and API keys.
"""

from dotenv import load_dotenv
import os
import sys
import logging
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env file explicitly at the top
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ASTRA-SENTINEL] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("astra_sentinel.config")

# Direct export of GEMINI_API_KEY
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


class Settings(BaseSettings):
    GEMINI_API_KEY: str = GEMINI_API_KEY
    DB_PATH: str = os.getenv("DB_PATH", "data/sentinel.db")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-2.5-flash")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", 8000))
    REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", 15.0))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def has_gemini_key(self) -> bool:
        key = self.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
        return bool(key and key.strip())


settings = Settings()

# Ensure parent directory for database exists
db_path = Path(settings.DB_PATH)
db_path.parent.mkdir(parents=True, exist_ok=True)

# Defensive warning / status
if not settings.has_gemini_key:
    logger.info(
        "[ASTRA SENTINEL] Running in self-contained local reasoning mode. "
        "Set GEMINI_API_KEY in .env to connect to online Gemini API."
    )
else:
    logger.info(
        f"[ASTRA SENTINEL] Google GenAI SDK initialized with model target '{settings.MODEL_NAME}'."
    )
