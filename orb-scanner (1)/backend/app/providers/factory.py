"""Factory that picks the active data provider from settings.DATA_PROVIDER.
This is the only place that needs to change to add a new vendor."""
from functools import lru_cache

from app.core.config import get_settings
from app.providers.alpaca_provider import AlpacaProvider
from app.providers.base import DataProvider
from app.providers.finnhub_provider import FinnhubProvider
from app.providers.ib_provider import IBProvider
from app.providers.polygon_provider import PolygonProvider
from app.providers.twelvedata_provider import TwelveDataProvider

_PROVIDERS: dict[str, type[DataProvider]] = {
    "polygon": PolygonProvider,
    "finnhub": FinnhubProvider,
    "alpaca": AlpacaProvider,
    "twelvedata": TwelveDataProvider,
    "ib": IBProvider,
}


@lru_cache
def get_provider() -> DataProvider:
    settings = get_settings()
    provider_cls = _PROVIDERS.get(settings.DATA_PROVIDER)
    if provider_cls is None:
        raise ValueError(
            f"Unknown DATA_PROVIDER '{settings.DATA_PROVIDER}'. "
            f"Choose one of: {', '.join(_PROVIDERS)}"
        )
    return provider_cls()
