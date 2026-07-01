"""
Polygon.io data provider.

Design: Polygon's full-market snapshot endpoint returns ~10k tickers in one
call, which is cheap enough to fetch every scan. Per-ticker endpoints
(reference details, historical aggregates, news) are comparatively expensive
at market scale, so we run a two-stage funnel:

  1. CHEAP PASS  — one bulk snapshot call, filter on price + gap% (fields
     already present in the snapshot). This trims ~10,000 tickers down to a
     few dozen candidates.
  2. ENRICH PASS — only for survivors: fetch ticker reference details
     (market cap, float proxy, sector, security type) and a short daily-bar
     history (avg volume, ATR, support/resistance) concurrently.

This keeps the scan fast and stays well within API rate limits even on
Polygon's lower tiers.

NOTE / production follow-ups (left as clearly-marked simplifications so this
is honest about what a v1 covers):
  - Average volume uses a real 20-day daily-bar lookback for survivors (not
    a single-day proxy) — see `_avg_volume_and_atr`.
  - Spread% requires real-time NBBO quotes (a higher Polygon tier). We
    attempt the last-quote endpoint and fall back to a float-based heuristic
    if it's unavailable on the current plan.
  - Short interest and trading halts are not available from Polygon at all;
    those need a separate feed (e.g. FINRA short interest files, a halts
    feed from the exchanges or a vendor like Benzinga). Left as 0.0 / False
    with a clear TODO — wiring a second provider in here is straightforward
    since TickerSnapshot is provider-agnostic.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from app.core.config import get_settings
from app.providers.base import DataProvider, NewsItem, ProviderHealth, TickerSnapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"
ENRICH_CONCURRENCY = 10


class PolygonProvider(DataProvider):
    name = "polygon"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.api_key = self.settings.POLYGON_API_KEY

    def _configured(self) -> bool:
        return bool(self.api_key)

    async def health(self) -> ProviderHealth:
        if self._configured():
            return ProviderHealth(name=self.name, configured=True, mode="live")
        return ProviderHealth(
            name=self.name,
            configured=False,
            mode="mock",
            detail="POLYGON_API_KEY not set — serving bundled sample data. "
            "Set the key in .env to run live scans.",
        )

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    async def get_premarket_universe(self) -> list[TickerSnapshot]:
        if not self._configured():
            if self.settings.ALLOW_MOCK_DATA_FALLBACK:
                logger.warning("Polygon API key not set — returning mock universe")
                return _mock_universe()
            raise RuntimeError("POLYGON_API_KEY is not configured")

        async with httpx.AsyncClient(timeout=20.0) as client:
            candidates = await self._cheap_pass(client)
            logger.info("Polygon cheap pass: %d candidates before enrichment", len(candidates))
            enriched = await self._enrich_pass(client, candidates)
            return enriched

    async def get_news(self, ticker: str, limit: int = 5) -> list[NewsItem]:
        if not self._configured():
            return _mock_news(ticker)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BASE_URL}/v2/reference/news",
                params={"ticker": ticker, "limit": limit, "apiKey": self.api_key},
            )
            if resp.status_code != 200:
                logger.warning("Polygon news fetch failed for %s: %s", ticker, resp.status_code)
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

    # ------------------------------------------------------------------ #
    # Stage 1: cheap bulk pass
    # ------------------------------------------------------------------ #

    async def _cheap_pass(self, client: httpx.AsyncClient) -> list[dict]:
        resp = await client.get(
            f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"apiKey": self.api_key},
        )
        resp.raise_for_status()
        tickers = resp.json().get("tickers", [])

        s = self.settings
        survivors = []
        for t in tickers:
            price = (t.get("day") or {}).get("c") or (t.get("min") or {}).get("c") or 0
            prev_close = (t.get("prevDay") or {}).get("c") or 0
            change_pct = t.get("todaysChangePerc", 0.0)
            volume_so_far = (t.get("day") or {}).get("v") or 0

            if not price or not prev_close:
                continue
            if not (s.MIN_PRICE <= price <= s.MAX_PRICE):
                continue
            if change_pct < s.MIN_GAP_PCT:
                continue
            if volume_so_far < s.MIN_PREMARKET_VOLUME * 0.1:
                # cheap pre-filter well below the real threshold; the real
                # premarket-volume filter is enforced after enrichment once
                # we trust the number more (see _enrich_pass)
                continue

            survivors.append(t)

        return survivors

    # ------------------------------------------------------------------ #
    # Stage 2: enrichment for survivors only
    # ------------------------------------------------------------------ #

    async def _enrich_pass(self, client: httpx.AsyncClient, candidates: list[dict]) -> list[TickerSnapshot]:
        sem = asyncio.Semaphore(ENRICH_CONCURRENCY)

        async def enrich_one(raw: dict) -> TickerSnapshot | None:
            ticker = raw.get("ticker", "")
            async with sem:
                details = await self._get_ticker_details(client, ticker)
                if details is None:
                    return None
                if self._should_exclude(details):
                    return None

                avg_volume, atr, support, resistance = await self._avg_volume_and_atr(client, ticker)

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

                spread_pct = await self._estimate_spread_pct(client, ticker, price)

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
                    recent_halt=False,  # TODO: wire a halts feed; not available from Polygon
                    short_interest_pct=0.0,  # TODO: wire FINRA/short-interest feed
                )

        results = await asyncio.gather(*(enrich_one(c) for c in candidates))
        return [r for r in results if r is not None]

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

    async def _get_ticker_details(self, client: httpx.AsyncClient, ticker: str) -> dict | None:
        resp = await client.get(
            f"{BASE_URL}/v3/reference/tickers/{ticker}",
            params={"apiKey": self.api_key},
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("results")

    async def _avg_volume_and_atr(
        self, client: httpx.AsyncClient, ticker: str, days: int = 20
    ) -> tuple[float, float, float, float]:
        """Real 20-day daily-bar lookback -> avg volume, ATR(N), and a simple
        support/resistance heuristic (recent swing low/high)."""
        end = datetime.utcnow().date()
        start = end - timedelta(days=days * 2)  # pad for weekends/holidays
        resp = await client.get(
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
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        atr = sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]
        resistance = max(highs) if highs else 0.0
        support = min(lows) if lows else 0.0

        return avg_volume, round(atr, 2), round(support, 2), round(resistance, 2)

    async def _estimate_spread_pct(self, client: httpx.AsyncClient, ticker: str, price: float) -> float:
        try:
            resp = await client.get(
                f"{BASE_URL}/v2/last/nbbo/{ticker}",
                params={"apiKey": self.api_key},
            )
            if resp.status_code == 200:
                q = resp.json().get("results", {})
                bid, ask = q.get("P"), q.get("p")
                if bid and ask and ask > bid:
                    return round((ask - bid) / price * 100, 3)
        except httpx.HTTPError:
            pass
        # Fallback heuristic: thin, low-priced float names tend to run wider
        # spreads intraday. This is a rough stand-in until NBBO quotes are
        # available on the account's plan tier.
        return round(max(0.1, 2.0 - price / 20), 2)


# ---------------------------------------------------------------------- #
# Mock data (used only when no API key is configured, so the full
# scan -> score -> store -> UI pipeline is demoable without credentials).
# All tickers below are clearly-labeled sample data, not real securities.
# ---------------------------------------------------------------------- #


def _mock_universe() -> list[TickerSnapshot]:
    samples = [
        ("DEMO1", "Sample Biotech One (Demo)", "Biotechnology", 4.20, 20_500_000, 2_100_000, ["FDA", "Clinical Trial"]),
        ("DEMO2", "Sample Robotics Two (Demo)", "Industrial Automation", 2.85, 12_000_000, 3_400_000, ["Government Contract"]),
        ("DEMO3", "Sample AI Labs Three (Demo)", "Software", 9.10, 28_000_000, 1_800_000, ["AI", "Partnership"]),
        ("DEMO4", "Sample Mining Four (Demo)", "Metals & Mining", 1.65, 9_500_000, 4_600_000, []),
        ("DEMO5", "Sample Pharma Five (Demo)", "Pharmaceuticals", 6.40, 18_200_000, 2_900_000, ["Earnings", "Merger"]),
    ]
    out = []
    for ticker, company, sector, price, float_shares, premarket_vol, tags in samples:
        prev_close = round(price / 1.35, 2)
        avg_vol = int(premarket_vol / 6)
        out.append(
            TickerSnapshot(
                ticker=ticker,
                company=company,
                sector=sector,
                price=price,
                previous_close=prev_close,
                gap_pct=round((price - prev_close) / prev_close * 100, 2),
                premarket_pct=round((price - prev_close) / prev_close * 100, 2),
                premarket_volume=premarket_vol,
                premarket_high=round(price * 1.03, 2),
                premarket_low=round(price * 0.96, 2),
                average_volume=avg_vol,
                relative_volume=round(premarket_vol / avg_vol, 2) if avg_vol else 0.0,
                float_shares=float_shares,
                market_cap=float_shares * price * 1.1,
                atr=round(price * 0.12, 2),
                previous_day_high=round(prev_close * 1.05, 2),
                previous_day_low=round(prev_close * 0.95, 2),
                support=round(price * 0.90, 2),
                resistance=round(price * 1.10, 2),
                spread_pct=round(max(0.1, 2.0 - price / 20), 2),
                historical_volatility_pct=round((price * 0.12) / price * 100, 2),
            )
        )
    return out


def _mock_news(ticker: str) -> list[NewsItem]:
    return [
        NewsItem(
            headline=f"[Sample] {ticker} announces preliminary clinical data (demo headline)",
            url="",
            published_at=datetime.utcnow().isoformat(),
            source="Demo Wire",
        )
    ]
