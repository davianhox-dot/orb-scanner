"""
Strategy Scanner — scans a universe of US stocks against any StrategyConfig
and reports which tickers have a signal TODAY (i.e. the strategy's trend
filters + entry conditions are all true on the most recent daily bar),
along with a ready-to-use trade plan (entry / stop / target) derived from
the strategy's own exit rules.

Data strategy (why it's built this way):
- Polygon's "grouped daily" endpoint returns EVERY US ticker's OHLCV for
  one date in a single API call. So instead of one call per ticker
  (8,000+ calls), we make one call per trading day (~100 calls for ~100
  bars of history) regardless of universe size.
- The full market's history would be millions of rows — far too much to
  cache in a free-tier database. So scan data is held in memory only and
  the universe is capped (top-N most liquid tickers within your price
  band). When you then backtest ONE ticker from the results, that single
  ticker's bars go through the normal persistent cache as usual.
- "Top-N by dollar volume" is an honest trade-off, not a limitation hidden
  from you: genuinely illiquid names are excluded on purpose — they're the
  ones where backtest fills are least realistic anyway.

Security-type filtering uses a light heuristic (ticker shape) since the
grouped endpoint carries no reference data; a few ETFs/units may slip
through. The per-ticker reference lookup that would fix this costs one API
call per ticker — deliberately skipped here.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx

from cloud.db import HistoricalBar
from cloud.strategy_engine import StrategyConfig, _resolve_stop, _resolve_target
from cloud.strategy_rules import IndicatorCache, evaluate_all

logger = logging.getLogger(__name__)
BASE_URL = "https://api.polygon.io"

MAX_UNIVERSE = 1000


@dataclass
class SignalHit:
    ticker: str
    signal_date: str
    close: float
    volume: int
    entry: float
    stop: float
    target: float
    risk_reward: float


def fetch_grouped_daily(client: httpx.Client, api_key: str, day: date) -> list[dict]:
    """One API call -> every US ticker's OHLCV for one date. Empty list on
    weekends/holidays."""
    resp = client.get(
        f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
        params={"adjusted": "true", "apiKey": api_key},
    )
    if resp.status_code != 200:
        logger.warning("Grouped daily %s -> HTTP %s", day, resp.status_code)
        return []
    return resp.json().get("results", []) or []


def _looks_like_common_stock(ticker: str) -> bool:
    """Heuristic only: skip obvious warrants/units/preferreds/odd classes.
    (No reference data available on the grouped endpoint.)"""
    if not ticker.isalpha():
        return False  # excludes tickers with '.', '-', digits (preferred shares, classes)
    if len(ticker) == 5 and ticker[-1] in ("W", "U", "R"):
        return False  # 5-letter W/U/R suffixes are typically warrants/units/rights
    return True


def build_universe(
    latest_rows: list[dict],
    min_price: float,
    max_price: float,
    top_n: int,
    min_dollar_volume: float = 1_000_000,
) -> list[str]:
    """Filter the latest grouped-daily rows by price band + liquidity and
    return the top-N tickers by dollar volume."""
    candidates = []
    for row in latest_rows:
        ticker = row.get("T", "")
        close = row.get("c") or 0
        volume = row.get("v") or 0
        if not ticker or not _looks_like_common_stock(ticker):
            continue
        if not (min_price <= close <= max_price):
            continue
        dollar_volume = close * volume
        if dollar_volume < min_dollar_volume:
            continue
        candidates.append((ticker, dollar_volume))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in candidates[: min(top_n, MAX_UNIVERSE)]]


def fetch_history(
    api_key: str,
    universe: list[str],
    n_bars: int = 100,
    end_day: date | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, list[HistoricalBar]], date | None]:
    """Walk backward day by day collecting grouped-daily data until every
    date bucket adds up to ~n_bars trading days. Returns bars per universe
    ticker (in memory only, oldest-first) plus the most recent trading day
    found."""
    universe_set = set(universe)
    bars_by_ticker: dict[str, list[HistoricalBar]] = {t: [] for t in universe}

    day = end_day or date.today()
    collected_days = 0
    misses_in_a_row = 0
    latest_trading_day: date | None = None

    with httpx.Client(timeout=30.0) as client:
        while collected_days < n_bars and misses_in_a_row < 10:
            rows = fetch_grouped_daily(client, api_key, day)
            if rows:
                misses_in_a_row = 0
                if latest_trading_day is None:
                    latest_trading_day = day
                ts = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                for row in rows:
                    ticker = row.get("T", "")
                    if ticker in universe_set:
                        bars_by_ticker[ticker].append(
                            HistoricalBar(
                                ticker=ticker, timeframe="day", timestamp=ts,
                                open=row.get("o", 0), high=row.get("h", 0),
                                low=row.get("l", 0), close=row.get("c", 0),
                                volume=int(row.get("v", 0)),
                            )
                        )
                collected_days += 1
                if progress_callback:
                    progress_callback(collected_days, n_bars)
            else:
                misses_in_a_row += 1
            day -= timedelta(days=1)

    for ticker in bars_by_ticker:
        bars_by_ticker[ticker].sort(key=lambda b: b.timestamp)

    return bars_by_ticker, latest_trading_day


def scan_for_signals(
    bars_by_ticker: dict[str, list[HistoricalBar]],
    config: StrategyConfig,
    min_bars: int = 30,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[SignalHit]:
    """Evaluate the strategy's trend filters + entry conditions on the LAST
    bar of every ticker. A hit means: had you been running this strategy,
    today's close would have been the signal day — entry would be at
    (approximately) the next open. Entry shown = signal close; treat it as
    'enter above this level'."""
    hits: list[SignalHit] = []
    tickers = list(bars_by_ticker.keys())

    for idx, ticker in enumerate(tickers):
        bars = bars_by_ticker[ticker]
        if len(bars) < min_bars:
            if progress_callback:
                progress_callback(idx + 1, len(tickers))
            continue

        cache = IndicatorCache(bars)
        i = len(bars) - 1

        trend_ok = evaluate_all(config.trend_filters, "AND", cache, i)
        entry_ok = evaluate_all(config.entry_conditions, config.entry_logic, cache, i)

        if trend_ok is True and entry_ok is True:
            if config.entry_fill == "break_signal_high":
                # Buy-stop plan: enter only if price breaks the signal candle's high
                entry_price = bars[i].high
            else:
                entry_price = bars[i].close  # proxy: real entry = next day's open
            stop_price = _resolve_stop(entry_price, config.exit_rules, cache, i)
            if stop_price is None or stop_price >= entry_price:
                if progress_callback:
                    progress_callback(idx + 1, len(tickers))
                continue
            target_price = _resolve_target(entry_price, stop_price, config.exit_rules, cache, i)
            if target_price is None:
                if progress_callback:
                    progress_callback(idx + 1, len(tickers))
                continue

            risk = entry_price - stop_price
            reward = target_price - entry_price
            hits.append(
                SignalHit(
                    ticker=ticker,
                    signal_date=bars[i].timestamp.date().isoformat(),
                    close=round(bars[i].close, 2),
                    volume=bars[i].volume,
                    entry=round(entry_price, 2),
                    stop=round(stop_price, 2),
                    target=round(target_price, 2),
                    risk_reward=round(reward / risk, 2) if risk > 0 else 0.0,
                )
            )

        if progress_callback:
            progress_callback(idx + 1, len(tickers))

    hits.sort(key=lambda h: h.risk_reward, reverse=True)
    return hits
