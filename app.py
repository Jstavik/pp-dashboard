# ╔══════════════════════════════════════════════════════════════╗
# ║  PP DASHBOARD — Streamlit app                                ║
# ║  Spuštění:  streamlit run app.py                             ║
# ╚══════════════════════════════════════════════════════════════╝

import pandas as pd
import streamlit as st
from datetime import date, timedelta

from config import (
    CSS_STYLES, THRESHOLD,
    C_DEFICIT, C_SURPLUS, C_OK, C_WARN, C_NEW, C_TEXT, C_MUTED,
    sparkline_svg, storage_color, data_status_row,
    ENTSOG_NOMINATION_DEFAULT_MONTHS, ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS,
    RESERVES_FALLBACK_RANGE_DAYS, RESERVES_DEFAULT_RANGE_DAYS,
    GAS_FLOWS_DEFAULT_RANGE_DAYS, GASSCO_REPORT_DEFAULT_RANGE_DAYS,
)
from data.entsoe import (
    fetch_entsoe_data, fetch_installed_capacity, fetch_reserves,
)
from data.deltagreen import fetch_deltagreen
from data.entsog import load_entsog_history, _short_name
from data.gie import load_gie_all
from data.hydro import load_hydro
from data.entsog_operational import (
    load_cz_operational, load_eu_operational_open_ended, EU_COUNTRY_NAMES,
    HISTORY_START, NOMINATION_CHART_INDICATORS, QUALITY_INDICATORS,
    active_capacity_dedup,
)
from charts.gas import (
    fig_gas_point_history, fig_gas_map,
    fig_flow_timeseries, fig_flow_seasonality,
)
from charts.storage import fig_storage_grid
from charts.capacity import fig_flow_stacked, fig_point_capacity
from charts.entsog_operational import fig_cz_operational
from data.gassco import load_gassco
from charts.gassco import fig_gassco_kpi, fig_gassco_timeseries, fig_gassco_seasonality
from charts.imbalance import parse_imbalance
from charts.generation import fig_deltagreen
from charts.outages import (
    parse_outages, detect_changes,
    fig_outages_gantt, fig_installed_capacity,
)
from charts.reserves import fig_reserve_volumes, fig_reserve_prices
from charts.dap_map import fig_dap_map
from data.dap_europe import load_dap_europe, load_dap_europe_all, dap_europe_last_write
from views.hydro import render_hydro_tab
from views.storage import render_storage_tab
from views.lng import render_lng_tab
from views.gassco import render_gassco_tab
from views.electricity_dashboard import render_dashboard_tab
from views.ceps import render_ceps_tab
from views.electricity_outages import render_outages_tab
from views.dap import render_dap_tab


# ── PAGE CONFIG ─────────────────────────────────────────────────
st.set_page_config(
    page_title="PP Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(CSS_STYLES, unsafe_allow_html=True)

# ── SESSION STATE ────────────────────────────────────────────────
for key, default in [
    ("df_out_prev", None),
    ("dg_api_key", ""),
    ("iteration", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── SIDEBAR ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Nastavení")

    st.session_state.dg_api_key = st.text_input(
        "Delta Green API klíč",
        value=st.session_state.dg_api_key,
        type="password",
        placeholder="Vložte klíč…",
    )

    refresh_min = st.slider("Auto-refresh (min)", 5, 120, 30, step=5)
    auto_refresh = st.checkbox("Auto refresh", value=False)

    if st.button("🔄 Obnovit data", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    with st.expander("🔋 Nastavení baterie"):
        bat_capacity_kwh = st.number_input(
            "Kapacita baterie [kWh]", value=100, min_value=10, max_value=10000, step=10)
        bat_power_kw = st.number_input(
            "Výkon baterie [kW]", value=50, min_value=5, max_value=5000, step=5)
        max_cycles = st.slider("Max cyklů za den", min_value=1, max_value=5, value=2)
        cycle_cost = st.number_input(
            "Cena cyklování [EUR/MWh]", value=15.0, min_value=0.0, max_value=100.0, step=0.5,
            help="Zahrnuje degradaci baterie + kompenzaci zákazníkovi. Průměr trhu: 12–18 EUR/MWh")
        hold_enabled = st.checkbox(
            "Povolit stav HOLD (drž SoC)", value=False,
            help="Baterie drží aktuální SoC místo nabíjení/vybíjení pokud není jasný cenový signál")

    st.markdown("---")
    show_commodity = st.radio(
        "Komodita",
        ["⚡ Elektřina", "🔵 Plyn", "📋 Report", "🔧 ČEPS odstávky"],
        horizontal=False,
    )
    show_ee  = show_commodity == "⚡ Elektřina"
    show_gas = show_commodity == "🔵 Plyn"
    show_rep = show_commodity == "📋 Report"
    show_out = show_commodity == "🔧 ČEPS odstávky"

    st.markdown("---")
    st.markdown("### Zdroje dat")
    if show_ee:
        st.caption(
            "**ENTSO-E Transparency Platform**  \n"
            "DAP ceny · Generace · Odstávky · Rezervy"
        )
        st.caption(
            "**ČEPS API (SOAP)**  \n"
            "Odchylka · Zatížení · SVR aktivace · "
            "Cena odchylky · Generace · Přeshraniční toky · Frekvence"
        )
        st.caption(
            "**Delta Green API**  \n"
            "Portfolio stav · Disponibilní flexibilita (volitelné)"
        )
    elif show_gas:
        st.caption(
            "**ENTSO-G Transparency Platform**  \n"
            "Fyzické toky · Hraniční přechody · Kapacity · Denní data"
        )
        st.caption(
            "**GIE AGSI+**  \n"
            "Zásobníky plynu · CZ + EU · Injekce · Těžba · Plnost %"
        )
        st.caption(
            "**GIE ALSI**  \n"
            "LNG terminály EU · Send-out · Inventory"
        )
        st.caption(
            "**GASSCO UMM**  \n"
            "Norské nominace · Výstupní body · UMM odstávky polí"
        )
        st.caption(
            "**ENTSO-E 16.1.D**  \n"
            "Vodní zásobníky · 20 zemí · Týdenní data"
        )

if auto_refresh:
    st.markdown(
        f'<meta http-equiv="refresh" content="{refresh_min * 60}">',
        unsafe_allow_html=True,
    )

# ── NAČTENÍ DAT ──────────────────────────────────────────────────
# fetch_entsoe_data() (CZ odchylka/generace/zatížení/odstávky) patří jen
# stránkám Elektřina a ČEPS odstávky (ta čte df_out/now/changes/n_pu/n_gu
# z týchž dat) — NIKDY se nevolá pro Plyn/Report, ať appka na těch
# stránkách nečeká na ENTSO-E, i kdyby úplně nereagovalo. Dřív se volalo
# nepodmíněně před celým if/elif page-splitem — appku to na Plyn/Report
# tvrdě blokovalo na ENTSO-E timeoutu (ověřeno naživo 2026-09-09).
if show_ee or show_out:
    with st.spinner("Načítám data z ENTSO-E…"):
        try:
            imbal_raw, gen_raw, load_actual, load_fc, out_raw, now = fetch_entsoe_data()
        except Exception as e:
            st.warning(f"⚠️ ENTSO-E dočasně nedostupné ({type(e).__name__}): {e}")
            now         = pd.Timestamp.now(tz="Europe/Prague")
            imbal_raw   = pd.DataFrame(columns=["odchylka_MWh", "price_Short", "price_Long"])
            gen_raw     = pd.DataFrame()
            load_actual = pd.Series(dtype="float64", name="actual_MW")
            load_fc     = pd.Series(dtype="float64", name="forecast_MW")
            out_raw     = pd.DataFrame()

    df_imbal = parse_imbalance(imbal_raw)
    df_out   = parse_outages(out_raw)
    changes  = detect_changes(st.session_state.df_out_prev, df_out)
    st.session_state.df_out_prev = df_out.copy() if not df_out.empty else None

    last_imbal = float(df_imbal["odchylka_MWh"].iloc[-1]) if not df_imbal.empty else 0.0

    n_pu  = int((df_out["unit_level"] == "PU").sum()) if not df_out.empty else 0
    n_gu  = int((df_out["unit_level"] == "GU").sum()) if not df_out.empty else 0
    n_new = len(changes["new"])

if show_ee:
    with st.spinner("Načítám data rezerv…"):
        try:
            reserves = fetch_reserves()
        except Exception:
            reserves = dict(afrr_d_amt=pd.DataFrame(), afrr_d_pri=pd.DataFrame(),
                            afrr_y_amt=pd.DataFrame(), afrr_y_pri=pd.DataFrame(),
                            mfrr_d_amt=pd.DataFrame(), mfrr_d_pri=pd.DataFrame(),
                            start=now.normalize(), end=now.normalize()+pd.Timedelta(days=RESERVES_FALLBACK_RANGE_DAYS), now=now)

    # ── BANNER ───────────────────────────────────────────────────────
    if last_imbal < -THRESHOLD:
        bcls, bstate = "banner-bad",  f"DEFICIT &nbsp; {last_imbal:+.1f} MWh"
    elif last_imbal > THRESHOLD:
        bcls, bstate = "banner-warn", f"SURPLUS &nbsp; {last_imbal:+.1f} MWh"
    else:
        bcls, bstate = "banner-ok",   f"VYVÁŽENO &nbsp; {last_imbal:+.1f} MWh"

    data_age = (pd.Timestamp.now(tz="Europe/Prague") - now).total_seconds() / 60
    fresh    = f"{data_age:.0f} min" if data_age < 60 else f"{data_age/60:.1f} h ⚠"

    st.markdown(
        f'<div class="banner {bcls}">'
        f'<div class="banner-left"><span class="pulse-dot"></span><span>⚡ PP DASHBOARD</span></div>'
        f'<div class="banner-center">{bstate}</div>'
        f'<div class="banner-right">{now.strftime("%a %d.%m.%Y · %H:%M:%S")}'
        f'<span class="fresh-badge">{fresh}</span></div></div>',
        unsafe_allow_html=True,
    )

    # ── KPI STRIP ────────────────────────────────────────────────────
    n_pu  = int((df_out["unit_level"] == "PU").sum()) if not df_out.empty else 0
    n_gu  = int((df_out["unit_level"] == "GU").sum()) if not df_out.empty else 0
    n_new = len(changes["new"])
    total_unavail = float(df_out["unavailable_MW"].sum()) if not df_out.empty else 0.0
    total_install = float(df_out["installed_MW"].sum())   if not df_out.empty else 0.0
    unavail_pct   = total_unavail / total_install * 100   if total_install else 0.0
    cur_gen = (float(gen_raw.dropna(how="all").tail(1).iloc[0].sum(skipna=True))
               if not gen_raw.empty and not gen_raw.dropna(how="all").empty else 0.0)
    last_short = (float(df_imbal["price_Short"].dropna().iloc[-1])
                  if "price_Short" in df_imbal and df_imbal["price_Short"].notna().any() else None)
    spark_i = sparkline_svg(df_imbal["odchylka_MWh"].tail(96).tolist(),
                            C_DEFICIT if last_imbal < 0 else C_SURPLUS)
    spark_g = (sparkline_svg(gen_raw.fillna(0).sum(axis=1).tail(96).tolist(), C_OK)
               if not gen_raw.empty else "")
    imbal_col   = C_DEFICIT if last_imbal < -THRESHOLD else (C_SURPLUS if last_imbal > THRESHOLD else C_TEXT)
    unavail_col = C_DEFICIT if unavail_pct > 30 else (C_WARN if unavail_pct > 15 else C_OK)

    kpi_html = f"""
    <div class="kpi-row">
      <div class="kpi-card" style="border-top-color:{imbal_col}">
        <div class="kpi-label">Systémová odchylka</div>
        <div class="kpi-value" style="color:{imbal_col}">{last_imbal:+.1f}<span style="font-size:.9rem;color:{C_MUTED}"> MWh</span></div>
        <div class="kpi-sub">práh ±{THRESHOLD} MWh</div>
        <div>{spark_i}</div>
      </div>
      <div class="kpi-card" style="border-top-color:{C_OK}">
        <div class="kpi-label">Aktuální výroba</div>
        <div class="kpi-value">{cur_gen:,.0f}<span style="font-size:.9rem;color:{C_MUTED}"> MW</span></div>
        <div class="kpi-sub">posledních 24 h</div>
        <div>{spark_g}</div>
      </div>
      <div class="kpi-card" style="border-top-color:{unavail_col}">
        <div class="kpi-label">Výpadek kapacit</div>
        <div class="kpi-value" style="color:{unavail_col}">{total_unavail:,.0f}<span style="font-size:.9rem;color:{C_MUTED}"> MW</span></div>
        <div class="kpi-sub">z {total_install:,.0f} MW instalovaných · {unavail_pct:.0f}%</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Aktivní odstávky</div>
        <div class="kpi-value">{n_pu + n_gu}</div>
        <div class="kpi-sub">PU {n_pu} · GU {n_gu}{f' · <span style="color:{C_NEW};font-weight:600">+{n_new} nových</span>' if n_new else ''}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Cena odchylky (Short)</div>
        <div class="kpi-value">{f'{last_short:,.0f}' if last_short is not None else '—'}<span style="font-size:.9rem;color:{C_MUTED}"> €/MWh</span></div>
        <div class="kpi-sub">CZ imbalance price</div>
      </div>
    </div>
    """
    st.markdown(kpi_html, unsafe_allow_html=True)

    if n_new or len(changes.get("ended", set())) or not changes["changed_mw"].empty:
        n_ended = len(changes.get("ended", set()))
        n_chmw  = len(changes["changed_mw"])
        parts   = []
        if n_new:
            parts.append(f"🆕 <strong>{n_new} nových</strong> odstávek")
        if n_ended:
            parts.append(f"✅ <strong>{n_ended} ukončených</strong>")
        if n_chmw:
            parts.append(f"⚡ <strong>{n_chmw} změn MW</strong>")
        st.markdown(f'<div class="alert-box">{"  ·  ".join(parts)}</div>',
                    unsafe_allow_html=True)

    # ── ZÁLOŽKY ──────────────────────────────────────────────────────
    tab_dash, tab_ceps, tab_out, tab_dap, tab_rezervy, tab_hydro, tab_dg, tab_data, tab_dap_mapa = st.tabs([
        "📊 Odchylka & Generace",
        "⚡ ČEPS",
        "Výroba/Odstávky",
        "💶 DAP Ceny",
        "⚖️ Rezervy",
        "💧 Hydro",
        "🌿 Delta Green",
        "📋 Data",
        "🗺️ DAP Mapa",
    ])

    # ──────────── TAB 1: ODCHYLKA + GENERACE ─────────────────────────
    with tab_dash:
        render_dashboard_tab(now, df_imbal, gen_raw, load_fc, reserves)


    # ──────────── TAB ČEPS: REAL-TIME DASHBOARD ──────────────────────
    with tab_ceps:
        render_ceps_tab()


    # ──────────── TAB 2: VÝROBA/ODSTÁVKY ─────────────────────────────
    with tab_out:
        render_outages_tab()


    # ──────────── TAB 3: DAP CENY ────────────────────────────────────
    with tab_dap:
        render_dap_tab(now, reserves, bat_capacity_kwh, bat_power_kw, max_cycles, cycle_cost, hold_enabled)


    # ──────────── TAB 4: REZERVY ─────────────────────────────────────
    with tab_rezervy:
        res_start = now.normalize()
        res_end   = now.normalize() + pd.Timedelta(days=RESERVES_DEFAULT_RANGE_DAYS)
        if now.month < 7:
            _a04_label = f"{now.year}-01-01 – {now.year}-07-01"
        else:
            _a04_label = f"{now.year}-07-01 – {now.year + 1}-01-01"

        st.markdown(
            '<div class="section-title">'
            f'aFRR + mFRR — D0 až D+7 &nbsp;·&nbsp; '
            f'Solid = A01 denní &nbsp;·&nbsp; Dash = A04 roční ({_a04_label})'
            '</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            fig_reserve_volumes(reserves, now, res_start, res_end, height=420),
            use_container_width=True, config={"displayModeBar": False},
        )
        st.plotly_chart(
            fig_reserve_prices(reserves, now, res_start, res_end, height=420),
            use_container_width=True, config={"displayModeBar": False},
        )

        with st.expander("📥 Stáhnout surová data rezerv"):
            ec1, ec2, ec3, ec4, ec5, ec6 = st.columns(6)
            for col_obj, df_r, label, fname in [
                (ec1, reserves["afrr_d_amt"], "aFRR denní obj.", "afrr_d_amount.csv"),
                (ec2, reserves["afrr_d_pri"], "aFRR denní ceny", "afrr_d_price.csv"),
                (ec3, reserves["afrr_y_amt"], "aFRR roční obj.", "afrr_y_amount.csv"),
                (ec4, reserves["afrr_y_pri"], "aFRR roční ceny", "afrr_y_price.csv"),
                (ec5, reserves["mfrr_d_amt"], "mFRR denní obj.", "mfrr_d_amount.csv"),
                (ec6, reserves["mfrr_d_pri"], "mFRR denní ceny", "mfrr_d_price.csv"),
            ]:
                with col_obj:
                    if not df_r.empty:
                        st.download_button(f"⬇ {label}", df_r.to_csv().encode(), fname, "text/csv")
                    else:
                        st.caption(f"{label}: —")


    # ──────────── TAB HYDRO: VODNÍ ZÁSOBNÍKY ─────────────────────────
    df_hydro = load_hydro()
    with tab_hydro:
        render_hydro_tab(df_hydro)

    # ──────────── TAB 5: DELTA GREEN ─────────────────────────────────
    with tab_dg:
        dg_key = st.session_state.get("dg_api_key", "").strip()
        if not dg_key:
            st.info("Zadejte Delta Green API klíč v levém panelu (⚙️ Nastavení).")
        else:
            with st.spinner("Načítám Delta Green…"):
                try:
                    df1_dg, df2_dg = fetch_deltagreen(dg_key)
                    st.plotly_chart(fig_deltagreen(df1_dg, df2_dg), use_container_width=True,
                                    config={"displayModeBar": False})
                    last2 = df2_dg.dropna(subset=["upPowerKW","downBatteryPowerKW",
                                                   "downSolarCurtailmentPowerKW"]).iloc[-1]
                    last1 = df1_dg.dropna(subset=["batteryPowerKW","consumptionPowerKW",
                                                   "photovoltaicPowerKW","gridPowerKW"]).iloc[-1]
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("Baterie",      f"{float(last1['batteryPowerKW']):+.0f} kW")
                    k2.metric("Fotovoltaika", f"{float(last1['photovoltaicPowerKW']):.0f} kW")
                    k3.metric("Max UP",       f"{float(last2['upPowerKW']):.0f} kW")
                    total_down = float(last2["downBatteryPowerKW"]) + float(last2["downSolarCurtailmentPowerKW"])
                    k4.metric("Max DOWN",     f"{total_down:.0f} kW")
                except Exception as e:
                    st.error(f"Delta Green nedostupný: {e}")


    # ──────────── TAB 6: SUROVÁ DATA ─────────────────────────────────
    with tab_data:
        t1, t2, t3, t4 = st.tabs(["Odchylka", "Odstávky PU", "Odstávky GU", "Generace"])

        with t1:
            if not df_imbal.empty:
                st.dataframe(df_imbal.iloc[::-1], use_container_width=True)
                st.download_button("⬇ CSV odchylka", df_imbal.to_csv().encode(),
                                   "odchylka.csv", "text/csv")

        def _out_tab(lvl):
            sub = df_out[df_out["unit_level"] == lvl] if not df_out.empty else pd.DataFrame()
            if sub.empty:
                st.info(f"Žádné odstávky {lvl}.")
                return
            cols = ["unit_name","outage_start","outage_end","installed_MW",
                    "available_MW","unavailable_MW","available_pct","outage_type"]
            st.dataframe(sub[[c for c in cols if c in sub.columns]],
                         use_container_width=True, hide_index=True)
            st.download_button(f"⬇ CSV {lvl}", sub.to_csv(index=False).encode(),
                               f"outages_{lvl}.csv", "text/csv")

        with t2: _out_tab("PU")
        with t3: _out_tab("GU")

        with t4:
            if not gen_raw.empty:
                from config import psr_lookup as _psr_lookup
                display_gen = gen_raw.copy()
                display_gen.columns = [_psr_lookup(c)[0] for c in display_gen.columns]
                st.dataframe(display_gen.iloc[::-1], use_container_width=True)
                st.download_button("⬇ CSV generace", display_gen.to_csv().encode(),
                                   "generace.csv", "text/csv")
            else:
                st.info("Data generace nejsou dostupná.")

    # ──────────── TAB: DAP MAPA ──────────────────────────────────────
    with tab_dap_mapa:
        df_dap_all = load_dap_europe_all()
        if df_dap_all.empty:
            st.info("Data DAP Mapa nejsou k dispozici. "
                    "Spusťte GitHub Actions pro stažení.")
        else:
            available_dap_dates = sorted(df_dap_all["date"].unique(), reverse=True)
            latest_dap_date = available_dap_dates[0]
            sel_dap_date = st.selectbox(
                "📅 Den dodávky",
                options=available_dap_dates,
                format_func=lambda d: pd.Timestamp(d).strftime("%d.%m.%Y"),
                key="dap_mapa_date",
            )

            last_write = dap_europe_last_write()
            last_write_str = (
                last_write.strftime("%d.%m.%Y %H:%M CET/CEST")
                if last_write is not None else "neznámo"
            )
            today_dap = date.today()
            if today_dap not in available_dap_dates:
                st.warning(
                    f"Pro {today_dap.strftime('%d.%m.%Y')} zatím nejsou data — "
                    f"poslední dostupný den: {pd.Timestamp(latest_dap_date).strftime('%d.%m.%Y')}."
                )
            st.caption(f"📥 Poslední úspěšné stažení: {last_write_str}")

            df_dap = df_dap_all[df_dap_all["date"] == sel_dap_date]
            st.plotly_chart(
                fig_dap_map(df_dap),
                use_container_width=True,
                key="dap_mapa_chart",
                config={
                    "displayModeBar": True,
                    "scrollZoom": True,
                    "toImageButtonOptions": {
                        "format": "png",
                        "filename": "dap_mapa_evropa",
                        "height": 800,
                        "width": 1200,
                        "scale": 2,
                    },
                },
            )
            st.caption(
                f"Zobrazeno: {pd.Timestamp(sel_dap_date).strftime('%d.%m.%Y')}  |  "
                "Zdroj: ENTSO-E Transparency Platform  |  "
                "Base | Peak = denní průměr EUR/MWh  |  "
                "Δ = DoD změna"
            )

elif show_gas:
    st.markdown(
        '<div class="banner banner-ok">'
        '<div class="banner-left"><span class="pulse-dot"></span>'
        '<span>🔵 PP DASHBOARD — PLYN</span></div>'
        '<div class="banner-center"></div>'
        '<div class="banner-right">'
        + pd.Timestamp.now(tz="Europe/Prague").strftime("%a %d.%m.%Y · %H:%M:%S") +
        '</div></div>',
        unsafe_allow_html=True,
    )
    # df_hist (fyzické toky ENTSO-G) krmí Mapu/Toky/Sezonnost/Kapacitu/LNG
    # najednou (eager st.tabs — všechny podzáložky se počítají na každý
    # rerun bez ohledu na to, která je vidět). Sezonnost (tab_season) a
    # tlačítka "Maximum"/"Max" (tab_bar, tab_lng) explicitně potřebují
    # CELOU historii (multiletý výběr let) — ostatní ne. Defaultně se
    # proto načte jen okno (ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS), plná
    # historie se dotáhne jen když ji uživatel skutečně vyžádal
    # (checkbox/tlačítko níž nastaví jeden z těchhle session_state flagů)
    # — ne automaticky na každý page load. Naměřeno naživo 2026-09-09:
    # okno 13 měsíců 0.25-0.3s / plná historie 4-8s.
    _ENTSOG_FULL_FLAGS = ["gas_seas_full", "gas_bar_full", "gas_lng_full", "gas_lng_full_via_max"]
    _need_entsog_full = any(st.session_state.get(k, False) for k in _ENTSOG_FULL_FLAGS)
    with st.spinner("Načítám data ENTSO-G..."):
        if _need_entsog_full:
            df_hist = load_entsog_history()
        else:
            _entsog_window_from = (
                pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS)
            ).normalize()
            df_hist = load_entsog_history(date_from=_entsog_window_from)

    # Status panel plyn
    def _safe_max_date(df, col):
        """Bezpečně vrátí max datum v Prague timezone."""
        try:
            if df.empty or col not in df.columns:
                return None
            s = pd.to_datetime(df[col], utc=True)
            return pd.Timestamp(
                s.dt.tz_convert("Europe/Prague").dt.date.max()
            )
        except Exception:
            return None

    _last_entsog   = _safe_max_date(df_hist, "date")
    df_gie         = load_gie_all()
    _last_gie      = (
        pd.Timestamp(df_gie["gasDayStart"]
                     .pipe(lambda s: pd.to_datetime(s, utc=True)
                           if s.dt.tz is None else s)
                     .dt.tz_convert("Europe/Prague")
                     .dt.date.max())
        if not df_gie.empty else None
    )
    _df_hydro_tmp  = load_hydro()
    _last_hydro    = _safe_max_date(_df_hydro_tmp, "date")
    df_gassco      = load_gassco()
    _last_gassco   = _safe_max_date(df_gassco, "date")
    data_status_row([
        {"name": "ENTSO-G",
         "date": _last_entsog,
         "freshness_hours": 48, "source": "ENTSO-G"},
        {"name": "GIE zásobníky",
         "date": _last_gie,
         "freshness_hours": 72, "source": "GIE AGSI+"},
        {"name": "GASSCO",
         "date": _last_gassco,
         "freshness_hours": 24, "source": "GASSCO"},
        {"name": "Hydro",
         "date": _last_hydro,
         "freshness_hours": 200, "source": "ENTSO-E"},
    ])

    if df_hist.empty:
        st.warning("ENTSO-G data nejsou dostupná.")
    else:
        df_cz = df_hist[df_hist["countryLabel"] == "Czechia"].copy()
        df_cz["date_prague"] = df_cz["date"].dt.tz_convert("Europe/Prague").dt.normalize()
        df_cz["point_short"] = df_cz["pointsNames"].apply(_short_name)
        _entry = df_cz[df_cz["directionKey"] == "entry"].groupby(
            ["date_prague", "point_short"])["value_GWh"].sum()
        _exit  = df_cz[df_cz["directionKey"] == "exit"].groupby(
            ["date_prague", "point_short"])["value_GWh"].sum()
        pivot_gas = (_entry.unstack(fill_value=0) - _exit.unstack(fill_value=0)).fillna(0)
        pivot_gas.index = pd.to_datetime(pivot_gas.index)

        tab_map, tab_bar, tab_season, tab_cap, tab_nom, tab_stor, tab_lng, tab_gassco, tab_hist = st.tabs(
            ["🗺️ Mapa", "📊 Toky", "📈 Sezonnost", "🔲 Kapacity", "📋 Nominace",
             "🏭 Zásobníky", "🚢 LNG", "🇳🇴 GASSCO", "📈 Historie"]
        )

        with tab_map:
            if df_hist.empty:
                st.warning("Data nejsou dostupná.")
            else:
                st.plotly_chart(
                    fig_gas_map(df_hist, df_gassco=df_gassco),
                    use_container_width=True,
                    config={"displayModeBar": True, "scrollZoom": True},
                )
                st.caption(
                    f"Data ENTSO-G: D-2 ({date.today() - timedelta(days=2)}) | "
                    f"Norské nominace: live (realTimeAtom.xml)"
                )

                df_gie_map = df_gie.copy()
                if not df_gie_map.empty:
                    df_gie_map["gasDayStart"] = pd.to_datetime(
                        df_gie_map["gasDayStart"], errors="coerce")
                    last_gie_map = (df_gie_map
                                    .dropna(subset=["gasDayStart"])
                                    .sort_values("gasDayStart")
                                    .groupby("country_code").last()
                                    .reset_index())
                    last_gie_map = last_gie_map[
                        last_gie_map["country_code"] != "EU"
                    ].copy().sort_values("country_code")

                    st.markdown("#### Zásobníky plynu — aktuální stav")

                    cols = st.columns(len(last_gie_map))
                    for i, (_, row) in enumerate(last_gie_map.iterrows()):
                        cc    = row["country_code"]
                        full  = float(str(row.get("full", "0")).replace(",", "."))
                        twh   = float(row.get("gasInStorage", 0))
                        inj   = float(row.get("injection", 0))
                        with_ = float(row.get("withdrawal", 0))
                        net   = inj - with_
                        net_str = f"+{net:.0f}" if net >= 0 else f"{net:.0f}"
                        icon  = {"#C62828": "🔴", "#FF8F00": "🟠",
                                 "#1565C0": "🔵", "#2E7D32": "🟢"}[storage_color(full)]
                        cols[i].metric(
                            label=f"{icon} {cc}",
                            value=f"{full:.1f}%",
                            delta=f"{net_str} GWh/d",
                        )
                        cols[i].caption(f"{twh:.1f} TWh")

        with tab_bar:
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

        with tab_season:
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

        with tab_cap:
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

        with tab_nom:
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

        with tab_stor:
            render_storage_tab(df_gie)

        with tab_lng:
            render_lng_tab(df_hist)

        with tab_gassco:
            render_gassco_tab(df_gassco)

        with tab_hist:
            point_sel = st.selectbox(
                "Hraniční přechod",
                options=[c for c in pivot_gas.columns if pivot_gas[c].abs().sum() > 0],
                key="gas_point_sel",
            )
            st.plotly_chart(
                fig_gas_point_history(pivot_gas, point_sel),
                use_container_width=True,
            )

elif show_rep:
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
                fig_map_rep = fig_gas_map(
                    df_hist,
                    df_gassco=df_g,
                )
                fig_map_rep.update_layout(
                    height=1050,
                    width=744,
                    margin=dict(l=10, r=10, t=50, b=30),
                    paper_bgcolor="white",
                )

            with st.container():
                st.plotly_chart(
                    fig_map_rep,
                    use_container_width=False,
                    config={
                        "displayModeBar": True,
                        "modeBarButtonsToRemove": [
                            "pan2d", "lasso2d", "select2d",
                            "autoScale2d", "resetScale2d"
                        ],
                        "toImageButtonOptions": {
                            "format": "png",
                            "filename": f"toky_plynu_{pd.Timestamp.now().strftime('%Y%m%d')}",
                            "height": 1123,
                            "width": 794,
                            "scale": 2,
                        },
                    },
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
            import plotly.graph_objects as go

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

elif show_out:
    st.markdown('<div class="section-title">Instalovaná kapacita podle zdroje (14.1.A)</div>',
                unsafe_allow_html=True)
    cap = fetch_installed_capacity()
    if not cap.empty:
        st.plotly_chart(fig_installed_capacity(cap), use_container_width=True,
                        config={"displayModeBar": False})

    st.markdown(f'<div class="section-title">Výrobní jednotky (PU) — {n_pu} aktivních</div>',
                unsafe_allow_html=True)
    st.plotly_chart(fig_outages_gantt(df_out, "PU", now, changes),
                    use_container_width=True, config={"displayModeBar": False})

    st.markdown(f'<div class="section-title">Generační jednotky (GU) — {n_gu} aktivních</div>',
                unsafe_allow_html=True)
    st.plotly_chart(fig_outages_gantt(df_out, "GU", now, changes),
                    use_container_width=True, config={"displayModeBar": False})

    n_ended_tab = len(changes.get("ended", set()))
    n_chmw_tab  = len(changes["changed_mw"])
    with st.expander(f"📋 Detail změn  ·  {n_new} nových · {n_ended_tab} ukončených · {n_chmw_tab} změn MW",
                     expanded=bool(n_new or n_ended_tab or n_chmw_tab)):
        if not (n_new or n_ended_tab or n_chmw_tab):
            st.markdown("<em style='color:#888'>Žádné změny od posledního obnovení.</em>",
                        unsafe_allow_html=True)
        else:
            if n_new and not df_out.empty:
                new_df = (df_out[df_out[["unit_raw","outage_start","outage_end"]]
                                 .apply(tuple, axis=1).isin(changes["new"])]
                          .sort_values("unavailable_MW", ascending=False))
                st.markdown("**🆕 Nové odstávky**")
                st.dataframe(
                    new_df[["unit_name","unit_level","outage_start","outage_end",
                             "installed_MW","unavailable_MW","outage_type"]],
                    use_container_width=True, hide_index=True,
                )
            if not changes["changed_mw"].empty:
                st.markdown("**⚡ Změny výkonu**")
                st.dataframe(changes["changed_mw"], use_container_width=True, hide_index=True)

st.session_state.iteration += 1
