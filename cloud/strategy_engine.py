"""
Generic swing-strategy backtest engine.

Where cloud/swing_breakout_engine.py runs exactly one fixed strategy, this
runs *any* StrategyConfig built in the Strategy Builder UI: an arbitrary
combination of entry conditions (AND/OR), trend filters (always AND'd on
top), a choice of stop/target/trailing-stop rules, and a choice of position
sizing method.

Entry is executed at the *next* trading day's open after a signal day
(same look-ahead-bias avoidance as the fixed engines). One open position
per ticker at a time.
"""
import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from cloud.backtest_engine import BacktestResult, Trade, compute_metrics
from cloud.config import Settings
from cloud.db import BacktestRun, BacktestTrade, HistoricalBar
from cloud.historical_data import ensure_bars_cached, get_bars
from cloud.strategy_rules import Condition, IndicatorCache, evaluate_all

logger = logging.getLogger(__name__)

STOP_TYPES = ("fixed_pct", "atr_multiple", "swing_low")
TARGET_TYPES = ("r_multiple", "fixed_pct", "atr_multiple")
SIZING_METHODS = ("fixed_pct_risk", "fixed_dollar_risk")


@dataclass
class ExitRuleConfig:
    stop_type: str = "swing_low"
    stop_value: float = 10.0  # meaning depends on stop_type: % / ATR multiple / lookback days
    stop_atr_period: int = 14
    target_type: str = "r_multiple"
    target_value: float = 3.0
    trailing_stop: bool = False
    trailing_pct: float = 8.0
    max_holding_days: int = 30
    # Indicator-based exit, evaluated on each day's CLOSE (after intraday
    # stop/target checks): "close_below_ema" = protective trend exit,
    # "close_above_ema" = mean-reversion profit exit (e.g. Connors RSI(2)
    # exits when price closes back above a short EMA).
    indicator_exit: bool = False
    indicator_exit_type: str = "close_below_ema"  # "close_below_ema" | "close_above_ema"
    indicator_exit_period: int = 10


@dataclass
class PositionSizingConfig:
    method: str = "fixed_pct_risk"
    value: float = 1.0  # % of capital, or $ amount, depending on method


ENTRY_FILL_MODES = ("next_open", "break_signal_high")


@dataclass
class StrategyConfig:
    name: str = "Custom Strategy"
    trend_filters: list[Condition] = field(default_factory=list)
    entry_conditions: list[Condition] = field(default_factory=list)
    entry_logic: str = "AND"
    # "next_open": buy at the next day's open unconditionally (original behavior).
    # "break_signal_high": buy-stop just above the signal candle's high — the
    # next day must actually trade above that high, otherwise NO trade. If
    # the next day gaps open above the trigger, fill at the open (realistic
    # for a stop order).
    entry_fill: str = "next_open"
    exit_rules: ExitRuleConfig = field(default_factory=ExitRuleConfig)
    position_sizing: PositionSizingConfig = field(default_factory=PositionSizingConfig)
    initial_capital: float = 10_000.0


def _resolve_stop(entry_price: float, exit_rules: ExitRuleConfig, cache: IndicatorCache, i: int) -> float | None:
    if exit_rules.stop_type == "fixed_pct":
        return entry_price * (1 - exit_rules.stop_value / 100)
    if exit_rules.stop_type == "atr_multiple":
        atr_series = cache.atr(exit_rules.stop_atr_period)
        if atr_series[i] is None:
            return None
        return entry_price - atr_series[i] * exit_rules.stop_value
    if exit_rules.stop_type == "swing_low":
        lookback = int(exit_rules.stop_value)
        if i < lookback:
            return None
        return min(b.low for b in cache.bars[i - lookback : i])
    raise ValueError(f"Unknown stop_type: {exit_rules.stop_type}")


def _resolve_target(
    entry_price: float, stop_price: float, exit_rules: ExitRuleConfig, cache: IndicatorCache, i: int
) -> float | None:
    risk_per_share = entry_price - stop_price
    if exit_rules.target_type == "r_multiple":
        return entry_price + risk_per_share * exit_rules.target_value
    if exit_rules.target_type == "fixed_pct":
        return entry_price * (1 + exit_rules.target_value / 100)
    if exit_rules.target_type == "atr_multiple":
        atr_series = cache.atr(exit_rules.stop_atr_period)
        if atr_series[i] is None:
            return None
        return entry_price + atr_series[i] * exit_rules.target_value
    raise ValueError(f"Unknown target_type: {exit_rules.target_type}")


def _find_trades_for_ticker(
    ticker: str, bars: list[HistoricalBar], config: StrategyConfig, capital: float
) -> list[Trade]:
    trades: list[Trade] = []
    bars = sorted(bars, key=lambda b: b.timestamp)
    n = len(bars)
    cache = IndicatorCache(bars)

    i = 1
    while i < n - 1:
        trend_ok = evaluate_all(config.trend_filters, "AND", cache, i)
        entry_ok = evaluate_all(config.entry_conditions, config.entry_logic, cache, i)

        if trend_ok is True and entry_ok is True:
            entry_bar = bars[i + 1]

            if config.entry_fill == "break_signal_high":
                trigger = bars[i].high
                if entry_bar.high <= trigger:
                    i += 1  # next day never broke the signal candle's high -> no trade
                    continue
                # Buy-stop fill: at the trigger, or at the open if it gapped above
                entry_price = max(trigger, entry_bar.open)
            else:
                entry_price = entry_bar.open

            stop_price = _resolve_stop(entry_price, config.exit_rules, cache, i)
            if stop_price is None or stop_price >= entry_price:
                i += 1
                continue
            target_price = _resolve_target(entry_price, stop_price, config.exit_rules, cache, i)
            if target_price is None:
                i += 1
                continue

            risk_per_share = entry_price - stop_price
            sizing = config.position_sizing
            if sizing.method == "fixed_pct_risk":
                shares = capital * (sizing.value / 100) / risk_per_share
            elif sizing.method == "fixed_dollar_risk":
                shares = sizing.value / risk_per_share
            else:
                raise ValueError(f"Unknown position sizing method: {sizing.method}")

            exit_price = exit_time = None
            exit_reason = "time_exit"
            exit_index = i + 1
            search_end = min(i + 2 + config.exit_rules.max_holding_days, n)
            running_high = entry_bar.high
            effective_stop = stop_price

            for j in range(i + 2, search_end):
                bar = bars[j]
                running_high = max(running_high, bar.high)
                if config.exit_rules.trailing_stop:
                    trail_price = running_high * (1 - config.exit_rules.trailing_pct / 100)
                    effective_stop = max(effective_stop, trail_price)

                if bar.low <= effective_stop:
                    exit_price = effective_stop
                    exit_time = bar.timestamp
                    exit_reason = "trailing_stop" if effective_stop > stop_price else "stop"
                    exit_index = j
                    break
                if bar.high >= target_price:
                    exit_price, exit_time, exit_reason = target_price, bar.timestamp, "target"
                    exit_index = j
                    break
                if config.exit_rules.indicator_exit:
                    ema_series = cache.ema(int(config.exit_rules.indicator_exit_period))
                    ema_val = ema_series[j]
                    if ema_val is not None:
                        exit_type = config.exit_rules.indicator_exit_type
                        triggered = (
                            bar.close < ema_val if exit_type == "close_below_ema" else bar.close > ema_val
                        )
                        if triggered:
                            exit_price, exit_time, exit_reason = bar.close, bar.timestamp, "indicator_exit"
                            exit_index = j
                            break

            if exit_price is None:
                last_index = min(i + 1 + config.exit_rules.max_holding_days, n - 1)
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
            i = exit_index + 1
        elif trend_ok is None or entry_ok is None:
            i += 1  # not enough lookback data yet
        else:
            i += 1

    return trades


def run_strategy_backtest(
    db: Session, settings: Settings, tickers: list[str], start: date, end: date, config: StrategyConfig
) -> BacktestResult:
    all_trades: list[Trade] = []

    for ticker in tickers:
        ensure_bars_cached(db, settings, ticker, start, end, timeframe="day")
        bars = get_bars(db, ticker, start, end, timeframe="day")
        if len(bars) < 10:
            logger.info("Not enough daily bars for %s to run this strategy", ticker)
            continue

        capital = config.initial_capital
        all_trades.extend(_find_trades_for_ticker(ticker, bars, config, capital))

    all_trades.sort(key=lambda t: t.entry_time)

    equity_curve: list[tuple] = []
    capital = config.initial_capital
    for trade in all_trades:
        capital += trade.pnl
        equity_curve.append((trade.exit_time, capital))

    metrics = compute_metrics(all_trades, config.initial_capital, capital)
    return BacktestResult(trades=all_trades, equity_curve=equity_curve, metrics=metrics)


def save_run(
    db: Session, tickers: list[str], start: date, end: date, config: StrategyConfig, result: BacktestResult
) -> BacktestRun:
    run = BacktestRun(
        strategy_name=config.name,
        tickers=tickers,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        params={
            "trend_filters": [{"type": c.type, "params": c.params} for c in config.trend_filters],
            "entry_conditions": [{"type": c.type, "params": c.params} for c in config.entry_conditions],
            "entry_logic": config.entry_logic,
            "exit_rules": config.exit_rules.__dict__,
            "position_sizing": config.position_sizing.__dict__,
            "initial_capital": config.initial_capital,
        },
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
