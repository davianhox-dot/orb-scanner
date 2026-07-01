"""Scoring engine — identical logic to the backend version, self-contained
for the cloud path. See backend/app/services/scoring.py for full comments."""
from dataclasses import dataclass

from cloud.catalyst import news_quality_score
from cloud.config import Settings
from cloud.providers import TickerSnapshot


@dataclass
class ScoreResult:
    total: float
    breakdown: dict[str, float]
    risk: str


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score_gap(gap_pct: float) -> float:
    return _clamp((gap_pct / 50) * 100)


def _score_float(float_shares: float) -> float:
    if float_shares <= 0:
        return 0.0
    if float_shares <= 5_000_000:
        return 100.0
    if float_shares >= 30_000_000:
        return 20.0
    return _clamp(100 - ((float_shares - 5_000_000) / 25_000_000) * 80)


def _score_relative_volume(rel_vol: float) -> float:
    return _clamp((rel_vol / 20) * 100)


def _score_premarket_volume(volume: int) -> float:
    return _clamp((volume / 3_000_000) * 100)


def _score_atr(atr: float, price: float) -> float:
    if price <= 0:
        return 0.0
    atr_pct = atr / price * 100
    if atr_pct <= 15:
        return _clamp((atr_pct / 15) * 100)
    return _clamp(100 - (atr_pct - 15) * 2)


def _score_average_volume(avg_volume: int) -> float:
    return _clamp((avg_volume / 1_000_000) * 100)


def _score_spread(spread_pct: float) -> float:
    return _clamp(100 - spread_pct * 40)


def _score_prev_resistance(price: float, resistance: float) -> float:
    if resistance <= 0:
        return 50.0
    proximity = 1 - abs(price - resistance) / resistance
    return _clamp(proximity * 100)


def _score_historical_volatility(vol_pct: float) -> float:
    return _score_atr(vol_pct, 100)


def _score_recent_halts(recent_halt: bool) -> float:
    return 30.0 if recent_halt else 100.0


def calculate_score(snapshot: TickerSnapshot, catalyst_tags: list[str], settings: Settings) -> ScoreResult:
    s = settings
    factors = {
        "gap": (_score_gap(snapshot.gap_pct), s.WEIGHT_GAP),
        "float": (_score_float(snapshot.float_shares), s.WEIGHT_FLOAT),
        "relative_volume": (_score_relative_volume(snapshot.relative_volume), s.WEIGHT_REL_VOLUME),
        "premarket_volume": (_score_premarket_volume(snapshot.premarket_volume), s.WEIGHT_PREMARKET_VOLUME),
        "news_quality": (news_quality_score(catalyst_tags), s.WEIGHT_NEWS_QUALITY),
        "atr": (_score_atr(snapshot.atr, snapshot.price), s.WEIGHT_ATR),
        "average_volume": (_score_average_volume(snapshot.average_volume), s.WEIGHT_AVG_VOLUME),
        "spread": (_score_spread(snapshot.spread_pct), s.WEIGHT_SPREAD),
        "previous_resistance": (_score_prev_resistance(snapshot.price, snapshot.resistance), s.WEIGHT_PREV_RESISTANCE),
        "historical_volatility": (_score_historical_volatility(snapshot.historical_volatility_pct), s.WEIGHT_HISTORICAL_VOLATILITY),
        "recent_halts": (_score_recent_halts(snapshot.recent_halt), s.WEIGHT_RECENT_HALTS),
    }

    total_weight = sum(w for _, w in factors.values()) or 1.0
    breakdown = {name: round(value, 1) for name, (value, _) in factors.items()}
    weighted_sum = sum(value * weight for value, weight in factors.values())
    total = round(weighted_sum / total_weight, 1)

    return ScoreResult(total=_clamp(total), breakdown=breakdown, risk=_assess_risk(snapshot))


def _assess_risk(snapshot: TickerSnapshot) -> str:
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
