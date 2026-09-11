import streamlit as st

from config import ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS
from charts.gas import fig_flow_seasonality


def render_gas_seasonality_tab(df_hist):
    st.checkbox(
        "📅 Načíst celou historii (víceleté srovnání) — pomalejší načtení",
        key="gas_seas_full",
        help="Bez zaškrtnutí se srovnání omezí na posledních "
             f"{ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS} měsíců (rychlé). "
             "Zaškrtnutím se na příštím načtení dotáhne celá historie "
             "od 2020 (pomalejší, ale umožní srovnání starších let).",
    )
    if df_hist.empty:
        st.warning("Data nejsou dostupná.")
    else:
        all_countries_s = sorted(df_hist["countryLabel"].dropna().unique())
        sel_countries_s = st.multiselect(
            "🌍 Země", all_countries_s, default=["Czechia"], key="seas_countries")
        df_s1 = df_hist[df_hist["countryLabel"].isin(sel_countries_s)] \
                if sel_countries_s else df_hist

        all_dir_s = sorted(df_s1["directionKey"].dropna().unique())
        sel_dir_s = st.multiselect(
            "↕ Směr", all_dir_s, default=all_dir_s, key="seas_dir")
        df_s2 = df_s1[df_s1["directionKey"].isin(sel_dir_s)] \
                if sel_dir_s else df_s1

        all_sys_s = sorted(df_s2["adjacentSystemsKey"].dropna().unique())
        sel_sys_s = st.multiselect(
            "🔧 Systém", all_sys_s, default=[], key="seas_sys",
            help="Prázdný = všechny")
        df_s3 = df_s2[df_s2["adjacentSystemsKey"].isin(sel_sys_s)] \
                if sel_sys_s else df_s2

        all_pts_s = sorted(df_s3["pointsNames"].dropna().unique())
        sel_pts_s = st.multiselect(
            "📍 Bod", all_pts_s, default=[], key="seas_pts",
            help="Prázdný = všechny")
        df_s4 = df_s3[df_s3["pointsNames"].isin(sel_pts_s)] \
                if sel_pts_s else df_s3

        all_years_s = sorted(df_hist["date"].dt.year.unique())
        sel_years_s = st.multiselect(
            "📅 Roky", all_years_s, default=all_years_s[-5:], key="seas_years")

        chart_type_s = st.radio(
            "Typ grafu", ["Linie", "Plocha", "Sloupcový"],
            horizontal=True, key="seas_chart_type")

        st.markdown("---")
        st.plotly_chart(
            fig_flow_seasonality(df_s4, [], [], [], [], sel_years_s, chart_type_s),
            use_container_width=True,
        )
