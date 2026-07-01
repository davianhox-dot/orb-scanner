"""Pydantic I/O schemas — kept separate from ORM models so the API contract
can evolve independently of the database schema."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ScanResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticker: str
    company: str
    sector: str
    price: float
    gap_pct: float
    premarket_pct: float
    premarket_volume: int
    relative_volume: float
    float_shares: float
    market_cap: float
    has_catalyst: bool
    catalyst_tags: list[str]
    news_headline: str
    news_url: str
    score: float
    score_breakdown: dict
    risk: str
    premarket_high: float
    premarket_low: float
    support: float
    resistance: float
    average_volume: int
    atr: float
    expected_volatility_pct: float
    recent_halt: bool
    short_interest_pct: float
    previous_day_high: float
    previous_day_low: float
    created_at: datetime


class ScanRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scheduled_slot: str
    started_at: datetime
    finished_at: datetime | None
    provider: str
    candidates_scanned: int
    candidates_passed: int
    status: str
    error_message: str | None
    results: list[ScanResultOut] = []


class ScanRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scheduled_slot: str
    started_at: datetime
    finished_at: datetime | None
    provider: str
    candidates_scanned: int
    candidates_passed: int
    status: str


class TradingPlan(BaseModel):
    orb_entry: float
    pullback_entry: float
    stop: float
    target: float
    risk_reward_ratio: float


class StockDetailOut(ScanResultOut):
    trading_plan: TradingPlan


class WatchlistItemIn(BaseModel):
    ticker: str
    note: str = ""


class WatchlistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticker: str
    note: str
    added_at: datetime


class ScoringWeights(BaseModel):
    gap: float = 20.0
    float_: float = 15.0
    relative_volume: float = 15.0
    premarket_volume: float = 10.0
    news_quality: float = 15.0
    atr: float = 5.0
    average_volume: float = 5.0
    spread: float = 5.0
    previous_resistance: float = 5.0
    historical_volatility: float = 3.0
    recent_halts: float = 2.0


class FilterSettings(BaseModel):
    min_price: float = 1.0
    max_price: float = 20.0
    max_market_cap: float = 2_000_000_000
    max_float: float = 30_000_000
    min_gap_pct: float = 20.0
    min_premarket_volume: int = 500_000
    min_relative_volume: float = 5.0
    score_alert_threshold: float = 75.0


class SettingsOut(BaseModel):
    data_provider: str
    filters: FilterSettings
    weights: ScoringWeights
