"""Strategy Scanner — scans a universe of liquid US stocks against any
preset or saved strategy and lists every ticker with a FRESH signal on the
most recent trading day, complete with entry / stop / target. Any hit can
be backtested over the past years with one click, right on this page."""
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

import httpx
import pandas as pd
import streamlit as st
from sqlalchemy import select

from cloud.config import get_settings
from cloud.db import SavedStrategy, get_session_factory, init_db
from cloud.strategy_engine import run_strategy_backtest
from cloud.strategy_presets import PRESET_NAMES, config_from_dict, get_preset
from cloud.strategy_scanner import MAX_UNIVERSE, build_universe, fetch_grouped_daily, fetch_history, scan_for_signals

st.set_page_config(page_title="Strategy Scanner — ORB Scanner", page_icon="📡", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("📡 Strategy Scanner — Swing Signals Across the US Market")
st.caption(
    "Scans the most liquid US stocks in your price band against a strategy and lists every "
    "ticker whose signal fired on the most recent trading day — with entry, stop, and target "
    "from the strategy's own exit rules. Pick a hit below the table to backtest it instantly."
)

# --- 1 · Strategy ---
st.subheader("1 · Strategy")
source = st.radio("Source", ["Preset", "Saved strategy"], horizontal=True)

scan_config = None
if source == "Preset":
    preset_choice = st.selectbox("Preset", PRESET_NAMES)
    scan_config = get_preset(preset_choice)
else:
    with Session() as db:
        saved = db.execute(select(SavedStrategy).order_by(SavedStrategy.name)).scalars().all()
    if not saved:
        st.warning("No saved strategies yet — build one in the Strategy Builder first, or use a preset.")
        st.stop()
    saved_names = [s.name for s in saved]
    saved_choice = st.selectbox("Saved strategy", saved_names)
    row = next(s for s in saved if s.name == saved_choice)
    scan_config = config_from_dict(row.config)

# --- 2 · Universe ---
st.subheader("2 · Universe")
u1, u2, u3, u4 = st.columns(4)
min_price = u1.number_input("Min price ($)", min_value=0.01, value=0.10, step=0.10, format="%.2f")
max_price = u2.number_input("Max price ($)", min_value=1.0, value=10000.0, step=100.0)
top_n = u3.number_input(f"Universe size (top-N by $ volume, max {MAX_UNIVERSE})", min_value=10, max_value=MAX_UNIVERSE, value=150, step=10)
history_bars = u4.number_input("History (trading days)", min_value=40, max_value=250, value=100, step=10)

st.caption(
    "The scanner ranks all US stocks in your price band by dollar volume and scans the top N. "
    "That's a deliberate choice, not a shortcut: thinly-traded names are exactly where "
    "backtest fills are least realistic. Each additional history day costs one API call, so "
    "~100 days ≈ ~100 calls ≈ 1-2 minutes. **Important:** the history must cover your "
    "strategy's longest indicator — e.g. an EMA200 trend filter needs 220+ trading days of "
    "history, otherwise no signal can ever fire (the EMA Pullback preset uses EMA200)."
)

if not settings.POLYGON_API_KEY:
    st.warning("`POLYGON_API_KEY` isn't set in your secrets — the scanner needs live market data and has no demo mode.")

if st.button("📡 Scan Market", type="primary"):
    if not settings.POLYGON_API_KEY:
        st.error("Can't scan without `POLYGON_API_KEY` — add it to your secrets first.")
    elif not scan_config.entry_conditions:
        st.warning("This strategy has no entry conditions — nothing to scan for.")
    else:
        try:
            with st.status("Scanning…", expanded=True) as status:
                st.write("Fetching the latest trading day (all US tickers, 1 call)…")
                latest_rows = []
                probe_day = date.today()
                with httpx.Client(timeout=30.0) as client:
                    for _ in range(7):
                        latest_rows = fetch_grouped_daily(client, settings.POLYGON_API_KEY, probe_day)
                        if latest_rows:
                            break
                        probe_day -= timedelta(days=1)
                if not latest_rows:
                    status.update(label="Scan failed", state="error")
                    st.error("Couldn't fetch grouped market data for any recent day — check your API key/plan.")
                    st.stop()

                universe = build_universe(latest_rows, float(min_price), float(max_price), int(top_n))
                st.write(f"Universe: **{len(universe)}** tickers (top by dollar volume, ${min_price}-{max_price})")
                if not universe:
                    status.update(label="Scan finished — empty universe", state="complete")
                    st.warning("No tickers matched the price/liquidity filters.")
                    st.stop()

                hist_progress = st.progress(0.0, text="Fetching history…")
                bars_by_ticker, latest_day = fetch_history(
                    settings.POLYGON_API_KEY, universe, n_bars=int(history_bars), end_day=probe_day,
                    progress_callback=lambda done, total: hist_progress.progress(done / total, text=f"History day {done}/{total}"),
                )

                scan_progress = st.progress(0.0, text="Evaluating strategy…")
                hits = scan_for_signals(
                    bars_by_ticker, scan_config,
                    progress_callback=lambda done, total: scan_progress.progress(done / total, text=f"Ticker {done}/{total}"),
                )
                status.update(label=f"Scan complete — {len(hits)} signal(s) on {latest_day}", state="complete", expanded=False)

            st.session_state["scan_hits"] = [h.__dict__ for h in hits]
            st.session_state["scan_config_name"] = scan_config.name
            st.session_state["scan_latest_day"] = str(latest_day)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Scan failed: {exc}")
            st.stop()

# --- Results (persisted in session so backtest clicks don't wipe them) ---
if st.session_state.get("scan_hits") is not None:
    hits = st.session_state["scan_hits"]
    st.divider()
    st.subheader(f"Signals — {st.session_state.get('scan_config_name', '')} · {st.session_state.get('scan_latest_day', '')}")

    if not hits:
        st.info(
            "No ticker in the universe has a fresh signal on the latest trading day. That's a "
            "normal outcome — genuine setups don't fire every day on every strategy. Try a "
            "larger universe, a different strategy, or simply check back tomorrow."
        )
    else:
        st.caption(
            "**How to read the trade plan:** the signal fired on the latest close. A real entry "
            "would be the *next* session — treat **Entry** as 'enter above this level', with the "
            "stop and target from the strategy's exit rules. Volume is the signal day's volume."
        )
        df = pd.DataFrame(hits)[["ticker", "signal_date", "close", "volume", "entry", "stop", "target", "risk_reward"]]
        df.columns = ["Ticker", "Signal Date", "Close", "Volume", "Entry", "Stop", "Target", "R:R"]
        st.dataframe(
            df, width="stretch", hide_index=True,
            column_config={
                "Close": st.column_config.NumberColumn(format="$%.2f"),
                "Entry": st.column_config.NumberColumn(format="$%.2f"),
                "Stop": st.column_config.NumberColumn(format="$%.2f"),
                "Target": st.column_config.NumberColumn(format="$%.2f"),
                "R:R": st.column_config.NumberColumn(format="%.2f"),
            },
        )

        # --- One-click backtest of any hit ---
        st.subheader("Backtest a signal")
        bt1, bt2, bt3 = st.columns([2, 1, 1])
        chosen_ticker = bt1.selectbox("Ticker from the results", [h["ticker"] for h in hits])
        years_back = bt2.selectbox("Period", [1, 2, 3], index=1, format_func=lambda y: f"{y} year(s)")
        run_bt = bt3.button("🧪 Backtest this ticker", type="primary")

        if run_bt:
            bt_start = date.today() - timedelta(days=365 * int(years_back))
            bt_end = date.today() - timedelta(days=1)
            # Rebuild the same config that was scanned with
            if source == "Preset":
                bt_config = get_preset(preset_choice)
            else:
                bt_config = config_from_dict(row.config)
            try:
                with st.spinner(f"Backtesting {chosen_ticker} over the last {years_back} year(s)…"):
                    with Session() as db:
                        result = run_strategy_backtest(db, settings, [chosen_ticker], bt_start, bt_end, bt_config)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Backtest failed: {exc}")
                st.stop()

            if not result.trades:
                st.warning(
                    f"{chosen_ticker} produced no historical trades with this strategy over the "
                    f"last {years_back} year(s). Today's signal may be its first in a long time — "
                    "which also means there's no track record here to lean on."
                )
            else:
                m = result.metrics
                st.markdown(f"**{chosen_ticker} — {bt_config.name}, last {years_back} year(s)**")
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Trades", m["total_trades"])
                c2.metric("Win Rate", f"{m['win_rate_pct']:.1f}%")
                c3.metric("Profit Factor", f"{m['profit_factor']:.2f}" if m["profit_factor"] is not None else "∞")
                c4.metric("Expectancy", f"${m['expectancy']:.2f}")
                c5.metric("Max Drawdown", f"{m['max_drawdown_pct']:.1f}%")

                if m["total_trades"] < 10:
                    st.caption(
                        f"⚠️ Only {m['total_trades']} historical trade(s) — too few to judge this "
                        "strategy on this ticker. Treat the numbers as anecdote, not evidence."
                    )

                equity_df = pd.DataFrame(result.equity_curve, columns=["time", "equity"]).set_index("time")
                st.line_chart(equity_df)

                trades_df = pd.DataFrame(
                    [
                        {
                            "Entry Date": t.entry_time.strftime("%Y-%m-%d"),
                            "Exit Date": t.exit_time.strftime("%Y-%m-%d"),
                            "Entry": round(t.entry_price, 2),
                            "Exit": round(t.exit_price, 2),
                            "Stop": round(t.stop_price, 2),
                            "Target": round(t.target_price, 2),
                            "P/L": round(t.pnl, 2),
                            "R": round(t.r_multiple, 2),
                            "Days Held": (t.exit_time - t.entry_time).days,
                            "Exit Reason": t.exit_reason,
                        }
                        for t in result.trades
                    ]
                )
                st.dataframe(trades_df, width="stretch", hide_index=True)
