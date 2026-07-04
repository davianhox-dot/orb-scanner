"""
Strategy validation — Phase 4.

Four independent checks, then a combined 0-100 robustness score:

1. IN-SAMPLE / OUT-OF-SAMPLE (IS/OOS)
   Run the same config on an early window (IS) and the later window (OOS).
   If you tuned parameters on recent history (e.g. in the Optimizer), the
   later window acts as data the parameters never "saw". A strategy whose
   OOS expectancy collapses relative to IS is likely curve-fit.

2. WALK-FORWARD SEGMENTS
   Split the full range into K equal time segments and run the config on
   each one separately. A robust strategy is profitable across most
   segments; one giant winner in a single segment carrying everything is a
   fragility signal.

3. MONTE CARLO SIMULATION
   Bootstrap-resample the backtest's trade P&Ls (with replacement) many
   times to build a distribution of alternative equity paths. The question
   it answers: "if the same kinds of trades had arrived in a different
   order/mix, how bad could the drawdown have been, and how often would I
   have lost money overall?" Deterministic given a seed.

4. PARAMETER ROBUSTNESS (sensitivity analysis)
   Perturb every numeric parameter by ±10% and ±20% around its base value
   and re-run. If small nudges to a parameter destroy performance, the
   base value sits on a curve-fit cliff rather than in a robust region.

ROBUSTNESS SCORE (0-100)
   Weighted blend of four component scores (each 0-100):
     35%  segment consistency  (fraction of WF segments that are profitable)
     30%  parameter stability  (avg expectancy retention under perturbation)
     20%  Monte Carlo risk     (P95 max-drawdown mapped to a score)
     15%  OOS retention        (OOS expectancy / IS expectancy, capped)
   Weights are stated here on purpose: this is a heuristic summary to focus
   attention, not a statistical guarantee. Anything under OVERFIT_THRESHOLD
   triggers an explicit overfitting warning in the UI.
"""
import copy
import logging
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from cloud.backtest_engine import Trade
from cloud.config import Settings
from cloud.historical_data import ensure_bars_cached
from cloud.optimizer import apply_param, list_tunable_params
from cloud.strategy_engine import StrategyConfig, run_strategy_backtest

logger = logging.getLogger(__name__)

OVERFIT_THRESHOLD = 50.0
MIN_TRADES_FOR_CONFIDENCE = 10


# --------------------------------------------------------------------- #
# 1. In-sample / out-of-sample
# --------------------------------------------------------------------- #

@dataclass
class ISOOSResult:
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date
    is_metrics: dict
    oos_metrics: dict
    retention_pct: float | None  # OOS expectancy as % of IS expectancy


def in_sample_out_of_sample(
    db: Session, settings: Settings, tickers: list[str], start: date, end: date,
    config: StrategyConfig, split_pct: float = 70.0,
) -> ISOOSResult:
    total_days = (end - start).days
    split_point = start + timedelta(days=int(total_days * split_pct / 100))

    is_result = run_strategy_backtest(db, settings, tickers, start, split_point, config)
    oos_result = run_strategy_backtest(db, settings, tickers, split_point + timedelta(days=1), end, config)

    retention: float | None = None
    is_exp = is_result.metrics.get("expectancy")
    oos_exp = oos_result.metrics.get("expectancy")
    if is_exp and is_exp > 0 and oos_exp is not None:
        retention = round(oos_exp / is_exp * 100, 1)

    return ISOOSResult(
        is_start=start, is_end=split_point,
        oos_start=split_point + timedelta(days=1), oos_end=end,
        is_metrics=is_result.metrics, oos_metrics=oos_result.metrics,
        retention_pct=retention,
    )


# --------------------------------------------------------------------- #
# 2. Walk-forward segments
# --------------------------------------------------------------------- #

@dataclass
class WalkForwardResult:
    segments: list[dict] = field(default_factory=list)  # {start, end, metrics}
    profitable_segments: int = 0
    segments_with_trades: int = 0


def walk_forward(
    db: Session, settings: Settings, tickers: list[str], start: date, end: date,
    config: StrategyConfig, n_segments: int = 4,
) -> WalkForwardResult:
    total_days = (end - start).days
    seg_days = max(total_days // n_segments, 1)

    out = WalkForwardResult()
    for k in range(n_segments):
        seg_start = start + timedelta(days=k * seg_days)
        seg_end = end if k == n_segments - 1 else seg_start + timedelta(days=seg_days - 1)
        result = run_strategy_backtest(db, settings, tickers, seg_start, seg_end, config)
        m = result.metrics
        out.segments.append({"start": seg_start.isoformat(), "end": seg_end.isoformat(), "metrics": m})
        if m.get("total_trades", 0) > 0:
            out.segments_with_trades += 1
            if m.get("expectancy", 0) > 0:
                out.profitable_segments += 1
    return out


# --------------------------------------------------------------------- #
# 3. Monte Carlo
# --------------------------------------------------------------------- #

@dataclass
class MonteCarloResult:
    n_sims: int
    n_trades: int
    final_equity_p5: float
    final_equity_p50: float
    final_equity_p95: float
    max_drawdown_p50: float
    max_drawdown_p95: float
    prob_losing_overall_pct: float
    equity_distribution: list[float] = field(default_factory=list)  # final equities, for the histogram


def monte_carlo(
    trades: list[Trade], initial_capital: float, n_sims: int = 1000, seed: int = 42
) -> MonteCarloResult | None:
    """Bootstrap-resamples the trade P&L list (with replacement, same
    length) and replays each resampled sequence to get a distribution of
    final equity and max drawdown. Deterministic for a given seed."""
    if not trades:
        return None

    pnls = [t.pnl for t in trades]
    rng = random.Random(seed)

    final_equities: list[float] = []
    max_drawdowns: list[float] = []

    for _ in range(n_sims):
        sample = [rng.choice(pnls) for _ in range(len(pnls))]
        equity = initial_capital
        peak = initial_capital
        max_dd = 0.0
        for pnl in sample:
            equity += pnl
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100)
        final_equities.append(equity)
        max_drawdowns.append(max_dd)

    final_equities.sort()
    max_drawdowns.sort()

    def pct(sorted_vals: list[float], p: float) -> float:
        idx = min(int(len(sorted_vals) * p / 100), len(sorted_vals) - 1)
        return sorted_vals[idx]

    losing = sum(1 for e in final_equities if e < initial_capital)

    return MonteCarloResult(
        n_sims=n_sims,
        n_trades=len(trades),
        final_equity_p5=round(pct(final_equities, 5), 2),
        final_equity_p50=round(pct(final_equities, 50), 2),
        final_equity_p95=round(pct(final_equities, 95), 2),
        max_drawdown_p50=round(pct(max_drawdowns, 50), 2),
        max_drawdown_p95=round(pct(max_drawdowns, 95), 2),
        prob_losing_overall_pct=round(losing / n_sims * 100, 1),
        equity_distribution=final_equities,
    )


# --------------------------------------------------------------------- #
# 4. Parameter robustness (sensitivity)
# --------------------------------------------------------------------- #

@dataclass
class PerturbationRow:
    param_label: str
    perturbation_pct: float
    value: float
    expectancy: float | None
    total_trades: int
    retention_pct: float | None  # vs base expectancy


@dataclass
class RobustnessResult:
    base_expectancy: float
    base_trades: int
    rows: list[PerturbationRow] = field(default_factory=list)
    avg_retention_pct: float | None = None


def parameter_robustness(
    db: Session, settings: Settings, tickers: list[str], start: date, end: date,
    config: StrategyConfig,
    perturbations: tuple[float, ...] = (-20.0, -10.0, 10.0, 20.0),
    progress_callback: Callable[[int, int], None] | None = None,
) -> RobustnessResult:
    base_result = run_strategy_backtest(db, settings, tickers, start, end, config)
    base_exp = base_result.metrics.get("expectancy", 0.0) or 0.0
    base_trades = base_result.metrics.get("total_trades", 0)

    out = RobustnessResult(base_expectancy=base_exp, base_trades=base_trades)

    specs = list_tunable_params(config)
    # Only perturb params whose base value is meaningful (non-zero); e.g.
    # trailing_pct on a strategy without a trailing stop is skipped.
    active_specs = []
    for spec in specs:
        base_val = _read_param(config, spec.path)
        if base_val and base_val != 0:
            if spec.path == ("exit", "trailing_pct") and not config.exit_rules.trailing_stop:
                continue
            if spec.path == ("exit", "indicator_exit_period") and not config.exit_rules.indicator_exit:
                continue
            active_specs.append((spec, base_val))

    total_runs = len(active_specs) * len(perturbations)
    done = 0

    retentions: list[float] = []
    for spec, base_val in active_specs:
        for pert in perturbations:
            new_val = base_val * (1 + pert / 100)
            cfg = copy.deepcopy(config)
            apply_param(cfg, spec.path, new_val)
            result = run_strategy_backtest(db, settings, tickers, start, end, cfg)
            exp = result.metrics.get("expectancy")
            trades_n = result.metrics.get("total_trades", 0)

            retention: float | None = None
            if base_exp > 0 and exp is not None:
                retention = round(exp / base_exp * 100, 1)
                # clamp: a perturbed run that got LUCKIER than base still
                # counts as (at most) 100% retained, and deep losses floor at 0
                retentions.append(max(0.0, min(retention, 100.0)))

            out.rows.append(
                PerturbationRow(
                    param_label=spec.label, perturbation_pct=pert, value=round(new_val, 3),
                    expectancy=round(exp, 2) if exp is not None else None,
                    total_trades=trades_n, retention_pct=retention,
                )
            )
            done += 1
            if progress_callback:
                progress_callback(done, total_runs)

    if retentions:
        out.avg_retention_pct = round(sum(retentions) / len(retentions), 1)
    return out


def _read_param(config: StrategyConfig, path: tuple) -> float | None:
    kind = path[0]
    if kind == "entry":
        _, idx, pname = path
        return config.entry_conditions[idx].params.get(pname)
    if kind == "trend":
        _, idx, pname = path
        return config.trend_filters[idx].params.get(pname)
    if kind == "exit":
        return getattr(config.exit_rules, path[1])
    if kind == "sizing":
        return config.position_sizing.value
    return None


# --------------------------------------------------------------------- #
# Combined robustness score
# --------------------------------------------------------------------- #

@dataclass
class ValidationSummary:
    score: float  # 0-100
    overfit_warning: bool
    low_sample_warning: bool
    components: dict = field(default_factory=dict)  # name -> (score, note)


def robustness_score(
    wf: WalkForwardResult,
    robustness: RobustnessResult,
    mc: MonteCarloResult | None,
    isoos: ISOOSResult,
) -> ValidationSummary:
    components: dict = {}

    # 35% — segment consistency
    if wf.segments_with_trades > 0:
        consistency = wf.profitable_segments / wf.segments_with_trades * 100
        note = f"{wf.profitable_segments}/{wf.segments_with_trades} segments with trades were profitable"
    else:
        consistency = 0.0
        note = "no segment produced any trades"
    components["segment_consistency"] = (round(consistency, 1), note)

    # 30% — parameter stability
    if robustness.avg_retention_pct is not None:
        stability = max(0.0, min(robustness.avg_retention_pct, 100.0))
        note = f"average expectancy retention under ±10-20% parameter shifts: {robustness.avg_retention_pct}%"
    else:
        stability = 0.0
        note = "base run unprofitable or produced no trades — stability not measurable"
    components["parameter_stability"] = (round(stability, 1), note)

    # 20% — Monte Carlo drawdown risk: p95 max-DD of 0% -> 100, 50%+ -> 0
    if mc is not None:
        mc_score = max(0.0, 100 - mc.max_drawdown_p95 * 2)
        note = f"P95 max drawdown {mc.max_drawdown_p95}%, chance of overall loss {mc.prob_losing_overall_pct}%"
    else:
        mc_score = 0.0
        note = "no trades available for simulation"
    components["monte_carlo_risk"] = (round(mc_score, 1), note)

    # 15% — OOS retention, capped at 100
    if isoos.retention_pct is not None:
        oos_score = max(0.0, min(isoos.retention_pct, 100.0))
        note = f"out-of-sample expectancy retained {isoos.retention_pct}% of in-sample"
    elif isoos.oos_metrics.get("total_trades", 0) == 0:
        oos_score = 0.0
        note = "no out-of-sample trades — cannot verify the strategy on unseen data"
    else:
        oos_score = 0.0
        note = "in-sample was unprofitable — OOS retention not meaningful"
    components["oos_retention"] = (round(oos_score, 1), note)

    score = round(
        components["segment_consistency"][0] * 0.35
        + components["parameter_stability"][0] * 0.30
        + components["monte_carlo_risk"][0] * 0.20
        + components["oos_retention"][0] * 0.15,
        1,
    )

    total_trades = (mc.n_trades if mc else 0)
    return ValidationSummary(
        score=score,
        overfit_warning=score < OVERFIT_THRESHOLD,
        low_sample_warning=total_trades < MIN_TRADES_FOR_CONFIDENCE,
        components=components,
    )


def run_full_validation(
    db: Session, settings: Settings, tickers: list[str], start: date, end: date,
    config: StrategyConfig,
    n_segments: int = 4, split_pct: float = 70.0, n_sims: int = 1000, seed: int = 42,
    progress_callback: Callable[[str], None] | None = None,
):
    """Convenience wrapper running all four checks + the combined score."""
    for ticker in tickers:
        ensure_bars_cached(db, settings, ticker, start, end, timeframe="day")

    if progress_callback:
        progress_callback("Running full-period backtest…")
    full = run_strategy_backtest(db, settings, tickers, start, end, config)

    if progress_callback:
        progress_callback("In-sample / out-of-sample split…")
    isoos = in_sample_out_of_sample(db, settings, tickers, start, end, config, split_pct)

    if progress_callback:
        progress_callback(f"Walk-forward across {n_segments} segments…")
    wf = walk_forward(db, settings, tickers, start, end, config, n_segments)

    if progress_callback:
        progress_callback("Parameter robustness (this is the slow part)…")
    robust = parameter_robustness(db, settings, tickers, start, end, config)

    if progress_callback:
        progress_callback(f"Monte Carlo ({n_sims} simulations)…")
    mc = monte_carlo(full.trades, config.initial_capital, n_sims=n_sims, seed=seed)

    summary = robustness_score(wf, robust, mc, isoos)
    return full, isoos, wf, robust, mc, summary
