import pandas as pd
import streamlit as st

from config import (
    ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS, LNG_DEFAULT_RANGE_DAYS,
    LNG_FALLBACK_RANGE_DAYS,
)
from data.lng import load_lng
from charts.lng import (
    fig_lng_sendout_timeseries,
    fig_lng_seasonality,
    fig_lng_inventory,
    fig_lng_monthly_bars,
    fig_lng_storage_seasonality,
)


def render_lng_tab(df_hist):
    st.checkbox(
        "📅 Načíst celou historii toků (víceleté srovnání sezonnosti) — pomalejší načtení",
        key="gas_lng_full",
        help="Bez zaškrtnutí se 'Roky (sezonnost)' níž a tlačítko "
             f"'Max' omezí na posledních {ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS} "
             "měsíců (rychlé). Netýká se zásob ALSI níž (ty jsou vždy celé).",
    )
    df_lng_alsi = load_lng()

    df_lng_flows = df_hist[
        (df_hist["adjacentSystemsKey"] == "LNG Terminals") &
        (df_hist["directionKey"] == "entry")
    ].copy() if not df_hist.empty else pd.DataFrame()

    all_countries_lng = sorted(
        df_lng_flows["countryLabel"].dropna().unique().tolist()
    ) if not df_lng_flows.empty else []

    max_date_lng = (
        df_lng_flows["date"]
        .dt.tz_convert("Europe/Prague").dt.date.max()
        if not df_lng_flows.empty else pd.Timestamp.now().date()
    )
    all_years_lng = sorted(
        df_lng_flows["date"].dt.tz_convert("Europe/Prague")
        .dt.year.unique().tolist()
    ) if not df_lng_flows.empty else []

    # ── Filtry ──────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        sel_countries_lng = st.multiselect(
            "🌍 Země (prázdné = EU součet)",
            options=all_countries_lng,
            default=[],
            key="lng_countries",
        )
    with col2:
        sel_agg_lng = st.radio(
            "📊 Agregace",
            options=["Denní", "Týdenní", "Měsíční"],
            horizontal=True,
            key="lng_agg",
        )
        sel_chart_lng = st.radio(
            "📈 Typ grafu",
            options=["Linie", "Plocha", "Sloupcový"],
            horizontal=True,
            key="lng_chart",
        )
    with col3:
        if "lng_dr" not in st.session_state:
            st.session_state["lng_dr"] = (
                (pd.Timestamp(max_date_lng) -
                 pd.Timedelta(days=LNG_DEFAULT_RANGE_DAYS)).date(),
                max_date_lng,
            )
        # "Max" potřebuje CELOU historii toků — stejný odložený
        # sentinel vzor jako tab_bar's "Maximum" výš (df_lng_flows
        # na TÉHLE rerun je pořád oknovaná, skutečné minimum se
        # dopočítá až na příští rerun, kdy je df_hist už plná).
        if st.session_state.get("lng_dr") == "PENDING_MAX":
            st.session_state["lng_dr"] = (
                df_lng_flows["date"].dt.tz_convert("Europe/Prague").dt.date.min(),
                max_date_lng,
            )
        qd_cols_lng = st.columns(5)
        for i, (lbl, delta) in enumerate(zip(
            ["Týden", "Měsíc", "3M", "Rok", "Max"],
            [7, 30, 90, 365, None]
        )):
            if qd_cols_lng[i].button(lbl, key=f"lng_qd_{lbl}"):
                if delta:
                    st.session_state["lng_dr"] = (
                        (pd.Timestamp(max_date_lng) -
                         pd.Timedelta(days=delta)).date(),
                        max_date_lng,
                    )
                else:
                    # NE "gas_lng_full" (to je klíč checkboxu výš —
                    # widget s tímhle klíčem se v tomhle běhu skriptu
                    # už vykreslil, Streamlit by na přepsání jeho
                    # session_state za během shodil
                    # StreamlitAPIException). Samostatný flag,
                    # sloučený s checkboxem až v _ENTSOG_FULL_FLAGS.
                    st.session_state["gas_lng_full_via_max"] = True
                    st.session_state["lng_dr"] = "PENDING_MAX"
                st.rerun()

        date_range_lng = st.date_input(
            "📆 Rozsah",
            value=st.session_state["lng_dr"],
            key="lng_daterange",
        )
        st.session_state["lng_dr"] = date_range_lng \
            if isinstance(date_range_lng, tuple) \
            else st.session_state["lng_dr"]

    if isinstance(date_range_lng, (list, tuple)) and len(date_range_lng) == 2:
        lng_from = pd.Timestamp(date_range_lng[0])
        lng_to   = pd.Timestamp(date_range_lng[1])
    else:
        lng_from = pd.Timestamp(max_date_lng) - pd.Timedelta(days=LNG_FALLBACK_RANGE_DAYS)
        lng_to   = pd.Timestamp(max_date_lng)

    st.markdown("---")

    # ── Graf 1 — měsíční Power BI styl ──────────────
    if not df_lng_flows.empty:
        st.plotly_chart(
            fig_lng_monthly_bars(df_lng_flows, sel_countries_lng),
            use_container_width=True,
        )
        st.markdown("---")

    # ── Graf 2 — časová osa ──────────────────────────
    if not df_lng_flows.empty:
        st.plotly_chart(
            fig_lng_sendout_timeseries(
                df_lng_flows,
                sel_countries_lng,
                lng_from, lng_to,
                sel_agg_lng,
                sel_chart_lng,
            ),
            use_container_width=True,
        )

        # ── Graf 2 — sezonnost ───────────────────────
        st.markdown("---")
        sel_years_lng = st.multiselect(
            "📅 Roky (sezonnost)",
            options=all_years_lng,
            default=all_years_lng[-5:] if all_years_lng else [],
            key="lng_years",
        )
        st.plotly_chart(
            fig_lng_seasonality(
                df_lng_flows,
                sel_countries_lng,
                sel_years_lng,
            ),
            use_container_width=True,
        )
    else:
        st.warning("ENTSO-G LNG data nejsou dostupná.")

    # ── Graf 3 — ALSI zásoby ─────────────────────────
    st.markdown("---")
    st.markdown("#### Zásoby LNG terminálů (GIE ALSI)")

    if df_lng_alsi.empty:
        st.info("ALSI data nejsou dostupná.")
    else:
        df_lng_alsi["gasDayStart"] = pd.to_datetime(
            df_lng_alsi["gasDayStart"])

        all_countries_alsi = sorted(
            df_lng_alsi["country_code"].dropna().unique().tolist()
        )
        all_years_alsi = sorted(
            df_lng_alsi["gasDayStart"].dt.year.unique().tolist()
        )

        col_a1, col_a2 = st.columns(2)
        with col_a1:
            sel_countries_alsi = st.multiselect(
                "🌍 Země (zásobníky)",
                options=all_countries_alsi,
                default=[],
                key="alsi_countries",
                help="Prázdné = všechny země",
            )
        with col_a2:
            sel_years_alsi = st.multiselect(
                "📅 Roky (sezonnost plnosti)",
                options=all_years_alsi,
                default=all_years_alsi[-5:],
                key="alsi_years",
            )

        df_alsi_filtered = (
            df_lng_alsi[
                df_lng_alsi["country_code"].isin(sel_countries_alsi)
            ] if sel_countries_alsi else df_lng_alsi
        )

        st.plotly_chart(
            fig_lng_inventory(df_alsi_filtered),
            use_container_width=True,
        )

        st.plotly_chart(
            fig_lng_storage_seasonality(
                df_alsi_filtered, sel_years_alsi
            ),
            use_container_width=True,
        )
