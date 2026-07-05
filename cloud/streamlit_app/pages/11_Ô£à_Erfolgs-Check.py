"""✅ Erfolgs-Check — der Scanner benotet sich selbst: Für jeden vergangenen
Top-Pick wird gemessen, was danach wirklich passiert ist (Rendite 5/10/20
Handelstage nach Auslösung des Buy-Stops), aggregiert nach Note und nach
Strategie. Das ist die ehrlichste Seite im ganzen System — sie beweist
oder widerlegt, ob den Noten zu trauen ist."""
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

from cloud.config import get_settings
from cloud.db import get_session_factory, init_db
from cloud.pick_performance import HORIZONS, build_report

st.set_page_config(page_title="Erfolgs-Check — ORB Scanner", page_icon="✅", layout="wide")

settings = get_settings()


@st.cache_resource
def _session_factory():
    init_db(settings)
    return get_session_factory(settings)


Session = _session_factory()

st.title("✅ Erfolgs-Check — wie gut waren die Picks wirklich?")
st.caption(
    "Für jeden vergangenen Top-Pick: Hat der Buy-Stop ausgelöst, und wo stand der Kurs 5/10/20 "
    "Handelstage später? Aggregiert nach Note und Strategie."
)
st.info(
    "**Wie die Zahlen zu lesen sind:** Gemessen wird die passive Rendite ab dem Entry-Trigger "
    "(\"was wäre, wenn ich einfach gehalten hätte\") — deine echten Ergebnisse mit Stop und "
    "Ziel weichen davon in beide Richtungen ab. Die Frage, die diese Seite beantwortet, ist: "
    "**Zeigen die Picks nach dem Signal überhaupt in die richtige Richtung — und "
    "unterscheiden die Noten wirklich gut von schlecht?** Buy-Stops, die nie auslösten, "
    "zählen ehrlich als \"nicht ausgelöst\" statt die Statistik zu verfälschen. Und: Unter "
    "~20 ausgelösten Picks pro Gruppe ist alles hier Anekdote, nicht Statistik."
)

if not settings.POLYGON_API_KEY:
    st.warning("`POLYGON_API_KEY` fehlt — der Erfolgs-Check braucht Kursdaten nach den Signal-Tagen.")

if st.button("✅ Erfolgs-Check ausführen", type="primary"):
    if not settings.POLYGON_API_KEY:
        st.error("Ohne `POLYGON_API_KEY` geht es nicht.")
        st.stop()
    with st.spinner("Werte alle gespeicherten Läufe aus (1 Datenabruf pro Ticker)…"):
        with Session() as db:
            report = build_report(db, settings)
    st.session_state["perf_report"] = report

report = st.session_state.get("perf_report")
if report is None:
    st.caption(
        "Noch keine Auswertung in dieser Sitzung. Der Check nutzt alle gespeicherten "
        "Top-Setups-Läufe — je länger der Nacht-Scan läuft, desto aussagekräftiger wird er."
    )
    st.stop()

if report.total_picks == 0:
    st.info("Noch keine gespeicherten Picks vorhanden — nach den ersten Nacht-Scans füllt sich diese Seite.")
    st.stop()

triggered = report.total_picks - report.untriggered
m1, m2, m3 = st.columns(3)
m1.metric("Picks gesamt", report.total_picks)
m2.metric("Buy-Stop ausgelöst", triggered)
m3.metric("Nicht ausgelöst", report.untriggered)
if report.untriggered:
    st.caption(
        f"{report.untriggered} Pick(s) brachen nie über ihr Entry-Level aus — korrektes "
        "Buy-Stop-Verhalten: kein Ausbruch, kein Trade, kein Verlust."
    )

def _stats_table(stats: dict, key_label: str) -> pd.DataFrame:
    rows = []
    for key, data in sorted(stats.items()):
        row = {key_label: key, "Picks (ausgelöst)": data["picks"]}
        for h in HORIZONS:
            hd = data["horizons"].get(h)
            row[f"Ø Rendite {h}T"] = f"{hd['avg_return']:+.2f}%" if hd else "—"
            row[f"Trefferquote {h}T"] = f"{hd['win_rate']:.0f}%" if hd else "—"
        rows.append(row)
    return pd.DataFrame(rows)


st.subheader("Nach Note")
grade_stats = report.stats_by(lambda o: o.grade)
if grade_stats:
    st.dataframe(_stats_table(grade_stats, "Note"), width="stretch", hide_index=True)
    st.caption(
        "Der Lackmustest des ganzen Systems: A sollte B schlagen und B sollte C schlagen. "
        "Tut es das über genügend Picks nicht, sind die Noten-Gewichte zu überarbeiten — "
        "das wäre ein wichtiges, kein peinliches Ergebnis."
    )
else:
    st.caption("Noch keine ausgelösten Picks mit messbarem Horizont.")

st.subheader("Nach Strategie")
strat_stats = report.stats_by(lambda o: o.strategy_name)
if strat_stats:
    st.dataframe(_stats_table(strat_stats, "Strategie"), width="stretch", hide_index=True)

st.subheader("Alle Picks im Detail")
detail_rows = []
for o in sorted(report.outcomes, key=lambda x: x.scan_day, reverse=True):
    row = {
        "Scan-Tag": o.scan_day, "Ticker": o.ticker, "Note": o.grade, "Strategie": o.strategy_name,
        "Entry": o.entry, "Ausgelöst": "✅ " + (o.trigger_day or "") if o.triggered else "❌ nie",
    }
    for h in HORIZONS:
        v = o.returns.get(h)
        row[f"{h}T"] = f"{v:+.2f}%" if v is not None else ("zu frisch" if o.triggered else "—")
    detail_rows.append(row)
st.dataframe(pd.DataFrame(detail_rows), width="stretch", hide_index=True)
