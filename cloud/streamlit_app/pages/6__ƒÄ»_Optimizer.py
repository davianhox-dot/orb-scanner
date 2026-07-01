"""Optimizer — Phase 3. Load a saved strategy (or preset), choose up to
three parameters to vary over value lists, run every combination against
cached historical data, and see the results ranked. Includes an explicit
overfitting caveat: the top result fit the PAST best, which is not the same
as being most likely to work in the future."""
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
from cloud.optimizer import (
    MAX_COMBINATIONS,
    RANK_METRICS,
    ParamSpec,
    count_combinations,
    list_tunable_params,
    run_optimization,
    save_optimization,
)
from cloud.strategy_presets import PRESET_NAMES, config_from_dict, get_preset

st.set_page_config(page_title="Optimizer — ORB Scanner", page_icon="🎯", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("🎯 Optimizer — Parameter Grid Search")
st.caption(
    "Pick a strategy, choose which parameters to vary over which values, and run every "
    "combination. Results are ranked by the metric you choose."
)

st.info(
    "**Read this before trusting the #1 result:** the top-ranked combination is, by "
    "construction, the one that fit *this past data* best — that is not the same as "
    "the one most likely to work in the future. If one value wins big while its "
    "neighbors lose money, that's usually curve-fitting, not edge. Prefer parameter "
    "*regions* where many neighboring values all perform decently. Formal robustness "
    "testing (walk-forward, Monte Carlo) is the next phase."
)

# --- Pick the base strategy ---
st.subheader("1 · Base strategy")
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

with st.expander("Base strategy details"):
    st.json(
        {
            "entry_conditions": [{"type": c.type, "params": c.params} for c in base_config.entry_conditions],
            "trend_filters": [{"type": c.type, "params": c.params} for c in base_config.trend_filters],
            "entry_logic": base_config.entry_logic,
            "exit_rules": base_config.exit_rules.__dict__,
            "position_sizing": base_config.position_sizing.__dict__,
        }
    )

# --- Choose parameters to vary ---
st.subheader("2 · Parameters to vary (up to 3)")
tunable = list_tunable_params(base_config)
tunable_labels = [s.label for s in tunable]

selected_labels = st.multiselect("Parameters", tunable_labels, max_selections=3)

specs: list[ParamSpec] = []
for label in selected_labels:
    spec = next(s for s in tunable if s.label == label)
    values_str = st.text_input(
        f"Values for **{label}** (comma-separated)",
        value="",
        placeholder="e.g. 10, 15, 20, 25",
        key=f"opt_values_{label}",
    )
    try:
        values = [float(v.strip()) for v in values_str.split(",") if v.strip()]
    except ValueError:
        st.error(f"Couldn't parse the values for {label} — use numbers separated by commas.")
        values = []
    specs.append(ParamSpec(label=spec.label, path=spec.path, values=values))

active_specs = [s for s in specs if s.values]
if active_specs:
    total = count_combinations(active_specs)
    if total > MAX_COMBINATIONS:
        st.error(f"{total} combinations exceeds the cap of {MAX_COMBINATIONS}. Reduce values per parameter.")
    else:
        st.caption(f"This grid will run **{total}** backtest combination(s).")

# --- Backtest settings ---
st.subheader("3 · Backtest settings")
b1, b2, b3 = st.columns([2, 1, 1])
tickers_input = b1.text_input("Tickers (comma-separated)", value="AAPL")
start_date = b2.date_input("Start", value=date.today() - timedelta(days=365))
end_date = b3.date_input("End", value=date.today() - timedelta(days=1))
rank_by = st.selectbox(
    "Rank results by", list(RANK_METRICS.keys()), format_func=lambda k: RANK_METRICS[k]
)

if not settings.POLYGON_API_KEY:
    st.warning("`POLYGON_API_KEY` isn't set in your secrets — the optimizer needs real historical price data.")

if st.button("🎯 Run Optimization", type="primary"):
    tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]

    if not tickers:
        st.warning("Enter at least one ticker.")
    elif start_date >= end_date:
        st.warning("Start date must be before end date.")
    elif not active_specs:
        st.warning("Select at least one parameter and give it a list of values.")
    elif count_combinations(active_specs) > MAX_COMBINATIONS:
        st.error(f"Too many combinations (cap: {MAX_COMBINATIONS}).")
    elif not settings.POLYGON_API_KEY:
        st.error("Can't run without `POLYGON_API_KEY` — add it to your secrets first.")
    else:
        progress_bar = st.progress(0.0, text="Starting…")

        def on_progress(done: int, total: int) -> None:
            progress_bar.progress(done / total, text=f"Combination {done} / {total}")

        try:
            with Session() as db:
                rows = run_optimization(
                    db, settings, tickers, start_date, end_date, base_config,
                    active_specs, rank_by=rank_by, progress_callback=on_progress,
                )
                save_optimization(db, tickers, start_date, end_date, base_config, active_specs, rank_by, rows)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Optimization failed: {exc}")
            st.stop()

        progress_bar.progress(1.0, text="Done")

        rows_with_trades = [r for r in rows if r.metrics.get("total_trades", 0) > 0]
        if not rows_with_trades:
            st.warning(
                "No combination produced any trades. Loosen the strategy's thresholds, "
                "widen the date range, or try different tickers."
            )
        else:
            st.subheader("Results (ranked)")
            table_rows = []
            for rank, r in enumerate(rows, start=1):
                m = r.metrics
                entry = {"Rank": rank}
                entry.update({k: v for k, v in r.params.items()})
                entry.update(
                    {
                        "Trades": m.get("total_trades", 0),
                        "Win Rate %": m.get("win_rate_pct"),
                        "Profit Factor": m.get("profit_factor"),
                        "Expectancy $": m.get("expectancy"),
                        "Total Return %": m.get("total_return_pct"),
                        "Avg R": m.get("avg_r_multiple"),
                        "Max DD %": m.get("max_drawdown_pct"),
                    }
                )
                table_rows.append(entry)

            df = pd.DataFrame(table_rows)
            st.dataframe(df, width="stretch", hide_index=True)

            low_trade_rows = sum(1 for r in rows_with_trades if r.metrics.get("total_trades", 0) < 10)
            if low_trade_rows:
                st.caption(
                    f"⚠️ {low_trade_rows} combination(s) produced fewer than 10 trades — "
                    "results with so few trades are mostly noise. Widen the date range or "
                    "add more tickers before drawing conclusions from them."
                )
