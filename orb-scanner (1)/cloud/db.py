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

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
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
