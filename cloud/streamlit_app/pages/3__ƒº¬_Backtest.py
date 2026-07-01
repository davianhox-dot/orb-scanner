"""Backtest page — Phase 1: a single fixed ORB strategy against real
historical minute data, with real metrics, an equity curve, and a trade
list. Strategy builder, optimizer, and walk-forward analysis are later
phases — see cloud/README.md for the roadmap."""
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

from cloud.backtest_engine import ORBParams, run_backtest, save_run
from cloud.config import get_settings
from cloud.db import get_session_factory, init_db

st.set_page_config(page_title="Backtest — ORB Scanner", page_icon="🧪", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("🧪 Backtest — Opening Range Breakout")
st.caption(
    "Phase 1 of the backtesting roadmap: one fixed, well-tested strategy against real "
    "historical minute data. Strategy builder, optimizer, and walk-forward/Monte Carlo "
    "validation come in later phases."
)

if not settings.POLYGON_API_KEY:
    st.warning(
        "`POLYGON_API_KEY` isn't set in your secrets. Backtesting needs real historical "
        "minute data — there's no sample/demo mode for this page (unlike the scanner)."
    )

with st.form("backtest_config"):
    col1, col2 = st.columns([2, 1])
    tickers_input = col1.text_input("Tickers (comma-separated)", value="AAPL")

    date_col1, date_col2 = col2.columns(2)
    start_date = date_col1.date_input("Start date", value=date.today() - timedelta(days=30))
    end_date = date_col2.date_input("End date", value=date.today() - timedelta(days=1))

    p1, p2, p3, p4 = st.columns(4)
    or_minutes = p1.number_input("Opening range (min)", min_value=1, max_value=60, value=5)
    target_r = p2.number_input("Target (R multiple)", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
    risk_pct = p3.number_input("Risk per trade (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)
    initial_capital = p4.number_input("Initial capital ($)", min_value=100, value=10_000, step=1_000)

    submitted = st.form_submit_button("Run Backtest", type="primary")

if submitted:
    tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]

    if not tickers:
        st.warning("Enter at least one ticker.")
    elif start_date >= end_date:
        st.warning("Start date must be before end date.")
    elif not settings.POLYGON_API_KEY:
        st.error("Can't run a backtest without `POLYGON_API_KEY` — add it to your secrets first.")
    else:
        params = ORBParams(
            opening_range_minutes=int(or_minutes),
            target_r_multiple=float(target_r),
            risk_pct=float(risk_pct),
            initial_capital=float(initial_capital),
        )

        try:
            with st.spinner(f"Fetching/caching historical data and running backtest for {', '.join(tickers)}…"):
                with Session() as db:
                    result = run_backtest(db, settings, tickers, start_date, end_date, params)
                    if result.trades:
                        save_run(db, tickers, start_date, end_date, params, result)
        except Exception as exc:  # noqa: BLE001 — surface data/API errors plainly instead of a stack trace
            st.error(f"Backtest failed: {exc}")
            st.stop()

        if not result.trades:
            st.warning(
                "No trades were generated for this combination of tickers/dates/settings. "
                "Try a longer date range, a more liquid/volatile ticker, or a smaller opening range."
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
                        "Entry Time": t.entry_time.strftime("%Y-%m-%d %H:%M"),
                        "Exit Time": t.exit_time.strftime("%Y-%m-%d %H:%M"),
                        "Entry": round(t.entry_price, 2),
                        "Exit": round(t.exit_price, 2),
                        "Stop": round(t.stop_price, 2),
                        "Target": round(t.target_price, 2),
                        "Shares": round(t.shares, 1),
                        "P/L": round(t.pnl, 2),
                        "R": round(t.r_multiple, 2),
                        "Exit Reason": t.exit_reason,
                    }
                    for t in result.trades
                ]
            )
            st.dataframe(trades_df, width="stretch", hide_index=True)
