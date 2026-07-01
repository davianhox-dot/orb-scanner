from app.core.config import Settings
from app.providers.base import NewsItem, TickerSnapshot
from app.services.catalyst import detect_catalysts, news_quality_score
from app.services.scanner import _passes_filters, build_trading_plan


def test_detect_fda_catalyst():
    news = [NewsItem(headline="Company X receives FDA approval for new drug")]
    tags, best = detect_catalysts(news)
    assert "FDA" in tags
    assert best is not None


def test_detect_multiple_catalysts_in_one_headline():
    news = [NewsItem(headline="Company X announces merger and FDA clearance in same week")]
    tags, _ = detect_catalysts(news)
    assert "FDA" in tags
    assert "Merger" in tags


def test_no_catalyst_when_headline_is_generic():
    news = [NewsItem(headline="Company X opens new regional office")]
    tags, best = detect_catalysts(news)
    assert tags == []
    # still surfaces the freshest headline even with no matched category
    assert best is not None


def test_no_news_returns_empty():
    tags, best = detect_catalysts([])
    assert tags == []
    assert best is None


def test_news_quality_score_ranks_fda_above_press_release():
    assert news_quality_score(["FDA"]) > news_quality_score(["Press Release"])


def test_news_quality_score_empty_is_zero():
    assert news_quality_score([]) == 0.0


def _snapshot(**overrides) -> TickerSnapshot:
    base = dict(
        ticker="TEST", price=5.0, gap_pct=40.0, premarket_volume=1_500_000,
        relative_volume=8.0, float_shares=8_000_000, market_cap=150_000_000,
        is_etf=False, is_preferred=False, is_warrant=False,
    )
    base.update(overrides)
    return TickerSnapshot(**base)


def test_passes_filters_happy_path():
    s = Settings()
    assert _passes_filters(_snapshot(), s) is True


def test_fails_filters_when_price_out_of_range():
    s = Settings()
    assert _passes_filters(_snapshot(price=25.0), s) is False


def test_fails_filters_when_gap_too_small():
    s = Settings()
    assert _passes_filters(_snapshot(gap_pct=5.0), s) is False


def test_fails_filters_when_etf():
    s = Settings()
    assert _passes_filters(_snapshot(is_etf=True), s) is False


def test_fails_filters_when_float_too_large():
    s = Settings()
    assert _passes_filters(_snapshot(float_shares=50_000_000), s) is False


def test_build_trading_plan_produces_positive_risk_reward():
    plan = build_trading_plan(price=5.0, premarket_high=5.5, premarket_low=4.8)
    assert plan["stop"] < plan["orb_entry"] < plan["target"]
    assert plan["risk_reward_ratio"] > 0
