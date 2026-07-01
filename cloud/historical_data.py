"""
Historical bar data for backtesting — minute bars (day-trading strategies)
or daily bars (swing-trading strategies), selected via `timeframe`.

Fetches from Polygon and caches every bar in the `historical_bars` table.
Before hitting the API, checks what's already cached for a ticker+timeframe
and only fetches the portion of the requested date range that's missing —
the "only download missing historical data" behavior from the spec.

Simplification (documented, not hidden): the gap-filling logic only checks
the *edges* of the cached range (extend backward / extend forward). It
doesn't detect holes in the middle of an already-cached range. Good enough
for how this is actually used (extending backtests to earlier or later
dates over time) — a proper interval-tree gap-filler would be the next
refinement if that becomes a real need.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cloud.config import Settings
from cloud.db import HistoricalBar

logger = logging.getLogger(__name__)
BASE_URL = "https://api.polygon.io"

Timeframe = Literal["minute", "day"]


def _cached_range(db: Session, ticker: str, timeframe: Timeframe) -> tuple[date | None, date | None]:
    row = db.execute(
        select(func.min(HistoricalBar.timestamp), func.max(HistoricalBar.timestamp)).where(
            HistoricalBar.ticker == ticker, HistoricalBar.timeframe == timeframe
        )
    ).first()
    if not row or row[0] is None:
        return None, None
    return row[0].date(), row[1].date()


def ensure_bars_cached(
    db: Session, settings: Settings, ticker: str, start: date, end: date, timeframe: Timeframe = "minute"
) -> None:
    """Fetch and cache whatever part of [start, end] isn't already stored
    for this ticker+timeframe. No-op (and no API key required) if the range
    is already fully cached."""
    cached_start, cached_end = _cached_range(db, ticker, timeframe)

    ranges_to_fetch: list[tuple[date, date]] = []
    if cached_start is None:
        ranges_to_fetch.append((start, end))
    else:
        if start < cached_start:
            ranges_to_fetch.append((start, cached_start - timedelta(days=1)))
        if end > cached_end:
            ranges_to_fetch.append((cached_end + timedelta(days=1), end))

    if not ranges_to_fetch:
        logger.info("%s (%s) already fully cached for %s -> %s", ticker, timeframe, start, end)
        return

    if not settings.POLYGON_API_KEY:
        raise RuntimeError(
            f"POLYGON_API_KEY is required to fetch new historical data for {ticker} "
            f"(missing ranges: {ranges_to_fetch}). Backtesting has no mock/demo mode."
        )

    for range_start, range_end in ranges_to_fetch:
        if range_start > range_end:
            continue
        _fetch_and_store(db, settings, ticker, range_start, range_end, timeframe)


def _fetch_and_store(
    db: Session, settings: Settings, ticker: str, start: date, end: date, timeframe: Timeframe
) -> None:
    logger.info("Fetching %s %s bars: %s -> %s", ticker, timeframe, start, end)
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/{timeframe}/{start.isoformat()}/{end.isoformat()}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": settings.POLYGON_API_KEY},
        )
        resp.raise_for_status()
        bars = resp.json().get("results", [])

    if not bars:
        logger.warning(
            "No %s bars returned for %s %s -> %s (holiday/no trading/no data on plan)",
            timeframe, ticker, start, end,
        )
        return

    day_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    day_end = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
    existing_timestamps = {
        row[0]
        for row in db.execute(
            select(HistoricalBar.timestamp).where(
                HistoricalBar.ticker == ticker,
                HistoricalBar.timeframe == timeframe,
                HistoricalBar.timestamp >= day_start,
                HistoricalBar.timestamp <= day_end,
            )
        ).all()
    }

    added = 0
    for bar in bars:
        ts = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc)
        if ts in existing_timestamps:
            continue
        db.add(
            HistoricalBar(
                ticker=ticker,
                timeframe=timeframe,
                timestamp=ts,
                open=bar["o"],
                high=bar["h"],
                low=bar["l"],
                close=bar["c"],
                volume=bar["v"],
            )
        )
        added += 1

    db.commit()
    logger.info("Cached %d new %s bars for %s (of %d returned)", added, timeframe, ticker, len(bars))


def get_bars(db: Session, ticker: str, start: date, end: date, timeframe: Timeframe = "minute") -> list[HistoricalBar]:
    day_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    day_end = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
    result = db.execute(
        select(HistoricalBar)
        .where(
            HistoricalBar.ticker == ticker,
            HistoricalBar.timeframe == timeframe,
            HistoricalBar.timestamp >= day_start,
            HistoricalBar.timestamp <= day_end,
        )
        .order_by(HistoricalBar.timestamp)
    )
    return list(result.scalars().all())
