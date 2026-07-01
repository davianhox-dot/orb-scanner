"""
Entrypoint for the scheduled scan job.

This is the script GitHub Actions runs. It:
  1. Confirms "now" is actually within one of the configured scan slots
     (handles DST correctly — see config.current_scan_slot)
  2. Fetches the pre-market universe from the configured provider
  3. Applies filters, detects catalysts, scores each candidate
  4. Persists a ScanRun + ScanResults to your hosted Postgres database
  5. Fires alerts for anything scoring at/above SCORE_ALERT_THRESHOLD

Run manually any time for testing:
    DATABASE_URL=sqlite:///local_scanner.db python -m cloud.run_scan --force
"""
import argparse
import logging
import sys
from datetime import datetime

from cloud.alerts import notify_high_score
from cloud.catalyst import detect_catalysts
from cloud.config import current_scan_slot, get_settings
from cloud.db import ScanResult, ScanRun, get_session_factory, init_db
from cloud.providers import get_provider
from cloud.scoring import calculate_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("run_scan")


def _passes_filters(snapshot, settings) -> bool:
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


def run(force: bool = False) -> int:
    settings = get_settings()

    slot = current_scan_slot(settings)
    if slot is None and not force:
        logger.info("Not within a scheduled scan window right now — nothing to do.")
        return 0
    slot = slot or "manual"

    logger.info("Running scan for slot=%s provider=%s", slot, settings.DATA_PROVIDER)
    init_db(settings)
    Session = get_session_factory(settings)
    provider = get_provider(settings)

    with Session() as db:
        scan_run = ScanRun(scheduled_slot=slot, provider=provider.name, status="running")
        db.add(scan_run)
        db.flush()

        try:
            universe = provider.get_premarket_universe()
            scan_run.candidates_scanned = len(universe)
            logger.info("Fetched %d candidates from provider", len(universe))

            passed = 0
            for snapshot in universe:
                if not _passes_filters(snapshot, settings):
                    continue

                news = provider.get_news(snapshot.ticker)
                tags, best_news = detect_catalysts(news)
                score_result = calculate_score(snapshot, tags, settings)

                result = ScanResult(
                    scan_run_id=scan_run.id,
                    ticker=snapshot.ticker, company=snapshot.company, sector=snapshot.sector,
                    price=snapshot.price, gap_pct=snapshot.gap_pct, premarket_pct=snapshot.premarket_pct,
                    premarket_volume=snapshot.premarket_volume, relative_volume=snapshot.relative_volume,
                    float_shares=snapshot.float_shares, market_cap=snapshot.market_cap,
                    has_catalyst=bool(tags), catalyst_tags=tags,
                    news_headline=best_news.headline if best_news else "",
                    news_url=best_news.url if best_news else "",
                    score=score_result.total, score_breakdown=score_result.breakdown, risk=score_result.risk,
                    premarket_high=snapshot.premarket_high, premarket_low=snapshot.premarket_low,
                    support=snapshot.support, resistance=snapshot.resistance,
                    average_volume=snapshot.average_volume, atr=snapshot.atr,
                    expected_volatility_pct=snapshot.historical_volatility_pct,
                    recent_halt=snapshot.recent_halt, short_interest_pct=snapshot.short_interest_pct,
                    previous_day_high=snapshot.previous_day_high, previous_day_low=snapshot.previous_day_low,
                )
                db.add(result)
                passed += 1

                if score_result.total >= settings.SCORE_ALERT_THRESHOLD:
                    notify_high_score(settings, result)

            scan_run.candidates_passed = passed
            scan_run.status = "success"
            logger.info("Scan complete: %d/%d candidates passed filters", passed, len(universe))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scan failed")
            scan_run.status = "error"
            scan_run.error_message = str(exc)
        finally:
            scan_run.finished_at = datetime.utcnow()
            db.commit()

    return 0 if scan_run.status == "success" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the pre-market scanner once.")
    parser.add_argument(
        "--force", action="store_true",
        help="Run even if 'now' isn't within a configured scan window (useful for manual testing).",
    )
    args = parser.parse_args()
    sys.exit(run(force=args.force))
