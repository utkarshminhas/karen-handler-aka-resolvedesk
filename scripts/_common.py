"""Shared settings and paths for the data scripts (reads .env at the repo root)."""

from datetime import datetime
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OLIST_DIR = DATA_DIR / "olist"
BITEXT_DIR = DATA_DIR / "bitext"
ABCD_DIR = DATA_DIR / "abcd"
EVAL_DATASETS_DIR = ROOT / "evals" / "datasets"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    admin_database_url: str
    store_now: datetime


def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
