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
from cloud.db import AppSetting, SavedStrategy, TopSetupRun, get_session_factory, init_db
from cloud.historical_data import get_bars
from cloud.indicators import bollinger_bands as indicator_bollinger, ema as indicator_ema
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

def _load_account() -> tuple[float, float]:
    with Session() as db:
        row = db.get(AppSetting, "account")
    if row and isinstance(row.value, dict):
        return float(row.value.get("size", 0.0)), float(row.value.get("risk_pct", 1.0))
    return 0.0, 1.0


def _save_account(size: float, risk_pct: float) -> None:
    with Session() as db:
        row = db.get(AppSetting, "account")
        if row is None:
            row = AppSetting(key="account", value={})
            db.add(row)
        row.value = {"size": size, "risk_pct": risk_pct}
        db.commit()


_acct_size, _acct_risk = _load_account()
with st.expander("⚙️ Konto für Positionsgrößen-Rechner" + (f" — ${_acct_size:,.0f}, {_acct_risk}% Risiko/Trade" if _acct_size else " (nicht gesetzt)")):
    a1, a2, a3 = st.columns([1.5, 1, 1])
    new_size = a1.number_input("Kontogröße ($)", min_value=0.0, value=_acct_size, step=500.0)
    new_risk = a2.number_input("Risiko pro Trade (%)", min_value=0.1, max_value=5.0, value=_acct_risk, step=0.1)
    if a3.button("Speichern", key="save_account"):
        _save_account(float(new_size), float(new_risk))
        st.success("Gespeichert — die Setup-Karten zeigen ab jetzt konkrete Stückzahlen.")
        st.rerun()
    st.caption(
        "Stückzahl = (Konto × Risiko%) ÷ (Entry − Stop). Riskiert wird also immer derselbe "
        "Kontoanteil, egal wie weit der Stop ist — weiter Stop heißt automatisch kleinere Position."
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
        if status == "green":
            st.success(text)
        else:
            st.caption(text)


def _render_strategy_stats(stats: list | None) -> None:
    """Transparency table: proves every strategy was scanned and shows which
    ones produced how many signals. Also honestly reveals when one loose
    strategy floods the ranking."""
    if not stats:
        return
    with st.expander(f"📊 Welche Strategie fand wie viele Signale? ({len(stats)} Strategien geprüft)"):
        df = pd.DataFrame(
            [{"Strategie": s["strategy"], "Signale heute": s["signals"], "Qualifiziert (Backtest bestanden)": s["qualified"]} for s in stats]
        )
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(
            "Jede Zeile wurde vollständig gescannt und jeder Treffer rückgetestet. 0 Signale ist bei "
            "selektiven Strategien (Golden Cross, 52W-Hoch) an den meisten Tagen völlig normal."
        )


_REASON_COLORS = alt.Scale(
    domain=["target", "stop", "time_exit", "trailing_stop", "indicator_exit"],
    range=["#2e7d32", "#c62828", "#757575", "#1565c0", "#6a1b9a"],
)


def _render_history_chart(
    ticker: str, entry_level: float, history_trades: list[dict], overlays: list[dict] | None = None
) -> None:
    """Price chart from the cached daily bars with every historical
    backtest signal marked: green triangles = entries, exit dots colored by
    reason (green target / red stop / gray time exit / blue trailing stop),
    dashed line = the CURRENT setup's entry level. Overlays derived from
    the strategy's own conditions (EMAs, breakout range lines) show the
    confluences that produced each signal."""
    if not history_trades:
        st.caption("Keine historischen Trades zum Einzeichnen vorhanden.")
        return

    first_entry = min(t["entry_date"] for t in history_trades)
    chart_start = date.fromisoformat(first_entry) - timedelta(days=30)
    chart_end = date.today()

    # Fetch extra lead-in history so long EMAs (e.g. EMA200) are accurate
    # from the left edge of the chart instead of starting mid-way.
    max_ema_period = max((o.get("period", 0) for o in (overlays or []) if o.get("kind") in ("ema", "bollinger")), default=0)
    max_lookback = max((o.get("lookback", 0) for o in (overlays or []) if o.get("kind") != "ema"), default=0)
    lead_in_days = int(max(max_ema_period, max_lookback) * 1.6) + 10

    with Session() as db:
        bars = get_bars(db, ticker, chart_start - timedelta(days=lead_in_days), chart_end, timeframe="day")
    if len(bars) < 10:
        st.caption(
            "Kursdaten für das Chart sind (noch) nicht im Cache — sie werden beim nächsten "
            "Scan/Backtest dieses Tickers automatisch angelegt."
        )
        return

    bars = sorted(bars, key=lambda b: b.timestamp)
    visible_from = chart_start.isoformat()

    price_rows = [
        {"Datum": b.timestamp.date().isoformat(), "Kurs": b.close}
        for b in bars
        if b.timestamp.date().isoformat() >= visible_from
    ]
    price_df = pd.DataFrame(price_rows)

    # --- overlay series (computed on the full extended window, displayed trimmed) ---
    ema_rows: list[dict] = []
    range_rows: list[dict] = []
    closes = [b.close for b in bars]
    for o in overlays or []:
        if o.get("kind") == "ema":
            period = int(o["period"])
            series = indicator_ema(closes, period)
            for b, v in zip(bars, series):
                d = b.timestamp.date().isoformat()
                if v is not None and d >= visible_from:
                    ema_rows.append({"Datum": d, "Wert": round(v, 2), "Linie": f"EMA{period}"})
        elif o.get("kind") == "bollinger":
            period = int(o.get("period", 20))
            num_std = float(o.get("num_std", 2.0))
            upper, middle, lower = indicator_bollinger(closes, period, num_std)
            for b, u, lo in zip(bars, upper, lower):
                d = b.timestamp.date().isoformat()
                if u is not None and d >= visible_from:
                    range_rows.append({"Datum": d, "Wert": round(u, 2), "Linie": f"BB{period} oben"})
                    range_rows.append({"Datum": d, "Wert": round(lo, 2), "Linie": f"BB{period} unten"})
        elif o.get("kind") in ("rolling_high", "rolling_low"):
            lookback = int(o["lookback"])
            is_high = o["kind"] == "rolling_high"
            label = f"{lookback}T-{'Hoch' if is_high else 'Tief'}"
            for i in range(lookback, len(bars)):
                d = bars[i].timestamp.date().isoformat()
                if d < visible_from:
                    continue
                window = bars[i - lookback : i]
                v = max(b.high for b in window) if is_high else min(b.low for b in window)
                range_rows.append({"Datum": d, "Wert": round(v, 2), "Linie": label})

    entries_df = pd.DataFrame(
        [{"Datum": t["entry_date"], "Kurs": t["entry"], "Art": "Einstieg", "R": t["r"], "Grund": t["reason"]} for t in history_trades]
    )
    exits_df = pd.DataFrame(
        [{"Datum": t["exit_date"], "Kurs": t["exit"], "Grund": t["reason"], "R": t["r"]} for t in history_trades]
    )

    layers = [
        alt.Chart(price_df).mark_line(color="#455a64", strokeWidth=1.5).encode(
            x=alt.X("Datum:T", title=None),
            y=alt.Y("Kurs:Q", title="Kurs ($)", scale=alt.Scale(zero=False)),
        )
    ]
    _OVERLAY_COLORS = alt.Scale(
        domain=["EMA20", "EMA50", "EMA100", "EMA200"],
        range=["#f9a825", "#8e24aa", "#00838f", "#37474f"],
    )
    if ema_rows:
        layers.append(
            alt.Chart(pd.DataFrame(ema_rows)).mark_line(strokeWidth=1.2, opacity=0.9).encode(
                x="Datum:T", y="Wert:Q",
                color=alt.Color("Linie:N", scale=_OVERLAY_COLORS, legend=alt.Legend(title="Konfluenzen", orient="bottom")),
                tooltip=["Datum:T", "Linie:N", alt.Tooltip("Wert:Q", format=".2f")],
            )
        )
    if range_rows:
        layers.append(
            alt.Chart(pd.DataFrame(range_rows)).mark_line(strokeWidth=1.2, strokeDash=[4, 3], opacity=0.85).encode(
                x="Datum:T", y="Wert:Q",
                color=alt.Color("Linie:N", legend=alt.Legend(title="Range", orient="bottom")),
                tooltip=["Datum:T", "Linie:N", alt.Tooltip("Wert:Q", format=".2f")],
            )
        )

    layers.append(
        alt.Chart(entries_df).mark_point(shape="triangle-up", size=110, color="#2e7d32", filled=True).encode(
            x="Datum:T", y="Kurs:Q",
            tooltip=[alt.Tooltip("Datum:T"), alt.Tooltip("Kurs:Q", title="Entry"), alt.Tooltip("R:Q", title="Ergebnis (R)")],
        )
    )
    layers.append(
        alt.Chart(exits_df).mark_point(shape="circle", size=80, filled=True).encode(
            x="Datum:T", y="Kurs:Q",
            color=alt.Color("Grund:N", scale=_REASON_COLORS, legend=alt.Legend(title="Exit-Grund", orient="bottom")),
            tooltip=[alt.Tooltip("Datum:T"), alt.Tooltip("Kurs:Q", title="Exit"), alt.Tooltip("Grund:N"), alt.Tooltip("R:Q", title="R")],
        )
    )
    layers.append(
        alt.Chart(pd.DataFrame({"y": [entry_level]})).mark_rule(
            strokeDash=[6, 4], color="#e65100", strokeWidth=1.5
        ).encode(y="y:Q")
    )

    chart = layers[0]
    for layer in layers[1:]:
        chart = chart + layer
    st.altair_chart(chart.properties(height=300).resolve_scale(color="independent"), width="stretch")
    overlay_note = ""
    if ema_rows or range_rows:
        drawn = sorted({r["Linie"] for r in ema_rows} | {r["Linie"] for r in range_rows})
        overlay_note = f" · Konfluenz-Linien aus den Strategie-Bedingungen: {', '.join(drawn)}"
    st.caption(
        "▲ grün = historischer Einstieg · ● Ausstieg gefärbt nach Grund (grün Ziel, rot Stop, "
        "grau Zeit, blau Trailing) · gestrichelte orange Linie = Entry-Level des **aktuellen** "
        f"Setups{overlay_note}. Punkte anklicken/hovern zeigt Datum und R-Ergebnis."
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

            if _acct_size > 0:
                risk_dollars = _acct_size * _acct_risk / 100
                per_share_risk = s["entry"] - s["stop"]
                if per_share_risk > 0:
                    shares = int(risk_dollars / per_share_risk)
                    cost = shares * s["entry"]
                    if shares < 1:
                        st.caption("📐 Positionsgröße: Stop zu weit für dein Risikobudget — selbst 1 Stück überschreitet es.")
                    elif cost > _acct_size:
                        afford = int(_acct_size / s["entry"])
                        st.caption(
                            f"📐 **{shares} Stück** (Risiko ${risk_dollars:,.0f}) — aber Positionswert "
                            f"${cost:,.0f} übersteigt dein Konto; maximal leistbar: {afford} Stück "
                            f"(dann Risiko nur ${afford * per_share_risk:,.0f})."
                        )
                    else:
                        st.caption(
                            f"📐 Positionsgröße bei {_acct_risk}% Risiko: **{shares} Stück** ≈ "
                            f"${cost:,.0f} Positionswert ({cost / _acct_size * 100:.0f}% vom Konto) · "
                            f"Risiko ${shares * per_share_risk:,.0f} bis zum Stop."
                        )

            also = s.get("also_matched") or []
            if also:
                qualified = [o for o in also if o.get("qualified")]
                signal_only = [o for o in also if not o.get("qualified")]
                parts = []
                if qualified:
                    parts.append(
                        "**"
                        + "**, **".join(f"{o['strategy']} (Note {o.get('grade', '?')}, Score {o.get('score', 0):.0f})" for o in qualified)
                        + "**"
                    )
                if signal_only:
                    parts.append(", ".join(f"{o['strategy']} (Signal, aber Historie nicht qualifiziert)" for o in signal_only))
                bonus = s.get("confluence_bonus", 0)
                bonus_note = f" · Konfluenz-Bonus: +{bonus:.0f} Punkte" if bonus else ""
                st.info(f"🔗 **Konfluenz** — diese Aktie ist heute außerdem Setup für: {' · '.join(parts)}{bonus_note}")

            grade_reasons = s.get("grade_reasons") or []
            if grade_reasons:
                with st.expander(f"🎓 Wie die Note {grade} zustande kommt"):
                    for reason in grade_reasons:
                        st.markdown(f"- {reason}")

            history_trades = s.get("history_trades") or []
            if history_trades:
                with st.expander(f"📉 Chart: wie die Strategie auf {s['ticker']} historisch lief", expanded=(i == 1)):
                    _render_history_chart(s["ticker"], s["entry"], history_trades, s.get("overlays"))

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
    _render_strategy_stats((last_run.settings_used or {}).get("strategy_stats"))
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
min_price = u1.number_input("Min price ($)", min_value=0.01, value=0.10, step=0.10, format="%.2f")
max_price = u2.number_input("Max price ($)", min_value=1.0, value=10000.0, step=100.0)
top_n = u3.number_input(f"Universe (top-N, max {MAX_UNIVERSE})", min_value=10, max_value=MAX_UNIVERSE, value=400, step=10)
history_bars = u4.number_input("History (trading days)", min_value=40, max_value=250, value=250, step=10)

s1, s2, s3, s4 = st.columns(4)
backtest_years = s1.selectbox("Auto-Backtest über", [1, 2, 3], index=1, format_func=lambda y: f"{y} Jahr(e)")
min_trades = s2.number_input("Min. historische Trades", min_value=1, max_value=50, value=5)
top_k = s3.selectbox("Wie viele Top-Setups", [1, 2, 3], index=2)
max_per_strategy = s4.selectbox(
    "Max. pro Strategie in den Top", [1, 2, 3], index=0,
    help="Erzwingt Vielfalt: bei 1 kann keine einzelne Strategie alle Top-Plätze fluten. "
         "Bleiben Plätze frei, werden sie mit den nächstbesten Setups aufgefüllt — egal welcher Strategie.",
)

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
                        max_per_strategy=int(max_per_strategy),
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
                                "strategy_stats": result.strategy_stats,
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
        _render_strategy_stats(result.strategy_stats)
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
