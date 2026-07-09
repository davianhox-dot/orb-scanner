"""
Long-term Investor Screener — engine.

Scans the US common-stock universe for possible long-term investments
using the shared fundamentals engine, with aggressive caching because
every quality check costs one financials API call per company:

  ① ticker list (type=CS, a few paginated calls)
  ② last close + dollar volume for ALL tickers (ONE grouped-daily call)
  ③ details (market cap, sector) — only for tickers above a liquidity
     floor, in batches
  ④ quality scan (fundamentals -> quant score) — only for tickers inside
     the chosen size band, in batches; results cached on the row forever
     (re-scan is explicit, never implicit)

Honesty rules:
- The quant score covers ONLY the measurable half (growth, margins,
  balance sheet, cash flow, valuation). Moat/management stay human work —
  the results table links each hit to the Investor Modus deep dive.
- Companies whose financials Polygon doesn't deliver get score None and
  show up in a diagnostics count instead of silently vanishing.
"""
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cloud.config import Settings
from cloud.db import USCompany
from cloud.fundamentals import (
    compute_metrics, compute_valuation, fetch_financials, parse_reports, score_quant,
)

logger = logging.getLogger(__name__)
BASE_URL = "https://api.polygon.io"


def sync_us_universe(db: Session, settings: Settings, max_pages: int = 15) -> int:
    """Upsert all active US common stocks (type=CS). Returns fetched count."""
    if not settings.POLYGON_API_KEY:
        return 0
    fetched = 0
    url = f"{BASE_URL}/v3/reference/tickers"
    params: dict | None = {"type": "CS", "market": "stocks", "active": "true",
                           "limit": 1000, "apiKey": settings.POLYGON_API_KEY}
    try:
        with httpx.Client(timeout=30.0) as client:
            for _ in range(max_pages):
                resp = client.get(url, params=params)
                if resp.status_code != 200:
                    break
                payload = resp.json()
                for t in payload.get("results", []) or []:
                    sym = t.get("ticker", "")
                    if not sym:
                        continue
                    row = db.get(USCompany, sym)
                    if row is None:
                        row = USCompany(ticker=sym)
                        db.add(row)
                    row.name = t.get("name", "") or row.name
                    row.active = True
                    fetched += 1
                db.commit()
                next_url = payload.get("next_url")
                if not next_url:
                    break
                url, params = next_url + f"&apiKey={settings.POLYGON_API_KEY}", None
    except httpx.HTTPError as exc:
        logger.warning("US universe fetch failed: %s", exc)
        db.commit()
    return fetched


def attach_us_volume(db: Session, settings: Settings) -> int:
    """ONE grouped-daily call -> last close + dollar volume on all rows."""
    if not settings.POLYGON_API_KEY:
        return 0
    rows = {r.ticker: r for r in db.execute(
        select(USCompany).where(USCompany.active == True)  # noqa: E712
    ).scalars().all()}
    if not rows:
        return 0
    updated = 0
    try:
        with httpx.Client(timeout=90.0) as client:
            for back in range(1, 8):
                day = date.today() - timedelta(days=back)
                resp = client.get(
                    f"{BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
                    params={"adjusted": "true", "apiKey": settings.POLYGON_API_KEY},
                )
                if resp.status_code != 200:
                    continue
                results = resp.json().get("results") or []
                if not results:
                    continue
                for r in results:
                    row = rows.get(r.get("T", ""))
                    if row is not None:
                        row.last_close = float(r.get("c") or 0)
                        row.dollar_volume = float(r.get("c") or 0) * float(r.get("v") or 0)
                        updated += 1
                break
    except httpx.HTTPError as exc:
        logger.warning("Grouped daily failed: %s", exc)
    db.commit()
    return updated


def _fetch_details(settings: Settings, ticker: str) -> dict:
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{BASE_URL}/v3/reference/tickers/{ticker}",
                              params={"apiKey": settings.POLYGON_API_KEY})
            if resp.status_code != 200:
                return {}
            return resp.json().get("results", {}) or {}
    except httpx.HTTPError:
        return {}


def enrich_us_details(db: Session, settings: Settings, min_dollar_volume: float,
                      limit: int = 300, progress_cb=None) -> tuple[int, int]:
    """Details (market cap, sector) for liquid rows missing them, in one
    batch of `limit`. Returns (enriched_now, still_missing)."""
    base = select(USCompany).where(
        USCompany.active == True,  # noqa: E712
        USCompany.details_fetched_at.is_(None),
        USCompany.dollar_volume.is_not(None),
        USCompany.dollar_volume >= min_dollar_volume,
    )
    rows = db.execute(base.order_by(USCompany.dollar_volume.desc()).limit(limit)).scalars().all()
    for i, row in enumerate(rows):
        details = _fetch_details(settings, row.ticker)
        row.market_cap = details.get("market_cap")
        row.sic_description = details.get("sic_description", "") or ""
        row.details_fetched_at = datetime.now(timezone.utc)
        if progress_cb and (i % 10 == 0 or i == len(rows) - 1):
            progress_cb(i + 1, len(rows))
        if i % 25 == 24:
            db.commit()
    db.commit()
    from sqlalchemy import func
    missing = db.execute(
        select(func.count()).select_from(USCompany).where(
            USCompany.active == True,  # noqa: E712
            USCompany.details_fetched_at.is_(None),
            USCompany.dollar_volume.is_not(None),
            USCompany.dollar_volume >= min_dollar_volume,
        )
    ).scalar() or 0
    return len(rows), missing


def deep_scan_batch(db: Session, settings: Settings, mcap_min: float, mcap_max: float,
                    limit: int = 200, progress_cb=None) -> tuple[int, int]:
    """Quality-scan un-scored companies in the size band: fetch annual
    financials, run the fundamentals engine, cache the summary on the row.
    Returns (scanned_now, still_unscanned). Companies without usable
    reports get years_covered=0 + score None (visible, not vanished)."""
    base_filter = (
        USCompany.active == True,  # noqa: E712
        USCompany.details_fetched_at.is_not(None),
        USCompany.market_cap.is_not(None),
        USCompany.market_cap >= mcap_min,
        USCompany.market_cap <= mcap_max,
        USCompany.score_computed_at.is_(None),
    )
    rows = db.execute(
        select(USCompany).where(*base_filter).order_by(USCompany.market_cap.desc()).limit(limit)
    ).scalars().all()
    for i, row in enumerate(rows):
        reports = fetch_financials(settings, row.ticker, timeframe="annual", limit=10)
        if reports and row.last_close:
            records = parse_reports(reports)
            m = compute_metrics(records)
            v = compute_valuation(m, price=row.last_close, market_cap=row.market_cap)
            s = score_quant(m, v)
            row.quant_score = s.total
            row.revenue_cagr = m.revenue_cagr
            row.eps_cagr = m.eps_cagr
            row.roe = m.roe
            row.net_margin = m.net_margin
            row.debt_to_equity = m.debt_to_equity
            row.fcf_yield = v.fcf_yield
            row.pe = v.pe
            row.years_covered = m.years_covered
            row.fcf_is_approx = m.fcf_is_approx
        else:
            row.quant_score = None
            row.years_covered = 0
        row.score_computed_at = datetime.now(timezone.utc)
        if progress_cb and (i % 5 == 0 or i == len(rows) - 1):
            progress_cb(i + 1, len(rows))
        if i % 25 == 24:
            db.commit()
    db.commit()
    from sqlalchemy import func
    remaining = db.execute(select(func.count()).select_from(USCompany).where(*base_filter)).scalar() or 0
    return len(rows), remaining


def query_results(db: Session, mcap_min: float, mcap_max: float,
                  min_score: float = 0.0, min_years: int = 0,
                  limit: int = 100) -> list[USCompany]:
    """Scored companies in the band, best first."""
    rows = db.execute(
        select(USCompany).where(
            USCompany.active == True,  # noqa: E712
            USCompany.quant_score.is_not(None),
            USCompany.market_cap.is_not(None),
            USCompany.market_cap >= mcap_min,
            USCompany.market_cap <= mcap_max,
            USCompany.quant_score >= min_score,
            USCompany.years_covered >= min_years,
        ).order_by(USCompany.quant_score.desc()).limit(limit)
    ).scalars().all()
    return rows
