"""
Scanner pipeline.

Ties the provider, catalyst detector, and scoring engine together into one
scan run: fetch the premarket universe -> detect catalysts -> score ->
persist a ScanRun + its ScanResults. This is what both the scheduler
(automatic 08:00/08:30/.../09:28 runs) and the manual "run scan now" API
endpoint call.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.models import ScanResult, ScanRun
from app.providers.base import DataProvider, TickerSnapshot
from app.services.catalyst import detect_catalysts
from app.services.scoring import calculate_score

logger = logging.getLogger(__name__)


def build_trading_plan(price: float, premarket_high: float, premarket_low: float) -> dict:
    """Derive a simple, explainable ORB/pullback trading plan from the
    premarket range. This is a heuristic starting point for a trader's own
    plan, not a recommendation to execute."""
    orb_entry = round(premarket_high * 1.001, 2) if premarket_high else price
    pullback_entry = round(premarket_high * 0.985, 2) if premarket_high else price
    stop = round(premarket_low, 2) if premarket_low else round(price * 0.92, 2)
    risk_per_share = max(orb_entry - stop, 0.01)
    target = round(orb_entry + risk_per_share * 2, 2)  # default 2R target
    rr = round((target - orb_entry) / risk_per_share, 2) if risk_per_share else 0.0
    return {
        "orb_entry": orb_entry,
        "pullback_entry": pullback_entry,
        "stop": stop,
        "target": target,
        "risk_reward_ratio": rr,
    }


async def run_scan(db: AsyncSession, provider: DataProvider, scheduled_slot: str) -> ScanRun:
    settings = get_settings()
    scan_run = ScanRun(scheduled_slot=scheduled_slot, provider=provider.name, status="running")
    db.add(scan_run)
    await db.flush()

    try:
        universe = await provider.get_premarket_universe()
        scan_run.candidates_scanned = len(universe)
        logger.info("Scan %s (%s): %d candidates from provider", scheduled_slot, provider.name, len(universe))

        passed = 0
        for snapshot in universe:
            if not _passes_filters(snapshot, settings):
                continue

            news = await provider.get_news(snapshot.ticker)
            tags, best_news = detect_catalysts(news)
            score_result = calculate_score(snapshot, tags, settings)

            result = ScanResult(
                scan_run_id=scan_run.id,
                ticker=snapshot.ticker,
                company=snapshot.company,
                sector=snapshot.sector,
                price=snapshot.price,
                gap_pct=snapshot.gap_pct,
                premarket_pct=snapshot.premarket_pct,
                premarket_volume=snapshot.premarket_volume,
                relative_volume=snapshot.relative_volume,
                float_shares=snapshot.float_shares,
                market_cap=snapshot.market_cap,
                has_catalyst=bool(tags),
                catalyst_tags=tags,
                news_headline=best_news.headline if best_news else "",
                news_url=best_news.url if best_news else "",
                score=score_result.total,
                score_breakdown=score_result.breakdown,
                risk=score_result.risk,
                premarket_high=snapshot.premarket_high,
                premarket_low=snapshot.premarket_low,
                support=snapshot.support,
                resistance=snapshot.resistance,
                average_volume=snapshot.average_volume,
                atr=snapshot.atr,
                expected_volatility_pct=snapshot.historical_volatility_pct,
                recent_halt=snapshot.recent_halt,
                short_interest_pct=snapshot.short_interest_pct,
                previous_day_high=snapshot.previous_day_high,
                previous_day_low=snapshot.previous_day_low,
            )
            db.add(result)
            passed += 1

        scan_run.candidates_passed = passed
        scan_run.status = "success"
    except Exception as exc:  # noqa: BLE001 — persist any failure onto the run for visibility
        logger.exception("Scan %s failed", scheduled_slot)
        scan_run.status = "error"
        scan_run.error_message = str(exc)
    finally:
        from datetime import datetime

        scan_run.finished_at = datetime.utcnow()
        await db.commit()
        await db.refresh(scan_run)

    return scan_run


def _passes_filters(snapshot: TickerSnapshot, settings) -> bool:
    if snapshot.is_etf and settings.EXCLUDE_ETFS:
        return False
    if snapshot.is_preferred and settings.EXCLUDE_PREFERRED:
        return False
    if snapshot.is_warrant and settings.EXCLUDE_WARRANTS:
        return False
    if not (settings.MIN_PRICE <= snapshot.price <= settings.MAX_PRICE):
        return False
    if snapshot.gap_pct < settings.MIN_GAP_PCT:
        return False
    if snapshot.premarket_volume < settings.MIN_PREMARKET_VOLUME:
        return False
    if snapshot.relative_volume < settings.MIN_RELATIVE_VOLUME:
        return False
    if snapshot.market_cap and snapshot.market_cap > settings.MAX_MARKET_CAP:
        return False
    if snapshot.float_shares and snapshot.float_shares > settings.MAX_FLOAT:
        return False
    return True
