"""💼 Positionen — trage deine ECHTEN Käufe ein (Entry, Stop, Ziel, optional
die Strategie dahinter), und der nächtliche Monitor prüft jede offene
Position: Stop berührt? Ziel erreicht? Indikator-Exit ausgelöst?
Haltedauer abgelaufen? Bei Handlungsbedarf kommt ein Alert. Hier kannst du
den Check auch sofort manuell laufen lassen und Positionen schließen."""
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

from datetime import date

import pandas as pd
import streamlit as st
from sqlalchemy import select

from cloud.config import get_settings
from cloud.db import TrackedPosition, get_session_factory, init_db
from cloud.position_monitor import STATUS_EMOJI, check_open_positions
from cloud.strategy_presets import PRESET_NAMES, config_from_dict, get_preset
from cloud.db import SavedStrategy

st.set_page_config(page_title="Positionen — ORB Scanner", page_icon="💼", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("💼 Meine Positionen")
st.caption(
    "Der nächtliche Scan prüft jede offene Position und meldet sich, wenn etwas zu tun ist — "
    "insbesondere beim Indikator-Exit, den kein Broker als Order abbilden kann."
)
st.info(
    "**Wichtig:** Positionen werden **nie automatisch geschlossen** — das System kennt deine "
    "echten Ausführungen nicht. Es meldet, was die Regeln sagen; verkaufen und hier auf "
    "\"Schließen\" klicken musst du selbst. Stop- und Ziel-Order gehören trotzdem zusätzlich "
    "zum Broker (Bracket/OCO) — der nächtliche Check ist das Sicherheitsnetz für alles, was "
    "eine Broker-Order nicht abdecken kann, kein Ersatz dafür."
)

# --- Add a position ---
st.subheader("Position hinzufügen")

with Session() as _db:
    _saved_names = [s.name for s in _db.execute(select(SavedStrategy).order_by(SavedStrategy.name)).scalars().all()]
strategy_choice = st.selectbox(
    "Strategie dahinter (übernimmt Indikator-Exit & Haltedauer automatisch)",
    ["— Keine / manuell —"] + PRESET_NAMES + _saved_names,
)

prefill_ind_exit, prefill_ind_type, prefill_ind_period, prefill_max_hold = False, "close_below_ema", 10, 0
if strategy_choice != "— Keine / manuell —":
    if strategy_choice in PRESET_NAMES:
        _cfg = get_preset(strategy_choice)
    else:
        with Session() as db:
            _row = db.execute(select(SavedStrategy).where(SavedStrategy.name == strategy_choice)).scalars().first()
        _cfg = config_from_dict(_row.config) if _row else None
    if _cfg:
        prefill_ind_exit = _cfg.exit_rules.indicator_exit
        prefill_ind_type = _cfg.exit_rules.indicator_exit_type
        prefill_ind_period = int(_cfg.exit_rules.indicator_exit_period)
        prefill_max_hold = int(_cfg.exit_rules.max_holding_days)
        st.caption(
            f"Aus '{strategy_choice}' übernommen: "
            + (f"Indikator-Exit ({prefill_ind_type}, EMA{prefill_ind_period}), " if prefill_ind_exit else "kein Indikator-Exit, ")
            + f"max. Haltedauer {prefill_max_hold} Tage."
        )

with st.form("add_position"):
    c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1])
    ticker = c1.text_input("Ticker", placeholder="z.B. AAPL")
    entry_price = c2.number_input("Entry ($)", min_value=0.01, value=10.00, step=0.01, format="%.2f")
    stop_price = c3.number_input("Stop Loss ($)", min_value=0.01, value=9.00, step=0.01, format="%.2f")
    target_price = c4.number_input("Take Profit ($)", min_value=0.01, value=13.00, step=0.01, format="%.2f")
    shares = c5.number_input("Stückzahl (optional)", min_value=0.0, value=0.0, step=1.0)

    d1, d2, d3, d4 = st.columns(4)
    entry_date = d1.date_input("Einstiegsdatum", value=date.today())
    ind_exit = d2.checkbox("Indikator-Exit überwachen", value=prefill_ind_exit)
    ind_type = d3.selectbox(
        "Exit-Typ", ["close_below_ema", "close_above_ema"],
        index=0 if prefill_ind_type == "close_below_ema" else 1,
        format_func=lambda v: "Schluss UNTER EMA (Trend-Exit)" if v == "close_below_ema" else "Schluss ÜBER EMA (Gewinnmitnahme)",
    )
    ind_period = d4.number_input("Exit-EMA Periode", min_value=2, max_value=300, value=prefill_ind_period)
    max_hold = st.number_input("Max. Haltedauer in Tagen (0 = kein Limit)", min_value=0, max_value=365, value=prefill_max_hold)

    submitted = st.form_submit_button("💼 Position anlegen", type="primary")
    if submitted:
        t = ticker.strip().upper()
        if not t:
            st.warning("Ticker fehlt.")
        elif stop_price >= entry_price:
            st.warning("Der Stop muss unter dem Entry liegen (Long-Position).")
        elif target_price <= entry_price:
            st.warning("Das Ziel muss über dem Entry liegen (Long-Position).")
        else:
            with Session() as db:
                db.add(TrackedPosition(
                    ticker=t, entry_price=float(entry_price), stop_price=float(stop_price),
                    target_price=float(target_price), entry_date=entry_date.isoformat(),
                    shares=float(shares), strategy_name=strategy_choice if strategy_choice != "— Keine / manuell —" else "",
                    indicator_exit=bool(ind_exit), indicator_exit_type=ind_type,
                    indicator_exit_period=int(ind_period), max_holding_days=int(max_hold),
                ))
                db.commit()
            st.success(f"{t} angelegt — wird ab sofort jede Nacht geprüft.")
            st.rerun()

st.divider()

# --- Open positions ---
with Session() as db:
    open_positions = db.execute(
        select(TrackedPosition).where(TrackedPosition.status == "open").order_by(TrackedPosition.created_at)
    ).scalars().all()

st.subheader(f"Offene Positionen ({len(open_positions)})")

if not open_positions:
    st.caption("Keine offenen Positionen. Leg oben deine echten Käufe an.")
else:
    check_col, _sp = st.columns([1, 3])
    if check_col.button("🔍 Jetzt alle prüfen", type="primary"):
        if not settings.POLYGON_API_KEY:
            st.error("`POLYGON_API_KEY` fehlt — der Check braucht aktuelle Kursdaten.")
        else:
            with st.spinner("Prüfe Positionen (1 Datenabruf pro Ticker)…"):
                with Session() as db:
                    checks = check_open_positions(db, settings)
            for c in checks:
                if c.action_needed:
                    st.error(c.message)
                elif c.status == "hold":
                    st.success(c.message)
                else:
                    st.warning(c.message)

    for pos in open_positions:
        with st.container(border=True):
            h1, h2, h3, h4, h5, h6 = st.columns([1.4, 1, 1, 1, 1.2, 1])
            h1.markdown(f"### {pos.ticker}")
            if pos.strategy_name:
                h1.caption(pos.strategy_name)
            h2.metric("Entry", f"${pos.entry_price:.2f}")
            h3.metric("Stop", f"${pos.stop_price:.2f}")
            h4.metric("Ziel", f"${pos.target_price:.2f}")
            h5.caption(
                f"Seit {pos.entry_date}"
                + (f" · {pos.shares:.0f} Stück" if pos.shares else "")
                + (f" · Exit: EMA{pos.indicator_exit_period}" if pos.indicator_exit else " · kein Indikator-Exit")
                + (f" · max {pos.max_holding_days}T" if pos.max_holding_days else "")
            )
            with h6:
                if st.button("Schließen", key=f"close_{pos.id}"):
                    with Session() as db:
                        row = db.get(TrackedPosition, pos.id)
                        row.status = "closed"
                        db.commit()
                    st.rerun()

            if pos.last_signal:
                checked = pos.last_checked_at.strftime("%Y-%m-%d %H:%M UTC") if pos.last_checked_at else "—"
                if "✅" in pos.last_signal:
                    st.success(f"{pos.last_signal}  \n*(geprüft: {checked})*")
                elif "⚪" in pos.last_signal:
                    st.warning(f"{pos.last_signal}  \n*(geprüft: {checked})*")
                else:
                    st.error(f"{pos.last_signal}  \n*(geprüft: {checked})*")
            else:
                st.caption("Noch nicht geprüft — läuft heute Nacht automatisch, oder oben manuell starten.")

# --- Closed positions ---
with Session() as db:
    closed = db.execute(
        select(TrackedPosition).where(TrackedPosition.status == "closed").order_by(TrackedPosition.created_at.desc()).limit(20)
    ).scalars().all()
if closed:
    with st.expander(f"Geschlossene Positionen ({len(closed)} letzte)"):
        df = pd.DataFrame(
            [{"Ticker": p.ticker, "Entry": p.entry_price, "Stop": p.stop_price, "Ziel": p.target_price,
              "Seit": p.entry_date, "Strategie": p.strategy_name, "Letztes Signal": (p.last_signal or "")[:80]}
             for p in closed]
        )
        st.dataframe(df, width="stretch", hide_index=True)
