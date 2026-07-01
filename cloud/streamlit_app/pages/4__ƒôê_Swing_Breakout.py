"""Swing Trading Backtest page — Consolidation Breakout strategy, using
daily bars. Same design pattern as the ORB day-trading backtest page, just
a different strategy engine and parameter set."""
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

from cloud.config import get_settings
from cloud.db import get_session_factory, init_db
from cloud.swing_breakout_engine import SwingBreakoutParams, run_swing_backtest, save_run

st.set_page_config(page_title="Swing Breakout — ORB Scanner", page_icon="📈", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("📈 Swing Backtest — Consolidation Breakout")
st.caption(
    "A tight multi-day trading range, then a close above the range high — entered the next "
    "day's open, held for days to weeks. Uses daily bars, not minute bars, so it's cheap to "
    "run over long date ranges."
)

if not settings.POLYGON_API_KEY:
    st.warning(
        "`POLYGON_API_KEY` isn't set in your secrets. Backtesting needs real historical "
        "price data — there's no sample/demo mode for this page."
    )

with st.form("swing_backtest_config"):
    col1, col2 = st.columns([2, 1])
    tickers_input = col1.text_input("Tickers (comma-separated)", value="AAPL")

    date_col1, date_col2 = col2.columns(2)
    start_date = date_col1.date_input("Start date", value=date.today() - timedelta(days=365))
    end_date = date_col2.date_input("End date", value=date.today() - timedelta(days=1))

    p1, p2, p3 = st.columns(3)
    consolidation_days = p1.number_input("Consolidation length (days)", min_value=5, max_value=60, value=15)
    max_range_pct = p2.number_input("Max range width (%)", min_value=2.0, max_value=50.0, value=15.0, step=1.0)
    max_holding_days = p3.number_input("Max holding period (days)", min_value=1, max_value=120, value=20)

    p4, p5, p6 = st.columns(3)
    target_r = p4.number_input("Target (R multiple)", min_value=0.5, max_value=10.0, value=3.0, step=0.5)
    risk_pct = p5.number_input("Risk per trade (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)
    initial_capital = p6.number_input("Initial capital ($)", min_value=100, value=10_000, step=1_000)

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
        params = SwingBreakoutParams(
            consolidation_days=int(consolidation_days),
            max_range_pct=float(max_range_pct),
            target_r_multiple=float(target_r),
            max_holding_days=int(max_holding_days),
            risk_pct=float(risk_pct),
            initial_capital=float(initial_capital),
        )

        try:
            with st.spinner(f"Fetching/caching daily data and running backtest for {', '.join(tickers)}…"):
                with Session() as db:
                    result = run_swing_backtest(db, settings, tickers, start_date, end_date, params)
                    if result.trades:
                        save_run(db, tickers, start_date, end_date, params, result)
        except Exception as exc:  # noqa: BLE001 — surface data/API errors plainly instead of a stack trace
            st.error(f"Backtest failed: {exc}")
            st.stop()

        if not result.trades:
            st.warning(
                "No trades were generated for this combination of tickers/dates/settings. "
                "Try a longer date range, a wider max range %, or a different ticker — "
                "genuine tight consolidations followed by a clean breakout aren't common "
                "on every ticker/period."
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
