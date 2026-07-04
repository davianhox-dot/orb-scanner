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
        "entry_fill": config.entry_fill,
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
        entry_fill=d.get("entry_fill", "next_open"),  # older saved strategies default to original behavior
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
        # Checklist: above EMA50 AND EMA200, EMA20 rising
        trend_filters=[
            Condition("above_ema", {"period": 50}),
            Condition("above_ema", {"period": 200}),
            Condition("ema_rising", {"period": 20, "lookback": 3}),
        ],
        # Checklist: pullback to EMA20 OR EMA50, plus a bullish confirmation candle
        entry_conditions=[
            Condition("pullback_to_any_ema", {"fast_period": 20, "slow_period": 50, "tolerance_pct": 2.0}),
            Condition("bullish_candle", {}),
        ],
        entry_logic="AND",
        # Checklist: entry above the confirmation candle's high (buy-stop) —
        # if the next day never breaks that high, there is NO trade.
        entry_fill="break_signal_high",
        # Checklist: stop under the recent low (swing low of the pullback)
        exit_rules=ExitRuleConfig(
            stop_type="swing_low", stop_value=5,
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
    "52W-Hoch Breakout": StrategyConfig(
        name="52W-Hoch Breakout",
        trend_filters=[Condition("ema_above_ema", {"fast_period": 50, "slow_period": 200})],
        entry_conditions=[
            Condition("breakout_high", {"lookback": 250}),
            Condition("volume_above_avg", {"period": 20, "multiplier": 1.5}),
        ],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="atr_multiple", stop_value=2.5, stop_atr_period=14,
            target_type="r_multiple", target_value=5.0,
            trailing_stop=True, trailing_pct=12.0, max_holding_days=60,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "Turtle 55-Tage Breakout": StrategyConfig(
        name="Turtle 55-Tage Breakout",
        entry_conditions=[
            Condition("breakout_high", {"lookback": 55}),
            Condition("adx_above", {"period": 14, "value": 20}),
        ],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="atr_multiple", stop_value=2.0, stop_atr_period=14,
            target_type="r_multiple", target_value=4.0,
            trailing_stop=True, trailing_pct=10.0, max_holding_days=60,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "RSI(2) Dip-Kauf (Connors)": StrategyConfig(
        name="RSI(2) Dip-Kauf (Connors)",
        trend_filters=[Condition("above_ema", {"period": 200})],
        entry_conditions=[Condition("rsi_below", {"period": 2, "value": 10.0})],
        entry_logic="AND",
        # Connors' original exits on a close back above a short MA — that's
        # the indicator exit. Fixed target sits far away on purpose so the
        # indicator exit is what usually fires.
        exit_rules=ExitRuleConfig(
            stop_type="fixed_pct", stop_value=6.0,
            target_type="fixed_pct", target_value=20.0, max_holding_days=10,
            indicator_exit=True, indicator_exit_type="close_above_ema", indicator_exit_period=5,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "Bull Flag": StrategyConfig(
        name="Bull Flag",
        entry_conditions=[
            Condition("strong_prior_run", {"lookback": 20, "min_gain_pct": 30.0}),
            Condition("consolidation_breakout", {"lookback": 7, "max_range_pct": 10.0}),
        ],
        entry_logic="AND",
        entry_fill="break_signal_high",
        exit_rules=ExitRuleConfig(
            stop_type="swing_low", stop_value=7,
            target_type="r_multiple", target_value=3.0, max_holding_days=15,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "VCP Breakout": StrategyConfig(
        name="VCP Breakout",
        trend_filters=[Condition("above_ema", {"period": 50})],
        entry_conditions=[
            Condition("range_contraction", {"recent_days": 5, "prior_days": 15, "max_ratio_pct": 60.0}),
            Condition("breakout_high", {"lookback": 20}),
            Condition("volume_above_avg", {"period": 20, "multiplier": 1.5}),
        ],
        entry_logic="AND",
        entry_fill="break_signal_high",
        exit_rules=ExitRuleConfig(
            stop_type="swing_low", stop_value=10,
            target_type="r_multiple", target_value=3.0,
            trailing_stop=True, trailing_pct=10.0, max_holding_days=30,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "Gap-Up Continuation": StrategyConfig(
        name="Gap-Up Continuation",
        trend_filters=[Condition("above_ema", {"period": 50})],
        entry_conditions=[
            Condition("gap_up", {"min_gap_pct": 3.0}),
            Condition("volume_above_avg", {"period": 20, "multiplier": 2.0}),
            Condition("bullish_candle", {}),
        ],
        entry_logic="AND",
        entry_fill="break_signal_high",
        exit_rules=ExitRuleConfig(
            stop_type="fixed_pct", stop_value=6.0,
            target_type="r_multiple", target_value=2.5, max_holding_days=10,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "Golden Cross": StrategyConfig(
        name="Golden Cross",
        entry_conditions=[Condition("ema_cross_above_ema", {"fast_period": 50, "slow_period": 200})],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="atr_multiple", stop_value=2.5, stop_atr_period=14,
            target_type="r_multiple", target_value=6.0, max_holding_days=120,
            indicator_exit=True, indicator_exit_type="close_below_ema", indicator_exit_period=50,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
    "Bollinger Bounce": StrategyConfig(
        name="Bollinger Bounce",
        trend_filters=[Condition("above_ema", {"period": 200})],
        entry_conditions=[Condition("bollinger_bounce", {"period": 20, "num_std": 2.0})],
        entry_logic="AND",
        exit_rules=ExitRuleConfig(
            stop_type="fixed_pct", stop_value=7.0,
            target_type="fixed_pct", target_value=25.0, max_holding_days=15,
            indicator_exit=True, indicator_exit_type="close_above_ema", indicator_exit_period=20,
        ),
        position_sizing=PositionSizingConfig(method="fixed_pct_risk", value=1.0),
    ),
}

PRESET_NAMES = list(_PRESETS.keys())


def get_preset(name: str) -> StrategyConfig:
    """Returns a deep copy so editing the loaded preset in the UI never
    mutates the shared template."""
    return copy.deepcopy(_PRESETS[name])
