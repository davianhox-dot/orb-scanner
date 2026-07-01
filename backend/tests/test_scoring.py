from app.core.config import Settings
from app.providers.base import TickerSnapshot
from app.services.scoring import calculate_score


def _snapshot(**overrides) -> TickerSnapshot:
    base = dict(
        ticker="TEST",
        price=5.0,
        gap_pct=40.0,
        premarket_volume=1_500_000,
        relative_volume=8.0,
        float_shares=8_000_000,
        market_cap=150_000_000,
        atr=0.6,
        average_volume=900_000,
        resistance=5.2,
        spread_pct=0.3,
        historical_volatility_pct=12.0,
        recent_halt=False,
    )
    base.update(overrides)
    return TickerSnapshot(**base)


def test_score_is_between_0_and_100():
    result = calculate_score(_snapshot(), catalyst_tags=["FDA"], weights=Settings())
    assert 0.0 <= result.total <= 100.0


def test_strong_setup_scores_higher_than_weak_setup():
    strong = calculate_score(_snapshot(), catalyst_tags=["FDA", "Clinical Trial"], weights=Settings())
    weak = calculate_score(
        _snapshot(gap_pct=21.0, relative_volume=5.1, float_shares=29_000_000, premarket_volume=520_000),
        catalyst_tags=[],
        weights=Settings(),
    )
    assert strong.total > weak.total


def test_no_catalyst_scores_lower_news_quality_component():
    with_catalyst = calculate_score(_snapshot(), catalyst_tags=["Acquisition"], weights=Settings())
    without_catalyst = calculate_score(_snapshot(), catalyst_tags=[], weights=Settings())
    assert with_catalyst.breakdown["news_quality"] > without_catalyst.breakdown["news_quality"]
    assert with_catalyst.total > without_catalyst.total


def test_low_float_scores_higher_float_component_than_high_float():
    low_float = calculate_score(_snapshot(float_shares=3_000_000), catalyst_tags=[], weights=Settings())
    high_float = calculate_score(_snapshot(float_shares=29_000_000), catalyst_tags=[], weights=Settings())
    assert low_float.breakdown["float"] > high_float.breakdown["float"]


def test_recent_halt_increases_risk_label():
    halted = calculate_score(
        _snapshot(recent_halt=True, float_shares=3_000_000, gap_pct=120, spread_pct=2.0),
        catalyst_tags=[],
        weights=Settings(),
    )
    assert halted.risk in ("medium", "high")


def test_custom_weights_change_ranking():
    """If we weight news quality at 0 and gap at 100, a huge-gap/no-catalyst
    stock should outrank a modest-gap/strong-catalyst stock."""
    weights = Settings(
        WEIGHT_GAP=100, WEIGHT_FLOAT=0, WEIGHT_REL_VOLUME=0, WEIGHT_PREMARKET_VOLUME=0,
        WEIGHT_NEWS_QUALITY=0, WEIGHT_ATR=0, WEIGHT_AVG_VOLUME=0, WEIGHT_SPREAD=0,
        WEIGHT_PREV_RESISTANCE=0, WEIGHT_HISTORICAL_VOLATILITY=0, WEIGHT_RECENT_HALTS=0,
    )
    big_gap_no_catalyst = calculate_score(_snapshot(gap_pct=90), catalyst_tags=[], weights=weights)
    small_gap_catalyst = calculate_score(_snapshot(gap_pct=21), catalyst_tags=["FDA"], weights=weights)
    assert big_gap_no_catalyst.total > small_gap_catalyst.total
