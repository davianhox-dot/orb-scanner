"""
Market regime ("Markt-Ampel") — a soft overall-market filter.

Swing longs have a measurably worse hit rate when the broad market is in a
downtrend; most breakouts fail in falling markets simply because the tide
is going out. This module classifies the current regime from SPY daily
bars and returns a score adjustment that find_top_setups applies to every
candidate — signals still show up in red regimes (soft filter, per user
choice), they just rank lower and carry a visible warning.

Classification (SMA-based, deliberately simple and inspectable):
  GREEN  — SPY close above SMA200 AND SMA50 above SMA200 (healthy uptrend)
  YELLOW — transition states (close above SMA200 with weak SMA50, or a
           fresh breakdown below SMA200 not yet confirmed by SMA50)
  RED    — SPY close below SMA200 AND SMA50 below SMA200 (confirmed downtrend)

Score adjustments: green 0, yellow -10, red -25.
"""
from dataclasses import dataclass

from cloud.db import HistoricalBar
from cloud.indicators import sma

REGIME_SCORE_ADJUST = {"green": 0.0, "yellow": -10.0, "red": -25.0, "unknown": 0.0}
REGIME_LABELS = {
    "green": "🟢 Grün — Gesamtmarkt im Aufwärtstrend, Rückenwind für Long-Setups",
    "yellow": "🟡 Gelb — Gesamtmarkt im Übergang, Setups mit reduzierter Erwartung",
    "red": "🔴 Rot — Gesamtmarkt im Abwärtstrend, die meisten Ausbrüche scheitern in diesem Umfeld",
    "unknown": "⚪ Unbekannt — keine SPY-Daten verfügbar, keine Markt-Anpassung angewendet",
}


@dataclass
class MarketRegime:
    status: str  # "green" | "yellow" | "red" | "unknown"
    score_adjust: float
    spy_close: float | None = None
    spy_sma50: float | None = None
    spy_sma200: float | None = None

    @property
    def label(self) -> str:
        return REGIME_LABELS[self.status]

    def to_dict(self) -> dict:
        return {
            "status": self.status, "score_adjust": self.score_adjust,
            "spy_close": self.spy_close, "spy_sma50": self.spy_sma50, "spy_sma200": self.spy_sma200,
        }


def compute_regime(spy_bars: list[HistoricalBar] | None) -> MarketRegime:
    if not spy_bars or len(spy_bars) < 200:
        return MarketRegime(status="unknown", score_adjust=0.0)

    bars = sorted(spy_bars, key=lambda b: b.timestamp)
    closes = [b.close for b in bars]
    i = len(closes) - 1
    sma50 = sma(closes, 50)[i]
    sma200 = sma(closes, 200)[i]
    close = closes[i]
    if sma50 is None or sma200 is None:
        return MarketRegime(status="unknown", score_adjust=0.0)

    if close > sma200 and sma50 > sma200:
        status = "green"
    elif close < sma200 and sma50 < sma200:
        status = "red"
    else:
        status = "yellow"

    return MarketRegime(
        status=status, score_adjust=REGIME_SCORE_ADJUST[status],
        spy_close=round(close, 2), spy_sma50=round(sma50, 2), spy_sma200=round(sma200, 2),
    )
