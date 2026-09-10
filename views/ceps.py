import streamlit as st

from data.ceps import fetch_ceps_all
from charts.imbalance import fig_ceps_dashboard


def render_ceps_tab():
    st.markdown(
        "Zdroj: **ČEPS a.s.** — data jsou anonymní, bez autentizace. "
        "Zpoždění ~1–5 minut. Výroba podle zdroje má granularitu 15 min, "
        "ostatní data jsou minutová."
    )
    with st.spinner("Načítám ČEPS real-time data..."):
        ceps_data = fetch_ceps_all()

    st.plotly_chart(
        fig_ceps_dashboard(ceps_data),
        use_container_width=True,
        config={"displayModeBar": False},
    )

    df_i = ceps_data["imbal"]
    df_f = ceps_data["freq"]
    df_l = ceps_data["load"]
    c1, c2, c3, c4 = st.columns(4)
    if not df_i.empty:
        last_imb = float(df_i.iloc[-1, 0])
        c1.metric("Odchylka", f"{last_imb:+.1f} MW",
                  delta="Surplus" if last_imb >= 0 else "Deficit")
    if not df_f.empty:
        last_hz = float(df_f.iloc[-1, 0])
        c2.metric("Frekvence", f"{last_hz:.3f} Hz",
                  delta=f"{last_hz-50:.3f} Hz")
    if not df_l.empty and "Load [MW]" in df_l.columns:
        last_load = float(df_l["Load [MW]"].iloc[-1])
        c3.metric("Zatížení", f"{last_load:,.0f} MW")
    if not ceps_data["cb"].empty and "Net Export (MW)" in ceps_data["cb"].columns:
        net = float(ceps_data["cb"]["Net Export (MW)"].iloc[-1])
        c4.metric("Net Export", f"{net:+.0f} MW",
                  delta="export" if net >= 0 else "import")
