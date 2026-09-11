import pandas as pd
import streamlit as st

from config import GAS_FLOWS_DEFAULT_RANGE_DAYS
from charts.gas import fig_flow_timeseries


def render_gas_flows_tab(df_hist):
    if df_hist.empty:
        st.warning("Historická data ENTSO-G nejsou dostupná.")
    else:
        # ── Kaskádové filtry ────────────────────────────
        df_hist["date"] = pd.to_datetime(df_hist["date"], utc=True)

        # 1. Země
        all_countries = sorted(df_hist["countryLabel"].dropna().unique())
        sel_countries = st.multiselect(
            "🌍 Země", all_countries,
            default=["Czechia"],
            key="gas_countries",
        )

        df_f1 = df_hist[df_hist["countryLabel"].isin(sel_countries)] \
                if sel_countries else df_hist

        # 2. Směr
        all_directions = sorted(df_f1["directionKey"].dropna().unique())
        sel_directions = st.multiselect(
            "↕ Směr", all_directions,
            default=all_directions,
            key="gas_directions",
        )

        df_f2 = df_f1[df_f1["directionKey"].isin(sel_directions)] \
                if sel_directions else df_f1

        # 3. Systém
        all_systems = sorted(df_f2["adjacentSystemsKey"].dropna().unique())
        sel_systems = st.multiselect(
            "🔧 Systém", all_systems,
            default=[],
            key="gas_systems",
            help="Prázdný výběr = všechny systémy",
        )

        df_f3 = df_f2[df_f2["adjacentSystemsKey"].isin(sel_systems)] \
                if sel_systems else df_f2

        # 4. Bod
        all_points = sorted(df_f3["pointsNames"].dropna().unique())
        sel_points = st.multiselect(
            "📍 Bod", all_points,
            default=[],
            key="gas_points",
            help="Prázdný výběr = všechny body",
        )

        df_f4 = df_f3[df_f3["pointsNames"].isin(sel_points)] \
                if sel_points else df_f3

        # 5. Typ grafu + rychlé datum
        col_ct, col_qd = st.columns([1, 2])
        with col_ct:
            chart_type = st.radio(
                "Typ grafu", ["Linie", "Plocha", "Sloupcový"],
                horizontal=True, key="gas_chart_type")
        with col_qd:
            max_date = df_hist["date"].dt.tz_localize(None).max()
            if "gas_dr" not in st.session_state:
                st.session_state["gas_dr"] = (
                    (max_date - pd.Timedelta(days=GAS_FLOWS_DEFAULT_RANGE_DAYS)).date(),
                    max_date.date(),
                )
            # "Maximum" potřebuje CELOU historii (viz gas_bar_full
            # flag u load_entsog_history výš), ale df_hist na TÉHLE
            # rerun (kliknutí) je pořád ta stará (oknovaná) verze —
            # nastavit skutečné datum minima teď by bylo špatně.
            # Místo toho jen nastav flag + sentinel a nech
            # st.rerun() proběhnout — na PŘÍŠTÍ rerun je df_hist už
            # plná a sentinel se rozřeší níž na skutečné minimum.
            if st.session_state.get("gas_dr") == "PENDING_MAX":
                st.session_state["gas_dr"] = (
                    df_hist["date"].dt.tz_convert("Europe/Prague").dt.date.min(),
                    max_date.date(),
                )
            st.markdown("**Rychlý výběr období:**")
            qd_cols = st.columns(6)
            labels  = ["Týden","Měsíc","Kvartál","Půlrok","Rok","Maximum"]
            deltas  = [7, 30, 90, 182, 365, None]
            for i, (lbl, delta) in enumerate(zip(labels, deltas)):
                if qd_cols[i].button(lbl, key=f"gas_qd_{lbl}"):
                    if delta:
                        st.session_state["gas_dr"] = (
                            (max_date - pd.Timedelta(days=delta)).date(),
                            max_date.date(),
                        )
                    else:
                        st.session_state["gas_bar_full"] = True
                        st.session_state["gas_dr"] = "PENDING_MAX"
                    st.rerun()
            date_range = st.date_input(
                "📆 Rozsah (časová osa)",
                value=st.session_state["gas_dr"],
                key="gas_daterange",
            )
            st.session_state["gas_dr"] = date_range \
                if isinstance(date_range, tuple) \
                else st.session_state["gas_dr"]

        st.markdown("---")

        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            ts_from = pd.Timestamp(date_range[0]).tz_localize("UTC")
            ts_to   = pd.Timestamp(date_range[1]).tz_localize("UTC")
            df_range = df_f4[
                (df_f4["date"] >= ts_from) &
                (df_f4["date"] <= ts_to)
            ]
        else:
            df_range = df_f4

        st.plotly_chart(
            fig_flow_timeseries(df_range, [], [], [], [], chart_type),
            use_container_width=True,
        )
        st.caption(
            f"ℹ️ Data ENTSO-G — různé hraniční body mohou mít různé zpoždění "
            f"publikace (D-1 až D-2). Poslední dostupný den: "
            f"{df_hist['date'].dt.tz_convert('Europe/Prague').dt.date.max().strftime('%d.%m.%Y')}"
        )
