"""Runtime settings, read from environment variables and the repo-root .env file."""

from datetime import datetime
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Where the Streamlit UI finds the API
    resolvedesk_api_url: str = "http://localhost:8000"

    # Store clock: every date rule uses this, never the real date
    store_now: datetime = datetime(2018, 8, 15, 12, 0)

    # Not used by the skeleton yet (no DB connections); wired in later
    app_database_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
