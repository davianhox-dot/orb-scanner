"""
Interactive Brokers provider stub — same `DataProvider` contract as Polygon.

IB requires a running TWS/IB Gateway session (host/port configured via
IB_GATEWAY_HOST / IB_GATEWAY_PORT) and a client library such as `ib_insync`
or IB's native API. Because it's a stateful socket connection rather than a
REST call, wrap connect/disconnect lifecycle in this class's __init__ /
a dedicated async context manager instead of per-call HTTP requests.
"""
import logging

from app.core.config import get_settings
from app.providers.base import DataProvider, NewsItem, ProviderHealth, TickerSnapshot
from app.providers.polygon_provider import _mock_news, _mock_universe

logger = logging.getLogger(__name__)


class IBProvider(DataProvider):
    name = "ib"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.host = self.settings.IB_GATEWAY_HOST
        self.port = self.settings.IB_GATEWAY_PORT

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name, configured=False, mode="mock",
            detail="IBProvider is a stub — requires a live TWS/IB Gateway session. Serving sample data.",
        )

    async def get_premarket_universe(self) -> list[TickerSnapshot]:
        logger.warning("IBProvider is a stub — returning mock universe. Implement me with ib_insync.")
        return _mock_universe()

    async def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        return _mock_news(ticker)
