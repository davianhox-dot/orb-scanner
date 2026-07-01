"""
TwelveData provider stub — same `DataProvider` contract as Polygon.

Suggested endpoints (https://twelvedata.com/docs):
  - /stocks?country=United States           (universe)
  - /quote?symbol=X                         (price/prevClose/volume)
  - /statistics?symbol=X                    (market cap, shares outstanding)
"""
import logging

from app.core.config import get_settings
from app.providers.base import DataProvider, NewsItem, ProviderHealth, TickerSnapshot
from app.providers.polygon_provider import _mock_news, _mock_universe

logger = logging.getLogger(__name__)


class TwelveDataProvider(DataProvider):
    name = "twelvedata"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_key = self.settings.TWELVEDATA_API_KEY

    async def health(self) -> ProviderHealth:
        if self.api_key:
            return ProviderHealth(name=self.name, configured=True, mode="live")
        return ProviderHealth(
            name=self.name, configured=False, mode="mock",
            detail="TWELVEDATA_API_KEY not set and adapter is a stub — serving sample data.",
        )

    async def get_premarket_universe(self) -> list[TickerSnapshot]:
        logger.warning("TwelveDataProvider is a stub — returning mock universe. Implement me.")
        return _mock_universe()

    async def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        return _mock_news(ticker)
