"""
Application configuration.

All tunables live here and are overridable via environment variables (.env),
so the same image can run in dev/staging/prod without code changes.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DataProviderName = Literal["polygon", "finnhub", "alpaca", "twelvedata", "ib"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    APP_NAME: str = "ORB Scanner"
    ENV: Literal["dev", "staging", "prod"] = "dev"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://orb:orb@postgres:5432/orb_scanner"

    # --- Redis ---
    REDIS_URL: str = "redis://redis:6379/0"

    # --- Data provider selection (swap without touching code) ---
    DATA_PROVIDER: DataProviderName = "polygon"
    POLYGON_API_KEY: str = ""
    FINNHUB_API_KEY: str = ""
    ALPACA_API_KEY: str = ""
    ALPACA_API_SECRET: str = ""
    TWELVEDATA_API_KEY: str = ""
    IB_GATEWAY_HOST: str = "127.0.0.1"
    IB_GATEWAY_PORT: int = 4002

    # If no key is configured for the selected provider, fall back to bundled
    # sample data so the full pipeline (scan -> score -> store -> UI) is
    # demoable out of the box. Real scans require a real key.
    ALLOW_MOCK_DATA_FALLBACK: bool = True

    # --- Scan schedule (America/New_York) ---
    SCAN_TIMES: list[str] = ["08:00", "08:30", "09:00", "09:20", "09:28"]
    SCAN_TIMEZONE: str = "America/New_York"

    # --- Universe filters ---
    MIN_PRICE: float = 1.0
    MAX_PRICE: float = 20.0
    MAX_MARKET_CAP: float = 2_000_000_000
    MAX_FLOAT: float = 30_000_000
    MIN_GAP_PCT: float = 20.0
    MIN_PREMARKET_VOLUME: int = 500_000
    MIN_RELATIVE_VOLUME: float = 5.0
    EXCLUDE_ETFS: bool = True
    EXCLUDE_PREFERRED: bool = True
    EXCLUDE_WARRANTS: bool = True

    # --- Scoring weights (must sum to 100; enforced in scoring.py) ---
    WEIGHT_GAP: float = 20.0
    WEIGHT_FLOAT: float = 15.0
    WEIGHT_REL_VOLUME: float = 15.0
    WEIGHT_PREMARKET_VOLUME: float = 10.0
    WEIGHT_NEWS_QUALITY: float = 15.0
    WEIGHT_ATR: float = 5.0
    WEIGHT_AVG_VOLUME: float = 5.0
    WEIGHT_SPREAD: float = 5.0
    WEIGHT_PREV_RESISTANCE: float = 5.0
    WEIGHT_HISTORICAL_VOLATILITY: float = 3.0
    WEIGHT_RECENT_HALTS: float = 2.0
    SCORE_ALERT_THRESHOLD: float = 75.0

    # --- Alerts ---
    DISCORD_WEBHOOK_URL: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    ALERT_EMAIL_TO: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
