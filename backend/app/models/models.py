"""
Database models.

ScanRun groups the stocks found at one scheduled scan (08:00, 08:30, ...).
ScanResult is one ticker's full row of data + score for that run — this is
what backs the sortable table and the historical record.
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ScanRun(Base):
    """One execution of the scanner at one of the scheduled scan times."""

    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scheduled_slot: Mapped[str] = mapped_column(String(8))  # e.g. "09:20"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    candidates_scanned: Mapped[int] = mapped_column(Integer, default=0)
    candidates_passed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|success|error
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    results: Mapped[list["ScanResult"]] = relationship(back_populates="scan_run", cascade="all, delete-orphan")


class ScanResult(Base):
    """A single ticker's row of scan data for one ScanRun — the table row."""

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
    risk: Mapped[str] = mapped_column(String(16), default="medium")  # low|medium|high

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


class AppSetting(Base):
    """Single-row key/value store for user-editable settings (filters, weights, alert config)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
