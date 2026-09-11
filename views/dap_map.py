from datetime import date

import pandas as pd
import streamlit as st

from data.dap_europe import load_dap_europe_all, dap_europe_last_write
from charts.dap_map import fig_dap_map


def render_dap_map_tab():
    df_dap_all = load_dap_europe_all()
    if df_dap_all.empty:
        st.info("Data DAP Mapa nejsou k dispozici. "
                "Spusťte GitHub Actions pro stažení.")
    else:
        available_dap_dates = sorted(df_dap_all["date"].unique(), reverse=True)
        latest_dap_date = available_dap_dates[0]
        sel_dap_date = st.selectbox(
            "📅 Den dodávky",
            options=available_dap_dates,
            format_func=lambda d: pd.Timestamp(d).strftime("%d.%m.%Y"),
            key="dap_mapa_date",
        )

        last_write = dap_europe_last_write()
        last_write_str = (
            last_write.strftime("%d.%m.%Y %H:%M CET/CEST")
            if last_write is not None else "neznámo"
        )
        today_dap = date.today()
        if today_dap not in available_dap_dates:
            st.warning(
                f"Pro {today_dap.strftime('%d.%m.%Y')} zatím nejsou data — "
                f"poslední dostupný den: {pd.Timestamp(latest_dap_date).strftime('%d.%m.%Y')}."
            )
        st.caption(f"📥 Poslední úspěšné stažení: {last_write_str}")

        df_dap = df_dap_all[df_dap_all["date"] == sel_dap_date]
        st.plotly_chart(
            fig_dap_map(df_dap),
            use_container_width=True,
            key="dap_mapa_chart",
            config={
                "displayModeBar": True,
                "scrollZoom": True,
                "toImageButtonOptions": {
                    "format": "png",
                    "filename": "dap_mapa_evropa",
                    "height": 800,
                    "width": 1200,
                    "scale": 2,
                },
            },
        )
        st.caption(
            f"Zobrazeno: {pd.Timestamp(sel_dap_date).strftime('%d.%m.%Y')}  |  "
            "Zdroj: ENTSO-E Transparency Platform  |  "
            "Base | Peak = denní průměr EUR/MWh  |  "
            "Δ = DoD změna"
        )
