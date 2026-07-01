"""
Data provider interface.

Every market data vendor (Polygon, Finnhub, Alpaca, TwelveData, Interactive
Brokers) implements this same contract. The scanner and scoring engine only
ever talk to `DataProvider` — swapping vendors is a one-line config change
(`DATA_PROVIDER` in .env), never a code change in `services/`.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TickerSnapshot:
    """Normalized shape every provider must return, regardless of its native API format."""

    ticker: str
    company: str = ""
    sector: str = ""
    price: float = 0.0
    previous_close: float = 0.0
    gap_pct: float = 0.0
    premarket_pct: float = 0.0
    premarket_volume: int = 0
    premarket_high: float = 0.0
    premarket_low: float = 0.0
    average_volume: int = 0
    relative_volume: float = 0.0
    float_shares: float = 0.0
    market_cap: float = 0.0
    is_etf: bool = False
    is_preferred: bool = False
    is_warrant: bool = False
    atr: float = 0.0
    previous_day_high: float = 0.0
    previous_day_low: float = 0.0
    support: float = 0.0
    resistance: float = 0.0
    spread_pct: float = 0.0
    historical_volatility_pct: float = 0.0
    recent_halt: bool = False
    short_interest_pct: float = 0.0


@dataclass
class NewsItem:
    headline: str
    url: str = ""
    published_at: str = ""
    source: str = ""


@dataclass
class ProviderHealth:
    name: str
    configured: bool
    mode: str  # "live" | "mock"
    detail: str = ""


class DataProvider(ABC):
    """Contract every market data vendor adapter must satisfy."""

    name: str = "base"

    @abstractmethod
    async def get_premarket_universe(self) -> list[TickerSnapshot]:
        """Return every US common-stock ticker with pre-market activity today,
        normalized into TickerSnapshot. The scanner applies filters on top of this."""
        raise NotImplementedError

    @abstractmethod
    async def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        """Return recent news for a ticker, most recent first."""
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Report whether this provider is configured with real credentials
        or is currently serving mock/sample data."""
        raise NotImplementedError
