"""
Nightly Top-Setups job — the automated version of the 🏆 Top Setups page.

Run by GitHub Actions each evening after US market close (see
.github/workflows/top_setups.yml). Scans all presets + saved strategies
against the liquid universe, auto-backtests every hit, saves the ranked
result to the database (so the app shows it next morning), and sends the
top picks via whichever alert channels are configured (Discord / Telegram
/ Email — same env vars as the pre-market scanner alerts).

Universe settings are env-overridable so you can tune the nightly run
without code changes:
  TOPSETUPS_MIN_PRICE (default 2), TOPSETUPS_MAX_PRICE (100),
  TOPSETUPS_UNIVERSE (150), TOPSETUPS_HISTORY_BARS (250),
  TOPSETUPS_BACKTEST_YEARS (2), TOPSETUPS_MIN_TRADES (5), TOPSETUPS_TOP_K (3)
"""
import logging
import os
import sys
from datetime import date, timedelta

import httpx
from sqlalchemy import select

from cloud.alerts import send_discord, send_email, send_telegram
from cloud.config import get_settings
from cloud.db import SavedStrategy, TopSetupRun, get_session_factory, init_db
from cloud.strategy_presets import PRESET_NAMES, config_from_dict, get_preset
from cloud.strategy_scanner import build_universe, fetch_grouped_daily, fetch_history
from cloud.top_setups import find_top_setups, format_alert_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def run() -> int:
    settings = get_settings()
    if not settings.POLYGON_API_KEY:
        logger.error("POLYGON_API_KEY not set — the top-setups job needs live market data.")
        return 1

    min_price = _env_float("TOPSETUPS_MIN_PRICE", 2.0)
    max_price = _env_float("TOPSETUPS_MAX_PRICE", 100.0)
    universe_n = int(_env_float("TOPSETUPS_UNIVERSE", 150))
    history_bars = int(_env_float("TOPSETUPS_HISTORY_BARS", 250))
    backtest_years = int(_env_float("TOPSETUPS_BACKTEST_YEARS", 2))
    min_trades = int(_env_float("TOPSETUPS_MIN_TRADES", 5))
    top_k = int(_env_float("TOPSETUPS_TOP_K", 3))

    init_db(settings)
    Session = get_session_factory(settings)

    # --- strategies: all presets + all saved ---
    strategies = [(name, get_preset(name)) for name in PRESET_NAMES]
    with Session() as db:
        for row in db.execute(select(SavedStrategy)).scalars().all():
            strategies.append((row.name, config_from_dict(row.config)))
    logger.info("Scanning with %d strategies", len(strategies))

    # --- latest trading day + universe ---
    latest_rows, probe_day = [], date.today()
    with httpx.Client(timeout=30.0) as client:
        for _ in range(7):
            latest_rows = fetch_grouped_daily(client, settings.POLYGON_API_KEY, probe_day)
            if latest_rows:
                break
            probe_day -= timedelta(days=1)
    if not latest_rows:
        logger.error("Couldn't fetch grouped market data for any recent day.")
        return 1

    universe = build_universe(latest_rows, min_price, max_price, universe_n)
    logger.info("Universe: %d tickers (top by $ volume, $%s-%s)", len(universe), min_price, max_price)

    # SPY rides along for the market regime + relative-strength grading —
    # it is NOT scanned as a setup candidate.
    bars_by_ticker, latest_day = fetch_history(
        settings.POLYGON_API_KEY, universe + ["SPY"], n_bars=max(history_bars, 210), end_day=probe_day,
        progress_callback=lambda done, total: (done % 25 == 0) and logger.info("History %d/%d days", done, total),
    )
    spy_bars = bars_by_ticker.pop("SPY", [])

    with Session() as db:
        result = find_top_setups(
            db, settings, strategies, bars_by_ticker,
            backtest_years=backtest_years, min_trades=min_trades, top_k=top_k,
            spy_bars=spy_bars,
            progress_callback=lambda msg: logger.info(msg),
        )

        db.add(
            TopSetupRun(
                scan_day=str(latest_day),
                source="nightly",
                settings_used={
                    "min_price": min_price, "max_price": max_price, "universe": universe_n,
                    "history_bars": history_bars, "backtest_years": backtest_years,
                    "min_trades": min_trades, "top_k": top_k,
                    "strategies": [n for n, _ in strategies],
                    "market_regime": result.market_regime,
                    "strategy_stats": result.strategy_stats,
                },
                top=[s.__dict__ for s in result.top],
                candidates_count=len(result.all_candidates),
                hits_scanned=result.hits_scanned,
            )
        )
        db.commit()

    logger.info(
        "Done: %d hits scanned, %d qualified, top %d saved.",
        result.hits_scanned, len(result.all_candidates), len(result.top),
    )

    # --- alerts (only when there's something to say) ---
    if result.top:
        message = format_alert_message(result.top, str(latest_day), regime=result.market_regime)
        sent = {
            "discord": send_discord(settings, message),
            "telegram": send_telegram(settings, message),
            "email": send_email(settings, f"Top Setups {latest_day}", message),
        }
        logger.info("Alerts sent: %s", {k: v for k, v in sent.items()})
    else:
        logger.info("No qualifying setups today — no alert sent.")

    # --- Position monitor: check the user's REAL open positions ---
    from cloud.position_monitor import check_open_positions, format_position_alert

    with Session() as db:
        checks = check_open_positions(db, settings)
    logger.info("Position monitor: %d open position(s) checked, %d need action.",
                len(checks), sum(1 for c in checks if c.action_needed))
    pos_message = format_position_alert(checks, str(latest_day))
    if pos_message:
        sent = {
            "discord": send_discord(settings, pos_message),
            "telegram": send_telegram(settings, pos_message),
            "email": send_email(settings, f"Positions-Check {latest_day}", pos_message),
        }
        logger.info("Position alerts sent: %s", {k: v for k, v in sent.items()})

    return 0


if __name__ == "__main__":
    sys.exit(run())
