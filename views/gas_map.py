from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import storage_color
from charts.gas import fig_gas_map


def render_gas_map_tab(df_hist, df_gassco, df_gie):
    if df_hist.empty:
        st.warning("Data nejsou dostupná.")
    else:
        st.plotly_chart(
            fig_gas_map(df_hist, df_gassco=df_gassco),
            use_container_width=True,
            config={"displayModeBar": True, "scrollZoom": True},
        )
        st.caption(
            f"Data ENTSO-G: D-2 ({date.today() - timedelta(days=2)}) | "
            f"Norské nominace: live (realTimeAtom.xml)"
        )

        df_gie_map = df_gie.copy()
        if not df_gie_map.empty:
            df_gie_map["gasDayStart"] = pd.to_datetime(
                df_gie_map["gasDayStart"], errors="coerce")
            last_gie_map = (df_gie_map
                            .dropna(subset=["gasDayStart"])
                            .sort_values("gasDayStart")
                            .groupby("country_code").last()
                            .reset_index())
            last_gie_map = last_gie_map[
                last_gie_map["country_code"] != "EU"
            ].copy().sort_values("country_code")

            st.markdown("#### Zásobníky plynu — aktuální stav")

            cols = st.columns(len(last_gie_map))
            for i, (_, row) in enumerate(last_gie_map.iterrows()):
                cc    = row["country_code"]
                full  = float(str(row.get("full", "0")).replace(",", "."))
                twh   = float(row.get("gasInStorage", 0))
                inj   = float(row.get("injection", 0))
                with_ = float(row.get("withdrawal", 0))
                net   = inj - with_
                net_str = f"+{net:.0f}" if net >= 0 else f"{net:.0f}"
                icon  = {"#C62828": "🔴", "#FF8F00": "🟠",
                         "#1565C0": "🔵", "#2E7D32": "🟢"}[storage_color(full)]
                cols[i].metric(
                    label=f"{icon} {cc}",
                    value=f"{full:.1f}%",
                    delta=f"{net_str} GWh/d",
                )
                cols[i].caption(f"{twh:.1f} TWh")
