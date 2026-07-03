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

import altair as alt
import httpx
import pandas as pd
import streamlit as st
from sqlalchemy import select

from cloud.config import get_settings
from cloud.db import SavedStrategy, TopSetupRun, get_session_factory, init_db
from cloud.historical_data import get_bars
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
    "keine Kaufliste. Und zur Frage \"würden Hedgefonds das kaufen?\": Das kann **niemand** "
    "messen — institutionelle Positionen werden erst Monate später öffentlich. Die A/B/C-Note "
    "unten prüft stattdessen die messbaren Kriterien, nach denen institutionelles Geld "
    "nachweislich auswählt: Liquidität, relative Stärke vs. Markt, Akkumulation, realistische "
    "Ziele, saubere Stops."
)

_GRADE_STYLE = {"A": ("🟢", "green"), "B": ("🟡", "orange"), "C": ("🔴", "red")}
_REGIME_LABELS = {
    "green": "🟢 Markt-Ampel: Grün — Gesamtmarkt im Aufwärtstrend, Rückenwind für Long-Setups",
    "yellow": "🟡 Markt-Ampel: Gelb — Gesamtmarkt im Übergang, Scores um 10 Punkte reduziert",
    "red": "🔴 Markt-Ampel: Rot — Gesamtmarkt im Abwärtstrend, Scores um 25 Punkte reduziert. Die meisten Ausbrüche scheitern in diesem Umfeld.",
    "unknown": "⚪ Markt-Ampel: unbekannt (keine SPY-Daten) — keine Markt-Anpassung angewendet",
}


def _render_regime(regime: dict | None) -> None:
    if not regime:
        return
    status = regime.get("status", "unknown")
    text = _REGIME_LABELS.get(status, _REGIME_LABELS["unknown"])
    if status == "red":
        st.error(text)
    elif status == "yellow":
        st.warning(text)
    else:
        st.success(text) if status == "green" else st.caption(text)


_REASON_COLORS = alt.Scale(
    domain=["target", "stop", "time_exit", "trailing_stop"],
    range=["#2e7d32", "#c62828", "#757575", "#1565c0"],
)


def _render_history_chart(ticker: str, entry_level: float, history_trades: list[dict]) -> None:
    """Price chart from the cached daily bars with every historical
    backtest signal marked: green triangles = entries, exit dots colored by
    reason (green target / red stop / gray time exit / blue trailing stop),
    dashed line = the CURRENT setup's entry level."""
    if not history_trades:
        st.caption("Keine historischen Trades zum Einzeichnen vorhanden.")
        return

    first_entry = min(t["entry_date"] for t in history_trades)
    chart_start = date.fromisoformat(first_entry) - timedelta(days=30)
    chart_end = date.today()

    with Session() as db:
        bars = get_bars(db, ticker, chart_start, chart_end, timeframe="day")
    if len(bars) < 10:
        st.caption(
            "Kursdaten für das Chart sind (noch) nicht im Cache — sie werden beim nächsten "
            "Scan/Backtest dieses Tickers automatisch angelegt."
        )
        return

    price_df = pd.DataFrame(
        [{"Datum": b.timestamp.date().isoformat(), "Kurs": b.close} for b in bars]
    )
    entries_df = pd.DataFrame(
        [{"Datum": t["entry_date"], "Kurs": t["entry"], "Art": "Einstieg", "R": t["r"], "Grund": t["reason"]} for t in history_trades]
    )
    exits_df = pd.DataFrame(
        [{"Datum": t["exit_date"], "Kurs": t["exit"], "Grund": t["reason"], "R": t["r"]} for t in history_trades]
    )

    base = alt.Chart(price_df).mark_line(color="#455a64", strokeWidth=1.5).encode(
        x=alt.X("Datum:T", title=None),
        y=alt.Y("Kurs:Q", title="Kurs ($)", scale=alt.Scale(zero=False)),
    )
    entry_marks = alt.Chart(entries_df).mark_point(
        shape="triangle-up", size=110, color="#2e7d32", filled=True
    ).encode(
        x="Datum:T", y="Kurs:Q",
        tooltip=[alt.Tooltip("Datum:T"), alt.Tooltip("Kurs:Q", title="Entry"), alt.Tooltip("R:Q", title="Ergebnis (R)")],
    )
    exit_marks = alt.Chart(exits_df).mark_point(shape="circle", size=80, filled=True).encode(
        x="Datum:T", y="Kurs:Q",
        color=alt.Color("Grund:N", scale=_REASON_COLORS, legend=alt.Legend(title="Exit-Grund", orient="bottom")),
        tooltip=[alt.Tooltip("Datum:T"), alt.Tooltip("Kurs:Q", title="Exit"), alt.Tooltip("Grund:N"), alt.Tooltip("R:Q", title="R")],
    )
    entry_rule = alt.Chart(pd.DataFrame({"y": [entry_level]})).mark_rule(
        strokeDash=[6, 4], color="#e65100", strokeWidth=1.5
    ).encode(y="y:Q")

    st.altair_chart(
        (base + entry_marks + exit_marks + entry_rule).properties(height=280),
        width="stretch",
    )
    st.caption(
        "▲ grün = historischer Einstieg · ● Ausstieg gefärbt nach Grund (grün Ziel, rot Stop, "
        "grau Zeit, blau Trailing) · gestrichelte Linie = Entry-Level des **aktuellen** Setups. "
        "Punkte anklicken/hovern zeigt Datum und R-Ergebnis."
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
            head, grade_col, score_col = st.columns([3, 1, 1])
            head.markdown(f"### {i}. {s['ticker']} · {s['strategy_name']}")
            grade = s.get("grade")
            if grade:
                emoji, color = _GRADE_STYLE.get(grade, ("", "gray"))
                grade_col.markdown(f"## :{color}[{emoji} Note {grade}]")
                grade_col.caption(f"{s.get('grade_points', 0):.0f}/100 Qualitätspunkte")
            score_col.metric("Score", f"{s.get('score_adjusted', s['score']):.0f} / 100")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Entry (über)", f"${s['entry']:.2f}")
            c2.metric("Stop Loss", f"${s['stop']:.2f}")
            c3.metric("Take Profit", f"${s['target']:.2f}")
            c4.metric("R:R", f"{s['risk_reward']:.1f}")

            grade_reasons = s.get("grade_reasons") or []
            if grade_reasons:
                with st.expander(f"🎓 Wie die Note {grade} zustande kommt"):
                    for reason in grade_reasons:
                        st.markdown(f"- {reason}")

            history_trades = s.get("history_trades") or []
            if history_trades:
                with st.expander(f"📉 Chart: wie die Strategie auf {s['ticker']} historisch lief", expanded=(i == 1)):
                    _render_history_chart(s["ticker"], s["entry"], history_trades)

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

            pros = s.get("pro_factors") or []
            risks = s.get("risk_factors") or []
            if pros or risks:
                pro_col, risk_col = st.columns(2)
                with pro_col:
                    st.markdown("**✅ Was dafür spricht**")
                    for f in pros:
                        st.markdown(f"- {f}")
                with risk_col:
                    st.markdown("**⚠️ Was dagegen spricht**")
                    if risks:
                        for f in risks:
                            st.markdown(f"- {f}")
                    else:
                        st.markdown("- Keine auffälligen Warnsignale in den geprüften Faktoren.")

            news = s.get("news") or []
            if news:
                with st.expander(f"📰 Aktuelle News zu {s['ticker']}"):
                    for n in news:
                        line = f"**{n.get('published', '')}** — {n.get('headline', '')} ({n.get('source', '')})"
                        if n.get("url"):
                            line += f" · [Artikel]({n['url']})"
                        st.markdown(line)


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
    _render_regime((last_run.settings_used or {}).get("market_regime"))
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
                    settings.POLYGON_API_KEY, universe + ["SPY"], n_bars=max(int(history_bars), 210), end_day=probe_day,
                    progress_callback=lambda done, total: hist_progress.progress(done / total, text=f"Tag {done}/{total}"),
                )
                spy_bars = bars_by_ticker.pop("SPY", [])

                progress_line = st.empty()
                with Session() as db:
                    result = find_top_setups(
                        db, settings, strategies, bars_by_ticker,
                        backtest_years=int(backtest_years), min_trades=int(min_trades), top_k=int(top_k),
                        spy_bars=spy_bars,
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
                                "market_regime": result.market_regime,
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
        _render_regime(result.market_regime)
        _render_setups([s.__dict__ for s in result.top], str(latest_day))

        if result.all_candidates:
            with st.expander(f"Alle {len(result.all_candidates)} qualifizierten Kandidaten"):
                df = pd.DataFrame(
                    [
                        {
                            "Ticker": c.ticker, "Note": c.grade, "Strategie": c.strategy_name,
                            "Score": c.score_adjusted,
                            "Entry": c.entry, "SL": c.stop, "TP": c.target, "R:R": c.risk_reward,
                            "Trades": c.total_trades, "WR %": c.win_rate_pct,
                            "PF": c.profit_factor, "Ø R": c.avg_r_multiple,
                        }
                        for c in result.all_candidates
                    ]
                )
                st.dataframe(df, width="stretch", hide_index=True)
