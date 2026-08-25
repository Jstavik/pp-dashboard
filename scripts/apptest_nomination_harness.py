"""AppTest harness — izolovaně cvičí přesně tu samou logiku jako tab
'Nominace' v app.py (data/entsog_operational.load_cz_operational +
charts/entsog_operational.fig_cz_operational), bez zbytku appky (ta se
v tomhle prostředí nedá spustit celá — config.py na řádku importu padá
na chybějící secrets.toml/ENTSOE_TOKEN, nesouvisí s touhle featurou).

Widget kód níž je záměrně identický s blokem `with tab_nom:` v app.py —
udržuj oba v sync při úpravách UI.
"""
import sys
sys.path.insert(0, ".")

from datetime import timedelta
import streamlit as st

from data.entsog_operational import (
    load_cz_operational, country_from_operator_key, EU_COUNTRY_NAMES,
)
from charts.entsog_operational import fig_cz_operational

df_nom = load_cz_operational()

if df_nom.empty:
    st.warning("ENTSOG EU Nomination/Renomination data nejsou dostupná.")
else:
    df_nom = df_nom.copy()
    df_nom["country"] = df_nom["operatorKey"].apply(country_from_operator_key)

    all_countries_nom = sorted(
        df_nom.loc[df_nom["pointLabel"].notna() & (df_nom["country"] != "??"), "country"]
        .unique()
    )

    col_country, col_ind = st.columns(2)
    with col_country:
        sel_country_nom = st.selectbox(
            "🌍 Země",
            options=all_countries_nom,
            index=all_countries_nom.index("CZ") if "CZ" in all_countries_nom else 0,
            format_func=lambda c: EU_COUNTRY_NAMES.get(c, c),
            key="nom_country",
        )
    with col_ind:
        all_indicators_nom = sorted(df_nom["indicator"].dropna().unique())
        sel_ind_nom = st.selectbox(
            "📊 Indikátor",
            options=all_indicators_nom,
            key="nom_indicator",
        )

    df_country_nom = df_nom[df_nom["country"] == sel_country_nom]
    all_points_nom = sorted(df_country_nom["pointLabel"].dropna().unique())

    point_key = f"nom_point__{sel_country_nom}"
    default_point_idx = (
        all_points_nom.index("VIP Brandov")
        if sel_country_nom == "CZ" and "VIP Brandov" in all_points_nom
        else 0
    )
    sel_point_nom = st.selectbox(
        "📍 Bod",
        options=all_points_nom,
        index=default_point_idx,
        key=point_key,
    )

    col_ct, col_dr = st.columns([1, 2])
    with col_ct:
        chart_type_nom = st.radio(
            "Typ grafu", ["Linie", "Plocha", "Sloupcový"],
            horizontal=True, key="nom_chart_type")
    with col_dr:
        min_date_nom = df_nom["periodFrom_dt"].min()
        max_date_nom = df_nom["periodFrom_dt"].max()
        default_from = max(min_date_nom, max_date_nom - timedelta(days=365))
        date_range_nom = st.date_input(
            "📆 Rozsah",
            value=(default_from, max_date_nom),
            min_value=min_date_nom,
            max_value=max_date_nom,
            key="nom_daterange",
        )

    if isinstance(date_range_nom, (list, tuple)) and len(date_range_nom) == 2:
        d_from_nom, d_to_nom = date_range_nom
    else:
        d_from_nom, d_to_nom = min_date_nom, max_date_nom

    st.plotly_chart(
        fig_cz_operational(
            df_country_nom, sel_point_nom, sel_ind_nom,
            d_from_nom, d_to_nom, chart_type_nom,
        ),
        use_container_width=True,
    )

    st.markdown("##### Posledních 30 dní — surové hodnoty")
    sub_table_nom = (
        df_country_nom[
            (df_country_nom["pointLabel"] == sel_point_nom) &
            (df_country_nom["indicator"] == sel_ind_nom)
        ]
        .sort_values("periodFrom_dt", ascending=False)
        .head(60)
    )
    st.dataframe(
        sub_table_nom[
            ["periodFrom_dt", "directionKey", "value_GWh",
             "operatorLabel", "unit"]
        ].rename(columns={
            "periodFrom_dt": "Datum",
            "directionKey": "Směr",
            "value_GWh": "Hodnota [GWh/d]",
            "operatorLabel": "Operátor",
            "unit": "Jednotka",
        }),
        use_container_width=True,
        hide_index=True,
    )
