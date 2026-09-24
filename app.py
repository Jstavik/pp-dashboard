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
    sparkline_svg, data_status_row,
    ENTSOG_FLOWS_DEFAULT_WINDOW_MONTHS,
    RESERVES_FALLBACK_RANGE_DAYS,
)
from data.entsoe import (
    fetch_entsoe_data, fetch_reserves,
)
from data.entsog import load_entsog_history, _short_name
from data.gie import load_gie_all
from data.hydro import load_hydro
from data.gassco import load_gassco
from charts.imbalance import parse_imbalance
from charts.outages import parse_outages, detect_changes
from views.hydro import render_hydro_tab
from views.storage import render_storage_tab
from views.lng import render_lng_tab
from views.gassco import render_gassco_tab
from views.electricity_dashboard import render_dashboard_tab
from views.ceps import render_ceps_tab
from views.electricity_outages import render_outages_tab
from views.dap import render_dap_tab
from views.reserves import render_reserves_tab
from views.delta_green import render_delta_green_tab
from views.dap_map import render_dap_map_tab
from views.gas_history import render_history_tab
from views.gas_map import render_gas_map_tab
from views.gas_seasonality import render_gas_seasonality_tab
from views.gas_flows import render_gas_flows_tab
from views.gas_capacity import render_gas_capacity_tab
from views.gas_nominations import render_gas_nominations_tab
from views.cot import render_cot_page
from views.ceps_outages_page import render_ceps_outages_page
from views.report import render_report_page


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
        ["⚡ Elektřina", "🔵 Plyn", "📋 Report", "🔧 ČEPS odstávky", "📈 CoT"],
        horizontal=False,
    )
    show_ee  = show_commodity == "⚡ Elektřina"
    show_gas = show_commodity == "🔵 Plyn"
    show_rep = show_commodity == "📋 Report"
    show_out = show_commodity == "🔧 ČEPS odstávky"
    show_cot = show_commodity == "📈 CoT"

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
    elif show_cot:
        st.caption(
            "**ICE MiFID II Commitments of Traders**  \n"
            "IFEU (Brent, Gasoil, TTF/USD, ...) + NDEX (TTF, EUA, ...) · Týdenní pozice tradérů"
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
        render_reserves_tab(now, reserves)


    # ──────────── TAB HYDRO: VODNÍ ZÁSOBNÍKY ─────────────────────────
    df_hydro = load_hydro()
    with tab_hydro:
        render_hydro_tab(df_hydro)

    # ──────────── TAB 5: DELTA GREEN ─────────────────────────────────
    with tab_dg:
        render_delta_green_tab()


    # ──────────── TAB 6: SUROVÁ DATA ─────────────────────────────────
    with tab_data:
        t1, t2 = st.tabs(["Odchylka", "Generace"])

        with t1:
            if not df_imbal.empty:
                st.dataframe(df_imbal.iloc[::-1], use_container_width=True)
                st.download_button("⬇ CSV odchylka", df_imbal.to_csv().encode(),
                                   "odchylka.csv", "text/csv")

        with t2:
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
        render_dap_map_tab()

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
            render_gas_map_tab(df_hist, df_gassco, df_gie)

        with tab_bar:
            render_gas_flows_tab(df_hist)

        with tab_season:
            render_gas_seasonality_tab(df_hist)

        with tab_cap:
            render_gas_capacity_tab(df_hist)

        with tab_nom:
            render_gas_nominations_tab()

        with tab_stor:
            render_storage_tab(df_gie)

        with tab_lng:
            render_lng_tab(df_hist)

        with tab_gassco:
            render_gassco_tab(df_gassco)

        with tab_hist:
            render_history_tab(pivot_gas)

elif show_rep:
    render_report_page()

elif show_out:
    render_ceps_outages_page(now, df_out, changes, n_pu, n_gu, n_new)

elif show_cot:
    render_cot_page()

st.session_state.iteration += 1
