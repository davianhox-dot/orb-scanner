"""🏛️ Investor Modus — Langfrist-Analyse einer Aktie nach der Checkliste des
Nutzers. Die MESSBARE Hälfte (Wachstum, Margen, Bilanz, Cashflow,
Bewertung, DCF, Dividende) rechnet das Tool aus echten Fundamentaldaten;
die QUALITATIVE Hälfte (Geschäftsmodell, Moat, Management, SWOT) wird als
geführte Checkliste gestellt — weil kein Algorithmus einen Burggraben aus
einer Bilanz ablesen kann, und so zu tun wäre gefährlicher als ehrliches
Nichtwissen."""
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
from cloud.fundamentals import (
    compute_metrics, compute_valuation, dcf_fair_value,
    fetch_dividends, fetch_financials, fetch_ticker_details, parse_reports, score_quant,
)
from cloud.historical_data import ensure_bars_cached, get_bars

st.set_page_config(page_title="Investor Modus — ORB Scanner", page_icon="🏛️", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("🏛️ Investor Modus — Langfrist-Analyse")
st.caption("Fundamentaldaten-Analyse für Anlagehorizonte von 10+ Jahren: Wachstum, Profitabilität, Bilanz, Cashflow, Bewertung, DCF, Dividende.")
st.info(
    "**Ehrliche Arbeitsteilung:** Dieses Tool berechnet die **messbare Hälfte** deiner "
    "Analyse-Checkliste aus echten Finanzberichten. Die **qualitative Hälfte** — Moat, "
    "Management-Qualität, Geschäftsmodell-Verständnis, SWOT — kann keine Formel beurteilen; "
    "dafür bekommst du unten deine eigene Checkliste als Leitfragen. Ein hoher Quant-Score "
    "bei einer Firma, deren Geschäft du nicht verstehst, ist **kein** Kaufsignal."
)

t1, t2 = st.columns([2, 1])
ticker_input = t1.text_input("Ticker", placeholder="z.B. AAPL", key="inv_ticker")
run = t2.button("🏛️ Analysieren", type="primary")

if not settings.POLYGON_API_KEY:
    st.warning("`POLYGON_API_KEY` fehlt — die Analyse braucht Fundamentaldaten von Polygon.")

if run and ticker_input.strip():
    ticker = ticker_input.strip().upper()
    if not settings.POLYGON_API_KEY:
        st.error("Ohne `POLYGON_API_KEY` geht es nicht.")
        st.stop()
    with st.status(f"Lade Fundamentaldaten für {ticker}…", expanded=True) as status:
        st.write("Jahresberichte (bis zu 10 Jahre)…")
        reports = fetch_financials(settings, ticker, timeframe="annual", limit=10)
        st.write("Firmendaten…")
        details = fetch_ticker_details(settings, ticker)
        st.write("Dividendenhistorie…")
        dividends = fetch_dividends(settings, ticker)
        st.write("Aktueller Kurs…")
        price = None
        try:
            with Session() as db:
                ensure_bars_cached(db, settings, ticker, date.today() - timedelta(days=14), date.today(), timeframe="day")
                bars = get_bars(db, ticker, date.today() - timedelta(days=14), date.today(), timeframe="day")
            if bars:
                price = sorted(bars, key=lambda b: b.timestamp)[-1].close
        except Exception:  # noqa: BLE001
            price = None
        status.update(label="Daten geladen", state="complete", expanded=False)

    if not reports:
        st.error(
            f"Keine Finanzberichte für {ticker} gefunden. Mögliche Gründe: Ticker existiert nicht, "
            "ausländischer Emittent ohne SEC-Berichte, oder der Polygon-Plan liefert für diesen "
            "Wert keine Fundamentaldaten."
        )
        st.stop()
    if price is None:
        st.error(f"Kein aktueller Kurs für {ticker} verfügbar — ohne Kurs keine Bewertung.")
        st.stop()

    st.session_state["inv_data"] = {
        "ticker": ticker, "reports": reports, "details": details,
        "dividends": dividends, "price": price,
    }

data = st.session_state.get("inv_data")
if not data:
    st.stop()

ticker = data["ticker"]
records = parse_reports(data["reports"])
m = compute_metrics(records)
market_cap = (data["details"] or {}).get("market_cap")
v = compute_valuation(m, price=data["price"], market_cap=market_cap)
score = score_quant(m, v)

# --- Header ---
st.divider()
name = (data["details"] or {}).get("name", ticker)
h1, h2, h3 = st.columns([2, 1, 1])
h1.markdown(f"## {name} ({ticker})")
h2.metric("Kurs", f"${data['price']:.2f}")
h3.metric("Market Cap", f"${(v.market_cap or 0) / 1e9:.1f} Mrd" if v.market_cap else "—")
desc = (data["details"] or {}).get("description")
if desc:
    with st.expander("Unternehmensbeschreibung"):
        st.write(desc)

# --- Quant score ---
sc1, sc2 = st.columns([1, 3])
sc1.metric("Quant-Score", f"{score.total:.0f} / 100")
with sc2:
    if score.total >= 70:
        st.success("Quantitativ stark — jetzt gehört die qualitative Hälfte geprüft (unten).")
    elif score.total >= 45:
        st.warning("Quantitativ gemischt — die Schwächen unten genau ansehen.")
    else:
        st.error("Quantitativ schwach — für einen 10-Jahres-Horizont spricht aus den Zahlen wenig.")
    st.caption("Der Score bewertet NUR die messbare Hälfte (kein Moat, kein Management).")
for bname, pts, mx, reason in score.components:
    st.caption(f"**{bname}** — {pts:.0f}/{mx:.0f} · {reason}")
    st.progress(min(1.0, pts / mx) if mx else 0.0)

# --- Multi-year table ---
st.subheader("Mehrjahres-Entwicklung")
year_rows = []
for r in records:
    year_rows.append({
        "Jahr": r.fiscal_year,
        "Umsatz ($M)": round(r.revenue / 1e6, 0) if r.revenue else None,
        "Gewinn ($M)": round(r.net_income / 1e6, 0) if r.net_income is not None else None,
        "EPS": r.eps,
        "Nettomarge %": round(r.net_income / r.revenue * 100, 1) if (r.revenue and r.net_income is not None) else None,
        "OCF ($M)": round(r.operating_cash_flow / 1e6, 0) if r.operating_cash_flow is not None else None,
        "FCF ($M)": round(r.fcf / 1e6, 0) if r.fcf is not None else None,
    })
st.dataframe(pd.DataFrame(year_rows), width="stretch", hide_index=True)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Umsatz-CAGR", f"{m.revenue_cagr}%" if m.revenue_cagr is not None else "—")
k2.metric("EPS-CAGR", f"{m.eps_cagr}%" if m.eps_cagr is not None else "—")
k3.metric("ROE", f"{m.roe}%" if m.roe is not None else "—")
k4.metric("Debt/Equity", m.debt_to_equity if m.debt_to_equity is not None else "—")
b1, b2, b3, b4 = st.columns(4)
b1.metric("Current Ratio", m.current_ratio if m.current_ratio is not None else "—")
b2.metric("Eigenkapitalquote", f"{m.equity_ratio}%" if m.equity_ratio is not None else "—")
b3.metric("Zinsdeckung", f"{m.interest_coverage}x" if m.interest_coverage is not None else "—")
b4.metric("ROIC (Näherung)", f"{m.roic_approx}%" if m.roic_approx is not None else "—")
for note in m.notes:
    st.caption(f"ℹ️ {note}")

# --- Valuation ---
st.subheader("Bewertung")
v1, v2, v3, v4 = st.columns(4)
v1.metric("KGV", v.pe if v.pe is not None else "— (kein Gewinn)")
v2.metric("KUV (P/S)", v.ps if v.ps is not None else "—")
v3.metric("P/FCF", v.p_fcf if v.p_fcf is not None else "—")
v4.metric("FCF-Rendite", f"{v.fcf_yield}%" if v.fcf_yield is not None else "—")
st.caption(
    "Einordnung ohne Branchendaten bewusst grob: FCF-Renditen ab ~5% gelten historisch als "
    "attraktiv, KGVs unter ~15 als günstig — aber ein billiges KGV bei schrumpfendem Geschäft "
    "ist eine Falle, kein Schnäppchen. Vergleiche selbst mit den direkten Wettbewerbern."
)

# --- DCF ---
st.subheader("Fair-Value-Schätzung (vereinfachtes DCF)")
if m.fcf_latest and m.fcf_latest > 0 and m.shares_latest:
    st.caption(
        f"Basis: FCF ${m.fcf_latest / 1e6:.0f}M{' (Näherung über OCF!)' if m.fcf_is_approx else ''}, "
        f"{m.shares_latest / 1e6:.0f}M Aktien · 10 Jahre Wachstum + Endwert. "
        "**Die Annahmen unten sind DEINE Eingaben — der Fair Value bewegt sich 1:1 mit ihnen.**"
    )
    a1, a2, a3, a4, a5 = st.columns(5)
    g_pess = a1.number_input("Wachstum pessimistisch (%/J)", value=min(m.revenue_cagr or 5.0, 5.0) - 3.0, step=0.5)
    g_real = a2.number_input("Wachstum realistisch (%/J)", value=min(m.fcf_cagr or m.revenue_cagr or 8.0, 15.0), step=0.5)
    g_opt = a3.number_input("Wachstum optimistisch (%/J)", value=min((m.fcf_cagr or m.revenue_cagr or 8.0) + 4.0, 22.0), step=0.5)
    disc = a4.number_input("Diskontsatz (%)", value=10.0, min_value=4.0, max_value=20.0, step=0.5)
    term = a5.number_input("Endwachstum (%)", value=2.5, min_value=0.0, max_value=4.0, step=0.5)

    rows = []
    for label, g in (("Pessimistisch", g_pess), ("Realistisch", g_real), ("Optimistisch", g_opt)):
        fv = dcf_fair_value(m.fcf_latest, m.shares_latest, growth_pct=g, discount_pct=disc, terminal_growth_pct=term)
        if fv is None:
            rows.append({"Szenario": label, "Wachstum": f"{g}%", "Fair Value": "—", "vs. Kurs": "—", "Margin of Safety": "—"})
        else:
            upside = (fv - data["price"]) / data["price"] * 100
            mos = (fv - data["price"]) / fv * 100
            rows.append({"Szenario": label, "Wachstum": f"{g}%", "Fair Value": f"${fv:,.2f}",
                         "vs. Kurs": f"{upside:+.0f}%", "Margin of Safety": f"{mos:.0f}%" if mos > 0 else "keine"})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption(
        "⚠️ Ein DCF ist ein Sensitivitäts-Werkzeug, kein Orakel: 2 Prozentpunkte mehr Wachstum "
        "oder weniger Diskont verschieben den 'fairen' Wert massiv. Kaufe nur mit Margin of "
        "Safety im REALISTISCHEN Szenario — das optimistische ist dein Traum, nicht dein Plan."
    )
else:
    st.warning(
        "DCF nicht möglich: " + ("kein positiver FCF" if not (m.fcf_latest and m.fcf_latest > 0) else "Aktienanzahl fehlt im Bericht")
        + ". Für Firmen ohne positiven Free Cash Flow ist eine DCF-Bewertung Kaffeesatzleserei — das Tool verweigert sie bewusst."
    )

# --- Dividends ---
st.subheader("Dividende")
divs = data.get("dividends") or []
if divs:
    by_year: dict[str, float] = {}
    for d in divs:
        y = str(d.get("ex_dividend_date", ""))[:4]
        if y and isinstance(d.get("cash_amount"), (int, float)):
            by_year[y] = by_year.get(y, 0.0) + float(d["cash_amount"])
    years_sorted = sorted(by_year)[-8:]
    div_df = pd.DataFrame([{"Jahr": y, "Dividende/Aktie": round(by_year[y], 2)} for y in years_sorted])
    dd1, dd2 = st.columns([1, 2])
    with dd1:
        latest_year = years_sorted[-1] if years_sorted else None
        if latest_year:
            annual = by_year[latest_year]
            st.metric(f"Dividende {latest_year}", f"${annual:.2f}")
            st.metric("Dividendenrendite", f"{annual / data['price'] * 100:.2f}%")
            if m.eps_latest and m.eps_latest > 0:
                payout = annual / m.eps_latest * 100
                st.metric("Ausschüttungsquote", f"{payout:.0f}%")
                if payout > 80:
                    st.caption("⚠️ Über 80% des Gewinns ausgeschüttet — wenig Puffer für schlechte Jahre.")
    with dd2:
        st.dataframe(div_df, width="stretch", hide_index=True)
else:
    st.caption("Keine Dividende gefunden — für einen Compounder kein Nachteil, solange das Kapital intern gut verzinst wird (siehe ROE/ROIC).")

# --- Qualitative checklist ---
st.divider()
st.subheader("🧭 Die qualitative Hälfte — deine Hausaufgaben")
st.caption(
    "Diese Fragen kann kein Tool beantworten. Sie stammen aus deiner eigenen Checkliste — "
    "wenn du drei davon nicht sicher beantworten kannst, kennst du die Firma noch nicht gut genug für 10 Jahre."
)
with st.expander("Geschäftsmodell & Moat", expanded=True):
    st.markdown(
        "- Wie verdient das Unternehmen Geld — kannst du es in zwei Sätzen erklären?\n"
        "- Wie abhängig ist es von einem einzigen Produkt oder Großkunden?\n"
        "- Was hindert einen gut finanzierten Konkurrenten, das Geschäft in 5 Jahren zu kopieren? (Marke, Netzwerkeffekte, Wechselkosten, Patente, Skalen)\n"
        "- Hat die Firma Preissetzungsmacht — kann sie Preise erhöhen, ohne Kunden zu verlieren?\n"
        "- Existiert der Burggraben in 10 Jahren noch? Was könnte ihn zerstören?"
    )
with st.expander("Management & Kapitalallokation"):
    st.markdown(
        "- Was hat das Management mit dem Free Cash Flow der letzten 5 Jahre gemacht — Rückkäufe unter Wert, sinnvolle Übernahmen, oder Imperiumsbau?\n"
        "- Kaufen Insider eigene Aktien, oder verkaufen sie?\n"
        "- Gibt das Management Fehler offen zu (Aktionärsbriefe lesen)?\n"
        "- Passen die Vergütungsanreize zu langfristigem Aktionärsinteresse?"
    )
with st.expander("Advocatus Diaboli — warum könnte das in 10 Jahren eine schlechte Investition sein?"):
    st.markdown(
        "- Welche Technologie oder Regulierung könnte das Geschäftsmodell obsolet machen?\n"
        "- Was passiert mit den Margen, wenn der stärkste Wettbewerber die Preise um 20% senkt?\n"
        "- Bezahle ich hier für vergangenes Wachstum, das sich nicht wiederholen lässt?\n"
        "- Würde ich nachkaufen, wenn der Kurs morgen 30% fällt — oder wäre ich erleichtert, raus zu sein?"
    )
st.info(
    "**Bevor du investierst, beantworte dir drei Fragen:** (1) Verstehe ich, wie diese Firma in "
    "10 Jahren mehr verdient als heute? (2) Was ist meine Margin of Safety im realistischen "
    "Szenario — nicht im optimistischen? (3) Was müsste passieren, damit ich verkaufe — und "
    "erkenne ich das rechtzeitig?"
)
