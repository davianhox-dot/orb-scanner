"""
Technical indicators computed from a list of daily bars.

Pure Python (no numpy/pandas) so this has zero new dependencies. Each
function returns a list the same length as `closes`/`bars`, with `None` for
indices where there isn't enough lookback data yet — callers should skip
indices where the indicator is None.

Formulas follow the standard/Wilder conventions used by most charting
platforms (TradingView, etc.) so periods behave the way traders expect.
"""
from cloud.db import HistoricalBar


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1 : i + 1]) / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out

    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_averages(avg_gain, avg_loss)

    return out


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    macd_line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None for f, s in zip(ema_fast, ema_slow)
    ]

    # EMA of the MACD line, but only over the contiguous non-None tail
    first_valid = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: list[float | None] = [None] * len(closes)
    if first_valid is not None:
        macd_values = [v for v in macd_line[first_valid:]]
        signal_partial = ema(macd_values, signal)  # type: ignore[arg-type]
        for i, v in enumerate(signal_partial):
            signal_line[first_valid + i] = v

    histogram: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


def atr(bars: list[HistoricalBar], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    if len(bars) < period + 1:
        return out

    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    avg = sum(true_ranges[:period]) / period
    out[period] = avg
    for i in range(period, len(true_ranges)):
        avg = (avg * (period - 1) + true_ranges[i]) / period
        out[i + 1] = avg

    return out


def adx(bars: list[HistoricalBar], period: int = 14) -> list[float | None]:
    """Wilder's ADX. Returns the ADX line (trend strength, 0-100)."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period * 2:
        return out

    plus_dm = [0.0]
    minus_dm = [0.0]
    tr_list = [0.0]
    for i in range(1, n):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i - 1].close),
            abs(bars[i].low - bars[i - 1].close),
        )
        tr_list.append(tr)

    def wilder_smooth(values: list[float], period: int) -> list[float | None]:
        smoothed: list[float | None] = [None] * len(values)
        if len(values) <= period:
            return smoothed
        seed = sum(values[1 : period + 1])
        smoothed[period] = seed
        prev = seed
        for i in range(period + 1, len(values)):
            prev = prev - (prev / period) + values[i]
            smoothed[i] = prev
        return smoothed

    smoothed_tr = wilder_smooth(tr_list, period)
    smoothed_plus_dm = wilder_smooth(plus_dm, period)
    smoothed_minus_dm = wilder_smooth(minus_dm, period)

    dx_list: list[float | None] = [None] * n
    for i in range(period, n):
        if smoothed_tr[i] in (None, 0) or smoothed_plus_dm[i] is None or smoothed_minus_dm[i] is None:
            continue
        plus_di = 100 * smoothed_plus_dm[i] / smoothed_tr[i]
        minus_di = 100 * smoothed_minus_dm[i] / smoothed_tr[i]
        di_sum = plus_di + minus_di
        dx_list[i] = 100 * abs(plus_di - minus_di) / di_sum if di_sum else 0.0

    valid_dx = [(i, v) for i, v in enumerate(dx_list) if v is not None]
    if len(valid_dx) < period:
        return out

    first_idx = valid_dx[0][0]
    seed_adx = sum(v for _, v in valid_dx[:period]) / period
    adx_index = first_idx + period - 1
    out[adx_index] = seed_adx
    prev = seed_adx
    for i in range(adx_index + 1, n):
        if dx_list[i] is None:
            continue
        prev = (prev * (period - 1) + dx_list[i]) / period
        out[i] = prev

    return out


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (upper_band, middle_band, lower_band)."""
    middle = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = middle[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance**0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std

    return upper, middle, lower


def volume_sma(bars: list[HistoricalBar], period: int = 20) -> list[float | None]:
    return sma([float(b.volume) for b in bars], period)
