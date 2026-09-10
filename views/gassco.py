import pandas as pd
import streamlit as st

from config import GASSCO_DEFAULT_RANGE_DAYS
from data.gassco import (
    load_gassco_umm, load_gassco_umm_snapshot,
    list_available_umm_snapshot_dates, _latest_per_base_id, compute_umm_delta,
)
from charts.gassco import (
    fig_gassco_kpi, fig_gassco_timeseries,
    fig_gassco_seasonality, fig_gassco_umm_active, fig_gassco_umm_cancelled,
    fig_gassco_umm_outlook, fig_gassco_umm_delta,
    fig_gassco_umm_delta_bars, fig_gassco_umm_delta_diff,
)


def render_gassco_tab(df_gassco):
    df_gassco_tab = df_gassco.copy()

    if df_gassco_tab.empty:
        st.warning(
            "GASSCO data nejsou dostupná. "
            "Spusť GitHub Actions: Update gas history."
        )
    else:
        df_gassco_tab["date"] = pd.to_datetime(df_gassco_tab["date"], utc=True)
        all_points_g = sorted(df_gassco_tab["point"].unique().tolist())
        all_years_g  = sorted(df_gassco_tab["date"].dt.year.unique().tolist())
        max_date_g   = df_gassco_tab["date"].max()

        st.plotly_chart(
            fig_gassco_kpi(df_gassco_tab),
            use_container_width=True,
        )

        st.markdown("---")

        st.markdown("#### Časová osa")
        col1, col2, col3 = st.columns(3)
        with col1:
            sel_points_g = st.multiselect(
                "📍 Výstupní body",
                options=all_points_g,
                default=[p for p in ["Emden", "Dornum", "Zeebrugge", "Nybro"]
                         if p in all_points_g],
                key="gassco_points",
            )
        with col2:
            if "gassco_dr" not in st.session_state:
                st.session_state["gassco_dr"] = (
                    (max_date_g - pd.Timedelta(days=GASSCO_DEFAULT_RANGE_DAYS)).date(),
                    max_date_g.date(),
                )
            qd_cols = st.columns(5)
            labels  = ["Týden", "Měsíc", "3M", "Rok", "Max"]
            deltas  = [7, 30, 90, 365, None]
            for i, (lbl, delta) in enumerate(zip(labels, deltas)):
                if qd_cols[i].button(lbl, key=f"gassco_qd_{lbl}"):
                    if delta:
                        st.session_state["gassco_dr"] = (
                            (max_date_g - pd.Timedelta(days=delta)).date(),
                            max_date_g.date(),
                        )
                    else:
                        st.session_state["gassco_dr"] = (
                            df_gassco_tab["date"].min().date(),
                            max_date_g.date(),
                        )
                    st.rerun()
        with col3:
            date_range_g = st.date_input(
                "📆 Rozsah",
                value=st.session_state["gassco_dr"],
                key="gassco_daterange",
            )
            st.session_state["gassco_dr"] = date_range_g \
                if isinstance(date_range_g, tuple) \
                else st.session_state["gassco_dr"]

        if isinstance(date_range_g, (list, tuple)) and len(date_range_g) == 2:
            ts_from = pd.Timestamp(date_range_g[0], tz="UTC")
            ts_to   = pd.Timestamp(date_range_g[1], tz="UTC")
        else:
            ts_from = max_date_g - pd.Timedelta(days=GASSCO_DEFAULT_RANGE_DAYS)
            ts_to   = max_date_g

        st.plotly_chart(
            fig_gassco_timeseries(df_gassco_tab, sel_points_g, ts_from, ts_to),
            use_container_width=True,
        )

        st.markdown("---")

        st.markdown("#### Sezonnost")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            sel_pts_season = st.multiselect(
                "📍 Body pro sezonnost",
                options=all_points_g,
                default=all_points_g,
                key="gassco_season_pts",
            )
        with col_s2:
            sel_years_g = st.multiselect(
                "📅 Roky",
                options=all_years_g,
                default=all_years_g[-5:],
                key="gassco_years",
            )

        st.plotly_chart(
            fig_gassco_seasonality(df_gassco_tab, sel_pts_season, sel_years_g),
            use_container_width=True,
        )

        st.markdown("---")
        st.markdown("#### UMM zprávy (odstávky polí)")

        df_umm_all = load_gassco_umm()
        if df_umm_all.empty:
            st.info("GASSCO UMM data nejsou dostupná.")
        else:
            umm_latest = _latest_per_base_id(df_umm_all)
            now_ts = pd.Timestamp.now(tz="UTC")
            umm_active = umm_latest[
                (umm_latest["eventStatus"] != "Dismissed")
                & (umm_latest["eventStop"] >= now_ts)
            ]

            st.plotly_chart(fig_gassco_umm_active(umm_active), use_container_width=True)

            cancel_days = st.slider("Zrušené za posledních N dní", min_value=1,
                                     max_value=90, value=14, key="umm_cancel_days")
            cancel_since = now_ts - pd.Timedelta(days=cancel_days)
            umm_cancelled = umm_latest[
                (umm_latest["eventStatus"] == "Dismissed")
                & (umm_latest["publicationDateTime"] >= cancel_since)
            ]
            st.plotly_chart(
                fig_gassco_umm_cancelled(umm_cancelled, cancel_since.strftime("%d.%m.%Y")),
                use_container_width=True,
            )

            st.markdown('<div class="section-title">Výhled</div>', unsafe_allow_html=True)
            # Pevný default 90 dní — dřívější odvození z max(eventStop)
            # vedlo k nesmyslně velkým výchozím hodnotám (412/877 dní),
            # když nějaká odstávka měla eventStop hodně daleko v
            # budoucnu. Slider dovoluje ručně rozšířit dál, kdo chce.
            umm_outlook_default = 90
            umm_days_forward = st.slider(
                "Dní dopředu", min_value=7, max_value=500,
                value=umm_outlook_default, key="umm_outlook_days",
            )

            outlook_cutoff = now_ts.normalize() + pd.Timedelta(days=umm_days_forward)
            umm_in_window = umm_active[umm_active["eventStart"] <= outlook_cutoff]
            if umm_in_window.empty:
                if not umm_active.empty:
                    nearest_start = umm_active["eventStart"].min()
                    days_to_nearest = (nearest_start.normalize() - now_ts.normalize()).days
                    st.info(
                        f"V příštích {umm_days_forward} dnech nejsou plánované žádné odstávky. "
                        f"Nejbližší ({nearest_start.strftime('%d.%m.%Y')}) začíná za "
                        f"{days_to_nearest} dní."
                    )
                else:
                    st.info("Žádné aktivní odstávky k zobrazení.")
            else:
                st.plotly_chart(
                    fig_gassco_umm_outlook(umm_active, umm_days_forward),
                    use_container_width=True,
                )

            st.markdown('<div class="section-title">Δ Srovnání s dřívějším snapshotem</div>',
                        unsafe_allow_html=True)
            umm_available_dates = list_available_umm_snapshot_dates()
            if not umm_available_dates:
                st.caption("Zatím není k dispozici žádný uložený snapshot UMM zpráv pro "
                           "srovnání — objeví se po prvním scheduled běhu update_gassco_umm().")
            else:
                umm_compare_date = st.selectbox(
                    "Porovnat s", umm_available_dates, index=0,
                    format_func=lambda d: d.strftime("%d.%m.%Y"), key="umm_compare",
                )
                umm_days_back = (now_ts.normalize() - umm_compare_date).days
                df_umm_past = load_gassco_umm_snapshot(umm_days_back)

                umm_past_latest = _latest_per_base_id(df_umm_past)
                umm_past_active = (
                    umm_past_latest[umm_past_latest["eventStatus"] != "Dismissed"]
                    if not umm_past_latest.empty else umm_past_latest
                )
                umm_delta_view = st.radio(
                    "Zobrazení", ["Vedle sebe", "Rozdíl"],
                    horizontal=True, key="umm_delta_view",
                )
                umm_compare_label = umm_compare_date.strftime("%d.%m.%Y")
                if umm_delta_view == "Vedle sebe":
                    st.plotly_chart(
                        fig_gassco_umm_delta_bars(
                            umm_active, umm_past_active, umm_days_forward,
                            compare_label=umm_compare_label,
                        ),
                        use_container_width=True,
                    )
                else:
                    st.plotly_chart(
                        fig_gassco_umm_delta_diff(
                            umm_active, umm_past_active, umm_days_forward,
                            compare_label=umm_compare_label,
                        ),
                        use_container_width=True,
                    )

                umm_delta = compute_umm_delta(df_umm_all, df_umm_past)
                st.plotly_chart(fig_gassco_umm_delta(umm_delta), use_container_width=True)
