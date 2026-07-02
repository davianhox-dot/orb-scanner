"""
Top Setups — the highest-level scan: every strategy × every universe ticker,
with automatic per-ticker backtesting and a composite quality score.

Pipeline:
  1. Scan the universe with EVERY strategy (presets + saved) via
     strategy_scanner.scan_for_signals — collecting every (strategy, ticker)
     pair whose signal fired on the latest trading day.
  2. For each hit, automatically backtest THAT strategy on THAT ticker over
     the past N years (using the persistent daily-bar cache).
  3. Filter: at least `min_trades` historical trades AND positive
     expectancy. Hits with 5-9 trades pass but carry a low-sample flag.
  4. Composite score (0-100):
        40%  profit factor   (capped at 4.0 -> 100; None i.e. no losses -> 100)
        30%  win rate        (already 0-100)
        30%  avg R multiple  (0R -> 0, 3R+ -> 100)
     Why avg R instead of dollar expectancy: dollars depend on account size
     and position sizing; R is unit-free and comparable across strategies.
  5. Deduplicate by ticker (a stock hit by two strategies keeps only its
     best-scoring one) and return the top K.

Honest caveat, stated here and in the UI: "backtested successfully on this
ticker" is itself a selection effect — screening 150 stocks for the
prettiest history will surface some lucky ones. The min-trades filter and
the low-sample flag are the mitigation, not a cure. Treat the output as a
pre-filtered shortlist for YOUR judgment, not as buy orders.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from cloud.config import Settings
from cloud.db import HistoricalBar
from cloud.strategy_engine import StrategyConfig, run_strategy_backtest
from cloud.strategy_scanner import scan_for_signals

logger = logging.getLogger(__name__)

LOW_SAMPLE_THRESHOLD = 10  # 5-9 trades => pass with warning


@dataclass
class TopSetup:
    ticker: str
    strategy_name: str
    signal_date: str
    entry: float
    stop: float
    target: float
    risk_reward: float
    score: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float | None
    avg_r_multiple: float
    expectancy: float
    max_drawdown_pct: float
    low_sample: bool
    pro_factors: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)


@dataclass
class TopSetupsResult:
    top: list[TopSetup] = field(default_factory=list)
    all_candidates: list[TopSetup] = field(default_factory=list)
    hits_scanned: int = 0
    hits_rejected: int = 0


def composite_score(metrics: dict) -> float:
    """0-100 blend: 40% profit factor, 30% win rate, 30% avg R."""
    pf = metrics.get("profit_factor")
    pf_score = 100.0 if pf is None else max(0.0, min(pf, 4.0)) / 4.0 * 100
    wr_score = max(0.0, min(metrics.get("win_rate_pct", 0.0) or 0.0, 100.0))
    avg_r = metrics.get("avg_r_multiple", 0.0) or 0.0
    r_score = max(0.0, min(avg_r / 3.0 * 100, 100.0))
    return round(pf_score * 0.40 + wr_score * 0.30 + r_score * 0.30, 1)


def find_top_setups(
    db: Session,
    settings: Settings,
    strategies: list[tuple[str, StrategyConfig]],
    bars_by_ticker: dict[str, list[HistoricalBar]],
    backtest_years: int = 2,
    min_trades: int = 5,
    top_k: int = 3,
    progress_callback: Callable[[str], None] | None = None,
) -> TopSetupsResult:
    result = TopSetupsResult()
    bt_end = date.today() - timedelta(days=1)
    bt_start = bt_end - timedelta(days=365 * backtest_years)

    # --- 1. collect signals across all strategies ---
    hits: list[tuple[str, StrategyConfig, object]] = []
    for name, config in strategies:
        if not config.entry_conditions:
            continue
        if progress_callback:
            progress_callback(f"Scanning universe with '{name}'…")
        for hit in scan_for_signals(bars_by_ticker, config):
            hits.append((name, config, hit))

    result.hits_scanned = len(hits)
    if not hits:
        return result

    # --- 2+3. auto-backtest each hit, filter on quality ---
    candidates: list[TopSetup] = []
    for idx, (name, config, hit) in enumerate(hits):
        if progress_callback:
            progress_callback(f"Backtesting hit {idx + 1}/{len(hits)}: {hit.ticker} × '{name}'…")
        try:
            bt = run_strategy_backtest(db, settings, [hit.ticker], bt_start, bt_end, config)
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the whole scan
            logger.warning("Backtest failed for %s × %s: %s", hit.ticker, name, exc)
            result.hits_rejected += 1
            continue

        m = bt.metrics
        trades_n = m.get("total_trades", 0)
        if trades_n < min_trades or (m.get("expectancy") or 0) <= 0:
            result.hits_rejected += 1
            continue

        candidates.append(
            TopSetup(
                ticker=hit.ticker,
                strategy_name=name,
                signal_date=hit.signal_date,
                entry=hit.entry,
                stop=hit.stop,
                target=hit.target,
                risk_reward=hit.risk_reward,
                score=composite_score(m),
                total_trades=trades_n,
                win_rate_pct=m.get("win_rate_pct", 0.0),
                profit_factor=m.get("profit_factor"),
                avg_r_multiple=m.get("avg_r_multiple", 0.0),
                expectancy=m.get("expectancy", 0.0),
                max_drawdown_pct=m.get("max_drawdown_pct", 0.0),
                low_sample=trades_n < LOW_SAMPLE_THRESHOLD,
            )
        )

    # --- 4+5. rank, dedup by ticker, take top K ---
    candidates.sort(key=lambda c: c.score, reverse=True)
    result.all_candidates = candidates

    seen_tickers: set[str] = set()
    for c in candidates:
        if c.ticker in seen_tickers:
            continue
        seen_tickers.add(c.ticker)
        result.top.append(c)
        if len(result.top) >= top_k:
            break

    # --- 6. rationale (pro/contra factors + news) — only for the final
    # top-K, so news fetching stays at max top_k API calls ---
    from cloud.setup_rationale import build_rationale  # local import avoids a cycle

    for setup in result.top:
        if progress_callback:
            progress_callback(f"Begründung für {setup.ticker} erstellen…")
        rationale = build_rationale(
            settings, setup.ticker, bars_by_ticker.get(setup.ticker, []),
            entry=setup.entry, stop=setup.stop,
            total_trades=setup.total_trades, win_rate_pct=setup.win_rate_pct,
            profit_factor=setup.profit_factor, low_sample=setup.low_sample,
        )
        setup.pro_factors = rationale.pro_factors
        setup.risk_factors = rationale.risk_factors
        setup.news = rationale.news

    return result


def format_alert_message(top: list[TopSetup], scan_day: str) -> str:
    """Compact Discord/Telegram message for the nightly job."""
    if not top:
        return f"📡 Top Setups {scan_day}: no qualifying setup today."
    lines = [f"🏆 Top Setups {scan_day}:"]
    for i, s in enumerate(top, start=1):
        flag = " ⚠️wenig Historie" if s.low_sample else ""
        lines.append(
            f"{i}. {s.ticker} · {s.strategy_name} · Score {s.score:.0f}{flag}\n"
            f"   Entry >{s.entry:.2f} · SL {s.stop:.2f} · TP {s.target:.2f} (R:R {s.risk_reward:.1f})\n"
            f"   Historie: {s.total_trades} Trades, {s.win_rate_pct:.0f}% WR, PF "
            f"{'∞' if s.profit_factor is None else f'{s.profit_factor:.2f}'}"
        )
    return "\n".join(lines)
