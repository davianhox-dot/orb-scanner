"""
Consolidation Breakout backtest engine — swing-trading strategy.

Uses DAILY bars (not minute bars) since swing trades are held over days to
weeks, not exited same-day. Much cheaper to fetch/cache than minute data,
so backtests can cover months to years quickly.

Strategy:
  1. Look back `consolidation_days` trading days (not including today).
     resistance = highest high, support = lowest low over that window.
  2. Require the range to be "tight": (resistance - support) / resistance
     * 100 must be <= max_range_pct — otherwise it's not a real
     consolidation (it's just a big trending swing), so skip it.
  3. Signal: a day's CLOSE breaks above resistance (close-confirmed, to cut
     down on single-bar fakeouts vs. requiring only an intraday poke above
     the level).
  4. Entry: executed at the *next* trading day's OPEN — this avoids the
     look-ahead bias of assuming you could trade at the exact close the
     instant the signal bar prints.
  5. Stop: the consolidation's support level.
  6. Target: entry + (entry - stop) * target_r_multiple.
  7. Exit: whichever of stop/target is hit first on subsequent daily bars
     (via that day's low/high); if neither is hit within
     `max_holding_days` trading days, exit at that day's close.
  8. One open position per ticker at a time — after an exit, scanning
     resumes looking for the next consolidation + breakout.
"""
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from cloud.backtest_engine import BacktestResult, Trade, compute_metrics
from cloud.config import Settings
from cloud.db import BacktestRun, BacktestTrade, HistoricalBar
from cloud.historical_data import ensure_bars_cached, get_bars

logger = logging.getLogger(__name__)


@dataclass
class SwingBreakoutParams:
    consolidation_days: int = 15
    max_range_pct: float = 15.0
    target_r_multiple: float = 3.0
    max_holding_days: int = 20
    risk_pct: float = 1.0
    initial_capital: float = 10_000.0


def _find_trades_for_ticker(
    ticker: str, bars: list[HistoricalBar], params: SwingBreakoutParams, capital: float
) -> list[Trade]:
    trades: list[Trade] = []
    bars = sorted(bars, key=lambda b: b.timestamp)
    n = len(bars)
    lookback = params.consolidation_days

    i = lookback
    while i < n - 1:  # need at least one more day after the signal day to execute entry
        window = bars[i - lookback : i]
        resistance = max(b.high for b in window)
        support = min(b.low for b in window)
        if resistance <= 0:
            i += 1
            continue

        range_pct = (resistance - support) / resistance * 100
        signal_bar = bars[i]

        if range_pct <= params.max_range_pct and signal_bar.close > resistance:
            entry_bar = bars[i + 1]
            entry_price = entry_bar.open
            stop_price = support
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                i += 1
                continue
            target_price = entry_price + risk_per_share * params.target_r_multiple

            risk_dollars = capital * (params.risk_pct / 100)
            shares = risk_dollars / risk_per_share

            exit_price = exit_time = None
            exit_reason = "time_exit"
            exit_index = i + 1
            search_end = min(i + 2 + params.max_holding_days, n)
            for j in range(i + 2, search_end):
                bar = bars[j]
                if bar.low <= stop_price:
                    exit_price, exit_time, exit_reason = stop_price, bar.timestamp, "stop"
                    exit_index = j
                    break
                if bar.high >= target_price:
                    exit_price, exit_time, exit_reason = target_price, bar.timestamp, "target"
                    exit_index = j
                    break

            if exit_price is None:
                last_index = min(i + 1 + params.max_holding_days, n - 1)
                last_bar = bars[last_index]
                exit_price, exit_time, exit_reason = last_bar.close, last_bar.timestamp, "time_exit"
                exit_index = last_index

            pnl = (exit_price - entry_price) * shares
            r_multiple = (exit_price - entry_price) / risk_per_share

            trades.append(
                Trade(
                    ticker=ticker,
                    entry_time=entry_bar.timestamp,
                    exit_time=exit_time,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    shares=shares,
                    pnl=pnl,
                    r_multiple=r_multiple,
                    exit_reason=exit_reason,
                )
            )
            capital += pnl
            i = exit_index + 1  # resume scanning only after this trade has closed
        else:
            i += 1

    return trades


def run_swing_backtest(
    db: Session,
    settings: Settings,
    tickers: list[str],
    start: date,
    end: date,
    params: SwingBreakoutParams,
) -> BacktestResult:
    all_trades: list[Trade] = []

    for ticker in tickers:
        ensure_bars_cached(db, settings, ticker, start, end, timeframe="day")
        bars = get_bars(db, ticker, start, end, timeframe="day")
        if len(bars) < params.consolidation_days + 2:
            logger.info("Not enough daily bars for %s to evaluate a consolidation window", ticker)
            continue

        # Each ticker sizes its own trades off a fresh initial_capital pool
        # (same simplification as the ORB engine) — the combined equity
        # curve below then walks through all trades chronologically as if
        # they shared one account. True concurrent portfolio-level capital
        # allocation across simultaneous positions is a later refinement.
        capital = params.initial_capital
        all_trades.extend(_find_trades_for_ticker(ticker, bars, params, capital))

    all_trades.sort(key=lambda t: t.entry_time)

    equity_curve: list[tuple] = []
    capital = params.initial_capital
    for trade in all_trades:
        capital += trade.pnl
        equity_curve.append((trade.exit_time, capital))

    metrics = compute_metrics(all_trades, params.initial_capital, capital)
    return BacktestResult(trades=all_trades, equity_curve=equity_curve, metrics=metrics)


def save_run(
    db: Session,
    tickers: list[str],
    start: date,
    end: date,
    params: SwingBreakoutParams,
    result: BacktestResult,
) -> BacktestRun:
    """Persist a completed swing backtest, reusing the same BacktestRun /
    BacktestTrade tables the ORB engine uses — `strategy_name` is what
    distinguishes them when you look at run history later."""
    run = BacktestRun(
        strategy_name="Consolidation Breakout (Swing)",
        tickers=tickers,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        params=params.__dict__,
        metrics=result.metrics,
    )
    db.add(run)
    db.flush()

    for t in result.trades:
        db.add(
            BacktestTrade(
                run_id=run.id,
                ticker=t.ticker,
                entry_time=t.entry_time,
                exit_time=t.exit_time,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                stop_price=t.stop_price,
                target_price=t.target_price,
                shares=t.shares,
                pnl=t.pnl,
                r_multiple=t.r_multiple,
                exit_reason=t.exit_reason,
            )
        )

    db.commit()
    db.refresh(run)
    return run
