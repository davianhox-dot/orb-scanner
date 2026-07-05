"""
Pick performance — the scanner grading ITSELF.

Every nightly/manual Top-Setups run is stored. This module walks all past
picks and measures what actually happened afterwards, so the user learns
which grades and strategies to trust — or that they shouldn't.

Honesty rules that shape the math:
- A pick's entry is a BUY-STOP at the stored entry level. If price never
  traded above that level within TRIGGER_WINDOW days after the signal, the
  trade never happened — such picks are counted separately as "nicht
  ausgelöst" instead of polluting the return stats with fills that were
  impossible.
- Returns are measured from the ENTRY level (the trigger), not the signal
  close, at 5/10/20 TRADING days after the trigger day. That's the
  passive "what if I just held" view — real results with stops/targets
  would differ in both directions; this measures pick QUALITY, not a
  specific exit policy.
- Picks younger than the horizon are marked "zu frisch" for that horizon
  rather than measured on partial data.
- Duplicates (same day + ticker + strategy across manual/nightly runs)
  are counted once.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cloud.config import Settings
from cloud.db import TopSetupRun
from cloud.historical_data import ensure_bars_cached, get_bars

logger = logging.getLogger(__name__)

TRIGGER_WINDOW = 3  # trading days the buy-stop stays "valid" after the signal
HORIZONS = (5, 10, 20)


@dataclass
class PickOutcome:
    scan_day: str
    ticker: str
    strategy_name: str
    grade: str
    entry: float
    triggered: bool
    trigger_day: str | None = None
    returns: dict = field(default_factory=dict)  # horizon -> pct or None ("zu frisch")


@dataclass
class PerformanceReport:
    outcomes: list[PickOutcome] = field(default_factory=list)
    total_picks: int = 0
    untriggered: int = 0

    def stats_by(self, key_fn) -> dict:
        """Aggregate triggered picks by an arbitrary key (grade, strategy):
        per horizon -> {n, avg_return, win_rate}."""
        groups: dict = {}
        for o in self.outcomes:
            if not o.triggered:
                continue
            groups.setdefault(key_fn(o), []).append(o)
        out: dict = {}
        for key, picks in groups.items():
            per_h = {}
            for h in HORIZONS:
                vals = [p.returns.get(h) for p in picks if p.returns.get(h) is not None]
                if vals:
                    per_h[h] = {
                        "n": len(vals),
                        "avg_return": round(sum(vals) / len(vals), 2),
                        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
                    }
            out[key] = {"picks": len(picks), "horizons": per_h}
        return out


def _dedupe_picks(runs: list[TopSetupRun]) -> list[tuple[str, dict]]:
    seen: set[tuple] = set()
    picks: list[tuple[str, dict]] = []
    for run in runs:
        for s in run.top or []:
            key = (run.scan_day, s.get("ticker"), s.get("strategy_name"))
            if key in seen:
                continue
            seen.add(key)
            picks.append((run.scan_day, s))
    return picks


def evaluate_pick(db: Session, settings: Settings, scan_day: str, setup: dict) -> PickOutcome:
    ticker = setup.get("ticker", "")
    entry = float(setup.get("entry", 0) or 0)
    outcome = PickOutcome(
        scan_day=scan_day, ticker=ticker,
        strategy_name=setup.get("strategy_name", ""), grade=setup.get("grade", "?"),
        entry=entry, triggered=False,
    )
    if not ticker or entry <= 0:
        return outcome

    signal_day = date.fromisoformat(setup.get("signal_date", scan_day))
    end = date.today()
    try:
        ensure_bars_cached(db, settings, ticker, signal_day, end, timeframe="day")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Data fetch failed for %s: %s", ticker, exc)

    bars = sorted(
        (b for b in get_bars(db, ticker, signal_day, end, timeframe="day") if b.timestamp.date() > signal_day),
        key=lambda b: b.timestamp,
    )
    if not bars:
        return outcome

    # Buy-stop trigger check within the window
    trigger_index: int | None = None
    for k, bar in enumerate(bars[:TRIGGER_WINDOW]):
        if bar.high >= entry:
            trigger_index = k
            break
    if trigger_index is None:
        return outcome

    outcome.triggered = True
    outcome.trigger_day = bars[trigger_index].timestamp.date().isoformat()

    for h in HORIZONS:
        idx = trigger_index + h
        if idx < len(bars):
            outcome.returns[h] = round((bars[idx].close - entry) / entry * 100, 2)
        else:
            outcome.returns[h] = None  # zu frisch für diesen Horizont
    return outcome


def build_report(db: Session, settings: Settings, max_runs: int = 200) -> PerformanceReport:
    runs = db.execute(
        select(TopSetupRun).order_by(TopSetupRun.created_at.desc()).limit(max_runs)
    ).scalars().all()

    report = PerformanceReport()
    for scan_day, setup in _dedupe_picks(runs):
        outcome = evaluate_pick(db, settings, scan_day, setup)
        report.outcomes.append(outcome)
        report.total_picks += 1
        if not outcome.triggered:
            report.untriggered += 1
    return report
