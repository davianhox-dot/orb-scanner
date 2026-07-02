"""Strategy Builder — Phase 2. Build a swing-trading strategy from
combinable entry conditions, trend filters, configurable exit rules, and
position sizing, without touching code. Start from a preset or from
scratch, save it, reload it later."""
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

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

from cloud.config import get_settings
from cloud.db import SavedStrategy, get_session_factory, init_db
from cloud.strategy_engine import ExitRuleConfig, PositionSizingConfig, StrategyConfig, run_strategy_backtest, save_run
from cloud.strategy_presets import PRESET_NAMES, config_from_dict, config_to_dict, get_preset
from cloud.strategy_rules import CONDITION_CATALOG, Condition

st.set_page_config(page_title="Strategy Builder — ORB Scanner", page_icon="🛠️", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("🛠️ Strategy Builder — Swing Trading")
st.caption(
    "Combine entry conditions and trend filters, choose exit rules and position sizing, "
    "then run, save, and reload — no code changes needed."
)


def _load_config_into_session(cfg: StrategyConfig) -> None:
    st.session_state["sb_name"] = cfg.name
    st.session_state["sb_trend_conditions"] = [{"type": c.type, "params": c.params} for c in cfg.trend_filters]
    st.session_state["sb_entry_conditions"] = [{"type": c.type, "params": c.params} for c in cfg.entry_conditions]
    st.session_state["sb_entry_logic"] = cfg.entry_logic
    st.session_state["sb_entry_fill"] = cfg.entry_fill
    st.session_state["sb_stop_type"] = cfg.exit_rules.stop_type
    st.session_state["sb_stop_value"] = float(cfg.exit_rules.stop_value)
    st.session_state["sb_stop_atr_period"] = int(cfg.exit_rules.stop_atr_period)
    st.session_state["sb_target_type"] = cfg.exit_rules.target_type
    st.session_state["sb_target_value"] = float(cfg.exit_rules.target_value)
    st.session_state["sb_trailing"] = cfg.exit_rules.trailing_stop
    st.session_state["sb_trailing_pct"] = float(cfg.exit_rules.trailing_pct)
    st.session_state["sb_max_holding"] = int(cfg.exit_rules.max_holding_days)
    st.session_state["sb_sizing_method"] = cfg.position_sizing.method
    st.session_state["sb_sizing_value"] = float(cfg.position_sizing.value)
    st.session_state["sb_capital"] = float(cfg.initial_capital)


def _render_condition_builder(key_prefix: str, title: str, help_text: str) -> list[Condition]:
    state_key = f"{key_prefix}_conditions"
    if state_key not in st.session_state:
        st.session_state[state_key] = []

    st.markdown(f"**{title}**")
    st.caption(help_text)

    for idx, cond in enumerate(list(st.session_state[state_key])):
        label = CONDITION_CATALOG[cond["type"]]["label"]
        params_str = ", ".join(f"{k}={v}" for k, v in cond["params"].items())
        row1, row2 = st.columns([6, 1])
        row1.write(f"{idx + 1}. {label} ({params_str})")
        if row2.button("Remove", key=f"{key_prefix}_remove_{idx}"):
            st.session_state[state_key].pop(idx)
            st.rerun()

    with st.expander(f"+ Add a condition"):
        type_options = list(CONDITION_CATALOG.keys())
        type_labels = [CONDITION_CATALOG[t]["label"] for t in type_options]
        selected_label = st.selectbox("Condition type", type_labels, key=f"{key_prefix}_new_type")
        selected_type = type_options[type_labels.index(selected_label)]

        new_params = {}
        for pname, meta in CONDITION_CATALOG[selected_type]["params"].items():
            default = meta["default"]
            widget_key = f"{key_prefix}_new_{selected_type}_{pname}"
            if isinstance(default, int):
                new_params[pname] = st.number_input(
                    meta["label"], min_value=int(meta["min"]), max_value=int(meta["max"]),
                    value=int(default), step=1, key=widget_key,
                )
            else:
                new_params[pname] = st.number_input(
                    meta["label"], min_value=float(meta["min"]), max_value=float(meta["max"]),
                    value=float(default), key=widget_key,
                )

        if st.button("Add condition", key=f"{key_prefix}_add_btn"):
            st.session_state[state_key].append({"type": selected_type, "params": new_params})
            st.rerun()

    return [Condition(c["type"], c["params"]) for c in st.session_state[state_key]]


# --- Load a preset or a saved strategy ---
load_col1, load_col2 = st.columns(2)
with load_col1:
    st.subheader("Start from a preset")
    preset_choice = st.selectbox("Preset", PRESET_NAMES, key="preset_choice")
    if st.button("Load Preset"):
        _load_config_into_session(get_preset(preset_choice))
        st.rerun()

with load_col2:
    st.subheader("Or load a saved strategy")
    with Session() as db:
        saved_strategies = db.execute(select(SavedStrategy).order_by(SavedStrategy.name)).scalars().all()
    if saved_strategies:
        saved_names = [s.name for s in saved_strategies]
        saved_choice = st.selectbox("Saved strategy", saved_names, key="saved_choice")
        load_col, delete_col = st.columns(2)
        if load_col.button("Load Saved Strategy"):
            row = next(s for s in saved_strategies if s.name == saved_choice)
            _load_config_into_session(config_from_dict(row.config))
            st.rerun()
        if delete_col.button("Delete", key="delete_saved"):
            with Session() as db:
                row = db.execute(select(SavedStrategy).where(SavedStrategy.name == saved_choice)).scalars().first()
                if row:
                    db.delete(row)
                    db.commit()
            st.rerun()
    else:
        st.caption("No saved strategies yet — build one below and save it.")

st.divider()

st.session_state.setdefault("sb_name", "My Strategy")
strategy_name = st.text_input("Strategy name", key="sb_name")

trend_conditions = _render_condition_builder(
    "sb_trend", "Trend Filters", "Always required (AND logic) — e.g. only trade above the 50-day EMA."
)
st.divider()
entry_conditions = _render_condition_builder(
    "sb_entry", "Entry Conditions", "Combined using the logic you choose below."
)
entry_logic = st.radio("Combine entry conditions using", ["AND", "OR"], key="sb_entry_logic", horizontal=True)
entry_fill = st.selectbox(
    "Entry execution",
    ["next_open", "break_signal_high"],
    format_func=lambda v: {
        "next_open": "Next day's open (unconditional)",
        "break_signal_high": "Buy-stop above signal candle's high (no break → no trade)",
    }[v],
    key="sb_entry_fill",
)

st.divider()
st.subheader("Exit Rules")
e1, e2, e3 = st.columns(3)
stop_type = e1.selectbox("Stop type", ["swing_low", "fixed_pct", "atr_multiple"], key="sb_stop_type")
stop_value_labels = {"swing_low": "Lookback (days)", "fixed_pct": "Stop (%)", "atr_multiple": "ATR multiple"}
st.session_state.setdefault("sb_stop_value", 15.0)
stop_value = e2.number_input(stop_value_labels[stop_type], min_value=0.1, key="sb_stop_value")
st.session_state.setdefault("sb_stop_atr_period", 14)
stop_atr_period = e3.number_input("ATR period (used by ATR-based stop/target)", min_value=2, max_value=100, key="sb_stop_atr_period")

t1, t2 = st.columns(2)
target_type = t1.selectbox("Target type", ["r_multiple", "fixed_pct", "atr_multiple"], key="sb_target_type")
st.session_state.setdefault("sb_target_value", 3.0)
target_value = t2.number_input("Target value", min_value=0.1, key="sb_target_value")

tr1, tr2, tr3 = st.columns(3)
trailing_stop = tr1.checkbox("Use trailing stop", key="sb_trailing")
st.session_state.setdefault("sb_trailing_pct", 8.0)
trailing_pct = tr2.number_input("Trailing (%)", min_value=0.5, max_value=50.0, key="sb_trailing_pct")
st.session_state.setdefault("sb_max_holding", 30)
max_holding_days = tr3.number_input("Max holding period (days)", min_value=1, max_value=250, key="sb_max_holding")

st.divider()
st.subheader("Position Sizing")
p1, p2 = st.columns(2)
sizing_method = p1.selectbox("Method", ["fixed_pct_risk", "fixed_dollar_risk"], key="sb_sizing_method")
sizing_value_label = "% of capital risked per trade" if sizing_method == "fixed_pct_risk" else "$ risked per trade"
st.session_state.setdefault("sb_sizing_value", 1.0)
sizing_value = p2.number_input(sizing_value_label, min_value=0.01, key="sb_sizing_value")

st.divider()
st.subheader("Backtest Settings")
b1, b2 = st.columns([2, 1])
tickers_input = b1.text_input("Tickers (comma-separated)", value="AAPL", key="sb_tickers")
bc1, bc2 = b2.columns(2)
start_date = bc1.date_input("Start", value=date.today() - timedelta(days=365), key="sb_start")
end_date = bc2.date_input("End", value=date.today() - timedelta(days=1), key="sb_end")
st.session_state.setdefault("sb_capital", 10_000.0)
initial_capital = st.number_input("Initial capital ($)", min_value=100.0, step=1_000.0, key="sb_capital")

if not settings.POLYGON_API_KEY:
    st.warning("`POLYGON_API_KEY` isn't set in your secrets — backtesting needs real historical price data.")

save_col, run_col = st.columns(2)


def _current_config() -> StrategyConfig:
    return StrategyConfig(
        name=strategy_name or "My Strategy",
        trend_filters=trend_conditions,
        entry_conditions=entry_conditions,
        entry_logic=entry_logic,
        entry_fill=entry_fill,
        exit_rules=ExitRuleConfig(
            stop_type=stop_type, stop_value=stop_value, stop_atr_period=int(stop_atr_period),
            target_type=target_type, target_value=target_value,
            trailing_stop=trailing_stop, trailing_pct=trailing_pct, max_holding_days=int(max_holding_days),
        ),
        position_sizing=PositionSizingConfig(method=sizing_method, value=sizing_value),
        initial_capital=initial_capital,
    )


with save_col:
    if st.button("💾 Save Strategy", width="stretch"):
        config = _current_config()
        with Session() as db:
            existing = db.execute(select(SavedStrategy).where(SavedStrategy.name == config.name)).scalars().first()
            if existing:
                existing.config = config_to_dict(config)
                existing.updated_at = datetime.utcnow()
            else:
                db.add(SavedStrategy(name=config.name, config=config_to_dict(config)))
            db.commit()
        st.success(f"Saved '{config.name}'")

with run_col:
    run_clicked = st.button("▶️ Run Backtest", type="primary", width="stretch")

if run_clicked:
    tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]

    if not tickers:
        st.warning("Enter at least one ticker.")
    elif start_date >= end_date:
        st.warning("Start date must be before end date.")
    elif not entry_conditions:
        st.warning("Add at least one entry condition.")
    elif not settings.POLYGON_API_KEY:
        st.error("Can't run a backtest without `POLYGON_API_KEY` — add it to your secrets first.")
    else:
        config = _current_config()
        try:
            with st.spinner(f"Fetching/caching daily data and running backtest for {', '.join(tickers)}…"):
                with Session() as db:
                    result = run_strategy_backtest(db, settings, tickers, start_date, end_date, config)
                    if result.trades:
                        save_run(db, tickers, start_date, end_date, config, result)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backtest failed: {exc}")
            st.stop()

        if not result.trades:
            st.warning(
                "No trades were generated for this combination of conditions/tickers/dates. "
                "Try loosening a threshold, widening the date range, or a different ticker."
            )
        else:
            m = result.metrics

            st.subheader("Performance")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Total Trades", m["total_trades"])
            c2.metric("Win Rate", f"{m['win_rate_pct']:.1f}%")
            c3.metric("Profit Factor", f"{m['profit_factor']:.2f}" if m["profit_factor"] is not None else "∞")
            c4.metric("Expectancy", f"${m['expectancy']:.2f}")
            c5.metric("Max Drawdown", f"{m['max_drawdown_pct']:.1f}%")

            c6, c7, c8, c9, c10 = st.columns(5)
            c6.metric("Total Return", f"{m['total_return_pct']:.1f}%")
            c7.metric("Avg R", f"{m['avg_r_multiple']:.2f}R")
            c8.metric("Avg Win", f"${m['avg_win']:.2f}")
            c9.metric("Avg Loss", f"${m['avg_loss']:.2f}")
            c10.metric("Final Capital", f"${m['final_capital']:,.0f}")

            st.caption(
                f"Largest win ${m['largest_win']:.2f} · Largest loss ${m['largest_loss']:.2f} · "
                f"Longest win streak {m['winning_streak']} · Longest loss streak {m['losing_streak']}"
            )

            st.subheader("Equity Curve")
            equity_df = pd.DataFrame(result.equity_curve, columns=["time", "equity"]).set_index("time")
            st.line_chart(equity_df)

            st.subheader("Trade List")
            trades_df = pd.DataFrame(
                [
                    {
                        "Ticker": t.ticker,
                        "Entry Date": t.entry_time.strftime("%Y-%m-%d"),
                        "Exit Date": t.exit_time.strftime("%Y-%m-%d"),
                        "Entry": round(t.entry_price, 2),
                        "Exit": round(t.exit_price, 2),
                        "Stop": round(t.stop_price, 2),
                        "Target": round(t.target_price, 2),
                        "Shares": round(t.shares, 1),
                        "P/L": round(t.pnl, 2),
                        "R": round(t.r_multiple, 2),
                        "Days Held": (t.exit_time - t.entry_time).days,
                        "Exit Reason": t.exit_reason,
                    }
                    for t in result.trades
                ]
            )
            st.dataframe(trades_df, width="stretch", hide_index=True)
