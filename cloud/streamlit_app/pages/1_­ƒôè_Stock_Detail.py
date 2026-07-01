"""Stock Detail page — trading plan, catalyst, and score breakdown for one ticker."""
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

import streamlit as st
from sqlalchemy import select

from cloud.config import get_settings
from cloud.db import ScanResult, get_session_factory, init_db
from cloud.trading_plan import build_trading_plan

st.set_page_config(page_title="Stock Detail — ORB Scanner", page_icon="📊", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("📊 Stock Detail")

with Session() as db:
    tickers = [row[0] for row in db.execute(select(ScanResult.ticker).distinct().order_by(ScanResult.ticker)).all()]

if not tickers:
    st.info("No scan data yet — run a scan from the Home page first.")
    st.stop()

ticker = st.selectbox("Ticker", tickers)

with Session() as db:
    result = (
        db.execute(
            select(ScanResult).where(ScanResult.ticker == ticker).order_by(ScanResult.created_at.desc()).limit(1)
        )
        .scalars()
        .first()
    )

if result is None:
    st.warning("No data found for that ticker.")
    st.stop()

risk_color = {"low": "green", "medium": "orange", "high": "red"}.get(result.risk, "gray")

top_left, top_right = st.columns([3, 1])
with top_left:
    st.header(f"{result.ticker} — {result.company}")
    st.caption(result.sector)
with top_right:
    st.metric("Score", f"{result.score:.0f} / 100")
    st.markdown(f":{risk_color}[**{result.risk.upper()} RISK**]")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Price", f"${result.price:.2f}")
m2.metric("Gap %", f"{result.gap_pct:+.1f}%")
m3.metric("Relative Volume", f"{result.relative_volume:.1f}x")
m4.metric("Premarket Volume", f"{result.premarket_volume:,}")

st.divider()

plan_col, catalyst_col = st.columns([2, 1])

with plan_col:
    st.subheader("Trading Plan")
    st.caption("Derived from the premarket range — a starting point for your own plan, not a recommendation.")
    plan = build_trading_plan(result.price, result.premarket_high, result.premarket_low)
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("ORB Entry", f"${plan['orb_entry']:.2f}")
    p2.metric("Pullback Entry", f"${plan['pullback_entry']:.2f}")
    p3.metric("Stop", f"${plan['stop']:.2f}")
    p4.metric("Target", f"${plan['target']:.2f}")
    st.write(f"**Risk/Reward:** {plan['risk_reward_ratio']:.2f}R")

    st.subheader("Key Stats")
    s1, s2, s3 = st.columns(3)
    s1.write(f"**Float:** {result.float_shares:,.0f}")
    s1.write(f"**Market Cap:** ${result.market_cap:,.0f}")
    s1.write(f"**Average Volume:** {result.average_volume:,}")
    s2.write(f"**ATR:** {result.atr:.2f}")
    s2.write(f"**Expected Volatility:** {result.expected_volatility_pct:.1f}%")
    s2.write(f"**Support:** ${result.support:.2f}")
    s3.write(f"**Resistance:** ${result.resistance:.2f}")
    s3.write(f"**Prev. Day High:** ${result.previous_day_high:.2f}")
    s3.write(f"**Prev. Day Low:** ${result.previous_day_low:.2f}")

with catalyst_col:
    st.subheader("Catalyst")
    if result.has_catalyst:
        st.write(" · ".join(f"`{tag}`" for tag in result.catalyst_tags))
        if result.news_headline:
            if result.news_url:
                st.markdown(f"[{result.news_headline}]({result.news_url})")
            else:
                st.write(result.news_headline)
    else:
        st.write("No catalyst detected in recent news.")

st.divider()
st.subheader("Score Breakdown")
labels = {
    "gap": "Gap %", "float": "Float", "relative_volume": "Relative Volume",
    "premarket_volume": "Premarket Volume", "news_quality": "News Quality", "atr": "ATR",
    "average_volume": "Average Volume", "spread": "Spread", "previous_resistance": "Prev. Resistance",
    "historical_volatility": "Historical Volatility", "recent_halts": "Recent Halts",
}
for key, value in result.score_breakdown.items():
    st.caption(f"{labels.get(key, key)} — {value:.0f}")
    st.progress(min(1.0, max(0.0, value / 100)))
