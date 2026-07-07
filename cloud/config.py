"""
Settings for the cloud deployment path (GitHub Actions scan job + Streamlit
dashboard). Deliberately separate from backend/app/core/config.py — this
path has no FastAPI process, so it reads plain environment variables (set
as GitHub Actions secrets and Streamlit Cloud secrets) with no server-only
concepts like CORS.
"""
import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


def _env_float(name: str, default: float) -> float:
    """Robust float from env: GitHub Actions passes MISSING secrets as
    EMPTY strings (not unset), so '' and garbage must fall back to the
    default instead of crashing the whole job."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    """Robust int from env — same empty-secret handling as _env_float."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- Database (same connection string used by the scan job and Streamlit) ---
    DATABASE_URL: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///local_scanner.db"))

    # --- Data provider ---
    DATA_PROVIDER: str = field(default_factory=lambda: os.environ.get("DATA_PROVIDER", "polygon"))
    POLYGON_API_KEY: str = field(default_factory=lambda: os.environ.get("POLYGON_API_KEY", ""))
    FINNHUB_API_KEY: str = field(default_factory=lambda: os.environ.get("FINNHUB_API_KEY", ""))
    ALPACA_API_KEY: str = field(default_factory=lambda: os.environ.get("ALPACA_API_KEY", ""))
    ALPACA_API_SECRET: str = field(default_factory=lambda: os.environ.get("ALPACA_API_SECRET", ""))
    TWELVEDATA_API_KEY: str = field(default_factory=lambda: os.environ.get("TWELVEDATA_API_KEY", ""))
    ALLOW_MOCK_DATA_FALLBACK: bool = field(default_factory=lambda: _env_bool("ALLOW_MOCK_DATA_FALLBACK", True))

    # --- AI chat (Top Setups page) — either key enables the per-stock chat ---
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    OPENAI_API_KEY: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))

    # --- Scan schedule ---
    SCAN_TIMES: tuple[str, ...] = ("08:00", "08:30", "09:00", "09:20", "09:28")
    SCAN_TIMEZONE: str = "America/New_York"
    SCAN_TOLERANCE_MINUTES: int = field(default_factory=lambda: _env_int("SCAN_TOLERANCE_MINUTES", 4))

    # --- Universe filters ---
    MIN_PRICE: float = field(default_factory=lambda: _env_float("MIN_PRICE", 1.0))
    MAX_PRICE: float = field(default_factory=lambda: _env_float("MAX_PRICE", 20.0))
    MAX_MARKET_CAP: float = field(default_factory=lambda: _env_float("MAX_MARKET_CAP", 2_000_000_000))
    MAX_FLOAT: float = field(default_factory=lambda: _env_float("MAX_FLOAT", 30_000_000))
    MIN_GAP_PCT: float = field(default_factory=lambda: _env_float("MIN_GAP_PCT", 20.0))
    MIN_PREMARKET_VOLUME: int = field(default_factory=lambda: _env_int("MIN_PREMARKET_VOLUME", 500_000))
    MIN_RELATIVE_VOLUME: float = field(default_factory=lambda: _env_float("MIN_RELATIVE_VOLUME", 5.0))
    EXCLUDE_ETFS: bool = field(default_factory=lambda: _env_bool("EXCLUDE_ETFS", True))
    EXCLUDE_PREFERRED: bool = field(default_factory=lambda: _env_bool("EXCLUDE_PREFERRED", True))
    EXCLUDE_WARRANTS: bool = field(default_factory=lambda: _env_bool("EXCLUDE_WARRANTS", True))

    # --- Scoring weights ---
    WEIGHT_GAP: float = field(default_factory=lambda: _env_float("WEIGHT_GAP", 20.0))
    WEIGHT_FLOAT: float = field(default_factory=lambda: _env_float("WEIGHT_FLOAT", 15.0))
    WEIGHT_REL_VOLUME: float = field(default_factory=lambda: _env_float("WEIGHT_REL_VOLUME", 15.0))
    WEIGHT_PREMARKET_VOLUME: float = field(default_factory=lambda: _env_float("WEIGHT_PREMARKET_VOLUME", 10.0))
    WEIGHT_NEWS_QUALITY: float = field(default_factory=lambda: _env_float("WEIGHT_NEWS_QUALITY", 15.0))
    WEIGHT_ATR: float = field(default_factory=lambda: _env_float("WEIGHT_ATR", 5.0))
    WEIGHT_AVG_VOLUME: float = field(default_factory=lambda: _env_float("WEIGHT_AVG_VOLUME", 5.0))
    WEIGHT_SPREAD: float = field(default_factory=lambda: _env_float("WEIGHT_SPREAD", 5.0))
    WEIGHT_PREV_RESISTANCE: float = field(default_factory=lambda: _env_float("WEIGHT_PREV_RESISTANCE", 5.0))
    WEIGHT_HISTORICAL_VOLATILITY: float = field(default_factory=lambda: _env_float("WEIGHT_HISTORICAL_VOLATILITY", 3.0))
    WEIGHT_RECENT_HALTS: float = field(default_factory=lambda: _env_float("WEIGHT_RECENT_HALTS", 2.0))
    SCORE_ALERT_THRESHOLD: float = field(default_factory=lambda: _env_float("SCORE_ALERT_THRESHOLD", 75.0))

    # --- Alerts ---
    DISCORD_WEBHOOK_URL: str = field(default_factory=lambda: os.environ.get("DISCORD_WEBHOOK_URL", ""))
    TELEGRAM_BOT_TOKEN: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    TELEGRAM_CHAT_ID: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))
    SMTP_HOST: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    SMTP_PORT: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    SMTP_USER: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    SMTP_PASSWORD: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    ALERT_EMAIL_TO: str = field(default_factory=lambda: os.environ.get("ALERT_EMAIL_TO", ""))


def get_settings() -> Settings:
    return Settings()


def current_scan_slot(settings: Settings, now: datetime | None = None) -> str | None:
    """Return the configured scan slot (e.g. "09:20") if `now` (defaults to
    the real current time) falls within SCAN_TOLERANCE_MINUTES of one of
    SCAN_TIMES in the scan timezone, on a weekday. Otherwise None.

    This is what lets the GitHub Actions workflow run every few minutes
    through the whole pre-market window and still land on the exact
    configured slots correctly across DST changes — cron alone can't do
    that since GitHub Actions cron is UTC-only.
    """
    tz = ZoneInfo(settings.SCAN_TIMEZONE)
    now = (now or datetime.now(tz)).astimezone(tz)

    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return None

    now_minutes = now.hour * 60 + now.minute
    for slot in settings.SCAN_TIMES:
        hour, minute = (int(x) for x in slot.split(":"))
        slot_minutes = hour * 60 + minute
        if abs(now_minutes - slot_minutes) <= settings.SCAN_TOLERANCE_MINUTES:
            return slot
    return None
