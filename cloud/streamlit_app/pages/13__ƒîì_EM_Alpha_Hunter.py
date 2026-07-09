"""🌍 EM Alpha Hunter — sucht Qualitäts-Compounder aus Emerging Markets, die
westliche Investoren kaum beachten. WICHTIG: Das Universum sind US-gelistete
Auslandsfirmen (ADRs), weil der Datenplan nur US-Börsen abdeckt — lokale
Börsen (Mumbai, Jakarta, São Paulo, ...) sind strukturell außer Reichweite.
"Unter dem Radar" wird über messbare Proxies bestimmt (US-Handelsvolumen,
englische Medienpräsenz); Analystenabdeckung und institutionelle Quoten
liefert der Plan nicht und werden nicht behauptet."""
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

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from cloud.config import get_settings
from cloud.db import EMCompany, get_session_factory, init_db
from cloud.em_alpha import (
    EM_COUNTRY_TERMS, analyze_candidates, attach_dollar_volume,
    enrich_missing_details, shortlist, sync_universe,
)

st.set_page_config(page_title="EM Alpha Hunter — ORB Scanner", page_icon="🌍", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("🌍 Emerging Markets Alpha Hunter")
st.caption("Qualitäts-Compounder aus Schwellenländern, die im Westen kaum jemand beachtet — Langfrist-Horizont, keine Momentum-Jagd.")
st.info(
    "**Was dieses Tool ehrlich kann und was nicht:** Durchsucht werden **US-gelistete "
    "Auslandsfirmen (ADRs)** — dein Polygon-Plan deckt nur US-Börsen ab, lokale Börsen in "
    "Mumbai, Jakarta oder São Paulo bleiben unerreichbar. Ein US-Listing macht eine Firma "
    "per se etwas sichtbarer als einen reinen Lokalwert — der Radar-Vorteil ist also real, "
    "aber kleiner als im Idealfall. \"Unter dem Radar\" wird über zwei **messbare** Proxies "
    "bestimmt: geringes US-Handelsvolumen und wenige englische News-Artikel. "
    "Analystenabdeckung und institutionelle Eigentümerquoten liefert der Datenplan **nicht** — "
    "sie fließen nicht in den Score ein, statt sie zu erfinden. Moat, Management und "
    "politisches Risiko bleiben deine Hausaufgaben (Checkliste unten)."
)

# ---------------- Step 1: Universe ----------------
st.subheader("Schritt 1 — ADR-Universum aufbauen")
with Session() as db:
    total = db.execute(select(func.count()).select_from(EMCompany)).scalar() or 0
    enriched = db.execute(select(func.count()).select_from(EMCompany).where(EMCompany.details_fetched_at.is_not(None))).scalar() or 0
    em_count = db.execute(select(func.count()).select_from(EMCompany).where(EMCompany.country != "")).scalar() or 0
    with_vol = db.execute(select(func.count()).select_from(EMCompany).where(EMCompany.dollar_volume.is_not(None))).scalar() or 0

u1, u2, u3, u4 = st.columns(4)
u1.metric("ADRs im Cache", total)
u2.metric("Mit Details", enriched)
u3.metric("Als EM erkannt", em_count)
u4.metric("Mit Volumen", with_vol)

c1, c2, c3 = st.columns(3)
if c1.button("① Ticker-Liste laden/aktualisieren"):
    if not settings.POLYGON_API_KEY:
        st.error("`POLYGON_API_KEY` fehlt.")
    else:
        with st.spinner("Lade alle US-gelisteten ADRs…"):
            with Session() as db:
                n = sync_universe(db, settings)
        st.success(f"{n} ADR-Ticker im Cache.")
        st.rerun()
if c2.button("② Details ergänzen (300er-Schritte)"):
    if not settings.POLYGON_API_KEY:
        st.error("`POLYGON_API_KEY` fehlt.")
    else:
        bar = st.progress(0.0, text="Hole Firmendetails (1 Abruf pro Ticker)…")
        with Session() as db:
            done = enrich_missing_details(
                db, settings, limit=300,
                progress_cb=lambda i, n: bar.progress(i / max(n, 1), text=f"Details {i}/{n}…"),
            )
        bar.empty()
        if done == 0:
            st.success("Alle Details bereits vorhanden.")
        else:
            st.success(f"{done} Firmen ergänzt — Button erneut klicken, bis 'Mit Details' = 'ADRs im Cache'.")
        st.rerun()
if c3.button("③ Handelsvolumen laden (1 Abruf)"):
    if not settings.POLYGON_API_KEY:
        st.error("`POLYGON_API_KEY` fehlt.")
    else:
        with st.spinner("Grouped-Daily für den letzten Handelstag…"):
            with Session() as db:
                n = attach_dollar_volume(db, settings)
        st.success(f"Volumen für {n} ADRs gesetzt.")
        st.rerun()
st.caption(
    "Einmalig nötig, danach gecacht: ① holt die Ticker-Liste (wenige Abrufe), ② ergänzt "
    "Marktkapitalisierung/Beschreibung/Land in 300er-Schritten (mehrfach klicken), "
    "③ holt Schlusskurs + Dollar-Volumen für alle in einem einzigen Abruf. "
    "Die Landeszuordnung ist eine **Heuristik** aus dem Beschreibungstext — Firmen ohne "
    "erkennbares Land werden ehrlich aussortiert statt geraten."
)

# ---------------- Step 2: Filter ----------------
st.subheader("Schritt 2 — Filter")
f1, f2, f3, f4 = st.columns(4)
mcap_min = f1.number_input("Market Cap min ($ Mio)", min_value=50.0, value=500.0, step=50.0)
mcap_max = f2.number_input("Market Cap max ($ Mrd)", min_value=1.0, value=15.0, step=1.0)
top_n = f3.number_input("Kandidaten für Tiefenanalyse", min_value=5, max_value=50, value=20, step=5)
all_countries = sorted(EM_COUNTRY_TERMS.keys())
sel_countries = f4.multiselect("Länder (leer = alle EM)", all_countries, default=[])

# ---------------- Step 3: Analyze ----------------
if st.button("🌍 Alpha-Suche starten", type="primary"):
    if not settings.POLYGON_API_KEY:
        st.error("`POLYGON_API_KEY` fehlt.")
        st.stop()
    with Session() as db:
        rows = shortlist(db, mcap_min=mcap_min * 1e6, mcap_max=mcap_max * 1e9,
                         countries=sel_countries or None, top_n=int(top_n))
    if not rows:
        st.warning(
            "Keine Kandidaten im Filter — vermutlich ist das Universum noch nicht aufgebaut "
            "(Schritt 1: erst ①, dann ② bis vollständig, dann ③)."
        )
        st.stop()
    bar = st.progress(0.0, text=f"Tiefenanalyse von {len(rows)} Kandidaten (Finanzberichte + News je Ticker)…")
    with Session() as db:
        cands = analyze_candidates(
            db, settings, rows,
            progress_cb=lambda i, n: bar.progress(i / max(n, 1), text=f"Analysiere {i}/{n}…"),
        )
    bar.empty()
    st.session_state["em_results"] = cands

cands = st.session_state.get("em_results")
if not cands:
    st.stop()

st.divider()
qualified = [c for c in cands if c.alpha_score >= 55 and not any("Keine Finanzberichte" in f for f in c.flags)]
top5 = qualified[:5]

if not top5:
    st.warning(
        "**Keine außergewöhnlichen Unternehmen gefunden.** Genau wie in deinem Briefing gilt: "
        "lieber keine Empfehlung als eine mittelmäßige. Häufigste Gründe: zu wenige SEC-Berichte "
        "bei kleinen ADRs, Red Flags (negativer FCF, Verwässerung), oder das Universum ist noch "
        "unvollständig aufgebaut. Die vollständige Kandidatenliste steht unten."
    )
else:
    st.subheader(f"🏆 Top {len(top5)} — nach Alpha-Score")
    for rank, c in enumerate(top5, start=1):
        with st.container(border=True):
            h1, h2, h3, h4 = st.columns([2.4, 1, 1, 1])
            h1.markdown(f"### {rank}. {c.name} ({c.ticker})")
            h1.caption(f"🌍 {c.country} · {c.sector or 'Branche unbekannt'}")
            h2.metric("Alpha-Score", f"{c.alpha_score:.0f}/100")
            h3.metric("Market Cap", f"${(c.market_cap or 0)/1e9:.1f} Mrd")
            h4.metric("Confidence", c.confidence)

            for pname, pts, mx, reason in c.alpha_parts:
                if mx:
                    st.caption(f"**{pname}** — {pts:.0f}/{mx:.0f} · {reason}")
                else:
                    st.caption(f"**{pname}** — {pts:.0f} · {reason}")

            if c.metrics:
                m = c.metrics
                k1, k2, k3, k4, k5 = st.columns(5)
                k1.metric("Umsatz-CAGR", f"{m.revenue_cagr}%" if m.revenue_cagr is not None else "—")
                k2.metric("Nettomarge", f"{m.net_margin}%" if m.net_margin is not None else "—")
                k3.metric("ROE", f"{m.roe}%" if m.roe is not None else "—")
                k4.metric("Debt/Equity", m.debt_to_equity if m.debt_to_equity is not None else "—")
                k5.metric("Verwässerung/J", f"{c.dilution}%" if c.dilution is not None else "—")

            if c.flags:
                st.error("🚩 " + " · ".join(c.flags))
            if c.description:
                with st.expander("Unternehmensbeschreibung"):
                    st.write(c.description)
            st.caption(
                f"➡️ Tiefenanalyse: Ticker **{c.ticker}** im 🏛️ Investor Modus eingeben "
                "(Mehrjahres-Tabelle, DCF, Dividende)."
            )

    st.info(
        "**Deine Hausaufgaben pro Kandidat (nicht berechenbar):** Wie stark ist die lokale "
        "Marktstellung wirklich — Marktführer oder Mitläufer? Wer kontrolliert die Firma "
        "(Familie, Staat, Streubesitz), und wie wurden Minderheitsaktionäre historisch "
        "behandelt? Wie hoch ist das politische Risiko im Heimatland (Kapitalverkehrskontrollen, "
        "Enteignung, Währung)? Sind die Umsätze wiederkehrend oder zyklisch? Und der "
        "Advocatus Diaboli: Warum handelt diese Aktie so still — weiß der lokale Markt etwas, "
        "das in den SEC-Berichten nicht steht?"
    )

with st.expander(f"Alle {len(cands)} analysierten Kandidaten"):
    df = pd.DataFrame([{
        "Ticker": c.ticker, "Name": c.name[:40], "Land": c.country,
        "MCap ($Mrd)": round((c.market_cap or 0) / 1e9, 2),
        "$-Vol (Mio)": round((c.dollar_volume or 0) / 1e6, 2),
        "News 90T": c.news_90d, "Quant": c.quality.total if c.quality else None,
        "Alpha": c.alpha_score, "Confidence": c.confidence,
        "Red Flags": len(c.flags),
    } for c in cands])
    st.dataframe(df, width="stretch", hide_index=True)
