import pandas as pd
import streamlit as st

from config import (
    COUNTRIES, COUNTRY_TIMEZONES, COUNTRY_NAMES, psr_lookup,
    OUTAGES_D7_COMPARISON_DAYS, OUTAGES_DELTA_MAX_WINDOW_DAYS,
)
from data.generation import load_generation, available_source_types
from data.outages import load_outages, load_outages_snapshot, list_available_snapshot_dates
from charts.electricity_generation import fig_generation_stacked, fig_generation_ytd, fig_seasonality
from charts.electricity_outages import fig_outages_table, fig_outlook, fig_outages_delta


def render_outages_tab():
    country = st.radio(
        "Země", COUNTRIES, horizontal=True,
        format_func=lambda c: COUNTRY_NAMES.get(c, c), key="eu_country",
    )
    sekce = st.radio("Sekce", ["Výroba", "Odstávky"], horizontal=True, key="eu_sekce")

    if sekce == "Výroba":
        # load_generation() se volá AŽ PO výběru Pohledu a jeho
        # parametrů (den/od data), s odpovídajícím start/end — Teď/
        # denní a Letos nepotřebují celou historii, jen Sezonnost
        # (víceletá srovnávací sezonnost) ano. Změřeno naživo na
        # DE_LU: celá historie 362.5MB → jen dnešní den 0.02MB,
        # jen letos 34.4MB.
        pohled = st.radio(
            "Pohled", ["Teď/denní", "Letos", "Sezonnost"],
            horizontal=True, key=f"gen_pohled_{country}",
        )
        tz = COUNTRY_TIMEZONES[country]

        if pohled == "Teď/denní":
            default_day = pd.Timestamp.now(tz=tz).date()
            sel_day = st.date_input("Den", value=default_day, key=f"gen_day_{country}")
            day_ts = pd.Timestamp(sel_day, tz=tz).tz_convert("UTC")
            with st.spinner(f"Načítám výrobu — {COUNTRY_NAMES.get(country, country)}..."):
                df_gen = load_generation(country, start=day_ts, end=day_ts + pd.Timedelta(days=1))
            st.plotly_chart(fig_generation_stacked(df_gen, day_ts), use_container_width=True,
                            config={"displayModeBar": False})

        elif pohled == "Letos":
            default_start = pd.Timestamp(year=pd.Timestamp.now(tz=tz).year, month=1, day=1, tz=tz).date()
            sel_start = st.date_input("Od data", value=default_start, key=f"gen_start_{country}")
            start_ts = pd.Timestamp(sel_start, tz=tz).tz_convert("UTC")
            with st.spinner(f"Načítám výrobu — {COUNTRY_NAMES.get(country, country)}..."):
                df_gen = load_generation(country, start=start_ts)
            st.plotly_chart(fig_generation_ytd(df_gen, start_ts), use_container_width=True,
                            config={"displayModeBar": False})

        else:  # Sezonnost — potřebuje celou historii (víceleté srovnání)
            with st.spinner(f"Načítám výrobu — {COUNTRY_NAMES.get(country, country)}..."):
                df_gen = load_generation(country)
            sources = available_source_types(df_gen)
            if sources:
                labels = {s: psr_lookup(s)[0] for s in sources}
                default_idx = sources.index("Nuclear") if "Nuclear" in sources else 0
                sel_source = st.selectbox(
                    "Zdroj", sources, index=default_idx,
                    format_func=lambda s: labels[s], key=f"gen_source_{country}",
                )
                chart_type = st.radio(
                    "Typ grafu", ["Linie", "Plocha", "Sloupcový"],
                    horizontal=True, key=f"gen_chart_type_{country}",
                )
                st.plotly_chart(fig_seasonality(df_gen, sel_source, chart_type),
                                use_container_width=True, config={"displayModeBar": False})
            else:
                st.info("Zatím nejsou k dispozici data o výrobě.")

    else:  # Odstávky
        with st.spinner(f"Načítám odstávky — {COUNTRY_NAMES.get(country, country)}..."):
            data_out = load_outages(country)

        active = data_out["active"]
        n_active     = len(active)
        unavail_now  = float(active["unavail_mw"].sum()) if not active.empty else 0.0
        n_unplanned  = (int(active["businesstype"].str.contains("Unplanned", na=False).sum())
                          if not active.empty else 0)
        n_types      = active["plant_type"].nunique() if not active.empty else 0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Aktivních odstávek", n_active)
        k2.metric("Nedostupno teď", f"{unavail_now:,.0f} MW")
        k3.metric("Unplanned", n_unplanned)
        k4.metric("Zasažené typy zdrojů", n_types)

        st.markdown('<div class="section-title">Aktivní odstávky</div>', unsafe_allow_html=True)
        plant_types_available = sorted(active["plant_type"].unique().tolist()) if not active.empty else []
        sel_types = st.multiselect(
            "Filtr typu zdroje", plant_types_available, default=plant_types_available,
            format_func=lambda pt: psr_lookup(pt)[0], key=f"out_filter_{country}",
        )
        st.dataframe(fig_outages_table(data_out, sel_types or None),
                    use_container_width=True, hide_index=True)

        st.markdown('<div class="section-title">Výhled</div>', unsafe_allow_html=True)
        days_forward = st.slider("Dní dopředu", min_value=7, max_value=500, value=30,
                                  key=f"out_days_{country}")
        st.plotly_chart(fig_outlook(data_out, days_forward), use_container_width=True,
                        config={"displayModeBar": False})

        st.markdown('<div class="section-title">Δ Srovnání s dřívějším snapshotem</div>',
                    unsafe_allow_html=True)
        available_dates = list_available_snapshot_dates(country)
        if not available_dates:
            st.caption("Zatím není k dispozici žádný uložený snapshot odstávek pro srovnání — "
                       "objeví se po prvním scheduled běhu update_outages() (viz "
                       "scripts/update_gas_history.py).")
        else:
            oldest = available_dates[0]
            compare_date = st.selectbox(
                "Porovnat s", available_dates, index=0,
                format_func=lambda d: d.strftime("%d.%m.%Y"), key=f"out_compare_{country}",
            )
            today_utc = pd.Timestamp.now(tz="UTC").normalize()
            days_back = (today_utc - compare_date).days
            df_now = load_outages_snapshot(country, days_back=0)
            df_compare = load_outages_snapshot(country, days_back=days_back)
            st.plotly_chart(
                fig_outages_delta(df_now, df_compare, window_days=min(days_forward, OUTAGES_DELTA_MAX_WINDOW_DAYS),
                                  compare_days_back=days_back),
                use_container_width=True, config={"displayModeBar": False},
            )
            full_available = oldest + pd.Timedelta(days=OUTAGES_D7_COMPARISON_DAYS)
            st.caption(f"Historie odstávek dostupná od {oldest.strftime('%d.%m.%Y')} — "
                       f"plné D-7 srovnání bude možné od {full_available.strftime('%d.%m.%Y')}.")
