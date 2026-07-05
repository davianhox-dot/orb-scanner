"""
Setup rationale — the "WARUM" behind each Top Setup.

For each of the top 1-3 setups this builds two lists, computed strictly
from real data (price/volume structure, indicators, and recent news via
Polygon + the existing catalyst keyword detector):

  pro_factors  — what supports the setup (trend alignment, consolidation
                 length before the breakout, volume behavior /
                 accumulation proxy, momentum, proximity to highs, healthy
                 RSI, recent catalyst news, historical edge)
  risk_factors — what argues for caution (overheated RSI, price extended
                 far above its EMA20, wide stop distance, thin historical
                 sample, no news behind the move)

Deliberate honesty rules:
- Every line is derived from a concrete measurement; nothing is generated
  as free-form opinion.
- Risks are always shown next to the pros — a one-sided "this is a great
  buy because…" list would be marketing, not analysis.
- All strategies in this system are long-only, so rationales are written
  for buys; there is no "good sell" case to generate.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from cloud.catalyst import NewsItem, detect_catalysts
from cloud.config import Settings
from cloud.db import HistoricalBar
from cloud.strategy_rules import IndicatorCache

logger = logging.getLogger(__name__)
BASE_URL = "https://api.polygon.io"


@dataclass
class SetupRationale:
    pro_factors: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)  # {headline, published, source, url}


def _fetch_recent_news(settings: Settings, ticker: str, limit: int = 5) -> list[NewsItem]:
    if not settings.POLYGON_API_KEY:
        return []
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{BASE_URL}/v2/reference/news",
                params={"ticker": ticker, "limit": limit, "apiKey": settings.POLYGON_API_KEY},
            )
            if resp.status_code != 200:
                return []
            return [
                NewsItem(
                    headline=item.get("title", ""),
                    url=item.get("article_url", ""),
                    published_at=item.get("published_utc", ""),
                    source=item.get("publisher", {}).get("name", ""),
                )
                for item in resp.json().get("results", [])
            ]
    except httpx.HTTPError as exc:
        logger.warning("News fetch failed for %s: %s", ticker, exc)
        return []


def _estimate_next_earnings_days(settings: Settings, ticker: str) -> int | None:
    """Best-effort ESTIMATE of days until the next earnings report:
    Polygon's Stocks Starter plan has no forward earnings calendar, but it
    does expose past quarterly financials. Companies report on a ~90-day
    cadence, so last quarterly filing + ~91 days is a usable estimate —
    clearly labeled as such wherever it's shown. Returns None if
    financials are unavailable (plan limits, foreign issuers, errors)."""
    if not settings.POLYGON_API_KEY:
        return None
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{BASE_URL}/vX/reference/financials",
                params={"ticker": ticker, "timeframe": "quarterly", "limit": 1,
                        "order": "desc", "sort": "filing_date", "apiKey": settings.POLYGON_API_KEY},
            )
            if resp.status_code != 200:
                return None
            results = resp.json().get("results", [])
            if not results or not results[0].get("filing_date"):
                return None
            last_filing = date.fromisoformat(results[0]["filing_date"])
            estimated_next = last_filing + timedelta(days=91)
            return (estimated_next - date.today()).days
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Earnings estimate failed for %s: %s", ticker, exc)
        return None


def _consolidation_length(bars: list[HistoricalBar], i: int, max_range_pct: float = 12.0) -> tuple[int, float]:
    """Longest window ending at i-1 that is a genuine sideways base:
    (a) high-low range within max_range_pct AND (b) net close-to-close
    drift across the window under half that range — a slow steady trend
    stays inside a wide range too, but it isn't a consolidation.
    Returns (length_days, range_pct) — (0, 0) if none."""
    best_len, best_range = 0, 0.0
    for length in range(10, min(i, 120) + 1):
        window = bars[i - length : i]
        hi = max(b.high for b in window)
        lo = min(b.low for b in window)
        if hi <= 0:
            break
        range_pct = (hi - lo) / hi * 100
        if range_pct > max_range_pct:
            break  # windows only get wider as they grow past the consolidation
        drift_pct = abs(window[-1].close - window[0].close) / hi * 100
        if drift_pct <= range_pct * 0.5:
            best_len, best_range = length, range_pct
    return best_len, round(best_range, 1)


def build_rationale(
    settings: Settings,
    ticker: str,
    bars: list[HistoricalBar],
    entry: float,
    stop: float,
    total_trades: int,
    win_rate_pct: float,
    profit_factor: float | None,
    low_sample: bool,
) -> SetupRationale:
    r = SetupRationale()
    if len(bars) < 30:
        return r

    bars = sorted(bars, key=lambda b: b.timestamp)
    i = len(bars) - 1
    cache = IndicatorCache(bars)
    close = bars[i].close

    # --- Trend alignment ---
    ema50 = cache.ema(50)[i] if len(bars) > 50 else None
    ema200 = cache.ema(200)[i] if len(bars) > 200 else None
    ema20_series = cache.ema(20)
    ema20 = ema20_series[i] if len(bars) > 20 else None

    if ema50 is not None and ema200 is not None and close > ema50 > ema200:
        r.pro_factors.append(
            f"Sauberer Aufwärtstrend: Kurs über EMA50 (+{(close - ema50) / ema50 * 100:.1f}%) "
            f"und EMA50 über EMA200 — die klassische bullische Staffelung."
        )
    elif ema50 is not None and close > ema50:
        r.pro_factors.append(f"Kurs notiert {(close - ema50) / ema50 * 100:.1f}% über dem EMA50 (mittelfristiger Aufwärtstrend).")
    if ema20 is not None and i >= 5 and ema20_series[i - 5] is not None and ema20 > ema20_series[i - 5]:
        r.pro_factors.append("EMA20 steigt an — das kurzfristige Momentum zieht in Trendrichtung.")

    # --- Consolidation / accumulation structure ---
    cons_len, cons_range = _consolidation_length(bars, i)
    if cons_len >= 15:
        r.pro_factors.append(
            f"Ausbruch aus ~{cons_len} Tagen Konsolidierung (Range nur {cons_range}%) — je länger die "
            f"Basis, desto tragfähiger ist ein Ausbruch tendenziell."
        )
    elif cons_len >= 10:
        r.pro_factors.append(f"Kurze Konsolidierungsbasis von ~{cons_len} Tagen (Range {cons_range}%) vor dem Signal.")

    # --- Volume behavior ---
    vol_sma20 = cache.volume_sma(20)[i] if len(bars) > 20 else None
    if vol_sma20:
        vol_multiple = bars[i].volume / vol_sma20
        if vol_multiple >= 1.5:
            r.pro_factors.append(
                f"Signaltag mit {vol_multiple:.1f}x dem 20-Tage-Durchschnittsvolumen — der Ausbruch wird von echtem Kaufinteresse getragen."
            )
    if len(bars) >= 60:
        recent_avg = sum(b.volume for b in bars[i - 19 : i + 1]) / 20
        prior_avg = sum(b.volume for b in bars[i - 59 : i - 19]) / 40
        if prior_avg > 0 and recent_avg / prior_avg >= 1.2:
            r.pro_factors.append(
                f"Volumen zieht seit Wochen an (+{(recent_avg / prior_avg - 1) * 100:.0f}% vs. Vorperiode) — typisches Akkumulationsverhalten."
            )
    if len(bars) >= 30:
        up_vol = sum(b.volume for b in bars[i - 29 : i + 1] if b.close > b.open)
        total_vol = sum(b.volume for b in bars[i - 29 : i + 1])
        if total_vol > 0 and up_vol / total_vol >= 0.58:
            r.pro_factors.append(
                f"Kaufseite dominiert: {up_vol / total_vol * 100:.0f}% des Volumens der letzten 30 Tage lief an grünen Tagen."
            )

    # --- Momentum / proximity to highs ---
    if len(bars) >= 63:
        perf_3m = (close - bars[i - 62].close) / bars[i - 62].close * 100
        if perf_3m >= 10:
            r.pro_factors.append(f"+{perf_3m:.0f}% in 3 Monaten — relatives Stärkezeichen.")
    period_high = max(b.high for b in bars)
    if period_high > 0:
        dist_high = (period_high - close) / period_high * 100
        if dist_high <= 5:
            r.pro_factors.append(
                f"Nur {dist_high:.1f}% unter dem Hoch des betrachteten Zeitraums — kaum Widerstand durch gefangene Verkäufer darüber."
            )

    # --- RSI: pro or risk ---
    rsi_series = cache.rsi(14)
    rsi_val = rsi_series[i] if len(bars) > 15 else None
    if rsi_val is not None:
        if 50 <= rsi_val <= 72:
            r.pro_factors.append(f"RSI bei {rsi_val:.0f} — bullisches Momentum ohne Überhitzung.")
        elif rsi_val > 72:
            r.risk_factors.append(f"RSI bei {rsi_val:.0f} — kurzfristig überhitzt, Rücksetzer-Risiko direkt nach dem Einstieg.")

    # --- Historical edge as a factor line ---
    pf_text = "∞" if profit_factor is None else f"{profit_factor:.2f}"
    r.pro_factors.append(
        f"Historische Bilanz genau dieser Strategie auf {ticker}: {total_trades} Trades, "
        f"{win_rate_pct:.0f}% Trefferquote, Profit Factor {pf_text}."
    )

    # --- Risk factors ---
    if low_sample:
        r.risk_factors.append(
            f"Nur {total_trades} historische Trades — die Statistik ist eher Anekdote als Beweis."
        )
    if entry > 0 and stop > 0:
        stop_dist = (entry - stop) / entry * 100
        if stop_dist > 8:
            r.risk_factors.append(
                f"Weiter Stop ({stop_dist:.1f}% unter dem Entry) — Positionsgröße entsprechend klein halten."
            )
    if ema20 is not None and close > ema20 * 1.10:
        r.risk_factors.append(
            f"Kurs {(close - ema20) / ema20 * 100:.0f}% über dem EMA20 — der Einstieg ist gestreckt; ein Rücksetzer Richtung EMA20 wäre normal."
        )

    # --- News + catalyst detection (reuses the scanner's keyword categories) ---
    news = _fetch_recent_news(settings, ticker)
    if news:
        tags, top_item = detect_catalysts(news)
        if tags:
            r.pro_factors.append(
                f"Aktuelle News mit erkanntem Katalysator ({', '.join(tags[:3])})"
                + (f': "{top_item.headline}"' if top_item else "")
            )
        r.news = [
            {"headline": n.headline, "published": (n.published_at or "")[:10], "source": n.source, "url": n.url}
            for n in news[:3]
        ]
        if not tags:
            r.risk_factors.append(
                "News vorhanden, aber ohne erkennbaren Katalysator — der Move ist primär technisch getrieben."
            )
    else:
        r.risk_factors.append(
            "Keine aktuellen News gefunden — rein technisches Setup ohne fundamentalen Rückenwind."
        )

    # --- Earnings proximity (ESTIMATE) ---
    days_to_earnings = _estimate_next_earnings_days(settings, ticker)
    if days_to_earnings is not None and -5 <= days_to_earnings <= 10:
        r.risk_factors.append(
            f"⚠️ Earnings vermutlich in ~{max(days_to_earnings, 0)} Tagen (GESCHÄTZT aus dem letzten "
            f"Quartalsbericht + ~90 Tage — kein offizieller Termin!). Ein Earnings-Gap kann jedes "
            f"Swing-Setup über Nacht zerschießen; Positionsgröße reduzieren oder Termin abwarten."
        )

    return r
