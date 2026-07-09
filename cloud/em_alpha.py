"""
Emerging Markets Alpha Hunter — engine.

What this HONESTLY is: a screener over US-LISTED foreign companies (ADRs),
because Polygon covers US exchanges only. Truly local EM stocks (NSE
India, IDX, B3, WSE, ...) are structurally out of reach of this data
plan — the UI says so prominently. Within that universe it hunts for the
user's brief: quality small/mid-caps that western investors pay little
attention to.

Measurable proxies used (and their limits):
- "Under the radar" = LOW US dollar volume + FEW English news articles.
  Analyst coverage and institutional ownership are NOT in the data plan
  and are therefore not claimed.
- Home country is INFERRED from the company description text — a
  heuristic, labeled as such, with an "unknown" bucket rather than
  guesses.
- Quality reuses the fundamentals engine (real filings of SEC-reporting
  ADRs). Dilution is measured from share count growth across years.
- Moat, management honesty, governance, political risk: NOT computable —
  the page presents them as the user's own diligence checklist.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cloud.config import Settings
from cloud.db import EMCompany
from cloud.fundamentals import (
    Metrics, QuantScore, YearRecord, compute_metrics, compute_valuation,
    fetch_financials, parse_reports, score_quant,
)

logger = logging.getLogger(__name__)
BASE_URL = "https://api.polygon.io"

# The user's preferred markets, plus a catch-all of further EM terms.
# Matching runs against the company DESCRIPTION text (heuristic!).
EM_COUNTRY_TERMS: dict[str, list[str]] = {
    "Indien": ["India", "Mumbai", "Bangalore", "Bengaluru", "New Delhi", "Chennai", "Hyderabad, India"],
    "Indonesien": ["Indonesia", "Jakarta"],
    "Vietnam": ["Vietnam", "Hanoi", "Ho Chi Minh"],
    "Malaysia": ["Malaysia", "Kuala Lumpur"],
    "Thailand": ["Thailand", "Bangkok"],
    "Philippinen": ["Philippines", "Manila"],
    "Mexiko": ["Mexico", "Mexico City", "Monterrey, Mexico"],
    "Brasilien": ["Brazil", "São Paulo", "Sao Paulo", "Rio de Janeiro"],
    "Polen": ["Poland", "Warsaw"],
    "Tschechien": ["Czech Republic", "Czechia", "Prague"],
    "Südafrika": ["South Africa", "Johannesburg", "Cape Town"],
    "Chile": ["Chile", "Santiago, Chile"],
    "Peru": ["Peru", "Lima, Peru"],
    "Türkei": ["Turkey", "Türkiye", "Istanbul"],
    "Taiwan": ["Taiwan", "Taipei", "Hsinchu"],
    "China": ["China", "Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Hong Kong"],
    "Argentinien": ["Argentina", "Buenos Aires"],
    "Kolumbien": ["Colombia", "Bogot"],
    "Griechenland": ["Greece", "Athens, Greece"],
    "Ägypten": ["Egypt", "Cairo"],
    "Kasachstan": ["Kazakhstan", "Almaty"],
    "VAE": ["United Arab Emirates", "Dubai", "Abu Dhabi"],
    "Singapur": ["Singapore"],
    "Südkorea": ["South Korea", "Korea, Republic", "Seoul"],
    "Israel": ["Israel", "Tel Aviv"],
}

# Developed-market markers that DISQUALIFY (checked first, so "US operations
# of a London company" doesn't slip through as EM):
DM_TERMS = [
    "United States", "United Kingdom", "London", "Germany", "France", "Paris",
    "Japan", "Tokyo", "Canada", "Toronto", "Australia", "Sydney", "Switzerland",
    "Netherlands", "Amsterdam", "Sweden", "Norway", "Denmark", "Ireland, ",
    "Spain", "Madrid", "Italy", "Milan", "Belgium", "Austria", "Finland",
]


def infer_country(description: str, name: str = "") -> str | None:
    """Heuristic home-country inference from the description text.
    Returns the German country label, or None when nothing matches.
    DM markers win over EM markers only when no EM term is present."""
    text = f"{description} {name}"
    if not text.strip():
        return None
    for country, terms in EM_COUNTRY_TERMS.items():
        for term in terms:
            if term in text:
                return country
    return None


# --------------------------------------------------------------------- #
# Universe building (cached in EMCompany)
# --------------------------------------------------------------------- #

def fetch_adr_tickers(settings: Settings, max_pages: int = 10) -> list[dict]:
    """All active US-listed ADR common shares (type=ADRC), paginated."""
    if not settings.POLYGON_API_KEY:
        return []
    out: list[dict] = []
    url = f"{BASE_URL}/v3/reference/tickers"
    params: dict | None = {"type": "ADRC", "market": "stocks", "active": "true",
                           "limit": 1000, "apiKey": settings.POLYGON_API_KEY}
    try:
        with httpx.Client(timeout=30.0) as client:
            for _ in range(max_pages):
                resp = client.get(url, params=params)
                if resp.status_code != 200:
                    break
                payload = resp.json()
                out.extend(payload.get("results", []) or [])
                next_url = payload.get("next_url")
                if not next_url:
                    break
                url, params = next_url + f"&apiKey={settings.POLYGON_API_KEY}", None
    except httpx.HTTPError as exc:
        logger.warning("ADR ticker fetch failed: %s", exc)
    return out


def fetch_ticker_details_raw(settings: Settings, ticker: str) -> dict:
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{BASE_URL}/v3/reference/tickers/{ticker}",
                              params={"apiKey": settings.POLYGON_API_KEY})
            if resp.status_code != 200:
                return {}
            return resp.json().get("results", {}) or {}
    except httpx.HTTPError:
        return {}


def sync_universe(db: Session, settings: Settings) -> int:
    """Upsert the ADR ticker list into EMCompany. Returns row count."""
    tickers = fetch_adr_tickers(settings)
    for t in tickers:
        sym = t.get("ticker", "")
        if not sym:
            continue
        row = db.get(EMCompany, sym)
        if row is None:
            row = EMCompany(ticker=sym)
            db.add(row)
        row.name = t.get("name", "") or row.name
        row.active = True
    db.commit()
    return len(tickers)


def enrich_missing_details(db: Session, settings: Settings, limit: int = 300,
                           progress_cb=None) -> int:
    """Fetch details for rows without them (1 call each), newest-first.
    `limit` bounds one batch so a first run stays predictable; repeated
    clicks continue where the last batch stopped. Returns enriched count."""
    rows = db.execute(
        select(EMCompany).where(EMCompany.active == True, EMCompany.details_fetched_at.is_(None))  # noqa: E712
        .order_by(EMCompany.ticker).limit(limit)
    ).scalars().all()
    done = 0
    for i, row in enumerate(rows):
        details = fetch_ticker_details_raw(settings, row.ticker)
        row.market_cap = details.get("market_cap")
        row.description = details.get("description", "") or ""
        row.sic_description = details.get("sic_description", "") or ""
        row.country = infer_country(row.description, row.name) or ""
        row.details_fetched_at = datetime.now(timezone.utc)
        done += 1
        if progress_cb and (i % 10 == 0 or i == len(rows) - 1):
            progress_cb(i + 1, len(rows))
        if i % 25 == 24:
            db.commit()
    db.commit()
    return done


def attach_dollar_volume(db: Session, settings: Settings) -> int:
    """One grouped-daily call: last close + dollar volume for ALL US
    tickers; stored on matching EMCompany rows. Returns updated count."""
    if not settings.POLYGON_API_KEY:
        return 0
    rows = {r.ticker: r for r in db.execute(select(EMCompany).where(EMCompany.active == True)).scalars().all()}  # noqa: E712
    if not rows:
        return 0
    updated = 0
    try:
        with httpx.Client(timeout=60.0) as client:
            for back in range(1, 8):  # find the most recent trading day
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


# --------------------------------------------------------------------- #
# Candidate analysis
# --------------------------------------------------------------------- #

def shortlist(db: Session, mcap_min: float, mcap_max: float,
              countries: list[str] | None = None, top_n: int = 25) -> list[EMCompany]:
    """EM companies in the size band, LEAST-traded first (obscurity proxy).
    Rows without dollar volume or price sort last / are skipped."""
    q = select(EMCompany).where(
        EMCompany.active == True,  # noqa: E712
        EMCompany.market_cap.is_not(None),
        EMCompany.market_cap >= mcap_min,
        EMCompany.market_cap <= mcap_max,
        EMCompany.country != "",
        EMCompany.dollar_volume.is_not(None),
        EMCompany.dollar_volume > 0,
    )
    rows = db.execute(q).scalars().all()
    if countries:
        rows = [r for r in rows if r.country in countries]
    rows.sort(key=lambda r: r.dollar_volume or 0)
    return rows[:top_n]


def news_count(settings: Settings, ticker: str, days: int = 90) -> int | None:
    """English news articles in the window — western-media-presence proxy."""
    if not settings.POLYGON_API_KEY:
        return None
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{BASE_URL}/v2/reference/news",
                              params={"ticker": ticker, "limit": 50,
                                      "published_utc.gte": (date.today() - timedelta(days=days)).isoformat(),
                                      "apiKey": settings.POLYGON_API_KEY})
            if resp.status_code != 200:
                return None
            return len(resp.json().get("results", []) or [])
    except httpx.HTTPError:
        return None


def dilution_pct_per_year(records: list[YearRecord]) -> float | None:
    """Share-count CAGR in % — positive = dilution, negative = buybacks."""
    recs = [r for r in records if r.shares]
    if len(recs) < 2:
        return None
    years = len(recs) - 1
    first, last = recs[0].shares, recs[-1].shares
    if not first or not last or first <= 0 or last <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 1)


def red_flags(m: Metrics, dilution: float | None) -> list[str]:
    """The user's hard exclusion criteria, limited to what's measurable."""
    flags: list[str] = []
    if m.fcf_latest is not None and m.fcf_latest <= 0:
        flags.append("Negativer Free Cash Flow")
    if m.debt_to_equity is not None and m.debt_to_equity > 2.5:
        flags.append(f"Hohe Verschuldung (Debt/Equity {m.debt_to_equity})")
    if dilution is not None and dilution > 3.0:
        flags.append(f"Laufende Verwässerung ({dilution}% mehr Aktien pro Jahr)")
    if m.net_income_cagr is not None and m.net_income_cagr < 0 and m.revenue_cagr is not None and m.revenue_cagr < 0:
        flags.append("Schrumpfender Umsatz UND Gewinn (Turnaround-Story)")
    if m.years_covered < 3:
        flags.append(f"Dünne Datenbasis ({m.years_covered} Jahresberichte)")
    return flags


@dataclass
class AlphaCandidate:
    ticker: str
    name: str
    country: str
    sector: str
    market_cap: float | None
    price: float | None
    dollar_volume: float | None
    volume_percentile: float | None = None  # within shortlist, low = obscure
    news_90d: int | None = None
    metrics: Metrics | None = None
    quality: QuantScore | None = None
    dilution: float | None = None
    flags: list[str] = field(default_factory=list)
    alpha_score: float = 0.0
    alpha_parts: list[tuple[str, float, float, str]] = field(default_factory=list)
    confidence: str = "Low"
    description: str = ""


def compute_alpha_score(c: AlphaCandidate) -> None:
    """Transparent composite: Quality 60 + Information Edge 40, minus 10
    per red flag (floor 0). Only measurable inputs; the qualitative
    categories from the user's brief (moat, management, governance,
    political risk) are deliberately NOT faked into this number."""
    parts: list[tuple[str, float, float, str]] = []
    q_pts = round((c.quality.total if c.quality else 0) * 0.6, 1)
    parts.append(("Qualität (Fundamentaldaten)", q_pts, 60,
                  f"Quant-Score {c.quality.total:.0f}/100" if c.quality else "keine Daten"))

    vol_pts = 0.0
    if c.volume_percentile is not None:
        vol_pts = round(25 * (1 - c.volume_percentile), 1)  # least traded -> 25
        parts.append(("Info-Edge: geringes US-Handelsvolumen", vol_pts, 25,
                      f"${(c.dollar_volume or 0)/1e6:.1f}M Tagesumsatz — Perzentil {c.volume_percentile*100:.0f} der Auswahl"))
    else:
        parts.append(("Info-Edge: geringes US-Handelsvolumen", 0, 25, "kein Volumen ermittelbar"))

    news_pts = 0.0
    if c.news_90d is not None:
        news_pts = 15.0 if c.news_90d <= 2 else 10.0 if c.news_90d <= 5 else 5.0 if c.news_90d <= 15 else 0.0
        parts.append(("Info-Edge: geringe Medienpräsenz", news_pts, 15,
                      f"{c.news_90d} englische Artikel in 90 Tagen"))
    else:
        parts.append(("Info-Edge: geringe Medienpräsenz", 0, 15, "News-Zahl nicht ermittelbar"))

    penalty = 10.0 * len(c.flags)
    if penalty:
        parts.append(("Red-Flag-Abzug", -penalty, 0, "; ".join(c.flags)))

    c.alpha_parts = parts
    c.alpha_score = round(max(0.0, q_pts + vol_pts + news_pts - penalty), 1)

    years = c.metrics.years_covered if c.metrics else 0
    solid = bool(c.metrics and not c.metrics.fcf_is_approx and c.country)
    c.confidence = "High" if (years >= 5 and solid and not c.flags) else "Medium" if years >= 3 else "Low"


def analyze_candidates(db: Session, settings: Settings, rows: list[EMCompany],
                       progress_cb=None) -> list[AlphaCandidate]:
    """Deep-check the shortlist: financials + news per ticker (2 calls each).
    Returns candidates sorted by alpha score."""
    vols = sorted(r.dollar_volume for r in rows if r.dollar_volume)
    out: list[AlphaCandidate] = []
    for i, row in enumerate(rows):
        c = AlphaCandidate(
            ticker=row.ticker, name=row.name, country=row.country,
            sector=row.sic_description, market_cap=row.market_cap,
            price=row.last_close, dollar_volume=row.dollar_volume,
            description=row.description,
        )
        if vols and row.dollar_volume:
            c.volume_percentile = vols.index(row.dollar_volume) / max(len(vols) - 1, 1)
        reports = fetch_financials(settings, row.ticker, timeframe="annual", limit=10)
        if reports:
            records = parse_reports(reports)
            c.metrics = compute_metrics(records)
            c.dilution = dilution_pct_per_year(records)
            if row.last_close:
                v = compute_valuation(c.metrics, price=row.last_close, market_cap=row.market_cap)
                c.quality = score_quant(c.metrics, v)
            c.flags = red_flags(c.metrics, c.dilution)
        else:
            c.flags = ["Keine Finanzberichte verfügbar (kein SEC-Filer?)"]
        c.news_90d = news_count(settings, row.ticker)
        compute_alpha_score(c)
        out.append(c)
        if progress_cb:
            progress_cb(i + 1, len(rows))
    out.sort(key=lambda x: -x.alpha_score)
    return out
