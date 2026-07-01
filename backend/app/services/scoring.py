"""
Scoring engine.

Produces a 0-100 composite score for a candidate plus a per-factor
breakdown (so the UI/detail page can explain *why* a stock scored the way
it did, not just show a number). Each factor is normalized to 0-100 first,
then combined using the configured weights.

Weights are pulled from Settings by default but can be overridden per-call
(e.g. from the /settings API so users can tune live without redeploying).
"""
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.providers.base import TickerSnapshot
from app.services.catalyst import news_quality_score


@dataclass
class ScoreResult:
    total: float
    breakdown: dict[str, float]
    risk: str  # low | medium | high


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score_gap(gap_pct: float) -> float:
    # 20% gap -> ~50, 50%+ gap -> 100. Diminishing returns above that; huge
    # gaps often mean the easy move already happened.
    return _clamp((gap_pct / 50) * 100)


def _score_float(float_shares: float) -> float:
    if float_shares <= 0:
        return 0.0
    # Lower float -> higher score. <5M is ideal for explosive moves.
    if float_shares <= 5_000_000:
        return 100.0
    if float_shares >= 30_000_000:
        return 20.0
    # Linear interpolation between 5M (100) and 30M (20)
    return _clamp(100 - ((float_shares - 5_000_000) / 25_000_000) * 80)


def _score_relative_volume(rel_vol: float) -> float:
    # 5x -> 50, 20x+ -> 100
    return _clamp((rel_vol / 20) * 100)


def _score_premarket_volume(volume: int) -> float:
    # 500k -> 50, 3M+ -> 100
    return _clamp((volume / 3_000_000) * 100)


def _score_atr(atr: float, price: float) -> float:
    if price <= 0:
        return 0.0
    atr_pct = atr / price * 100
    # ~15% daily ATR is a strong day-trading range; beyond ~30% gets
    # dangerously choppy and is penalized slightly.
    if atr_pct <= 15:
        return _clamp((atr_pct / 15) * 100)
    return _clamp(100 - (atr_pct - 15) * 2)


def _score_average_volume(avg_volume: int) -> float:
    # Enough liquidity to get in/out cleanly. 1M+ average daily volume -> 100.
    return _clamp((avg_volume / 1_000_000) * 100)


def _score_spread(spread_pct: float) -> float:
    # Tighter spread is better; >2% spread is punishing for day trades.
    return _clamp(100 - spread_pct * 40)


def _score_prev_resistance(price: float, resistance: float) -> float:
    # Reward setups trading near/through recent resistance (breakout context)
    if resistance <= 0:
        return 50.0
    proximity = 1 - abs(price - resistance) / resistance
    return _clamp(proximity * 100)


def _score_historical_volatility(vol_pct: float) -> float:
    return _score_atr(vol_pct, 100)  # same curve, vol_pct already a percent


def _score_recent_halts(recent_halt: bool) -> float:
    # A recent halt cuts both ways (violent moves, but also higher risk of
    # another halt against you) — scored as a mild negative by default.
    return 30.0 if recent_halt else 100.0


def calculate_score(
    snapshot: TickerSnapshot,
    catalyst_tags: list[str],
    weights: Settings | None = None,
) -> ScoreResult:
    s = weights or get_settings()

    factors = {
        "gap": (_score_gap(snapshot.gap_pct), s.WEIGHT_GAP),
        "float": (_score_float(snapshot.float_shares), s.WEIGHT_FLOAT),
        "relative_volume": (_score_relative_volume(snapshot.relative_volume), s.WEIGHT_REL_VOLUME),
        "premarket_volume": (_score_premarket_volume(snapshot.premarket_volume), s.WEIGHT_PREMARKET_VOLUME),
        "news_quality": (news_quality_score(catalyst_tags), s.WEIGHT_NEWS_QUALITY),
        "atr": (_score_atr(snapshot.atr, snapshot.price), s.WEIGHT_ATR),
        "average_volume": (_score_average_volume(snapshot.average_volume), s.WEIGHT_AVG_VOLUME),
        "spread": (_score_spread(snapshot.spread_pct), s.WEIGHT_SPREAD),
        "previous_resistance": (
            _score_prev_resistance(snapshot.price, snapshot.resistance),
            s.WEIGHT_PREV_RESISTANCE,
        ),
        "historical_volatility": (
            _score_historical_volatility(snapshot.historical_volatility_pct),
            s.WEIGHT_HISTORICAL_VOLATILITY,
        ),
        "recent_halts": (_score_recent_halts(snapshot.recent_halt), s.WEIGHT_RECENT_HALTS),
    }

    total_weight = sum(w for _, w in factors.values()) or 1.0
    breakdown = {name: round(value, 1) for name, (value, _) in factors.items()}
    weighted_sum = sum(value * weight for value, weight in factors.values())
    total = round(weighted_sum / total_weight, 1)

    risk = _assess_risk(snapshot)
    return ScoreResult(total=_clamp(total), breakdown=breakdown, risk=risk)


def _assess_risk(snapshot: TickerSnapshot) -> str:
    """Simple heuristic risk label surfaced next to the score — low float +
    huge gap + thin spread history reads as high risk/high reward."""
    risk_points = 0
    if snapshot.float_shares and snapshot.float_shares < 5_000_000:
        risk_points += 1
    if snapshot.gap_pct > 100:
        risk_points += 1
    if snapshot.spread_pct > 1.5:
        risk_points += 1
    if snapshot.recent_halt:
        risk_points += 1
    if snapshot.average_volume and snapshot.average_volume < 200_000:
        risk_points += 1

    if risk_points >= 3:
        return "high"
    if risk_points >= 1:
        return "medium"
    return "low"
