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

# Direct export of GEMINI_API_KEY and OPENAI settings
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


class Settings(BaseSettings):
    GEMINI_API_KEY: str = GEMINI_API_KEY
    OPENAI_API_KEY: str = OPENAI_API_KEY
    OPENAI_BASE_URL: str = OPENAI_BASE_URL
    OPENAI_MODEL: str = OPENAI_MODEL
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

    @property
    def has_openai_key(self) -> bool:
        key = self.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
        return bool(key and key.strip())


settings = Settings()

# Direct exports
DB_PATH = settings.DB_PATH
MODEL_NAME = settings.MODEL_NAME
OPENAI_API_KEY = settings.OPENAI_API_KEY
OPENAI_BASE_URL = settings.OPENAI_BASE_URL
OPENAI_MODEL = settings.OPENAI_MODEL

# Ensure parent directory for database exists
db_path = Path(settings.DB_PATH)
db_path.parent.mkdir(parents=True, exist_ok=True)

# Defensive warning / status
if not settings.has_gemini_key and not settings.has_openai_key:
    logger.info(
        "[ASTRA SENTINEL] Running in self-contained local reasoning mode. "
        "Set GEMINI_API_KEY or OPENAI_API_KEY in .env to connect to online LLM API."
    )
elif settings.has_gemini_key:
    logger.info(
        f"[ASTRA SENTINEL] Google GenAI SDK initialized with model target '{settings.MODEL_NAME}'."
    )
elif settings.has_openai_key:
    logger.info(
        f"[ASTRA SENTINEL] OpenAI-compatible endpoint initialized at '{settings.OPENAI_BASE_URL}' with model '{settings.OPENAI_MODEL}'."
    )
