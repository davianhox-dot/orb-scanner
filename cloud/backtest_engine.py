"""
Opening Range Breakout backtest engine — Phase 1.

Fixed strategy (not yet configurable beyond these parameters — the
strategy builder is a later phase):
  1. Opening range = high/low of the first N minutes of the regular session
     (default 5 min, i.e. 09:30-09:35 ET).
  2. Entry: long breakout above the opening range high, filled at the OR
     high (not the breakout bar's actual high, to avoid overstating fills).
  3. Stop: opening range low.
  4. Target: entry + (risk-per-share * target_r_multiple).
  5. Exit: whichever of stop/target is hit first on subsequent 1-min bars;
     if neither is hit by session close, exit at the close's closing price
     ("time_exit").
  6. Position sizing: fixed % of current capital risked per trade
     (risk_dollars / risk_per_share = shares).

One trade maximum per ticker per day. Pre-market and after-hours bars are
excluded from this v1 (regular session only, 09:30-16:00 ET).
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from cloud.config import Settings
from cloud.db import BacktestRun, BacktestTrade, HistoricalBar
from cloud.historical_data import ensure_bars_cached, get_bars

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


@dataclass
class ORBParams:
    opening_range_minutes: int = 5
    target_r_multiple: float = 2.0
    risk_pct: float = 1.0  # % of current capital risked per trade
    initial_capital: float = 10_000.0


@dataclass
class Trade:
    ticker: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    shares: float
    pnl: float
    r_multiple: float
    exit_reason: str  # "stop" | "target" | "time_exit"


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _group_by_session_day(bars: list[HistoricalBar]) -> dict[date, list[HistoricalBar]]:
    days: dict[date, list[HistoricalBar]] = {}
    for bar in bars:
        local_date = bar.timestamp.astimezone(ET).date()
        days.setdefault(local_date, []).append(bar)
    return days


def _simulate_day(
    ticker: str, day_bars: list[HistoricalBar], params: ORBParams, capital: float
) -> Trade | None:
    session_bars = [
        b for b in day_bars if MARKET_OPEN <= b.timestamp.astimezone(ET).time() < MARKET_CLOSE
    ]
    if len(session_bars) < params.opening_range_minutes + 1:
        return None  # not enough bars to establish an opening range + look for breakout

    opening_bars = session_bars[: params.opening_range_minutes]
    or_high = max(b.high for b in opening_bars)
    or_low = min(b.low for b in opening_bars)
    if or_high <= or_low:
        return None

    remaining = session_bars[params.opening_range_minutes :]

    entry_price = entry_time = None
    breakout_index = None
    for i, bar in enumerate(remaining):
        if bar.high > or_high:
            entry_price = or_high
            entry_time = bar.timestamp
            breakout_index = i
            break

    if entry_price is None:
        return None  # no breakout today for this ticker

    stop_price = or_low
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return None
    target_price = entry_price + risk_per_share * params.target_r_multiple

    risk_dollars = capital * (params.risk_pct / 100)
    shares = risk_dollars / risk_per_share

    exit_price = exit_time = None
    exit_reason = "time_exit"
    # Scan bars *after* the breakout bar for stop/target — avoids assuming
    # we know the intrabar order of events on the breakout bar itself.
    for bar in remaining[breakout_index + 1 :]:
        if bar.low <= stop_price:
            exit_price, exit_time, exit_reason = stop_price, bar.timestamp, "stop"
            break
        if bar.high >= target_price:
            exit_price, exit_time, exit_reason = target_price, bar.timestamp, "target"
            break

    if exit_price is None:
        last_bar = session_bars[-1]
        exit_price, exit_time, exit_reason = last_bar.close, last_bar.timestamp, "time_exit"

    pnl = (exit_price - entry_price) * shares
    r_multiple = (exit_price - entry_price) / risk_per_share

    return Trade(
        ticker=ticker,
        entry_time=entry_time,
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


def run_backtest(
    db: Session,
    settings: Settings,
    tickers: list[str],
    start: date,
    end: date,
    params: ORBParams,
) -> BacktestResult:
    all_trades: list[Trade] = []

    for ticker in tickers:
        ensure_bars_cached(db, settings, ticker, start, end, timeframe="minute")
        bars = get_bars(db, ticker, start, end, timeframe="minute")
        days = _group_by_session_day(bars)

        capital = params.initial_capital
        for day in sorted(days):
            day_bars = sorted(days[day], key=lambda b: b.timestamp)
            trade = _simulate_day(ticker, day_bars, params, capital)
            if trade:
                all_trades.append(trade)
                capital += trade.pnl  # compounding within a ticker's own walk-forward capital

    all_trades.sort(key=lambda t: t.entry_time)

    equity_curve: list[tuple[datetime, float]] = []
    capital = params.initial_capital
    for trade in all_trades:
        capital += trade.pnl
        equity_curve.append((trade.exit_time, capital))

    metrics = compute_metrics(all_trades, params.initial_capital, capital)
    return BacktestResult(trades=all_trades, equity_curve=equity_curve, metrics=metrics)


def compute_metrics(trades: list[Trade], initial_capital: float, final_capital: float) -> dict:
    if not trades:
        return {"total_trades": 0}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    win_rate = len(wins) / len(trades) * 100
    avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(t.pnl for t in losses) / len(losses)) if losses else 0.0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss else None
    expectancy = sum(t.pnl for t in trades) / len(trades)
    avg_r = sum(t.r_multiple for t in trades) / len(trades)
    total_return_pct = (final_capital - initial_capital) / initial_capital * 100

    peak = initial_capital
    running = initial_capital
    max_dd = 0.0
    for t in trades:
        running += t.pnl
        peak = max(peak, running)
        if peak > 0:
            max_dd = max(max_dd, (peak - running) / peak * 100)

    longest_win_streak = longest_loss_streak = cur_win = cur_loss = 0
    for t in trades:
        if t.pnl > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        longest_win_streak = max(longest_win_streak, cur_win)
        longest_loss_streak = max(longest_loss_streak, cur_loss)

    return {
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 2),
        "loss_rate_pct": round(100 - win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "largest_win": round(max((t.pnl for t in trades), default=0.0), 2),
        "largest_loss": round(min((t.pnl for t in trades), default=0.0), 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "expectancy": round(expectancy, 2),
        "avg_r_multiple": round(avg_r, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "winning_streak": longest_win_streak,
        "losing_streak": longest_loss_streak,
        "final_capital": round(final_capital, 2),
    }


def save_run(
    db: Session,
    tickers: list[str],
    start: date,
    end: date,
    params: ORBParams,
    result: BacktestResult,
) -> BacktestRun:
    """Persist a completed backtest so it shows up in run history later."""
    run = BacktestRun(
        strategy_name="Opening Range Breakout",
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
