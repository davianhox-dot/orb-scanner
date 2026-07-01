"""Validation — Phase 4. Runs IS/OOS split, walk-forward segments, Monte
Carlo simulation, and parameter robustness testing on a strategy, then
combines them into a 0-100 robustness score with an explicit overfitting
warning."""
import sys
from pathlib import Path


def _add_repo_root_to_path() -> None:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "cloud" / "config.py").exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return
    raise RuntimeError("Could not locate repo root (looked for cloud/config.py in parent directories)")


_add_repo_root_to_path()

from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

from cloud.config import get_settings
from cloud.db import SavedStrategy, get_session_factory, init_db
from cloud.strategy_presets import PRESET_NAMES, config_from_dict, get_preset
from cloud.validation import MIN_TRADES_FOR_CONFIDENCE, run_full_validation

st.set_page_config(page_title="Validation — ORB Scanner", page_icon="🔬", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("🔬 Validation — Robustness & Overfitting Check")
st.caption(
    "Four independent stress tests on one strategy: in-sample/out-of-sample split, "
    "walk-forward segments, Monte Carlo simulation, and parameter sensitivity — "
    "combined into a 0-100 robustness score."
)

# --- Pick strategy ---
st.subheader("1 · Strategy")
source = st.radio("Source", ["Preset", "Saved strategy"], horizontal=True)

base_config = None
if source == "Preset":
    preset_choice = st.selectbox("Preset", PRESET_NAMES)
    base_config = get_preset(preset_choice)
else:
    with Session() as db:
        saved = db.execute(select(SavedStrategy).order_by(SavedStrategy.name)).scalars().all()
    if not saved:
        st.warning("No saved strategies yet — build one in the Strategy Builder first, or use a preset.")
        st.stop()
    saved_names = [s.name for s in saved]
    saved_choice = st.selectbox("Saved strategy", saved_names)
    row = next(s for s in saved if s.name == saved_choice)
    base_config = config_from_dict(row.config)

# --- Settings ---
st.subheader("2 · Validation settings")
c1, c2, c3 = st.columns([2, 1, 1])
tickers_input = c1.text_input("Tickers (comma-separated)", value="AAPL")
start_date = c2.date_input("Start", value=date.today() - timedelta(days=730))
end_date = c3.date_input("End", value=date.today() - timedelta(days=1))

s1, s2, s3 = st.columns(3)
n_segments = s1.number_input("Walk-forward segments", min_value=2, max_value=12, value=4)
split_pct = s2.number_input("In-sample split (%)", min_value=50.0, max_value=90.0, value=70.0, step=5.0)
n_sims = s3.number_input("Monte Carlo simulations", min_value=100, max_value=5000, value=1000, step=100)

st.caption(
    "Tip: validation runs many backtests (segments + one per parameter perturbation), so use a "
    "date range long enough that each walk-forward segment still contains multiple trades — "
    "1-2 years is a reasonable minimum for swing strategies."
)

if not settings.POLYGON_API_KEY:
    st.warning("`POLYGON_API_KEY` isn't set in your secrets — validation needs real historical price data.")

if st.button("🔬 Run Validation", type="primary"):
    tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]

    if not tickers:
        st.warning("Enter at least one ticker.")
    elif start_date >= end_date:
        st.warning("Start date must be before end date.")
    elif not settings.POLYGON_API_KEY:
        st.error("Can't run without `POLYGON_API_KEY` — add it to your secrets first.")
    else:
        status = st.status("Running validation…", expanded=True)

        def on_progress(msg: str) -> None:
            status.write(msg)

        try:
            with Session() as db:
                full, isoos, wf, robust, mc, summary = run_full_validation(
                    db, settings, tickers, start_date, end_date, base_config,
                    n_segments=int(n_segments), split_pct=float(split_pct),
                    n_sims=int(n_sims), progress_callback=on_progress,
                )
        except Exception as exc:  # noqa: BLE001
            status.update(label="Validation failed", state="error")
            st.error(f"Validation failed: {exc}")
            st.stop()

        status.update(label="Validation complete", state="complete", expanded=False)

        if not full.trades:
            st.warning(
                "The strategy produced no trades at all over this period — nothing to validate. "
                "Loosen thresholds, widen the date range, or try different tickers."
            )
            st.stop()

        # --- Score header ---
        st.divider()
        score = summary.score
        if score >= 70:
            color, verdict = "green", "Robust across the tests run here"
        elif score >= 50:
            color, verdict = "orange", "Mixed — inspect the weak components below"
        else:
            color, verdict = "red", "Fragile — treat this configuration as likely overfitted"

        h1, h2 = st.columns([1, 3])
        h1.metric("Robustness Score", f"{score:.0f} / 100")
        h2.markdown(f"### :{color}[{verdict}]")

        if summary.overfit_warning:
            st.error(
                "⚠️ **Overfitting warning:** this configuration scored below 50. Its historical "
                "results are more likely a fit to this particular past than a repeatable edge. "
                "Do not trade it as-is — loosen parameters toward regions where neighboring "
                "values also perform, and re-validate."
            )
        if summary.low_sample_warning:
            st.warning(
                f"⚠️ **Low sample size:** fewer than {MIN_TRADES_FOR_CONFIDENCE} total trades. "
                "Every number on this page is statistically weak at this sample size — widen the "
                "date range or add tickers before drawing conclusions."
            )

        # --- Component breakdown ---
        st.subheader("Score components")
        comp_labels = {
            "segment_consistency": ("Segment Consistency", "35%"),
            "parameter_stability": ("Parameter Stability", "30%"),
            "monte_carlo_risk": ("Monte Carlo Risk", "20%"),
            "oos_retention": ("Out-of-Sample Retention", "15%"),
        }
        for key, (label, weight) in comp_labels.items():
            comp_score, note = summary.components[key]
            st.caption(f"**{label}** (weight {weight}) — {comp_score:.0f}/100 · {note}")
            st.progress(min(1.0, max(0.0, comp_score / 100)))

        # --- Walk-forward detail ---
        st.subheader("Walk-forward segments")
        wf_df = pd.DataFrame(
            [
                {
                    "Segment": f"{s['start']} → {s['end']}",
                    "Trades": s["metrics"].get("total_trades", 0),
                    "Expectancy $": s["metrics"].get("expectancy"),
                    "Win Rate %": s["metrics"].get("win_rate_pct"),
                    "Return %": s["metrics"].get("total_return_pct"),
                    "Max DD %": s["metrics"].get("max_drawdown_pct"),
                }
                for s in wf.segments
            ]
        )
        st.dataframe(wf_df, width="stretch", hide_index=True)

        # --- IS/OOS detail ---
        st.subheader("In-sample vs. out-of-sample")
        io1, io2 = st.columns(2)
        with io1:
            st.markdown(f"**In-sample** ({isoos.is_start} → {isoos.is_end})")
            st.write(
                f"Trades: {isoos.is_metrics.get('total_trades', 0)} · "
                f"Expectancy: ${isoos.is_metrics.get('expectancy', 0)} · "
                f"Return: {isoos.is_metrics.get('total_return_pct', 0)}%"
            )
        with io2:
            st.markdown(f"**Out-of-sample** ({isoos.oos_start} → {isoos.oos_end})")
            st.write(
                f"Trades: {isoos.oos_metrics.get('total_trades', 0)} · "
                f"Expectancy: ${isoos.oos_metrics.get('expectancy', 0)} · "
                f"Return: {isoos.oos_metrics.get('total_return_pct', 0)}%"
            )

        # --- Monte Carlo detail ---
        if mc:
            st.subheader(f"Monte Carlo ({mc.n_sims} resampled equity paths from {mc.n_trades} trades)")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Final equity — P5 (bad case)", f"${mc.final_equity_p5:,.0f}")
            m2.metric("Final equity — median", f"${mc.final_equity_p50:,.0f}")
            m3.metric("Max drawdown — P95", f"{mc.max_drawdown_p95:.1f}%")
            m4.metric("Chance of overall loss", f"{mc.prob_losing_overall_pct:.1f}%")

            hist = pd.cut(pd.Series(mc.equity_distribution), bins=20).value_counts().sort_index()
            hist_df = pd.DataFrame({"Final equity range": [str(i) for i in hist.index], "Simulations": hist.values}).set_index(
                "Final equity range"
            )
            st.bar_chart(hist_df)

        # --- Parameter sensitivity detail ---
        st.subheader("Parameter sensitivity (±10% / ±20% perturbations)")
        st.caption(
            "Retention = expectancy of the perturbed run as % of the base run. Consistently high "
            "retention means the strategy sits in a robust parameter region; a parameter whose "
            "small shifts collapse retention is a curve-fit cliff."
        )
        pert_df = pd.DataFrame(
            [
                {
                    "Parameter": r.param_label,
                    "Shift": f"{r.perturbation_pct:+.0f}%",
                    "Value": r.value,
                    "Expectancy $": r.expectancy,
                    "Trades": r.total_trades,
                    "Retention %": r.retention_pct,
                }
                for r in robust.rows
            ]
        )
        st.dataframe(pert_df, width="stretch", hide_index=True)
