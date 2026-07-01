"""
Alpaca provider stub — same `DataProvider` contract as Polygon.

Suggested endpoints (https://docs.alpaca.markets/):
  - GET /v2/assets                                  (universe, tradable US equities)
  - GET /v2/stocks/snapshots?symbols=...             (bulk price/prevClose)
  - Alpaca does not provide float/short-interest/news directly for free tiers;
    pair with a reference-data provider for those fields if selecting Alpaca
    as the primary source.
"""
import logging

from app.core.config import get_settings
from app.providers.base import DataProvider, NewsItem, ProviderHealth, TickerSnapshot
from app.providers.polygon_provider import _mock_news, _mock_universe

logger = logging.getLogger(__name__)


class AlpacaProvider(DataProvider):
    name = "alpaca"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_key = self.settings.ALPACA_API_KEY
        self.api_secret = self.settings.ALPACA_API_SECRET

    async def health(self) -> ProviderHealth:
        if self.api_key and self.api_secret:
            return ProviderHealth(name=self.name, configured=True, mode="live")
        return ProviderHealth(
            name=self.name, configured=False, mode="mock",
            detail="ALPACA_API_KEY/SECRET not set and adapter is a stub — serving sample data.",
        )

    async def get_premarket_universe(self) -> list[TickerSnapshot]:
        logger.warning("AlpacaProvider is a stub — returning mock universe. Implement me.")
        return _mock_universe()

    async def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        return _mock_news(ticker)
