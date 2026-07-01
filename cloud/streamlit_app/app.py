"""
ORB Scanner — Streamlit dashboard (Home page).

Reads scan results written by the GitHub Actions scheduled job (or by the
"Run Scan Now" button below, which runs the same scan logic directly). No
FastAPI server involved — this connects straight to the same hosted
Postgres database as the scan job via DATABASE_URL.
"""
import sys
from pathlib import Path


def _add_repo_root_to_path() -> None:
    """Streamlit runs this file directly, so Python doesn't automatically
    know where the repo root is. Walk upward until we find cloud/config.py
    and add that directory to sys.path so `import cloud.x` works."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "cloud" / "config.py").exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return
    raise RuntimeError("Could not locate repo root (looked for cloud/config.py in parent directories)")


_add_repo_root_to_path()

import pandas as pd
import streamlit as st
from sqlalchemy import select

from cloud.config import get_settings
from cloud.db import ScanResult, ScanRun, get_session_factory, init_db
from cloud.run_scan import run as run_scan_now

st.set_page_config(page_title="ORB Scanner", page_icon="📈", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


def load_latest_scan():
    Session = _session_factory()
    with Session() as db:
        run = db.execute(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(1)).scalars().first()
        if run is None:
            return None, []
        results = (
            db.execute(
                select(ScanResult)
                .where(ScanResult.scan_run_id == run.id)
                .order_by(ScanResult.score.desc())
            )
            .scalars()
            .all()
        )
        return run, results


st.title("📈 ORB Scanner")
st.caption("Pre-market momentum scanner — ORB, First Pullback, VWAP & Momentum Breakout setups")

header_left, header_right = st.columns([3, 1])
with header_right:
    if st.button("🔄 Run Scan Now", use_container_width=True):
        with st.spinner("Scanning…"):
            run_scan_now(force=True)
        st.rerun()

def _compact(value: float) -> str:
    if not value:
        return "—"
    for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= divisor:
            return f"{value / divisor:.1f}{suffix}"
    return f"{value:.0f}"


run, results = load_latest_scan()

if run is None:
    st.info(
        "No scans yet. Click **Run Scan Now** above to run your first scan. "
        "It'll use bundled sample data (tickers `DEMO1`-`DEMO5`) unless you've set "
        "`POLYGON_API_KEY` in your secrets."
    )
else:
    provider_note = " (sample/demo data)" if run.provider == "polygon" and results and results[0].company.endswith("(Demo)") else ""
    st.caption(
        f"Last run: `{run.scheduled_slot}` · Scanned **{run.candidates_scanned}** · "
        f"Passed **{run.candidates_passed}** · Provider: **{run.provider}**{provider_note}"
    )

    if not results:
        st.warning("No candidates passed the filters on the last scan. That's expected outside pre-market hours.")
    else:
        df = pd.DataFrame(
            [
                {
                    "Ticker": r.ticker,
                    "Company": r.company,
                    "Sector": r.sector,
                    "Price": r.price,
                    "Gap %": r.gap_pct,
                    "PM %": r.premarket_pct,
                    "PM Vol": _compact(r.premarket_volume),
                    "Rel Vol": r.relative_volume,
                    "Float": _compact(r.float_shares),
                    "Mkt Cap": _compact(r.market_cap),
                    "Catalyst": ", ".join(r.catalyst_tags) if r.catalyst_tags else "None",
                    "Score": r.score,
                    "Risk": r.risk,
                    "PM High": r.premarket_high,
                    "PM Low": r.premarket_low,
                    "Support": r.support,
                    "Resistance": r.resistance,
                    "Avg Vol": _compact(r.average_volume),
                    "ATR": r.atr,
                    "Exp Vol %": r.expected_volatility_pct,
                }
                for r in results
            ]
        )

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            height=min(600, 46 + 36 * len(df)),
            column_config={
                "Price": st.column_config.NumberColumn(format="$%.2f"),
                "Gap %": st.column_config.NumberColumn(format="%.1f%%"),
                "PM %": st.column_config.NumberColumn(format="%.1f%%"),
                "Rel Vol": st.column_config.NumberColumn(format="%.1fx"),
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "PM High": st.column_config.NumberColumn(format="$%.2f"),
                "PM Low": st.column_config.NumberColumn(format="$%.2f"),
                "Support": st.column_config.NumberColumn(format="$%.2f"),
                "Resistance": st.column_config.NumberColumn(format="$%.2f"),
                "Exp Vol %": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        st.caption("Click any column header to sort.")

st.divider()
st.caption("Use the sidebar to look up a stock's trading plan or manage your watchlist.")
