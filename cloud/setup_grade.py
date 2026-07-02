"""
Setup grade (A/B/C) — the "wie sicher ist dieser Trade?" answer, built
strictly from what is measurable.

An honest disclaimer first, stated here and in the UI: nobody can measure
whether hedge funds would take a given trade — institutional positioning
only becomes public in quarterly 13F filings, months after the fact. What
IS measurable are the criteria institutional swing/momentum money
demonstrably screens for. This grade aggregates four of them, 25 points
each (total 0-100 → A ≥ 75, B ≥ 50, C below):

1. TP-REALISMUS (0-25) — the UMAC problem. Two checks:
   (a) Does the target sit inside or near the stock's own traded range,
       or out in never-visited territory?
   (b) Is the required move achievable within the strategy's max holding
       period, given the stock's actual daily volatility (ATR)? A target
       needing 3x more movement than the stock typically produces in that
       window is a lottery ticket, not a plan.

2. STOP-QUALITÄT (0-25) — distance from entry to stop. Tight, structured
   stops (≤8%) are how professionals cap risk; a 20-30% stop means the
   setup itself is poorly located.

3. RELATIVE STÄRKE (0-25) — 3-month performance vs SPY. Institutional
   momentum strategies buy what is already outperforming the market;
   a stock lagging SPY has the opposite profile.

4. INSTITUTIONELLE HANDELBARKEIT (0-25) — average dollar volume (can size
   be moved in and out without moving the price?) plus buy-side volume
   dominance (accumulation footprint).
"""
from dataclasses import dataclass, field

from cloud.db import HistoricalBar
from cloud.strategy_rules import IndicatorCache

GRADE_THRESHOLDS = (("A", 75.0), ("B", 50.0))


@dataclass
class SetupGrade:
    grade: str  # "A" | "B" | "C"
    points: float  # 0-100
    reasons: list[str] = field(default_factory=list)


def _grade_letter(points: float) -> str:
    for letter, threshold in GRADE_THRESHOLDS:
        if points >= threshold:
            return letter
    return "C"


def grade_setup(
    bars: list[HistoricalBar],
    spy_bars: list[HistoricalBar] | None,
    entry: float,
    stop: float,
    target: float,
    max_holding_days: int,
) -> SetupGrade:
    reasons: list[str] = []
    if len(bars) < 30 or entry <= 0:
        return SetupGrade(grade="C", points=0.0, reasons=["Zu wenig Daten für eine Qualitätsnote."])

    bars = sorted(bars, key=lambda b: b.timestamp)
    i = len(bars) - 1
    cache = IndicatorCache(bars)
    close = bars[i].close

    # --- 1. TP realism (0-25) ---
    period_high = max(b.high for b in bars)
    needed_move_pct = (target - entry) / entry * 100
    atr_series = cache.atr(14)
    atr_val = atr_series[i] if len(bars) > 15 else None
    atr_pct = (atr_val / close * 100) if (atr_val and close) else None
    # Feasibility heuristic: directional drift rarely exceeds ~40% of the
    # summed daily ATR over the holding window.
    feasible_move_pct = atr_pct * max_holding_days * 0.4 if atr_pct else None

    if target <= period_high * 1.02:
        tp_points, tp_note = 25.0, "TP liegt innerhalb der bereits gehandelten Spanne"
    elif target <= period_high * 1.10:
        tp_points, tp_note = 15.0, f"TP liegt {((target / period_high) - 1) * 100:.0f}% über dem bisherigen Hoch"
    else:
        tp_points, tp_note = 5.0, f"TP liegt {((target / period_high) - 1) * 100:.0f}% über dem bisherigen Hoch — unerforschtes Terrain"
    if feasible_move_pct is not None and needed_move_pct > feasible_move_pct:
        tp_points = min(tp_points, 5.0)
        tp_note += (
            f"; benötigte Bewegung ({needed_move_pct:.0f}%) übersteigt, was die Aktie bei ihrer "
            f"Volatilität in {max_holding_days} Tagen typischerweise schafft (~{feasible_move_pct:.0f}%)"
        )
    reasons.append(f"TP-Realismus {tp_points:.0f}/25 — {tp_note}.")

    # --- 2. Stop quality (0-25) ---
    stop_dist_pct = (entry - stop) / entry * 100 if stop > 0 else 100.0
    if stop_dist_pct <= 8:
        stop_points, stop_note = 25.0, f"enger, strukturierter Stop ({stop_dist_pct:.1f}%)"
    elif stop_dist_pct <= 12:
        stop_points, stop_note = 15.0, f"moderater Stop ({stop_dist_pct:.1f}%)"
    elif stop_dist_pct <= 15:
        stop_points, stop_note = 8.0, f"weiter Stop ({stop_dist_pct:.1f}%)"
    else:
        stop_points, stop_note = 0.0, f"sehr weiter Stop ({stop_dist_pct:.1f}%) — das Setup ist strukturell unsauber"
    reasons.append(f"Stop-Qualität {stop_points:.0f}/25 — {stop_note}.")

    # --- 3. Relative strength vs SPY, 3 months (0-25) ---
    if spy_bars and len(spy_bars) >= 63 and len(bars) >= 63:
        spy_sorted = sorted(spy_bars, key=lambda b: b.timestamp)
        stock_3m = (close - bars[i - 62].close) / bars[i - 62].close * 100
        spy_3m = (spy_sorted[-1].close - spy_sorted[-63].close) / spy_sorted[-63].close * 100
        rs = stock_3m - spy_3m
        if rs >= 10:
            rs_points, rs_note = 25.0, f"deutlich stärker als der Markt (+{rs:.0f}pp vs. SPY in 3M)"
        elif rs >= 0:
            rs_points, rs_note = 15.0, f"leicht stärker als der Markt (+{rs:.0f}pp vs. SPY in 3M)"
        else:
            rs_points, rs_note = 5.0, f"schwächer als der Markt ({rs:.0f}pp vs. SPY in 3M) — institutionelles Momentum-Geld meidet Nachzügler"
    else:
        rs_points, rs_note = 12.0, "relative Stärke nicht messbar (SPY-Daten fehlen) — neutral gewertet"
    reasons.append(f"Relative Stärke {rs_points:.0f}/25 — {rs_note}.")

    # --- 4. Institutional tradability (0-25) ---
    window = bars[max(0, i - 19) : i + 1]
    avg_dollar_vol = sum(b.close * b.volume for b in window) / len(window)
    if avg_dollar_vol >= 10_000_000:
        liq_points, liq_note = 15.0, f"hohe Liquidität (Ø ${avg_dollar_vol / 1e6:.0f}M/Tag — auch für große Adressen handelbar)"
    elif avg_dollar_vol >= 2_000_000:
        liq_points, liq_note = 10.0, f"solide Liquidität (Ø ${avg_dollar_vol / 1e6:.1f}M/Tag)"
    else:
        liq_points, liq_note = 3.0, f"dünne Liquidität (Ø ${avg_dollar_vol / 1e6:.1f}M/Tag) — institutionell kaum handelbar, Fills unzuverlässig"
    up_window = bars[max(0, i - 29) : i + 1]
    up_vol = sum(b.volume for b in up_window if b.close > b.open)
    total_vol = sum(b.volume for b in up_window)
    up_share = (up_vol / total_vol * 100) if total_vol else 0.0
    if up_share >= 58:
        liq_points += 10.0
        liq_note += f"; Kaufseite dominiert ({up_share:.0f}% des Volumens an grünen Tagen — Akkumulations-Fußabdruck)"
    elif up_share >= 50:
        liq_points += 5.0
    reasons.append(f"Institutionelle Handelbarkeit {liq_points:.0f}/25 — {liq_note}.")

    points = round(tp_points + stop_points + rs_points + liq_points, 1)
    return SetupGrade(grade=_grade_letter(points), points=points, reasons=reasons)
