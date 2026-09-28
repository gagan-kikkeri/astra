"""
Configuration and settings management for ASTRA Sentinel.
Handles environment variables, default database paths, and API keys.
"""

import os
import sys
import logging
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ASTRA-SENTINEL] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("astra_sentinel.config")


class Settings(BaseSettings):
    GEMINI_API_KEY: str = ""
    DB_PATH: str = "data/sentinel.db"
    MODEL_NAME: str = "gemini-2.5-flash"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.GEMINI_API_KEY and self.GEMINI_API_KEY.strip())


settings = Settings()

# Ensure parent directory for database exists
db_path = Path(settings.DB_PATH)
db_path.parent.mkdir(parents=True, exist_ok=True)

# Defensive warning if GEMINI_API_KEY is not provided
if not settings.has_gemini_key:
    logger.warning(
        "[ASTRA SENTINEL ADVISORY] GEMINI_API_KEY is not configured. "
        "System operational in DETERMINISTIC RULE-BASED TRIAGE MODE (Mock fallback enabled). "
        "To enable online LLM extraction, set GEMINI_API_KEY in your environment or .env file."
    )
else:
    logger.info(
        f"[ASTRA SENTINEL] Google GenAI SDK initialized with model target '{settings.MODEL_NAME}'."
    )
