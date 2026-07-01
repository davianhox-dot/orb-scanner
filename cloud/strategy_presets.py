"""
Strategy presets (starting points you can tweak, not locked-in code paths)
and JSON serialization so a built strategy can be saved to and loaded back
from the database.
"""
import copy

from cloud.strategy_engine import ExitRuleConfig, PositionSizingConfig, StrategyConfig
from cloud.strategy_rules import Condition


def config_to_dict(config: StrategyConfig) -> dict:
    return {
        "name": config.name,
        "trend_filters": [{"type": c.type, "params": c.params} for c in config.trend_filters],
        "entry_conditions": [{"type": c.type, "params": c.params} for c in config.entry_conditions],
        "entry_logic": config.entry_logic,
        "exit_rules": config.exit_rules.__dict__,
        "position_sizing": config.position_sizing.__dict__,
        "initial_capital": config.initial_capital,
    }


def config_from_dict(d: dict) -> StrategyConfig:
    return StrategyConfig(
        name=d.get("name", "Custom Strategy"),
        trend_filters=[Condition(c["type"], c["params"]) for c in d.get("trend_filters", [])],
        entry_conditions=[Condition(c["type"], c["params"]) for c in d.get("entry_conditions", [])],
        entry_logic=d.get("entry_logic", "AND"),
        exit_rules=ExitRuleConfig(**d.get("exit_rules", {})),
        position_sizing=PositionSizingConfig(**d.get("position_sizing", {})),
        initial_capital=d.get("initial_capital", 10_000.0),
    )


_PRESETS: dict[str, StrategyConfig] = {
    "Consolidation Breakout": StrategyConfig(
        name="Consolidation Breakout",
        entry_conditions=[Condition("consolidation_breakout", {"lookback": 15, "max_range_pct": 15.0})],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="swing_low", stop_value=15, target_type="r_multiple", target_value=3.0, max_holding_days=20
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "EMA Pullback": StrategyConfig(
        name="EMA Pullback",
        trend_filters=[Condition("above_ema", {"period": 50})],
        entry_conditions=[Condition("pullback_to_ema", {"period": 20, "tolerance_pct": 2.0})],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="atr_multiple", stop_value=2.0, stop_atr_period=14,
            target_type="r_multiple", target_value=2.5, max_holding_days=25,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "Trend Following": StrategyConfig(
        name="Trend Following",
        trend_filters=[
            Condition("ema_above_ema", {"fast_period": 20, "slow_period": 50}),
            Condition("adx_above", {"period": 14, "value": 20}),
        ],
        entry_conditions=[Condition("breakout_high", {"lookback": 20})],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="atr_multiple", stop_value=2.5, stop_atr_period=14,
            target_type="r_multiple", target_value=4.0,
            trailing_stop=True, trailing_pct=10.0, max_holding_days=60,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "RSI Mean Reversion": StrategyConfig(
        name="RSI Mean Reversion",
        entry_conditions=[Condition("rsi_cross_above", {"period": 14, "value": 30})],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="fixed_pct", stop_value=7.0, target_type="fixed_pct", target_value=10.0, max_holding_days=15
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
}

PRESET_NAMES = list(_PRESETS.keys())


def get_preset(name: str) -> StrategyConfig:
    """Returns a deep copy so editing the loaded preset in the UI never
    mutates the shared template."""
    return copy.deepcopy(_PRESETS[name])
