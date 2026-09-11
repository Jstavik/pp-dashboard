from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import ENTSOG_NOMINATION_DEFAULT_MONTHS
from data.entsog_operational import (
    load_cz_operational, load_eu_operational_open_ended, EU_COUNTRY_NAMES,
    HISTORY_START, NOMINATION_CHART_INDICATORS, QUALITY_INDICATORS,
)
from charts.entsog_operational import fig_cz_operational


def render_gas_nominations_tab():
    # Kolik měsíců historie ENTSOG operational vrstvy se vůbec
    # NAČTE do paměti (viz data/entsog_operational.py::load_eu_operational)
    # — celá historie od 2020 je ~2GB v paměti, na Streamlit Cloud
    # free tier (~1GB) by to spadlo. Horní mez slideru = počet
    # měsíců od HISTORY_START dopočtený za běhu, ne pevné číslo —
    # na maximu tak slider přirozeně vybere celou dostupnou historii.
    today_nom = date.today()
    months_since_start_nom = (
        (today_nom.year - HISTORY_START.year) * 12
        + (today_nom.month - HISTORY_START.month) + 1
    )
    window_months_nom = st.slider(
        "📆 Historie (měsíců zpět)",
        min_value=1, max_value=months_since_start_nom,
        value=min(ENTSOG_NOMINATION_DEFAULT_MONTHS, months_since_start_nom),
        key="nom_window_months",
        help="Kolik měsíců zpět se má načíst do paměti. Menší okno = "
             "rychlejší načtení a míň paměti; na maximu se natáhne "
             "celá dostupná historie.",
    )
    load_from_nom = (pd.Timestamp(today_nom) - pd.DateOffset(months=window_months_nom)).date()
    df_nom = load_cz_operational(date_from=load_from_nom)

    if df_nom.empty:
        st.warning(
            "ENTSOG EU Nomination/Renomination data nejsou dostupná. "
            "Spusť GitHub Actions: Update gas history."
        )
    else:
        # country (odvozeno z operatorKey ISO prefixu) je už
        # sloupec v cachovaných datech — viz
        # data/entsog_operational.py::load_eu_operational().
        # ŽÁDNÝ .copy()/přepočet tady — na 1.5M řádcích to dřív
        # vedlo k ArrayMemoryError na konsolidaci object bloku
        # (ověřeno naživo). country se používá k rozlišení bodů
        # jako Cieszyn, co hlásí OBĚ strany hranice pod STEJNÝM
        # pointLabel, různým operatorKey — proto je země první
        # filtr, ne kosmetický krok.

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
            # Jen Nomination/Renomination — fig_cz_operational je
            # navržená pro denní Entry/Exit graf, ne pro kapacitní/
            # interrupční/kvalitní indikátory (ty mají vlastní
            # sekce níž, GCV/Wobbe navíc jinou fyzikální veličinu
            # než kWh/d). df_nom sám o sobě obsahuje VŠECH 13
            # indikátorů v okně — jen nabídka dropdownu je užší.
            available_ind_set_nom = set(df_nom["indicator"].dropna().unique())
            all_indicators_nom = [
                i for i in NOMINATION_CHART_INDICATORS if i in available_ind_set_nom
            ]
            sel_ind_nom = st.selectbox(
                "📊 Indikátor",
                options=all_indicators_nom,
                key="nom_indicator",
            )

        df_country_nom = df_nom[df_nom["country"] == sel_country_nom]
        all_points_nom = sorted(df_country_nom["pointLabel"].dropna().unique())

        # Klíč widgetu obsahuje vybranou zemi — bez tohohle by
        # přepnutí země mohlo Streamlitu poslat starou hodnotu
        # bodu z JINÉ země, co v nové nabídce vůbec není (crash
        # "value not in options"). S per-country klíčem má každá
        # země svou vlastní zapamatovanou volbu, bez kolize.
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
        df_point_nom = df_country_nom[df_country_nom["pointLabel"] == sel_point_nom]

        col_ct, col_dr = st.columns([1, 2])
        with col_ct:
            chart_type_nom = st.radio(
                "Typ grafu", ["Linie", "Plocha", "Sloupcový"],
                horizontal=True, key="nom_chart_type")
        with col_dr:
            min_date_nom = df_nom["periodFrom_dt"].min()
            max_date_nom = df_nom["periodFrom_dt"].max()
            default_from = max(
                min_date_nom, max_date_nom - timedelta(days=365)
            )
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
                df_point_nom, sel_point_nom, sel_ind_nom,
                d_from_nom, d_to_nom, chart_type_nom,
            ),
            use_container_width=True,
        )

        st.markdown("##### Posledních 30 dní — surové hodnoty")
        sub_table_nom = (
            df_point_nom[df_point_nom["indicator"] == sel_ind_nom]
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

        st.markdown("---")

        # Kapacita/interrupce žijí ve VLASTNÍM, vždy neomezeném
        # (bez date-okna) načtení — viz
        # data/entsog_operational.py::load_eu_operational_open_ended.
        # Jejich periodFrom_dt nese validity okno klidně z roku
        # 2013 (ověřeno naživo), takže by je "Historie (měsíců
        # zpět)" slider výš nesprávně vyřadil, i když jsou
        # AKTUÁLNĚ platné. Malý objem dat (~30MB za celou Evropu,
        # ověřeno), takže bezpečné načíst vždy celé.
        df_oe_nom = load_eu_operational_open_ended()
        if df_oe_nom.empty:
            df_oe_point_nom = df_oe_nom
        else:
            df_oe_point_nom = df_oe_nom[
                (df_oe_nom["country"] == sel_country_nom)
                & (df_oe_nom["pointLabel"] == sel_point_nom)
            ]
        st.markdown("##### 📦 Kapacita — aktuální a budoucí rezervace")
        if df_oe_point_nom.empty:
            st.caption("Bod nemá žádná kapacitní ani interrupční data.")
        else:
            is_interruption_oe = df_oe_point_nom["indicator"].str.contains(
                "interruption", case=False, na=False
            )
            # periodTo_dt >= dnes, BEZ podmínky na periodFrom_dt — už
            # dohodnuté, ale ještě NEZAČATÉ budoucí rezervace (viz
            # periodFrom_dt v budoucnu) patří do "dopředného" pohledu
            # stejně jako právě aktivní. Stejný vzor jako Interrupce
            # níž — ověřeno naživo na VIP Brandov: bez tyhle úpravy by
            # se skryly reálné budoucí rezervace (např. Firm Booked
            # 2026-09-02 → 2026-10-01, dnešek 2026-09-01).
            df_cap_nom = df_oe_point_nom[
                ~is_interruption_oe
                & (df_oe_point_nom["periodTo_dt"] >= today_nom)
            ].sort_values(["periodFrom_dt", "indicator", "directionKey"])
            if df_cap_nom.empty:
                st.caption("Žádná aktuální ani budoucí kapacitní rezervace pro tenhle bod.")
            else:
                st.dataframe(
                    df_cap_nom[
                        ["indicator", "directionKey", "periodFrom_dt",
                         "periodTo_dt", "value_GWh"]
                    ].rename(columns={
                        "indicator": "Typ",
                        "directionKey": "Směr",
                        "periodFrom_dt": "Od",
                        "periodTo_dt": "Do",
                        "value_GWh": "Hodnota [GWh/d]",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

        st.markdown("##### ⚠️ Interrupce — aktivní a plánované")
        if df_oe_point_nom.empty:
            st.caption("Bod nemá žádná kapacitní ani interrupční data.")
        else:
            is_interruption_oe = df_oe_point_nom["indicator"].str.contains(
                "interruption", case=False, na=False
            )
            df_int_nom = df_oe_point_nom[
                is_interruption_oe
                & (df_oe_point_nom["periodTo_dt"] >= today_nom)
            ].sort_values("periodFrom_dt")
            if df_int_nom.empty:
                st.caption("Žádné aktivní ani plánované interrupce pro tenhle bod.")
            else:
                st.dataframe(
                    df_int_nom[
                        ["indicator", "directionKey", "periodFrom_dt",
                         "periodTo_dt", "value_GWh"]
                    ].rename(columns={
                        "indicator": "Typ",
                        "directionKey": "Směr",
                        "periodFrom_dt": "Od",
                        "periodTo_dt": "Do",
                        "value_GWh": "Hodnota [GWh/d]",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

        st.markdown("##### 🧪 Kvalita plynu")
        # GCV/Wobbe jsou HISTORY_INDICATORS — v df_point_nom už
        # jsou (df_nom nese všech 13 indikátorů v okně, jen
        # dropdown výš je zúžený na Nomination/Renomination),
        # žádné extra načtení netřeba.
        available_quality_nom = [
            q for q in QUALITY_INDICATORS
            if q in set(df_point_nom["indicator"].dropna().unique())
        ]
        if not available_quality_nom:
            st.caption("Bod nehlásí GCV ani Wobbe Index.")
        else:
            sel_quality_nom = st.radio(
                "Ukazatel", available_quality_nom, horizontal=True,
                key=f"nom_quality__{sel_country_nom}__{sel_point_nom}",
            )
            st.plotly_chart(
                fig_cz_operational(
                    df_point_nom, sel_point_nom, sel_quality_nom,
                    d_from_nom, d_to_nom, chart_type_nom,
                ),
                use_container_width=True,
            )
