"""
Scan scheduler.

Registers one cron trigger per configured scan time (08:00, 08:30, 09:00,
09:20, 09:28 America/New_York by default) and runs the full scanner
pipeline at each. Started from `main.py`'s lifespan handler and shut down
cleanly on app exit.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.providers.factory import get_provider
from app.services.scanner import run_scan

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _scheduled_scan_job(slot: str) -> None:
    logger.info("Running scheduled scan for slot %s", slot)
    provider = get_provider()
    async with AsyncSessionLocal() as db:
        run = await run_scan(db, provider, slot)
        logger.info(
            "Scan %s complete: %d/%d candidates passed filters",
            slot, run.candidates_passed, run.candidates_scanned,
        )
    # TODO: after commit, check ScanResults against SCORE_ALERT_THRESHOLD
    # and dispatch via app/services/alerts.py (Discord/Telegram/Email/Browser)


def start_scheduler() -> None:
    settings = get_settings()
    if scheduler.running:
        return

    for slot in settings.SCAN_TIMES:
        hour, minute = slot.split(":")
        scheduler.add_job(
            _scheduled_scan_job,
            trigger=CronTrigger(
                hour=hour, minute=minute, timezone=settings.SCAN_TIMEZONE, day_of_week="mon-fri"
            ),
            args=[slot],
            id=f"scan_{slot}",
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("Registered scan job for %s %s (Mon-Fri)", slot, settings.SCAN_TIMEZONE)

    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
