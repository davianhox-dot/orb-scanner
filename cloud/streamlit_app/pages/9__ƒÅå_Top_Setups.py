"""🏆 Top Setups — the everything-scanner: every strategy (presets + your
saved ones) against the liquid US universe, every hit auto-backtested on
its own ticker over the past years, quality-filtered (min. 5 historical
trades AND positive expectancy), composite-scored, deduplicated, and
reduced to the top 1-3 stocks with entry / SL / TP and the historical
track record right next to them. Also displays the latest nightly
auto-scan saved by GitHub Actions."""
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
from cloud.db import SavedStrategy, TopSetupRun, get_session_factory, init_db
from cloud.strategy_presets import PRESET_NAMES, config_from_dict, get_preset
from cloud.strategy_scanner import MAX_UNIVERSE, build_universe, fetch_grouped_daily, fetch_history
from cloud.top_setups import LOW_SAMPLE_THRESHOLD, find_top_setups

st.set_page_config(page_title="Top Setups — ORB Scanner", page_icon="🏆", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("🏆 Top Setups — die 1-3 besten Swing-Kandidaten")
st.caption(
    "Alle Strategien × alle liquiden US-Aktien, jeder Treffer automatisch auf seinem eigenen "
    "Ticker rückgetestet, qualitätsgefiltert und zu den besten 1-3 verdichtet."
)
st.info(
    "**Ehrlicher Hinweis, bevor du den Zahlen vertraust:** \"hat auf dieser Aktie historisch "
    "funktioniert\" ist selbst ein Auswahleffekt — wer 150 Aktien nach der schönsten "
    "Vergangenheit durchsiebt, findet auch Zufallstreffer. Der Mindest-Trade-Filter mildert "
    "das, heilt es aber nicht. Das hier ist eine vorgefilterte Shortlist für **dein** Urteil, "
    "keine Kaufliste."
)


def _render_setups(setups: list[dict], scan_day: str) -> None:
    if not setups:
        st.info(
            f"Kein qualifizierendes Setup am {scan_day}. Das ist ein normales, ehrliches "
            "Ergebnis — lieber kein Trade als ein schlechter."
        )
        return
    for i, s in enumerate(setups, start=1):
        with st.container(border=True):
            head, score_col = st.columns([3, 1])
            head.markdown(f"### {i}. {s['ticker']} · {s['strategy_name']}")
            score_col.metric("Score", f"{s['score']:.0f} / 100")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Entry (über)", f"${s['entry']:.2f}")
            c2.metric("Stop Loss", f"${s['stop']:.2f}")
            c3.metric("Take Profit", f"${s['target']:.2f}")
            c4.metric("R:R", f"{s['risk_reward']:.1f}")

            pf = s.get("profit_factor")
            st.caption(
                f"Historie ({s['total_trades']} Trades auf diesem Ticker): "
                f"Win Rate {s['win_rate_pct']:.0f}% · Profit Factor {'∞' if pf is None else f'{pf:.2f}'} · "
                f"Ø {s['avg_r_multiple']:.2f}R · Expectancy ${s['expectancy']:.2f} · "
                f"Max DD {s['max_drawdown_pct']:.1f}% · Signal vom {s['signal_date']}"
            )
            if s.get("low_sample"):
                st.warning(
                    f"⚠️ Nur {s['total_trades']} historische Trades (unter {LOW_SAMPLE_THRESHOLD}) — "
                    "die Statistik ist dünn. Eher Anekdote als Beweis."
                )


# --- Latest nightly run ---
with Session() as db:
    last_run = db.execute(
        select(TopSetupRun).order_by(TopSetupRun.created_at.desc()).limit(1)
    ).scalars().first()

if last_run is not None:
    st.subheader(f"Letzter Scan — {last_run.scan_day} ({'automatisch' if last_run.source == 'nightly' else 'manuell'})")
    st.caption(
        f"{last_run.hits_scanned} Signale geprüft · {last_run.candidates_count} haben den "
        f"Qualitätsfilter bestanden · Top {len(last_run.top)} angezeigt"
    )
    _render_setups(last_run.top, last_run.scan_day)
    st.divider()
else:
    st.caption(
        "Noch kein gespeicherter Lauf. Der nächtliche Auto-Scan (GitHub Actions) legt hier "
        "jeden Abend nach US-Börsenschluss sein Ergebnis ab — oder starte unten manuell."
    )
    st.divider()

# --- Manual scan ---
st.subheader("Manuell scannen")

u1, u2, u3, u4 = st.columns(4)
min_price = u1.number_input("Min price ($)", min_value=0.5, value=2.0, step=0.5)
max_price = u2.number_input("Max price ($)", min_value=1.0, value=100.0, step=5.0)
top_n = u3.number_input(f"Universe (top-N, max {MAX_UNIVERSE})", min_value=10, max_value=MAX_UNIVERSE, value=150, step=10)
history_bars = u4.number_input("History (trading days)", min_value=40, max_value=250, value=250, step=10)

s1, s2, s3 = st.columns(3)
backtest_years = s1.selectbox("Auto-Backtest über", [1, 2, 3], index=1, format_func=lambda y: f"{y} Jahr(e)")
min_trades = s2.number_input("Min. historische Trades", min_value=1, max_value=50, value=5)
top_k = s3.selectbox("Wie viele Top-Setups", [1, 2, 3], index=2)

with Session() as db:
    saved_rows = db.execute(select(SavedStrategy).order_by(SavedStrategy.name)).scalars().all()
all_strategy_names = PRESET_NAMES + [s.name for s in saved_rows]
chosen = st.multiselect("Strategien", all_strategy_names, default=all_strategy_names)

st.caption(
    "History steht standardmäßig auf 250 Tagen, damit auch EMA200-Strategien (EMA Pullback) "
    "bewertbar sind. Der Scan dauert ein paar Minuten: ~1 API-Call pro History-Tag plus ein "
    "Auto-Backtest pro Treffer."
)

if not settings.POLYGON_API_KEY:
    st.warning("`POLYGON_API_KEY` fehlt in den Secrets — dieser Scan braucht Live-Marktdaten.")

if st.button("🏆 Top Setups finden", type="primary"):
    if not settings.POLYGON_API_KEY:
        st.error("Ohne `POLYGON_API_KEY` geht es nicht — zuerst in den Secrets hinterlegen.")
    elif not chosen:
        st.warning("Mindestens eine Strategie auswählen.")
    else:
        strategies = []
        for name in chosen:
            if name in PRESET_NAMES:
                strategies.append((name, get_preset(name)))
            else:
                row = next(s for s in saved_rows if s.name == name)
                strategies.append((name, config_from_dict(row.config)))

        try:
            with st.status("Scanne…", expanded=True) as status:
                st.write("Letzten Handelstag laden (gesamter US-Markt, 1 Call)…")
                latest_rows, probe_day = [], date.today()
                with httpx.Client(timeout=30.0) as client:
                    for _ in range(7):
                        latest_rows = fetch_grouped_daily(client, settings.POLYGON_API_KEY, probe_day)
                        if latest_rows:
                            break
                        probe_day -= timedelta(days=1)
                if not latest_rows:
                    status.update(label="Scan fehlgeschlagen", state="error")
                    st.error("Keine Marktdaten für die letzten Tage abrufbar — API-Key/Plan prüfen.")
                    st.stop()

                universe = build_universe(latest_rows, float(min_price), float(max_price), int(top_n))
                st.write(f"Universum: **{len(universe)}** Ticker")
                if not universe:
                    status.update(label="Scan beendet — leeres Universum", state="complete")
                    st.warning("Keine Ticker im Preis-/Liquiditätsfilter.")
                    st.stop()

                hist_progress = st.progress(0.0, text="Historie laden…")
                bars_by_ticker, latest_day = fetch_history(
                    settings.POLYGON_API_KEY, universe, n_bars=int(history_bars), end_day=probe_day,
                    progress_callback=lambda done, total: hist_progress.progress(done / total, text=f"Tag {done}/{total}"),
                )

                progress_line = st.empty()
                with Session() as db:
                    result = find_top_setups(
                        db, settings, strategies, bars_by_ticker,
                        backtest_years=int(backtest_years), min_trades=int(min_trades), top_k=int(top_k),
                        progress_callback=lambda msg: progress_line.write(msg),
                    )
                    db.add(
                        TopSetupRun(
                            scan_day=str(latest_day), source="manual",
                            settings_used={
                                "min_price": float(min_price), "max_price": float(max_price),
                                "universe": int(top_n), "history_bars": int(history_bars),
                                "backtest_years": int(backtest_years), "min_trades": int(min_trades),
                                "top_k": int(top_k), "strategies": chosen,
                            },
                            top=[s.__dict__ for s in result.top],
                            candidates_count=len(result.all_candidates),
                            hits_scanned=result.hits_scanned,
                        )
                    )
                    db.commit()
                status.update(
                    label=f"Fertig — {result.hits_scanned} Signale, {len(result.all_candidates)} qualifiziert",
                    state="complete", expanded=False,
                )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Scan fehlgeschlagen: {exc}")
            st.stop()

        st.subheader(f"Ergebnis — {latest_day}")
        _render_setups([s.__dict__ for s in result.top], str(latest_day))

        if result.all_candidates:
            with st.expander(f"Alle {len(result.all_candidates)} qualifizierten Kandidaten"):
                df = pd.DataFrame(
                    [
                        {
                            "Ticker": c.ticker, "Strategie": c.strategy_name, "Score": c.score,
                            "Entry": c.entry, "SL": c.stop, "TP": c.target, "R:R": c.risk_reward,
                            "Trades": c.total_trades, "WR %": c.win_rate_pct,
                            "PF": c.profit_factor, "Ø R": c.avg_r_multiple,
                        }
                        for c in result.all_candidates
                    ]
                )
                st.dataframe(df, width="stretch", hide_index=True)
