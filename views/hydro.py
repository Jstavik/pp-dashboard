import streamlit as st

from data.hydro import HYDRO_COUNTRY_NAMES
from charts.hydro import fig_hydro_main, fig_hydro_grid


def render_hydro_tab(df_hydro):
    if df_hydro.empty:
        st.warning(
            "Hydro data nejsou dostupná. "
            "Spusť GitHub Actions: Update gas history."
        )
    else:
        all_countries_h = sorted(df_hydro["country"].unique().tolist())
        all_years_h     = sorted(df_hydro["date"].dt.year.unique().tolist())

        # ── Hlavní graf ──────────────────────────────────────
        st.markdown("#### Hlavní graf")
        col1, col2 = st.columns(2)
        with col1:
            sel_country_h = st.selectbox(
                "Země",
                options=all_countries_h,
                format_func=lambda c: HYDRO_COUNTRY_NAMES.get(c, c),
                index=all_countries_h.index("NO")
                      if "NO" in all_countries_h else 0,
                key="hydro_country",
            )
        with col2:
            sel_years_h = st.multiselect(
                "Roky",
                options=all_years_h,
                default=all_years_h[-6:],
                key="hydro_years_main",
            )

        if sel_years_h:
            st.plotly_chart(
                fig_hydro_main(df_hydro, sel_country_h, sel_years_h),
                use_container_width=True,
            )

        st.markdown("---")

        # ── Grid 3×2 ─────────────────────────────────────────
        st.markdown("#### Přehled klíčových zemí")
        sel_years_hg = st.multiselect(
            "Roky (grid)",
            options=all_years_h,
            default=all_years_h[-6:],
            key="hydro_years_grid",
        )

        if sel_years_hg:
            st.plotly_chart(
                fig_hydro_grid(df_hydro, sel_years_hg),
                use_container_width=True,
            )
