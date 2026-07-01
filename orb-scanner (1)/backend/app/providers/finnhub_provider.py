"""
Finnhub provider stub.

Implements the same `DataProvider` contract as Polygon. Fill in
`get_premarket_universe` / `get_news` using Finnhub's REST API
(https://finnhub.io/docs/api) — the scanner and scoring engine will work
unchanged once this returns real `TickerSnapshot`/`NewsItem` objects.

Suggested endpoints:
  - /stock/symbol?exchange=US            (universe)
  - /quote?symbol=X                      (price / prev close)
  - /stock/profile2?symbol=X             (market cap, shares outstanding)
  - /company-news?symbol=X&from=&to=     (catalyst news)
"""
import logging

from app.core.config import get_settings
from app.providers.base import DataProvider, NewsItem, ProviderHealth, TickerSnapshot
from app.providers.polygon_provider import _mock_news, _mock_universe

logger = logging.getLogger(__name__)


class FinnhubProvider(DataProvider):
    name = "finnhub"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_key = self.settings.FINNHUB_API_KEY

    async def health(self) -> ProviderHealth:
        if self.api_key:
            return ProviderHealth(name=self.name, configured=True, mode="live")
        return ProviderHealth(
            name=self.name, configured=False, mode="mock",
            detail="FINNHUB_API_KEY not set and adapter is a stub — serving sample data.",
        )

    async def get_premarket_universe(self) -> list[TickerSnapshot]:
        logger.warning("FinnhubProvider is a stub — returning mock universe. Implement me.")
        return _mock_universe()

    async def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        return _mock_news(ticker)
