"""
Database layer for the cloud path (GitHub Actions scan job + Streamlit
dashboard). Synchronous on purpose: a one-shot Actions job and a Streamlit
app both work more simply and predictably without an asyncio event loop.

Works against any standard Postgres connection string (Supabase, Neon,
Railway, etc.) or a local sqlite file for testing — nothing here is
Postgres-specific beyond the URL you provide.
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from cloud.config import Settings


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scheduled_slot: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    candidates_scanned: Mapped[int] = mapped_column(Integer, default=0)
    candidates_passed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    results: Mapped[list["ScanResult"]] = relationship(back_populates="scan_run", cascade="all, delete-orphan")


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scan_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("scan_runs.id", ondelete="CASCADE"))
    scan_run: Mapped["ScanRun"] = relationship(back_populates="results")

    ticker: Mapped[str] = mapped_column(String(10), index=True)
    company: Mapped[str] = mapped_column(String(255), default="")
    sector: Mapped[str] = mapped_column(String(100), default="")

    price: Mapped[float] = mapped_column(Float, default=0.0)
    gap_pct: Mapped[float] = mapped_column(Float, default=0.0)
    premarket_pct: Mapped[float] = mapped_column(Float, default=0.0)
    premarket_volume: Mapped[int] = mapped_column(Integer, default=0)
    relative_volume: Mapped[float] = mapped_column(Float, default=0.0)
    float_shares: Mapped[float] = mapped_column(Float, default=0.0)
    market_cap: Mapped[float] = mapped_column(Float, default=0.0)

    has_catalyst: Mapped[bool] = mapped_column(default=False)
    catalyst_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    news_headline: Mapped[str] = mapped_column(Text, default="")
    news_url: Mapped[str] = mapped_column(String(1024), default="")

    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[str] = mapped_column(String(16), default="medium")

    premarket_high: Mapped[float] = mapped_column(Float, default=0.0)
    premarket_low: Mapped[float] = mapped_column(Float, default=0.0)
    support: Mapped[float] = mapped_column(Float, default=0.0)
    resistance: Mapped[float] = mapped_column(Float, default=0.0)
    average_volume: Mapped[int] = mapped_column(Integer, default=0)
    atr: Mapped[float] = mapped_column(Float, default=0.0)
    expected_volatility_pct: Mapped[float] = mapped_column(Float, default=0.0)

    recent_halt: Mapped[bool] = mapped_column(default=False)
    short_interest_pct: Mapped[float] = mapped_column(Float, default=0.0)
    previous_day_high: Mapped[float] = mapped_column(Float, default=0.0)
    previous_day_low: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticker: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class HistoricalBar(Base):
    """Cached OHLCV bar — either 1-minute (day-trading backtests) or 1-day
    (swing-trading backtests), distinguished by `timeframe`. Backtests read
    from here first and only hit Polygon for date ranges that aren't
    already cached — see cloud/historical_data.py."""

    __tablename__ = "historical_bars"
    __table_args__ = (
        UniqueConstraint("ticker", "timestamp", "timeframe", name="uq_historical_bar_ticker_ts_tf"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticker: Mapped[str] = mapped_column(String(10), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), default="minute", index=True)  # "minute" | "day"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # UTC
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)


class BacktestRun(Base):
    """One backtest execution: the strategy config used + the resulting
    summary metrics. Individual trades live in BacktestTrade."""

    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    strategy_name: Mapped[str] = mapped_column(String(64), default="Opening Range Breakout")
    tickers: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    trades: Mapped[list["BacktestTrade"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    run: Mapped["BacktestRun"] = relationship(back_populates="trades")

    ticker: Mapped[str] = mapped_column(String(10))
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    r_multiple: Mapped[float] = mapped_column(Float)
    exit_reason: Mapped[str] = mapped_column(String(32))


class SavedStrategy(Base):
    """A user-built strategy from the Strategy Builder page — the whole
    StrategyConfig (entry conditions, trend filters, exit rules, position
    sizing) serialized as JSON so it can be reloaded and re-run without
    rebuilding it from scratch."""

    __tablename__ = "saved_strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OptimizationRun(Base):
    """One optimizer execution: the base strategy, which parameters were
    varied over which values, and the full ranked results grid."""

    __tablename__ = "optimization_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    strategy_name: Mapped[str] = mapped_column(String(128), default="Custom Strategy")
    tickers: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    base_config: Mapped[dict] = mapped_column(JSON, default=dict)
    param_specs: Mapped[list] = mapped_column(JSON, default=list)
    rank_by: Mapped[str] = mapped_column(String(32), default="profit_factor")
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EMCompany(Base):
    """Cached universe of US-listed foreign companies (ADRs) for the
    Emerging-Markets Alpha Hunter. Details (market cap, description,
    inferred home country) are fetched once and cached here, because
    enriching ~1-2k tickers costs one API call each."""

    __tablename__ = "em_companies"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    country: Mapped[str] = mapped_column(String(64), default="")  # heuristically inferred
    sic_description: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    last_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    dollar_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    details_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    """Tiny key-value store for user-level settings (e.g. account size for
    the position-size calculator) — a new table on purpose, since adding
    columns to existing tables isn't supported by create_all."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TrackedPosition(Base):
    """A real position the user actually holds. The nightly monitor walks
    each open position's daily bars since entry and reports the FIRST
    triggered event (stop touched / target reached / indicator exit fired /
    max holding days elapsed) — positions are never auto-closed, because we
    don't know the user's real fills; the user closes them manually."""

    __tablename__ = "tracked_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[str] = mapped_column(String(10))
    shares: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = not specified
    strategy_name: Mapped[str] = mapped_column(String(128), default="")
    indicator_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    indicator_exit_type: Mapped[str] = mapped_column(String(32), default="close_below_ema")
    indicator_exit_period: Mapped[int] = mapped_column(Integer, default=10)
    max_holding_days: Mapped[int] = mapped_column(Integer, default=0)  # 0 = no limit
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | closed
    last_signal: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TopSetupRun(Base):
    """One Top-Setups scan (manual or nightly): which day was scanned, the
    settings used, and the resulting ranked setups — so the app can show
    'last night's picks' without re-scanning."""

    __tablename__ = "top_setup_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scan_day: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(16), default="manual")  # "manual" | "nightly"
    settings_used: Mapped[dict] = mapped_column(JSON, default=dict)
    top: Mapped[list] = mapped_column(JSON, default=list)
    candidates_count: Mapped[int] = mapped_column(Integer, default=0)
    hits_scanned: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


def get_engine(settings: Settings):
    # Supabase/Neon connection strings come as "postgresql://..." which
    # SQLAlchemy + psycopg2 handle natively — no URL rewriting needed.
    return create_engine(settings.DATABASE_URL, pool_pre_ping=True)


def get_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = get_engine(settings)
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(settings: Settings) -> None:
    """Creates tables if they don't exist yet. Safe to call on every run —
    the very first GitHub Actions run sets up your hosted database for you,
    no manual SQL required."""
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
