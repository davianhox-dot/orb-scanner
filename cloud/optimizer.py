"""
Strategy optimizer — Phase 3.

Takes a base StrategyConfig plus a set of "vary this parameter over these
values" specs, runs a backtest for every combination (cartesian product),
and returns results ranked by a chosen metric.

Design notes:
- Historical daily bars are fetched/cached ONCE up front, then every
  combination reuses the cache — so a 100-combination grid costs 100 fast
  in-memory simulations, not 100 API calls.
- MAX_COMBINATIONS is a hard cap. Grid search grows multiplicatively
  (5 values x 5 values x 5 values = 125 runs) and a runaway grid would
  freeze the Streamlit UI. The UI shows the count before running.
- Ranking metric is configurable; ties are broken by total trades (more
  trades = statistically more trustworthy result).

An honest word on interpreting results (also shown in the UI): the
top-ranked combination is, by construction, the one that fit THIS past
data best. That's not the same as the one most likely to work in the
future — a result that only wins at exactly one parameter value while
neighboring values lose money is usually curve-fitting, not edge. Prefer
parameter regions where many neighboring values all perform decently.
Formal robustness testing (walk-forward, Monte Carlo) is Phase 4.
"""
import copy
import itertools
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from sqlalchemy.orm import Session

from cloud.backtest_engine import BacktestResult
from cloud.config import Settings
from cloud.db import OptimizationRun
from cloud.historical_data import ensure_bars_cached
from cloud.strategy_engine import StrategyConfig, run_strategy_backtest
from cloud.strategy_presets import config_to_dict

logger = logging.getLogger(__name__)

MAX_COMBINATIONS = 200

RANK_METRICS = {
    "profit_factor": "Profit Factor",
    "expectancy": "Expectancy ($)",
    "total_return_pct": "Total Return (%)",
    "win_rate_pct": "Win Rate (%)",
    "avg_r_multiple": "Avg R Multiple",
}


@dataclass
class ParamSpec:
    """One parameter to vary.

    `path` addresses a field inside StrategyConfig:
      ("entry", index, param_name)   — a param of the i-th entry condition
      ("trend", index, param_name)   — a param of the i-th trend filter
      ("exit", field_name)           — a field of ExitRuleConfig
      ("sizing", "value")            — position sizing value
    """

    label: str
    path: tuple
    values: list[float] = field(default_factory=list)


@dataclass
class OptimizationResultRow:
    params: dict[str, float]
    metrics: dict


def list_tunable_params(config: StrategyConfig) -> list[ParamSpec]:
    """Enumerate every numeric knob in a config the optimizer can vary,
    with empty value lists (the UI fills those in)."""
    specs: list[ParamSpec] = []

    for i, cond in enumerate(config.entry_conditions):
        for pname, pval in cond.params.items():
            if isinstance(pval, (int, float)):
                specs.append(ParamSpec(label=f"Entry {i + 1} ({cond.type}) · {pname}", path=("entry", i, pname)))

    for i, cond in enumerate(config.trend_filters):
        for pname, pval in cond.params.items():
            if isinstance(pval, (int, float)):
                specs.append(ParamSpec(label=f"Trend {i + 1} ({cond.type}) · {pname}", path=("trend", i, pname)))

    for fname in ("stop_value", "target_value", "trailing_pct", "max_holding_days"):
        specs.append(ParamSpec(label=f"Exit · {fname}", path=("exit", fname)))

    specs.append(ParamSpec(label="Position sizing · value", path=("sizing", "value")))
    return specs


def apply_param(config: StrategyConfig, path: tuple, value: float) -> None:
    """Set one parameter on a (deep-copied) config in place."""
    kind = path[0]
    if kind == "entry":
        _, idx, pname = path
        original = config.entry_conditions[idx].params.get(pname)
        config.entry_conditions[idx].params[pname] = int(value) if isinstance(original, int) else value
    elif kind == "trend":
        _, idx, pname = path
        original = config.trend_filters[idx].params.get(pname)
        config.trend_filters[idx].params[pname] = int(value) if isinstance(original, int) else value
    elif kind == "exit":
        _, fname = path
        if fname == "max_holding_days":
            setattr(config.exit_rules, fname, int(value))
        else:
            setattr(config.exit_rules, fname, value)
    elif kind == "sizing":
        config.position_sizing.value = value
    else:
        raise ValueError(f"Unknown param path kind: {kind}")


def count_combinations(specs: list[ParamSpec]) -> int:
    total = 1
    for s in specs:
        total *= max(len(s.values), 1)
    return total


def run_optimization(
    db: Session,
    settings: Settings,
    tickers: list[str],
    start: date,
    end: date,
    base_config: StrategyConfig,
    specs: list[ParamSpec],
    rank_by: str = "profit_factor",
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[OptimizationResultRow]:
    specs = [s for s in specs if s.values]
    if not specs:
        raise ValueError("No parameters with values to optimize.")

    total = count_combinations(specs)
    if total > MAX_COMBINATIONS:
        raise ValueError(
            f"{total} combinations exceeds the cap of {MAX_COMBINATIONS}. "
            f"Reduce the number of values per parameter."
        )

    # Fetch/cache all data ONCE before the grid — every combination after
    # this reads purely from the local cache.
    for ticker in tickers:
        ensure_bars_cached(db, settings, ticker, start, end, timeframe="day")

    rows: list[OptimizationResultRow] = []
    value_lists = [s.values for s in specs]

    for run_index, combo in enumerate(itertools.product(*value_lists)):
        config = copy.deepcopy(base_config)
        combo_params: dict[str, float] = {}
        for spec, value in zip(specs, combo):
            apply_param(config, spec.path, value)
            combo_params[spec.label] = value

        result: BacktestResult = run_strategy_backtest(db, settings, tickers, start, end, config)
        rows.append(OptimizationResultRow(params=combo_params, metrics=result.metrics))

        if progress_callback:
            progress_callback(run_index + 1, total)

    def sort_key(row: OptimizationResultRow):
        value = row.metrics.get(rank_by)
        # None (e.g. profit_factor with zero losses -> stored as None) sorts
        # as "infinitely good"; missing metrics (zero trades) sort last.
        if row.metrics.get("total_trades", 0) == 0:
            return (-1, float("-inf"), 0)
        if value is None:
            return (1, float("inf"), row.metrics.get("total_trades", 0))
        return (0, value, row.metrics.get("total_trades", 0))

    rows.sort(key=sort_key, reverse=True)
    return rows


def save_optimization(
    db: Session,
    tickers: list[str],
    start: date,
    end: date,
    base_config: StrategyConfig,
    specs: list[ParamSpec],
    rank_by: str,
    rows: list[OptimizationResultRow],
) -> OptimizationRun:
    run = OptimizationRun(
        strategy_name=base_config.name,
        tickers=tickers,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        base_config=config_to_dict(base_config),
        param_specs=[{"label": s.label, "path": list(s.path), "values": s.values} for s in specs],
        rank_by=rank_by,
        results=[{"params": r.params, "metrics": r.metrics} for r in rows],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
