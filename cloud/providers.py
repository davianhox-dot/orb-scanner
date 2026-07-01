"""
Data providers for the cloud path — same pluggable design as backend/app/providers,
reimplemented synchronously (httpx.Client instead of AsyncClient) since the
GitHub Actions job is a short-lived script, not a long-running server.

Swap providers with one env var: DATA_PROVIDER=polygon|finnhub|alpaca|twelvedata
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from cloud.catalyst import NewsItem
from cloud.config import Settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"


@dataclass
class TickerSnapshot:
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
class ProviderHealth:
    name: str
    configured: bool
    mode: str
    detail: str = ""


class DataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_premarket_universe(self) -> list[TickerSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> ProviderHealth:
        raise NotImplementedError


class PolygonProvider(DataProvider):
    """Same two-stage funnel as the FastAPI backend's provider: one cheap
    bulk snapshot call, then per-ticker enrichment only for survivors. See
    backend/app/providers/polygon_provider.py for the full design notes —
    this is a synchronous port of the same logic."""

    name = "polygon"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.POLYGON_API_KEY

    def _configured(self) -> bool:
        return bool(self.api_key)

    def health(self) -> ProviderHealth:
        if self._configured():
            return ProviderHealth(name=self.name, configured=True, mode="live")
        return ProviderHealth(
            name=self.name, configured=False, mode="mock",
            detail="POLYGON_API_KEY not set — serving bundled sample data.",
        )

    def get_premarket_universe(self) -> list[TickerSnapshot]:
        if not self._configured():
            if self.settings.ALLOW_MOCK_DATA_FALLBACK:
                logger.warning("Polygon API key not set — returning mock universe")
                return _mock_universe()
            raise RuntimeError("POLYGON_API_KEY is not configured")

        with httpx.Client(timeout=20.0) as client:
            candidates = self._cheap_pass(client)
            logger.info("Cheap pass: %d candidates before enrichment", len(candidates))
            return self._enrich_pass(client, candidates)

    def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        if not self._configured():
            return _mock_news(ticker)
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{BASE_URL}/v2/reference/news",
                params={"ticker": ticker, "limit": limit, "apiKey": self.api_key},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                NewsItem(
                    headline=item.get("title", ""),
                    url=item.get("article_url", ""),
                    published_at=item.get("published_utc", ""),
                    source=item.get("publisher", {}).get("name", ""),
                )
                for item in data.get("results", [])
            ]

    def _cheap_pass(self, client: httpx.Client) -> list[dict]:
        resp = client.get(
            f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"apiKey": self.api_key},
        )
        resp.raise_for_status()
        tickers = resp.json().get("tickers", [])

        s = self.settings
        survivors = []
        stats = {
            "total": len(tickers),
            "no_price_or_prevclose": 0,
            "price_out_of_range": 0,
            "gap_too_small": 0,
            "volume_too_low": 0,
            "passed": 0,
        }
        for t in tickers:
            price = (t.get("day") or {}).get("c") or (t.get("min") or {}).get("c") or 0
            prev_close = (t.get("prevDay") or {}).get("c") or 0
            change_pct = t.get("todaysChangePerc", 0.0)
            volume_so_far = (t.get("day") or {}).get("v") or 0

            if not price or not prev_close:
                stats["no_price_or_prevclose"] += 1
                continue
            if not (s.MIN_PRICE <= price <= s.MAX_PRICE):
                stats["price_out_of_range"] += 1
                continue
            if change_pct < s.MIN_GAP_PCT:
                stats["gap_too_small"] += 1
                continue
            if volume_so_far < s.MIN_PREMARKET_VOLUME * 0.1:
                stats["volume_too_low"] += 1
                continue

            stats["passed"] += 1
            survivors.append(t)

        logger.info(
            "Cheap pass breakdown: total=%(total)d no_price_or_prevclose=%(no_price_or_prevclose)d "
            "price_out_of_range=%(price_out_of_range)d gap_too_small=%(gap_too_small)d "
            "volume_too_low=%(volume_too_low)d passed=%(passed)d",
            stats,
        )
        return survivors

    def _enrich_pass(self, client: httpx.Client, candidates: list[dict]) -> list[TickerSnapshot]:
        results = []
        for raw in candidates:
            snapshot = self._enrich_one(client, raw)
            if snapshot is not None:
                results.append(snapshot)
        return results

    def _enrich_one(self, client: httpx.Client, raw: dict) -> TickerSnapshot | None:
        ticker = raw.get("ticker", "")
        details = self._get_ticker_details(client, ticker)
        if details is None or self._should_exclude(details):
            return None

        avg_volume, atr, support, resistance = self._avg_volume_and_atr(client, ticker)

        day = raw.get("day") or {}
        prev_day = raw.get("prevDay") or {}
        price = day.get("c") or (raw.get("min") or {}).get("c") or 0
        prev_close = prev_day.get("c") or 0
        premarket_volume = day.get("v") or 0
        gap_pct = raw.get("todaysChangePerc", 0.0)
        relative_volume = (premarket_volume / avg_volume) if avg_volume else 0.0

        s = self.settings
        if premarket_volume < s.MIN_PREMARKET_VOLUME:
            return None
        if relative_volume < s.MIN_RELATIVE_VOLUME:
            return None
        if details.get("market_cap") and details["market_cap"] > s.MAX_MARKET_CAP:
            return None
        float_shares = details.get("share_class_shares_outstanding") or 0
        if float_shares and float_shares > s.MAX_FLOAT:
            return None

        spread_pct = self._estimate_spread_pct(client, ticker, price)

        return TickerSnapshot(
            ticker=ticker,
            company=details.get("name", ticker),
            sector=details.get("sic_description", "Unknown"),
            price=price,
            previous_close=prev_close,
            gap_pct=gap_pct,
            premarket_pct=gap_pct,
            premarket_volume=int(premarket_volume),
            premarket_high=day.get("h", price),
            premarket_low=day.get("l", price),
            average_volume=int(avg_volume),
            relative_volume=round(relative_volume, 2),
            float_shares=float_shares,
            market_cap=details.get("market_cap", 0.0),
            is_etf=details.get("type") == "ETF",
            is_preferred="PFD" in ticker or details.get("type") == "PFD",
            is_warrant=ticker.endswith("W") and details.get("type") == "WARRANT",
            atr=atr,
            previous_day_high=prev_day.get("h", 0.0),
            previous_day_low=prev_day.get("l", 0.0),
            support=support,
            resistance=resistance,
            spread_pct=spread_pct,
            historical_volatility_pct=round((atr / price * 100) if price else 0.0, 2),
            recent_halt=False,  # TODO: no halts feed wired in yet
            short_interest_pct=0.0,  # TODO: no short-interest feed wired in yet
        )

    def _should_exclude(self, details: dict) -> bool:
        s = self.settings
        security_type = details.get("type", "")
        if s.EXCLUDE_ETFS and security_type in ("ETF", "ETN", "FUND"):
            return True
        if s.EXCLUDE_PREFERRED and security_type == "PFD":
            return True
        if s.EXCLUDE_WARRANTS and security_type == "WARRANT":
            return True
        return False

    def _get_ticker_details(self, client: httpx.Client, ticker: str) -> dict | None:
        resp = client.get(f"{BASE_URL}/v3/reference/tickers/{ticker}", params={"apiKey": self.api_key})
        if resp.status_code != 200:
            return None
        return resp.json().get("results")

    def _avg_volume_and_atr(self, client: httpx.Client, ticker: str, days: int = 20) -> tuple[float, float, float, float]:
        end = datetime.utcnow().date()
        start = end - timedelta(days=days * 2)
        resp = client.get(
            f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            params={"adjusted": "true", "sort": "desc", "limit": days, "apiKey": self.api_key},
        )
        if resp.status_code != 200:
            return 0.0, 0.0, 0.0, 0.0
        bars = resp.json().get("results", [])[:days]
        if not bars:
            return 0.0, 0.0, 0.0, 0.0

        volumes = [b["v"] for b in bars]
        avg_volume = sum(volumes) / len(volumes)

        true_ranges = []
        for i in range(len(bars) - 1):
            high, low = bars[i]["h"], bars[i]["l"]
            prev_close = bars[i + 1]["c"]
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        atr = sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        return avg_volume, round(atr, 2), round(min(lows), 2) if lows else 0.0, round(max(highs), 2) if highs else 0.0

    def _estimate_spread_pct(self, client: httpx.Client, ticker: str, price: float) -> float:
        try:
            resp = client.get(f"{BASE_URL}/v2/last/nbbo/{ticker}", params={"apiKey": self.api_key})
            if resp.status_code == 200:
                q = resp.json().get("results", {})
                bid, ask = q.get("P"), q.get("p")
                if bid and ask and ask > bid:
                    return round((ask - bid) / price * 100, 3)
        except httpx.HTTPError:
            pass
        return round(max(0.1, 2.0 - price / 20), 2)


class _StubProvider(DataProvider):
    """Shared behavior for the not-yet-implemented providers: same interface,
    serves mock data, logs a clear warning. Fill in `get_premarket_universe`
    / `get_news` with the vendor's real REST calls when you're ready to add
    that provider — the scan job and scoring logic won't need to change."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name, configured=False, mode="mock",
            detail=f"{self.name} adapter is a stub — serving sample data.",
        )

    def get_premarket_universe(self) -> list[TickerSnapshot]:
        logger.warning("%s is a stub provider — returning mock universe", self.name)
        return _mock_universe()

    def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        return _mock_news(ticker)


class FinnhubProvider(_StubProvider):
    name = "finnhub"


class AlpacaProvider(_StubProvider):
    name = "alpaca"


class TwelveDataProvider(_StubProvider):
    name = "twelvedata"


_PROVIDERS = {
    "polygon": PolygonProvider,
    "finnhub": FinnhubProvider,
    "alpaca": AlpacaProvider,
    "twelvedata": TwelveDataProvider,
}


def get_provider(settings: Settings) -> DataProvider:
    provider_cls = _PROVIDERS.get(settings.DATA_PROVIDER)
    if provider_cls is None:
        raise ValueError(f"Unknown DATA_PROVIDER '{settings.DATA_PROVIDER}'. Choose one of: {', '.join(_PROVIDERS)}")
    return provider_cls(settings)


def _mock_universe() -> list[TickerSnapshot]:
    samples = [
        ("DEMO1", "Sample Biotech One (Demo)", "Biotechnology", 4.20, 20_500_000, 2_100_000),
        ("DEMO2", "Sample Robotics Two (Demo)", "Industrial Automation", 2.85, 12_000_000, 3_400_000),
        ("DEMO3", "Sample AI Labs Three (Demo)", "Software", 9.10, 28_000_000, 1_800_000),
        ("DEMO4", "Sample Mining Four (Demo)", "Metals & Mining", 1.65, 9_500_000, 4_600_000),
        ("DEMO5", "Sample Pharma Five (Demo)", "Pharmaceuticals", 6.40, 18_200_000, 2_900_000),
    ]
    out = []
    for ticker, company, sector, price, float_shares, premarket_vol in samples:
        prev_close = round(price / 1.35, 2)
        avg_vol = int(premarket_vol / 6)
        out.append(
            TickerSnapshot(
                ticker=ticker, company=company, sector=sector, price=price, previous_close=prev_close,
                gap_pct=round((price - prev_close) / prev_close * 100, 2),
                premarket_pct=round((price - prev_close) / prev_close * 100, 2),
                premarket_volume=premarket_vol, premarket_high=round(price * 1.03, 2),
                premarket_low=round(price * 0.96, 2), average_volume=avg_vol,
                relative_volume=round(premarket_vol / avg_vol, 2) if avg_vol else 0.0,
                float_shares=float_shares, market_cap=float_shares * price * 1.1,
                atr=round(price * 0.12, 2), previous_day_high=round(prev_close * 1.05, 2),
                previous_day_low=round(prev_close * 0.95, 2), support=round(price * 0.90, 2),
                resistance=round(price * 1.10, 2), spread_pct=round(max(0.1, 2.0 - price / 20), 2),
                historical_volatility_pct=round((price * 0.12) / price * 100, 2),
            )
        )
    return out


def _mock_news(ticker: str) -> list[NewsItem]:
    return [
        NewsItem(
            headline=f"[Sample] {ticker} announces preliminary clinical data (demo headline)",
            url="", published_at=datetime.utcnow().isoformat(), source="Demo Wire",
        )
    ]
