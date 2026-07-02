"""
Strategy condition catalog — the building blocks the strategy builder UI
lets you combine into entry rules and trend filters.

Each condition is a `Condition(type=..., params={...})`. `evaluate_condition`
returns True/False, or None if there isn't enough lookback data yet at that
bar index (callers should treat None as "not evaluable today", not False).

`IndicatorCache` computes each indicator series at most once per backtest
(keyed by type+parameters) even though many conditions may reference the
same indicator with the same settings.
"""
from dataclasses import dataclass, field

from cloud.db import HistoricalBar
from cloud.indicators import adx, atr, bollinger_bands, ema, macd, rsi, volume_sma


@dataclass
class Condition:
    type: str
    params: dict = field(default_factory=dict)


CONDITION_CATALOG: dict[str, dict] = {
    "above_ema": {
        "label": "Close above EMA",
        "params": {"period": {"label": "EMA period", "default": 20, "min": 2, "max": 300}},
    },
    "ema_above_ema": {
        "label": "Fast EMA above slow EMA",
        "params": {
            "fast_period": {"label": "Fast EMA period", "default": 20, "min": 2, "max": 300},
            "slow_period": {"label": "Slow EMA period", "default": 50, "min": 2, "max": 300},
        },
    },
    "breakout_high": {
        "label": "Close breaks above N-day high",
        "params": {"lookback": {"label": "Lookback days", "default": 20, "min": 2, "max": 250}},
    },
    "consolidation_breakout": {
        "label": "Breakout from tight consolidation",
        "params": {
            "lookback": {"label": "Consolidation length (days)", "default": 15, "min": 3, "max": 120},
            "max_range_pct": {"label": "Max range width (%)", "default": 15.0, "min": 2.0, "max": 60.0},
        },
    },
    "pullback_to_ema": {
        "label": "Pullback to EMA (bounce)",
        "params": {
            "period": {"label": "EMA period", "default": 20, "min": 2, "max": 300},
            "tolerance_pct": {"label": "Touch tolerance (%)", "default": 2.0, "min": 0.1, "max": 15.0},
        },
    },
    "pullback_to_any_ema": {
        "label": "Pullback to fast OR slow EMA (bounce)",
        "params": {
            "fast_period": {"label": "Fast EMA period", "default": 20, "min": 2, "max": 300},
            "slow_period": {"label": "Slow EMA period", "default": 50, "min": 2, "max": 300},
            "tolerance_pct": {"label": "Touch tolerance (%)", "default": 2.0, "min": 0.1, "max": 15.0},
        },
    },
    "ema_rising": {
        "label": "EMA rising (vs N days ago)",
        "params": {
            "period": {"label": "EMA period", "default": 20, "min": 2, "max": 300},
            "lookback": {"label": "Compare vs N days ago", "default": 3, "min": 1, "max": 30},
        },
    },
    "bullish_candle": {
        "label": "Bullish candle (close above open)",
        "params": {},
    },
    "rsi_above": {
        "label": "RSI above value",
        "params": {
            "period": {"label": "RSI period", "default": 14, "min": 2, "max": 100},
            "value": {"label": "RSI value", "default": 50.0, "min": 0.0, "max": 100.0},
        },
    },
    "rsi_below": {
        "label": "RSI below value",
        "params": {
            "period": {"label": "RSI period", "default": 14, "min": 2, "max": 100},
            "value": {"label": "RSI value", "default": 30.0, "min": 0.0, "max": 100.0},
        },
    },
    "rsi_cross_above": {
        "label": "RSI crosses above value",
        "params": {
            "period": {"label": "RSI period", "default": 14, "min": 2, "max": 100},
            "value": {"label": "RSI value", "default": 50.0, "min": 0.0, "max": 100.0},
        },
    },
    "macd_bullish_cross": {
        "label": "MACD bullish crossover",
        "params": {
            "fast": {"label": "Fast period", "default": 12, "min": 2, "max": 100},
            "slow": {"label": "Slow period", "default": 26, "min": 2, "max": 200},
            "signal": {"label": "Signal period", "default": 9, "min": 2, "max": 100},
        },
    },
    "volume_above_avg": {
        "label": "Volume above average",
        "params": {
            "period": {"label": "Avg volume period", "default": 20, "min": 2, "max": 250},
            "multiplier": {"label": "Multiplier (x average)", "default": 1.5, "min": 1.0, "max": 10.0},
        },
    },
    "adx_above": {
        "label": "ADX above value (trend strength)",
        "params": {
            "period": {"label": "ADX period", "default": 14, "min": 2, "max": 100},
            "value": {"label": "ADX value", "default": 20.0, "min": 0.0, "max": 100.0},
        },
    },
    "higher_highs": {
        "label": "Higher highs (last N days)",
        "params": {"count": {"label": "Number of days", "default": 3, "min": 2, "max": 20}},
    },
    "higher_lows": {
        "label": "Higher lows (last N days)",
        "params": {"count": {"label": "Number of days", "default": 3, "min": 2, "max": 20}},
    },
}


class IndicatorCache:
    """Computes each indicator series at most once per backtest run."""

    def __init__(self, bars: list[HistoricalBar]):
        self.bars = bars
        self.closes = [b.close for b in bars]
        self._cache: dict[tuple, object] = {}

    def ema(self, period: int):
        key = ("ema", period)
        if key not in self._cache:
            self._cache[key] = ema(self.closes, period)
        return self._cache[key]

    def rsi(self, period: int):
        key = ("rsi", period)
        if key not in self._cache:
            self._cache[key] = rsi(self.closes, period)
        return self._cache[key]

    def atr(self, period: int):
        key = ("atr", period)
        if key not in self._cache:
            self._cache[key] = atr(self.bars, period)
        return self._cache[key]

    def adx(self, period: int):
        key = ("adx", period)
        if key not in self._cache:
            self._cache[key] = adx(self.bars, period)
        return self._cache[key]

    def volume_sma(self, period: int):
        key = ("volsma", period)
        if key not in self._cache:
            self._cache[key] = volume_sma(self.bars, period)
        return self._cache[key]

    def macd(self, fast: int, slow: int, signal: int):
        key = ("macd", fast, slow, signal)
        if key not in self._cache:
            self._cache[key] = macd(self.closes, fast, slow, signal)
        return self._cache[key]

    def bollinger(self, period: int, num_std: float):
        key = ("bb", period, num_std)
        if key not in self._cache:
            self._cache[key] = bollinger_bands(self.closes, period, num_std)
        return self._cache[key]


def evaluate_condition(cond: Condition, cache: IndicatorCache, i: int) -> bool | None:
    t, p, bars = cond.type, cond.params, cache.bars

    if t == "above_ema":
        series = cache.ema(int(p["period"]))
        if series[i] is None:
            return None
        return bars[i].close > series[i]

    if t == "ema_above_ema":
        fast = cache.ema(int(p["fast_period"]))
        slow = cache.ema(int(p["slow_period"]))
        if fast[i] is None or slow[i] is None:
            return None
        return fast[i] > slow[i]

    if t == "breakout_high":
        lookback = int(p["lookback"])
        if i < lookback:
            return None
        resistance = max(b.high for b in bars[i - lookback : i])
        return bars[i].close > resistance

    if t == "consolidation_breakout":
        lookback = int(p["lookback"])
        if i < lookback:
            return None
        window = bars[i - lookback : i]
        resistance = max(b.high for b in window)
        support = min(b.low for b in window)
        if resistance <= 0:
            return False
        range_pct = (resistance - support) / resistance * 100
        return range_pct <= float(p["max_range_pct"]) and bars[i].close > resistance

    if t == "pullback_to_ema":
        series = cache.ema(int(p["period"]))
        if series[i] is None:
            return None
        ema_val = series[i]
        tolerance_pct = float(p.get("tolerance_pct", 2.0))
        near_ema = abs(bars[i].low - ema_val) / ema_val * 100 <= tolerance_pct
        bouncing = bars[i].close > ema_val
        return near_ema and bouncing

    if t == "pullback_to_any_ema":
        fast = cache.ema(int(p["fast_period"]))
        slow = cache.ema(int(p["slow_period"]))
        if fast[i] is None or slow[i] is None:
            return None
        tolerance_pct = float(p.get("tolerance_pct", 2.0))

        def _bounce(ema_val: float) -> bool:
            near = abs(bars[i].low - ema_val) / ema_val * 100 <= tolerance_pct
            return near and bars[i].close > ema_val

        return _bounce(fast[i]) or _bounce(slow[i])

    if t == "ema_rising":
        series = cache.ema(int(p["period"]))
        lookback = int(p.get("lookback", 3))
        if i < lookback or series[i] is None or series[i - lookback] is None:
            return None
        return series[i] > series[i - lookback]

    if t == "bullish_candle":
        return bars[i].close > bars[i].open

    if t == "rsi_above":
        series = cache.rsi(int(p["period"]))
        if series[i] is None:
            return None
        return series[i] > float(p["value"])

    if t == "rsi_below":
        series = cache.rsi(int(p["period"]))
        if series[i] is None:
            return None
        return series[i] < float(p["value"])

    if t == "rsi_cross_above":
        series = cache.rsi(int(p["period"]))
        if i < 1 or series[i] is None or series[i - 1] is None:
            return None
        return series[i - 1] <= float(p["value"]) < series[i]

    if t == "macd_bullish_cross":
        macd_line, signal_line, _ = cache.macd(int(p.get("fast", 12)), int(p.get("slow", 26)), int(p.get("signal", 9)))
        if i < 1 or None in (macd_line[i], signal_line[i], macd_line[i - 1], signal_line[i - 1]):
            return None
        return macd_line[i - 1] <= signal_line[i - 1] and macd_line[i] > signal_line[i]

    if t == "volume_above_avg":
        series = cache.volume_sma(int(p.get("period", 20)))
        if series[i] is None:
            return None
        return bars[i].volume > float(p.get("multiplier", 1.5)) * series[i]

    if t == "adx_above":
        series = cache.adx(int(p.get("period", 14)))
        if series[i] is None:
            return None
        return series[i] > float(p["value"])

    if t == "higher_highs":
        n = int(p.get("count", 3))
        if i < n - 1:
            return None
        highs = [bars[j].high for j in range(i - n + 1, i + 1)]
        return all(highs[k] < highs[k + 1] for k in range(len(highs) - 1))

    if t == "higher_lows":
        n = int(p.get("count", 3))
        if i < n - 1:
            return None
        lows = [bars[j].low for j in range(i - n + 1, i + 1)]
        return all(lows[k] < lows[k + 1] for k in range(len(lows) - 1))

    raise ValueError(f"Unknown condition type: {t}")


def evaluate_all(conditions: list[Condition], logic: str, cache: IndicatorCache, i: int) -> bool | None:
    """Combine a list of conditions with AND/OR. Returns None if any
    condition needed for evaluation isn't ready yet (not enough lookback),
    since a day we can't fully evaluate shouldn't count as a signal."""
    if not conditions:
        return True

    results = [evaluate_condition(c, cache, i) for c in conditions]
    if any(r is None for r in results):
        return None

    return all(results) if logic == "AND" else any(results)
