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
    grade: str = "C"
    grade_points: float = 0.0
    grade_reasons: list[str] = field(default_factory=list)
    score_adjusted: float = 0.0
    history_trades: list[dict] = field(default_factory=list)  # past backtest trades, for the signal-history chart
    overlays: list[dict] = field(default_factory=list)  # indicator overlays for the chart, derived from the strategy
    # Confluence: other strategies that ALSO fired on this ticker today.
    # Entries: {"strategy": name, "qualified": bool, "score": float|None, "grade": str|None}
    also_matched: list[dict] = field(default_factory=list)
    confluence_bonus: float = 0.0


def _overlays_from_config(config: StrategyConfig) -> list[dict]:
    """Derive which price-scale overlays the chart should draw so the
    'confluences' behind each signal are visible: EMAs for EMA-based
    conditions, rolling high/low range lines for breakout conditions.
    Non-price indicators (RSI, MACD, ADX, volume) live on other scales and
    are deliberately not drawn on the price chart."""
    overlays: list[dict] = []
    seen: set[tuple] = set()

    def add(kind: str, **params) -> None:
        key = (kind, tuple(sorted(params.items())))
        if key not in seen:
            seen.add(key)
            overlays.append({"kind": kind, **params})

    for cond in list(config.trend_filters) + list(config.entry_conditions):
        p = cond.params
        if cond.type in ("above_ema", "ema_rising", "pullback_to_ema"):
            add("ema", period=int(p.get("period", 20)))
        elif cond.type in ("ema_above_ema", "ema_cross_above_ema"):
            add("ema", period=int(p.get("fast_period", 20)))
            add("ema", period=int(p.get("slow_period", 50)))
        elif cond.type == "pullback_to_any_ema":
            add("ema", period=int(p.get("fast_period", 20)))
            add("ema", period=int(p.get("slow_period", 50)))
        elif cond.type == "breakout_high":
            add("rolling_high", lookback=int(p.get("lookback", 20)))
        elif cond.type == "consolidation_breakout":
            add("rolling_high", lookback=int(p.get("lookback", 15)))
            add("rolling_low", lookback=int(p.get("lookback", 15)))
        elif cond.type == "bollinger_bounce":
            add("bollinger", period=int(p.get("period", 20)), num_std=float(p.get("num_std", 2.0)))

    if config.exit_rules.indicator_exit:
        add("ema", period=int(config.exit_rules.indicator_exit_period))

    return overlays
    pro_factors: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)


@dataclass
class TopSetupsResult:
    top: list[TopSetup] = field(default_factory=list)
    all_candidates: list[TopSetup] = field(default_factory=list)
    hits_scanned: int = 0
    hits_rejected: int = 0
    market_regime: dict = field(default_factory=dict)
    # Per-strategy transparency: [{"strategy": name, "signals": n, "qualified": m}]
    strategy_stats: list[dict] = field(default_factory=list)


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
    spy_bars: list[HistoricalBar] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> TopSetupsResult:
    from cloud.market_regime import compute_regime
    from cloud.setup_grade import grade_setup

    result = TopSetupsResult()
    # Realistic execution costs for every auto-backtest in the scan —
    # env-overridable (TOPSETUPS_SLIPPAGE_PCT / TOPSETUPS_COMMISSION).
    # Applied to copies; saved strategies/presets themselves stay untouched.
    import copy as _copy
    import os as _os
    def _env_num(name: str, default: float) -> float:
        raw = _os.environ.get(name)
        if raw is None or not str(raw).strip():
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    _slip = _env_num("TOPSETUPS_SLIPPAGE_PCT", 0.1)
    _comm = _env_num("TOPSETUPS_COMMISSION", 1.0)
    costed: list[tuple[str, StrategyConfig]] = []
    for _name, _cfg in strategies:
        _c = _copy.deepcopy(_cfg)
        _c.slippage_pct = max(_c.slippage_pct, _slip)
        _c.commission_per_order = max(_c.commission_per_order, _comm)
        costed.append((_name, _c))
    strategies = costed

    regime = compute_regime(spy_bars)
    result.market_regime = regime.to_dict()
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

        base_score = composite_score(m)
        g = grade_setup(
            bars_by_ticker.get(hit.ticker, []), spy_bars,
            entry=hit.entry, stop=hit.stop, target=hit.target,
            max_holding_days=config.exit_rules.max_holding_days,
        )
        history_trades = [
            {
                "entry_date": t.entry_time.date().isoformat(),
                "exit_date": t.exit_time.date().isoformat(),
                "entry": round(t.entry_price, 2),
                "exit": round(t.exit_price, 2),
                "r": round(t.r_multiple, 2),
                "reason": t.exit_reason,
            }
            for t in bt.trades
        ]

        candidates.append(
            TopSetup(
                ticker=hit.ticker,
                strategy_name=name,
                signal_date=hit.signal_date,
                entry=hit.entry,
                stop=hit.stop,
                target=hit.target,
                risk_reward=hit.risk_reward,
                score=base_score,
                total_trades=trades_n,
                win_rate_pct=m.get("win_rate_pct", 0.0),
                profit_factor=m.get("profit_factor"),
                avg_r_multiple=m.get("avg_r_multiple", 0.0),
                expectancy=m.get("expectancy", 0.0),
                max_drawdown_pct=m.get("max_drawdown_pct", 0.0),
                low_sample=trades_n < LOW_SAMPLE_THRESHOLD,
                grade=g.grade,
                grade_points=g.points,
                grade_reasons=g.reasons,
                score_adjusted=round(max(0.0, base_score + regime.score_adjust), 1),
                history_trades=history_trades,
                overlays=_overlays_from_config(config),
            )
        )

    # --- 4. Confluence: a ticker firing for MULTIPLE strategies on the same
    # day is independent confirmation. Each candidate learns which other
    # strategies also matched its ticker (qualified or not), and gets a
    # small, transparent score bonus per additional QUALIFIED strategy
    # (+5 each, capped at +10 — confirmation helps, but shouldn't let a
    # C-grade pile beat a clean single-strategy A-setup).
    signals_by_ticker: dict[str, list[str]] = {}
    for name, _, hit in hits:
        signals_by_ticker.setdefault(hit.ticker, []).append(name)
    qualified_by_ticker: dict[str, list[TopSetup]] = {}
    for c in candidates:
        qualified_by_ticker.setdefault(c.ticker, []).append(c)

    for c in candidates:
        others: list[dict] = []
        for other in qualified_by_ticker.get(c.ticker, []):
            if other.strategy_name != c.strategy_name:
                others.append({
                    "strategy": other.strategy_name, "qualified": True,
                    "score": other.score, "grade": other.grade,
                })
        qualified_names = {o["strategy"] for o in others} | {c.strategy_name}
        for sig_name in signals_by_ticker.get(c.ticker, []):
            if sig_name not in qualified_names and not any(o["strategy"] == sig_name for o in others):
                others.append({"strategy": sig_name, "qualified": False, "score": None, "grade": None})
        c.also_matched = others
        c.confluence_bonus = min(5.0 * sum(1 for o in others if o["qualified"]), 10.0)
        c.score_adjusted = round(c.score_adjusted + c.confluence_bonus, 1)

    # --- Per-strategy transparency stats ---
    signal_counts: dict[str, int] = {}
    for name, _, _hit in hits:
        signal_counts[name] = signal_counts.get(name, 0) + 1
    qualified_counts: dict[str, int] = {}
    for c in candidates:
        qualified_counts[c.strategy_name] = qualified_counts.get(c.strategy_name, 0) + 1
    result.strategy_stats = [
        {"strategy": name, "signals": signal_counts.get(name, 0), "qualified": qualified_counts.get(name, 0)}
        for name, _cfg in strategies
        if _cfg.entry_conditions
    ]

    # --- 5. rank (grade first, then bonus-adjusted score, then sample size
    # as tiebreaker: identical scores are common when few-trade records are
    # perfect, and more historical trades = more trustworthy), dedup, top K ---
    grade_order = {"A": 0, "B": 1, "C": 2}
    candidates.sort(key=lambda c: (grade_order.get(c.grade, 3), -c.score_adjusted, -c.total_trades))
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


def format_alert_message(top: list[TopSetup], scan_day: str, regime: dict | None = None) -> str:
    """Compact Discord/Telegram message for the nightly job."""
    ampel = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get((regime or {}).get("status", ""), "")
    header = f"🏆 Top Setups {scan_day}" + (f" · Markt {ampel}" if ampel else "")
    if not top:
        return f"📡 {header}: no qualifying setup today."
    lines = [f"{header}:"]
    for i, s in enumerate(top, start=1):
        flag = " ⚠️wenig Historie" if s.low_sample else ""
        confl = ""
        qualified_others = [o["strategy"] for o in (s.also_matched or []) if o.get("qualified")]
        if qualified_others:
            confl = f" 🔗Konfluenz mit: {', '.join(qualified_others)}"
        lines.append(
            f"{i}. {s.ticker} · Note {s.grade} · {s.strategy_name} · Score {s.score_adjusted:.0f}{flag}{confl}\n"
            f"   Entry >{s.entry:.2f} · SL {s.stop:.2f} · TP {s.target:.2f} (R:R {s.risk_reward:.1f})\n"
            f"   Historie: {s.total_trades} Trades, {s.win_rate_pct:.0f}% WR, PF "
            f"{'∞' if s.profit_factor is None else f'{s.profit_factor:.2f}'}"
        )
    return "\n".join(lines)
