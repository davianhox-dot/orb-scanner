"""All API routes for v1, grouped by resource. Kept in one module for a
project this size; split into app/api/routes/{scans,stocks,...}.py once it
grows past a few hundred lines."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.models import AppSetting, ScanResult, ScanRun, WatchlistItem
from app.providers.factory import get_provider
from app.schemas.schemas import (
    FilterSettings,
    ScanRunOut,
    ScanRunSummary,
    ScoringWeights,
    SettingsOut,
    StockDetailOut,
    TradingPlan,
    WatchlistItemIn,
    WatchlistItemOut,
)
from app.services.scanner import build_trading_plan, run_scan

logger = logging.getLogger(__name__)
router = APIRouter()


# --------------------------------------------------------------------- #
# Scans
# --------------------------------------------------------------------- #

@router.get("/scans", response_model=list[ScanRunSummary])
async def list_scans(limit: int = 20, db: AsyncSession = Depends(get_db)):
    stmt = select(ScanRun).order_by(ScanRun.started_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/scans/latest", response_model=ScanRunOut)
async def latest_scan(db: AsyncSession = Depends(get_db)):
    stmt = select(ScanRun).order_by(ScanRun.started_at.desc()).limit(1)
    result = await db.execute(stmt)
    run = result.scalars().first()
    if not run:
        raise HTTPException(404, "No scans have run yet")
    await db.refresh(run, attribute_names=["results"])
    run.results.sort(key=lambda r: r.score, reverse=True)
    return run


@router.get("/scans/{scan_id}", response_model=ScanRunOut)
async def get_scan(scan_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(ScanRun, scan_id)
    if not run:
        raise HTTPException(404, "Scan not found")
    await db.refresh(run, attribute_names=["results"])
    run.results.sort(key=lambda r: r.score, reverse=True)
    return run


@router.post("/scans/run", response_model=ScanRunOut, status_code=201)
async def trigger_scan(db: AsyncSession = Depends(get_db)):
    """Manually trigger a scan outside the fixed schedule (useful for testing
    and for ad-hoc scans during the day)."""
    provider = get_provider()
    run = await run_scan(db, provider, scheduled_slot="manual")
    await db.refresh(run, attribute_names=["results"])
    return run


# --------------------------------------------------------------------- #
# Stocks
# --------------------------------------------------------------------- #

@router.get("/stocks/{ticker}", response_model=StockDetailOut)
async def get_stock_detail(ticker: str, db: AsyncSession = Depends(get_db)):
    """Latest scan result for a ticker, enriched with a derived trading plan."""
    stmt = (
        select(ScanResult)
        .where(ScanResult.ticker == ticker.upper())
        .order_by(ScanResult.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"No scan data found for {ticker.upper()}")

    plan = build_trading_plan(row.price, row.premarket_high, row.premarket_low)
    return StockDetailOut(**row.__dict__, trading_plan=TradingPlan(**plan))


# --------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------- #

@router.get("/watchlist", response_model=list[WatchlistItemOut])
async def list_watchlist(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WatchlistItem).order_by(WatchlistItem.added_at.desc()))
    return result.scalars().all()


@router.post("/watchlist", response_model=WatchlistItemOut, status_code=201)
async def add_to_watchlist(item: WatchlistItemIn, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(WatchlistItem).where(WatchlistItem.ticker == item.ticker.upper()))
    if existing.scalars().first():
        raise HTTPException(409, f"{item.ticker.upper()} is already on the watchlist")
    row = WatchlistItem(ticker=item.ticker.upper(), note=item.note)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/watchlist/{ticker}", status_code=204)
async def remove_from_watchlist(ticker: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(WatchlistItem).where(WatchlistItem.ticker == ticker.upper()))
    await db.commit()


# --------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------- #

@router.get("/settings", response_model=SettingsOut)
async def get_current_settings():
    s = get_settings()
    return SettingsOut(
        data_provider=s.DATA_PROVIDER,
        filters=FilterSettings(
            min_price=s.MIN_PRICE,
            max_price=s.MAX_PRICE,
            max_market_cap=s.MAX_MARKET_CAP,
            max_float=s.MAX_FLOAT,
            min_gap_pct=s.MIN_GAP_PCT,
            min_premarket_volume=s.MIN_PREMARKET_VOLUME,
            min_relative_volume=s.MIN_RELATIVE_VOLUME,
            score_alert_threshold=s.SCORE_ALERT_THRESHOLD,
        ),
        weights=ScoringWeights(
            gap=s.WEIGHT_GAP,
            float_=s.WEIGHT_FLOAT,
            relative_volume=s.WEIGHT_REL_VOLUME,
            premarket_volume=s.WEIGHT_PREMARKET_VOLUME,
            news_quality=s.WEIGHT_NEWS_QUALITY,
            atr=s.WEIGHT_ATR,
            average_volume=s.WEIGHT_AVG_VOLUME,
            spread=s.WEIGHT_SPREAD,
            previous_resistance=s.WEIGHT_PREV_RESISTANCE,
            historical_volatility=s.WEIGHT_HISTORICAL_VOLATILITY,
            recent_halts=s.WEIGHT_RECENT_HALTS,
        ),
    )


@router.put("/settings/filters", response_model=FilterSettings)
async def update_filters(filters: FilterSettings, db: AsyncSession = Depends(get_db)):
    """Persists filter overrides to app_settings. Note: process-wide Settings
    (env-driven) still govern defaults on restart — this row is read by the
    scanner at scan time to override them. See services/scanner.py."""
    row = await db.get(AppSetting, "filters")
    if row:
        row.value = filters.model_dump()
    else:
        row = AppSetting(key="filters", value=filters.model_dump())
        db.add(row)
    await db.commit()
    return filters


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    provider = get_provider()
    provider_health = await provider.health()
    return {
        "status": "ok",
        "provider": provider_health.__dict__,
    }
