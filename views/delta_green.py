import streamlit as st

from data.deltagreen import fetch_deltagreen
from charts.generation import fig_deltagreen


def render_delta_green_tab():
    dg_key = st.session_state.get("dg_api_key", "").strip()
    if not dg_key:
        st.info("Zadejte Delta Green API klíč v levém panelu (⚙️ Nastavení).")
    else:
        with st.spinner("Načítám Delta Green…"):
            try:
                df1_dg, df2_dg = fetch_deltagreen(dg_key)
                st.plotly_chart(fig_deltagreen(df1_dg, df2_dg), use_container_width=True,
                                config={"displayModeBar": False})
                last2 = df2_dg.dropna(subset=["upPowerKW","downBatteryPowerKW",
                                               "downSolarCurtailmentPowerKW"]).iloc[-1]
                last1 = df1_dg.dropna(subset=["batteryPowerKW","consumptionPowerKW",
                                               "photovoltaicPowerKW","gridPowerKW"]).iloc[-1]
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Baterie",      f"{float(last1['batteryPowerKW']):+.0f} kW")
                k2.metric("Fotovoltaika", f"{float(last1['photovoltaicPowerKW']):.0f} kW")
                k3.metric("Max UP",       f"{float(last2['upPowerKW']):.0f} kW")
                total_down = float(last2["downBatteryPowerKW"]) + float(last2["downSolarCurtailmentPowerKW"])
                k4.metric("Max DOWN",     f"{total_down:.0f} kW")
            except Exception as e:
                st.error(f"Delta Green nedostupný: {e}")
