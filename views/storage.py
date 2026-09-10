import pandas as pd
import streamlit as st

from data.gie import VARIABLES
from charts.storage import fig_storage_main, fig_storage_grid


def render_storage_tab(df_gie):
    df_gie_stor = df_gie.copy()

    if df_gie_stor.empty:
        st.warning(
            "GIE data nejsou dostupná. "
            "Spusť GitHub Actions: Update gas history."
        )
    else:
        df_gie_stor["gasDayStart"] = pd.to_datetime(df_gie_stor["gasDayStart"])
        all_years = sorted(
            df_gie_stor["gasDayStart"].dt.year.unique().tolist()
        )
        all_countries = sorted(
            df_gie_stor["country_code"].unique().tolist()
        )

        # ── Hlavní graf — filtry ──────────────────────────
        st.markdown("#### Hlavní graf")
        col1, col2, col3 = st.columns(3)
        with col1:
            sel_country_main = st.selectbox(
                "Země", all_countries,
                index=all_countries.index("CZ")
                      if "CZ" in all_countries else 0,
                key="stor_country_main",
            )
        with col2:
            sel_var_main = st.selectbox(
                "Proměnná",
                options=list(VARIABLES.keys()),
                format_func=lambda k: VARIABLES[k][0],
                key="stor_var_main",
            )
        with col3:
            sel_years_main = st.multiselect(
                "Roky",
                options=all_years,
                default=all_years[-5:],
                key="stor_years_main",
            )

        if sel_years_main:
            st.plotly_chart(
                fig_storage_main(
                    df_gie_stor,
                    sel_country_main,
                    sel_var_main,
                    sel_years_main,
                ),
                use_container_width=True,
            )

        st.markdown("---")

        # ── Grid 3×2 — filtry ────────────────────────────
        st.markdown("#### Přehled zemí")
        col_v, col_y = st.columns(2)
        with col_v:
            sel_var_grid = st.selectbox(
                "Proměnná",
                options=list(VARIABLES.keys()),
                format_func=lambda k: VARIABLES[k][0],
                key="stor_var_grid",
            )
        with col_y:
            sel_years_grid = st.multiselect(
                "Roky",
                options=all_years,
                default=all_years[-5:],
                key="stor_years_grid",
            )

        if sel_years_grid:
            st.plotly_chart(
                fig_storage_grid(
                    df_gie_stor,
                    sel_var_grid,
                    sel_years_grid,
                ),
                use_container_width=True,
            )
