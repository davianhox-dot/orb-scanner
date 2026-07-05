"""
Position monitor — the nightly guard for positions the user ACTUALLY holds.

Why this exists: stop-loss and take-profit can live at the broker as a
bracket order, but an indicator exit ("sell when the day closes below the
EMA50") cannot — someone has to look at the close every evening. This
module is that someone.

For each open TrackedPosition it walks the daily bars SINCE ENTRY in
chronological order and reports the FIRST event that triggered:

  🔴 stop      — some day's low touched the stop (your broker order should
                 have filled; verify!)
  🟢 target    — some day's high reached the target
  🟣 indicator — a day CLOSED below/above the exit EMA → sell next open
  ⏳ time      — max holding days elapsed without stop/target → the setup's
                 thesis has expired, consider closing
  ✅ hold      — nothing triggered; position stays on plan (with current
                 price and open P/L for context)

Honesty rules:
- Positions are NEVER auto-closed. We don't know the user's real fills,
  partial sells, or whether the broker order actually executed. The
  monitor reports; the human decides and closes the position in the app.
- Evaluation is on daily bars (end-of-day). An intraday stop touch shows
  up here at the earliest after that day's data exists — the broker-side
  stop order remains the real-time protection; this is the safety net for
  everything a broker can't watch.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from cloud.config import Settings
from cloud.db import TrackedPosition
from cloud.historical_data import ensure_bars_cached, get_bars
from cloud.indicators import ema

logger = logging.getLogger(__name__)

STATUS_EMOJI = {"stop": "🔴", "target": "🟢", "indicator_exit": "🟣", "time_exit": "⏳", "hold": "✅", "no_data": "⚪"}


@dataclass
class PositionCheck:
    position_id: str
    ticker: str
    status: str  # stop | target | indicator_exit | time_exit | hold | no_data
    message: str
    action_needed: bool
    current_price: float | None = None
    open_pnl_pct: float | None = None


def check_position(db: Session, settings: Settings, pos: TrackedPosition) -> PositionCheck:
    entry_date = date.fromisoformat(pos.entry_date)
    # Lead-in so the exit EMA is accurate from the entry day onward.
    lead_in = int(pos.indicator_exit_period * 1.6) + 10 if pos.indicator_exit else 5
    start = entry_date - timedelta(days=lead_in)
    end = date.today()

    try:
        ensure_bars_cached(db, settings, pos.ticker, start, end, timeframe="day")
    except Exception as exc:  # noqa: BLE001 — no fresh data must not kill the whole check run
        logger.warning("Data fetch failed for %s: %s", pos.ticker, exc)

    bars = sorted(get_bars(db, pos.ticker, start, end, timeframe="day"), key=lambda b: b.timestamp)
    since_entry = [b for b in bars if b.timestamp.date() >= entry_date]
    if not since_entry:
        return PositionCheck(pos.id, pos.ticker, "no_data",
                             f"{pos.ticker}: keine Kursdaten seit Einstieg ({pos.entry_date}) verfügbar.", False)

    ema_series = ema([b.close for b in bars], int(pos.indicator_exit_period)) if pos.indicator_exit else None
    offset = len(bars) - len(since_entry)

    current = since_entry[-1].close
    pnl_pct = round((current - pos.entry_price) / pos.entry_price * 100, 2) if pos.entry_price else None

    for k, bar in enumerate(since_entry):
        d = bar.timestamp.date().isoformat()
        if bar.low <= pos.stop_price:
            return PositionCheck(pos.id, pos.ticker, "stop",
                f"{pos.ticker}: 🔴 STOP wurde am {d} erreicht (Tief {bar.low:.2f} ≤ Stop {pos.stop_price:.2f}). "
                f"Prüfen, ob die Broker-Order ausgeführt wurde — falls nicht: Position schließen.",
                True, current, pnl_pct)
        if bar.high >= pos.target_price:
            return PositionCheck(pos.id, pos.ticker, "target",
                f"{pos.ticker}: 🟢 ZIEL wurde am {d} erreicht (Hoch {bar.high:.2f} ≥ TP {pos.target_price:.2f}). "
                f"Prüfen, ob die Take-Profit-Order ausgeführt wurde.",
                True, current, pnl_pct)
        if pos.indicator_exit and ema_series is not None:
            ema_val = ema_series[offset + k]
            if ema_val is not None:
                below = bar.close < ema_val
                triggered = below if pos.indicator_exit_type == "close_below_ema" else not below and bar.close > ema_val
                if triggered:
                    richtung = "unter" if pos.indicator_exit_type == "close_below_ema" else "über"
                    return PositionCheck(pos.id, pos.ticker, "indicator_exit",
                        f"{pos.ticker}: 🟣 INDIKATOR-EXIT am {d} ausgelöst — Tagesschluss {bar.close:.2f} "
                        f"{richtung} EMA{pos.indicator_exit_period} ({ema_val:.2f}). Die Strategie sagt: "
                        f"Ausstieg zur nächsten Eröffnung.",
                        True, current, pnl_pct)

    days_held = (since_entry[-1].timestamp.date() - entry_date).days
    if pos.max_holding_days and days_held >= pos.max_holding_days:
        return PositionCheck(pos.id, pos.ticker, "time_exit",
            f"{pos.ticker}: ⏳ Maximale Haltedauer erreicht ({days_held} Tage ≥ {pos.max_holding_days}). "
            f"Weder Stop noch Ziel wurden getroffen — die These des Setups ist abgelaufen, Schließen erwägen. "
            f"Aktuell {current:.2f} ({pnl_pct:+.1f}%).",
            True, current, pnl_pct)

    dist_stop = (current - pos.stop_price) / current * 100 if current else 0
    dist_target = (pos.target_price - current) / current * 100 if current else 0
    return PositionCheck(pos.id, pos.ticker, "hold",
        f"{pos.ticker}: ✅ Auf Kurs — {current:.2f} ({pnl_pct:+.1f}%), Tag {days_held}"
        + (f"/{pos.max_holding_days}" if pos.max_holding_days else "")
        + f", Stop {dist_stop:.1f}% entfernt, Ziel {dist_target:.1f}% entfernt.",
        False, current, pnl_pct)


def check_open_positions(db: Session, settings: Settings) -> list[PositionCheck]:
    """Check every open position, persist last_signal/last_checked_at on
    each row, and return all results (action-needed first)."""
    positions = db.execute(
        select(TrackedPosition).where(TrackedPosition.status == "open").order_by(TrackedPosition.created_at)
    ).scalars().all()

    results: list[PositionCheck] = []
    for pos in positions:
        check = check_position(db, settings, pos)
        pos.last_signal = check.message
        pos.last_checked_at = datetime.now(timezone.utc)
        results.append(check)
    db.commit()

    results.sort(key=lambda c: (not c.action_needed, c.ticker))
    return results


def format_position_alert(checks: list[PositionCheck], scan_day: str) -> str | None:
    """Alert text for the nightly job — only sent when action is needed."""
    urgent = [c for c in checks if c.action_needed]
    if not urgent:
        return None
    lines = [f"💼 Positions-Check {scan_day} — {len(urgent)} Position(en) brauchen deine Aufmerksamkeit:"]
    for c in urgent:
        lines.append(c.message)
    holding = [c for c in checks if not c.action_needed and c.status == "hold"]
    if holding:
        lines.append(f"✅ {len(holding)} weitere Position(en) auf Kurs.")
    return "\n".join(lines)
