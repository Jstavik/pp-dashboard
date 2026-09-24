import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import (
    ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS, GASSCO_REPORT_DEFAULT_RANGE_DAYS, storage_color,
)
from data.entsog import load_entsog_history
from data.gassco import load_gassco
from data.gie import load_gie_all
from data.dap_europe import load_dap_europe
from charts.gas import fig_gas_map
from charts.gassco import fig_gassco_kpi, fig_gassco_timeseries, fig_gassco_seasonality
from charts.storage import fig_storage_grid
from charts.dap_map import fig_dap_map


def render_report_page():
    # Report jen mapuje (fig_gas_map = poslední 2 celé dny) — žádná
    # víceletá funkce jako na Plyn stránce, oknovaný default vždy stačí.
    _entsog_window_from = (
        pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS)
    ).normalize()
    df_hist = load_entsog_history(date_from=_entsog_window_from)
    df_g = load_gassco()
    st.markdown("### 📋 Ranní report — přehledy")
    st.caption("Každá záložka = jedna stránka A4 na výšku. "
               "Použijte tlačítko ke stažení nebo zkopírování.")

    rep_map, rep_gassco, rep_stor, rep_dap = st.tabs([
        "🗺️ Toky plynu", "🇳🇴 GASSCO", "🏭 Zásobníky", "⚡ DAP Mapa"
    ])

    # ── Toky plynu ──────────────────────────────────────
    with rep_map:
        st.markdown("#### Fyzické toky plynu — Evropa")
        st.caption("Mapa ENTSO-G D-2 + norské nominace live (GASSCO)")

        if df_hist.empty:
            st.warning("Data nejsou k dispozici.")
        else:
            with st.spinner("Načítám data..."):
                # Formát sjednocený s Plyn → Mapa (views/gas_map.py) — na
                # šířku, žádný fixní height/width přepis ani
                # use_container_width=False (dřív "na výšku", A4-portrait
                # rozměry 744×1050, co neseděly s widescreen layoutem
                # zbytku appky).
                st.plotly_chart(
                    fig_gas_map(df_hist, df_gassco=df_g),
                    use_container_width=True,
                    config={"displayModeBar": True, "scrollZoom": True},
                )

            st.caption(
                "📷 Stáhnout PNG: ikona fotoaparátu vpravo nahoře v grafu  |  "
                "📋 Kopírovat: pravý klik na graf → Uložit obrázek jako → "
                "pak Ctrl+C z prohlížeče nebo vložit přímo do Wordu"
            )

    # ── GASSCO ──────────────────────────────────────────
    with rep_gassco:
        st.markdown("#### GASSCO — Norský export plynu")

        if df_g.empty:
            st.warning("Data nejsou k dispozici.")
        else:
            default_pts = ["Emden", "Dornum", "Zeebrugge",
                           "Nybro", "Dunkerque", "Easington", "St.Fergus"]
            default_yrs = sorted(df_g["date"].dt.tz_convert(
                "Europe/Prague").dt.year.unique())[-5:]

            st.plotly_chart(
                fig_gassco_kpi(df_g),
                use_container_width=True,
                config={"toImageButtonOptions": {
                    "format": "png", "filename": "gassco_kpi",
                    "height": 400, "width": 794, "scale": 2}})

            st.plotly_chart(
                fig_gassco_timeseries(
                    df_g, default_pts,
                    df_g["date"].max() - pd.Timedelta(days=GASSCO_REPORT_DEFAULT_RANGE_DAYS),
                    df_g["date"].max()),
                use_container_width=True,
                config={"toImageButtonOptions": {
                    "format": "png", "filename": "gassco_ts",
                    "height": 400, "width": 794, "scale": 2}})

            st.plotly_chart(
                fig_gassco_seasonality(df_g, default_pts, default_yrs),
                use_container_width=True,
                config={"toImageButtonOptions": {
                    "format": "png", "filename": "gassco_sea",
                    "height": 400, "width": 794, "scale": 2}})

            st.caption("📷 Stáhnout: ikona fotoaparátu v grafu")

    # ── Zásobníky ────────────────────────────────────────
    with rep_stor:
        st.markdown("#### Zásobníky plynu — Evropa")

        df_gie_r = load_gie_all()

        if df_gie_r.empty:
            st.warning("Data nejsou k dispozici.")
        else:
            default_yrs_s = sorted(
                pd.to_datetime(df_gie_r["gasDayStart"], errors="coerce")
                .dt.year.dropna().unique().astype(int))[-5:]

            # ── Aktuální stav — Plotly tabulka ───────────────────────
            df_gie_r2 = df_gie_r.copy()
            df_gie_r2["gasDayStart"] = pd.to_datetime(
                df_gie_r2["gasDayStart"], errors="coerce")
            for c in ["full", "gasInStorage", "injection", "withdrawal"]:
                df_gie_r2[c] = pd.to_numeric(df_gie_r2[c], errors="coerce")

            last_stor2 = (df_gie_r2.dropna(subset=["gasDayStart"])
                          .sort_values("gasDayStart")
                          .groupby("country_code").last()
                          .reset_index())

            show_cc = ["EU", "AT", "BE", "CZ", "DE", "ES", "FR",
                       "HR", "HU", "IT", "LV", "NL", "PL", "PT",
                       "RO", "SK", "UA"]
            tbl = last_stor2[last_stor2["country_code"].isin(show_cc)].copy()
            tbl = tbl.set_index("country_code").reindex(show_cc).reset_index()
            tbl["net"] = tbl["injection"].fillna(0) - tbl["withdrawal"].fillna(0)
            tbl["net_str"] = tbl["net"].apply(
                lambda x: f"+{x:.0f}" if x >= 0 else f"{x:.0f}")
            tbl["full_str"] = tbl["full"].apply(
                lambda x: f"{x:.1f}%" if not pd.isna(x) else "n/a")
            tbl["twh_str"] = tbl["gasInStorage"].apply(
                lambda x: f"{x:.1f} TWh" if not pd.isna(x) else "n/a")

            cell_colors = [
                ["#F5F5F5"] * len(tbl),
                ["#BDBDBD" if pd.isna(v) else storage_color(v) for v in tbl["full"]],
                ["white"] * len(tbl),
                ["#E8F5E9" if n >= 0 else "#FFEBEE" for n in tbl["net"]],
                ["white"] * len(tbl),
            ]
            font_colors = [
                ["#333"] * len(tbl),
                ["white"] * len(tbl),
                ["#333"] * len(tbl),
                ["#2E7D32" if n >= 0 else "#C62828" for n in tbl["net"]],
                ["#555"] * len(tbl),
            ]

            fig_tbl = go.Figure(data=[go.Table(
                columnwidth=[60, 80, 90, 90, 90],
                header=dict(
                    values=["<b>Země</b>", "<b>Plnost %</b>",
                            "<b>Objem TWh</b>", "<b>Net GWh/d</b>",
                            "<b>Vtláčení GWh/d</b>"],
                    fill_color="#1565C0",
                    font=dict(color="white", size=11),
                    align="center", height=32,
                ),
                cells=dict(
                    values=[
                        tbl["country_code"],
                        tbl["full_str"],
                        tbl["twh_str"],
                        tbl["net_str"],
                        tbl["injection"].apply(
                            lambda x: f"+{x:.0f}" if not pd.isna(x) else "n/a"),
                    ],
                    fill_color=cell_colors,
                    font=dict(color=font_colors, size=11),
                    align="center", height=28,
                ),
            )])
            fig_tbl.update_layout(
                height=len(tbl) * 28 + 60,
                margin=dict(l=0, r=0, t=0, b=0),
                paper_bgcolor="white",
            )
            st.plotly_chart(
                fig_tbl,
                use_container_width=True,
                config={"toImageButtonOptions": {
                    "format": "png",
                    "filename": f"zasobniky_stav_{pd.Timestamp.now().strftime('%Y%m%d')}",
                    "height": 600, "width": 794, "scale": 2,
                }},
            )
            last_date_stor = (df_gie_r2["gasDayStart"]
                .dropna()
                .dt.date.max())
            st.caption(
                f"Stav: {last_date_stor.strftime('%d.%m.%Y')}  |  "
                "📷 Stáhnout: ikona fotoaparátu v grafu"
            )

            st.markdown("---")

            # ── Sezonnost — grid 6 zemí ───────────────────────────────
            st.plotly_chart(
                fig_storage_grid(df_gie_r, "full", default_yrs_s),
                use_container_width=True,
                config={"toImageButtonOptions": {
                    "format": "png",
                    "filename": f"zasobniky_grid_{pd.Timestamp.now().strftime('%Y%m%d')}",
                    "height": 700, "width": 794, "scale": 2,
                }})

            st.caption("📷 Stáhnout: ikona fotoaparátu v grafu")

    # ── DAP Mapa ─────────────────────────────────────────
    with rep_dap:
        st.markdown("#### DAP ceny elektřiny — Evropa")
        df_dap_r = load_dap_europe()
        if df_dap_r.empty:
            st.info("Data nejsou k dispozici.")
        else:
            st.plotly_chart(
                fig_dap_map(df_dap_r),
                use_container_width=True,
                config={
                    "toImageButtonOptions": {
                        "format": "png",
                        "filename": f"dap_mapa_{pd.Timestamp.now().strftime('%Y%m%d')}",
                        "height": 800, "width": 1200, "scale": 2,
                    },
                },
            )
            st.caption("📷 Stáhnout: ikona fotoaparátu v grafu")
