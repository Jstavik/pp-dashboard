import pandas as pd
import streamlit as st

from data.entsog_operational import load_eu_operational_open_ended, EU_COUNTRY_NAMES, active_capacity_dedup
from charts.capacity import fig_flow_stacked, fig_point_capacity


def render_gas_capacity_tab(df_hist):
    # Cascade: Země → Směr → (stacked Physical Flow, klikací legenda
    # pro izolaci bodu) → Bod → Operátor → vrstvy (checkbox/slider).
    # Kapacita/interrupce jedou na load_eu_operational_open_ended()
    # (celá Evropa, stejný zdroj jako panel Nominace) — NE na staré
    # per-bodové entsog_capacity.py/load_capacity(), ta pokrývala
    # jen ručně vybraný seznam hraničních bodů (GAS_KEY_POINTS), ne
    # celé země. Fyzický tok jede na df_hist (ENTSOG /aggregateddata,
    # stejný zdroj jako Mapa/Toky/Sezonnost výš, žádné nové načtení).
    df_flow_cap = df_hist[df_hist["indicator"] == "Physical Flow"] if not df_hist.empty else df_hist

    if df_flow_cap.empty:
        st.warning(
            "Data fyzických toků nejsou dostupná. "
            "Spusť GitHub Actions: Update gas history."
        )
    else:
        all_countries_cap = sorted(df_flow_cap["countryKey"].dropna().unique())

        col_country_cap, col_dir_cap = st.columns(2)
        with col_country_cap:
            sel_country_cap = st.selectbox(
                "🌍 Země",
                options=all_countries_cap,
                index=all_countries_cap.index("CZ") if "CZ" in all_countries_cap else 0,
                format_func=lambda c: EU_COUNTRY_NAMES.get(c, c),
                key="cap_country",
            )
        with col_dir_cap:
            sel_dir_cap = st.radio(
                "↕ Směr", ["entry", "exit"], horizontal=True, key="cap_dir",
            )

        cap_date_from = pd.Timestamp.now(tz="Europe/Prague").normalize() - pd.Timedelta(days=365)
        df_flow_sel = df_flow_cap[
            (df_flow_cap["countryKey"] == sel_country_cap)
            & (df_flow_cap["directionKey"] == sel_dir_cap)
            & (df_flow_cap["date"] >= cap_date_from)
        ]

        if df_flow_sel.empty:
            st.info(f"Pro {EU_COUNTRY_NAMES.get(sel_country_cap, sel_country_cap)} "
                    f"/ {sel_dir_cap} nejsou k dispozici žádná data fyzických toků.")
        else:
            st.plotly_chart(
                fig_flow_stacked(df_flow_sel, EU_COUNTRY_NAMES.get(sel_country_cap, sel_country_cap), sel_dir_cap),
                use_container_width=True,
                key="cap_flow_chart",
            )
            st.caption(
                "💡 Klikej v legendě, ať postupně skryješ body, dokud "
                "nezůstane jeden zajímavý — pak ho vyber níž pro detail "
                "kapacity (Streamlit se o klik v legendě samotný nedozví, "
                "výběr níž je nutný explicitně)."
            )

            all_pts_cap = sorted(df_flow_sel["pointsNames"].dropna().unique())
            sel_pt_cap = st.selectbox(
                "📍 Bod (pro detail kapacity)",
                options=all_pts_cap,
                key="cap_flow_point",
            )

            # ENTSOG u některých bodů publikuje "A|B" kombinovaný název
            # ve /aggregateddata (fyzické toky) — NIKDY se nerozděluje
            # ani nesčítá do jednoho bodu (fyzikálně odlišné body/kapacity),
            # ale kapacitní data (/operationaldata, pointLabel) je mají
            # rozlišené jednotlivě → uživatel musí explicitně vybrat, KTERÝ
            # z kombinovaných bodů chce pro kapacitní vrstvy.
            combined_pts = [p.strip() for p in sel_pt_cap.split("|")]
            if len(combined_pts) > 1:
                sel_single_point = st.selectbox(
                    "📍 Konkrétní fyzický bod (kombinovaný název v tocích)",
                    options=combined_pts,
                    key="cap_single_point",
                    help="Fyzické toky ENTSOG hlásí tenhle bod jako kombinaci "
                         "víc bodů — kapacitní data (jiný zdroj) je mají "
                         "rozlišené. Vyber, který z nich tě zajímá.",
                )
            else:
                sel_single_point = combined_pts[0]

            df_oe_cap_all = load_eu_operational_open_ended()
            df_oe_point_cap = df_oe_cap_all[
                (df_oe_cap_all["pointLabel"] == sel_single_point)
                & (df_oe_cap_all["directionKey"] == sel_dir_cap)
            ] if not df_oe_cap_all.empty else df_oe_cap_all

            st.markdown("---")

            if df_oe_point_cap.empty:
                st.info(
                    f"Pro bod '{sel_single_point}' nejsou v kapacitních "
                    f"datech žádní operátoři — název bodu se mezi zdroji "
                    f"(toky vs. kapacita) může lišit. Zobrazen je jen "
                    f"fyzický tok výš."
                )
            else:
                all_operators_cap = sorted(df_oe_point_cap["operatorLabel"].dropna().unique())
                sel_operator_cap = st.selectbox(
                    "🏢 Operátor", options=all_operators_cap, key="cap_operator",
                )
                df_oe_final_cap = df_oe_point_cap[
                    df_oe_point_cap["operatorLabel"] == sel_operator_cap
                ]

                col_l1, col_l2, col_l3 = st.columns(3)
                with col_l1:
                    show_technical_cap = st.checkbox(
                        "📏 Firm Technical (fyzický strop)", value=True, key="cap_show_technical",
                    )
                with col_l2:
                    show_available_cap = st.checkbox(
                        "🟢 Firm Available (co zbývá k rezervaci)", value=False,
                        key="cap_show_available",
                        help="Available = Technical − Booked (ENTSOG manuál) — "
                             "co ještě není zarezervované z fyzického stropu.",
                    )
                with col_l3:
                    show_interrupt_cap = st.checkbox(
                        "⚠️ Odstávky/interrupce", value=False, key="cap_show_interrupt",
                    )

                forward_days_cap = st.slider(
                    "📆 Výhled dopředu (dní)", min_value=0, max_value=90, value=30,
                    key="cap_forward_days",
                    help="Kapacitní rezervace se v datech objevují i pro budoucí "
                         "období (už dohodnuté, ale ještě nezačaté) — kolik dní "
                         "dopředu od dneška se má zahrnout.",
                )

                cap_target_dates = pd.date_range(
                    cap_date_from,
                    pd.Timestamp.now(tz="Europe/Prague").normalize() + pd.Timedelta(days=forward_days_cap),
                    freq="D",
                )

                capacity_layers_cap = {}
                if show_technical_cap:
                    sub_tech = df_oe_final_cap[df_oe_final_cap["indicator"] == "Firm Technical"]
                    capacity_layers_cap["Firm Technical"] = active_capacity_dedup(sub_tech, cap_target_dates)
                if show_available_cap:
                    sub_avail = df_oe_final_cap[df_oe_final_cap["indicator"] == "Firm Available"]
                    capacity_layers_cap["Firm Available"] = active_capacity_dedup(sub_avail, cap_target_dates)

                interruptions_cap = pd.DataFrame()
                if show_interrupt_cap:
                    is_interrupt = df_oe_final_cap["indicator"].str.contains(
                        "interruption", case=False, na=False)
                    interruptions_cap = df_oe_final_cap[
                        is_interrupt & (df_oe_final_cap["value_GWh"] > 0)
                    ].copy()
                    if not interruptions_cap.empty:
                        interruptions_cap["_upd"] = pd.to_datetime(
                            interruptions_cap["lastUpdateDateTime"], utc=True)
                        interruptions_cap = (
                            interruptions_cap
                            .sort_values("_upd")
                            .drop_duplicates(
                                subset=["periodFrom_dt", "periodTo_dt", "indicator"],
                                keep="last",
                            )
                        )

                flow_series_cap = (
                    df_flow_sel[df_flow_sel["pointsNames"] == sel_pt_cap]
                    .groupby("date")["value_GWh"].sum()
                )

                st.plotly_chart(
                    fig_point_capacity(
                        sel_single_point, sel_operator_cap, sel_dir_cap,
                        flow_series_cap, capacity_layers_cap, interruptions_cap,
                    ),
                    use_container_width=True,
                    key="cap_detail_chart",
                )
