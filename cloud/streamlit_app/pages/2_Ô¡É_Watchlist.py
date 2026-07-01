"""Watchlist page — add, view, and remove tickers."""
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
from sqlalchemy import delete, select

from cloud.config import get_settings
from cloud.db import WatchlistItem, get_session_factory, init_db

st.set_page_config(page_title="Watchlist — ORB Scanner", page_icon="⭐", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("⭐ Watchlist")

with st.form("add_ticker", clear_on_submit=True):
    col1, col2, col3 = st.columns([1, 2, 1])
    ticker_input = col1.text_input("Ticker").strip().upper()
    note_input = col2.text_input("Note (optional)")
    submitted = col3.form_submit_button("Add", width='stretch')

    if submitted:
        if not ticker_input:
            st.warning("Enter a ticker symbol first.")
        else:
            with Session() as db:
                existing = db.execute(select(WatchlistItem).where(WatchlistItem.ticker == ticker_input)).scalars().first()
                if existing:
                    st.warning(f"{ticker_input} is already on your watchlist.")
                else:
                    db.add(WatchlistItem(ticker=ticker_input, note=note_input))
                    db.commit()
                    st.success(f"Added {ticker_input}.")
                    st.rerun()

with Session() as db:
    items = db.execute(select(WatchlistItem).order_by(WatchlistItem.added_at.desc())).scalars().all()

st.divider()

if not items:
    st.info("Your watchlist is empty. Add a ticker above.")
else:
    for item in items:
        col1, col2, col3 = st.columns([1, 3, 1])
        col1.markdown(f"**{item.ticker}**")
        col2.write(item.note or "—")
        if col3.button("Remove", key=f"remove_{item.id}"):
            with Session() as db:
                db.execute(delete(WatchlistItem).where(WatchlistItem.id == item.id))
                db.commit()
            st.rerun()
