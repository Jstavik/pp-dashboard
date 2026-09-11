import pandas as pd
import streamlit as st

from data.ice_cot import load_ice_cot, load_ice_cot_status, VENUES
from charts.cot import fig_cot_positioning, compute_financial_net_position

# (venue, product_code, tab label) — defaultní tři, vedle obecného selectoru.
DEFAULT_COMMODITIES = [
    ("NDEX", "TFM", "🇳🇱 TTF"),
    ("NDEX", "C", "🌍 EUA"),
    ("IFEU", "B", "🛢️ Brent"),
]


def _fmt_ts(iso_str) -> str:
    try:
        return pd.Timestamp(iso_str).tz_convert("Europe/Prague").strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return str(iso_str)


def _render_status_panel(df: pd.DataFrame, status_df: pd.DataFrame) -> None:
    st.markdown("#### 📡 Stav dat")
    st.caption(
        "IFEU_FUT/NDEX_FUT jsou stahované soubory — IFEU_FUT ale uvnitř "
        "nese i pár komodit pod vlastním venue kódem IFLX (ICE Futures "
        "Europe softs/agri), proto se v selectoru níž objeví i ten."
    )
    # IFEU/NDEX = venues, co se skutečně fetchují (viz data/ice_cot.py::VENUES)
    # — ne totéž co df["venue"].unique() (to obsahuje i IFLX, vnořené
    # uvnitř IFEU_FUT.csv, bez vlastního fetch/status záznamu).
    cols = st.columns(len(VENUES))
    for col, venue in zip(cols, VENUES):
        with col:
            st.markdown(f"**{venue}**")
            in_store = df[df["venue"] == venue]
            if not in_store.empty:
                st.caption(f"Nejnovější data v úložišti: report_date **{in_store['report_date'].max()}**")
            else:
                st.caption("V úložišti zatím žádná data pro tenhle venue.")

            v_status = status_df[status_df["venue"] == venue].sort_values("timestamp")
            if v_status.empty:
                st.caption("Žádný záznam o fetch pokusu v logu (data z jednorázového backfillu).")
                continue

            successes = v_status[v_status["success"] == True]  # noqa: E712
            if not successes.empty:
                last_ok = successes.iloc[-1]
                st.caption(
                    f"Poslední **úspěšné** stažení: {_fmt_ts(last_ok['timestamp'])} "
                    f"(report_date {last_ok['report_date']})"
                )
            else:
                st.caption("V logu zatím žádné úspěšné stažení.")

            last = v_status.iloc[-1]
            if not bool(last["success"]):
                reason = last.get("error") or f"HTTP {last.get('http_status')}"
                st.warning(f"⚠️ Poslední pokus ({_fmt_ts(last['timestamp'])}) selhal — {reason}")


def _render_commodity_section(df: pd.DataFrame, venue: str, product_code: str) -> None:
    fig = fig_cot_positioning(df, venue, product_code)
    st.plotly_chart(fig, use_container_width=True)

    kpi = compute_financial_net_position(df, venue, product_code)
    with st.expander("📊 Finanční net pozice (spekulanti) — percentil vůči historii"):
        if kpi is None:
            st.caption("Data nejsou dostupná.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Aktuální net pozice", f"{kpi['current_value']:+,.0f} {kpi['unit_label']}")
            c2.metric("Percentil vůči historii", f"{kpi['percentile']:.0f} %")
            c3.metric("Délka historie", f"{kpi['n_weeks']} týdnů")
            st.caption(
                "Net = (Investment Firms/Credit Institutions + Investment Funds + "
                "Other Financial Institutions), Long − Short, scope=Other "
                "(směrové/spekulativní pozice, ne Risk_Reducing hedging) — analog "
                f"„spekulantů“ z klasického CFTC COT reportu. Stav k {kpi['current_date']}."
            )
            st.line_chart(kpi["history"])


def render_cot_page() -> None:
    st.markdown(
        '<div class="banner banner-ok">'
        '<div class="banner-left"><span class="pulse-dot"></span>'
        '<span>📈 PP DASHBOARD — CoT (ICE MiFID II)</span></div>'
        '<div class="banner-center"></div>'
        '<div class="banner-right">'
        + pd.Timestamp.now(tz="Europe/Prague").strftime("%a %d.%m.%Y · %H:%M:%S") +
        '</div></div>',
        unsafe_allow_html=True,
    )

    df = load_ice_cot()
    status_df = load_ice_cot_status()

    _render_status_panel(df, status_df)
    st.markdown("---")

    if df.empty:
        st.warning("Žádná ICE MiFID II CoT data v úložišti — spusť scripts/backfill_ice_cot.py.")
        return

    all_products = (
        df[["venue", "product_code", "product_name"]]
        .drop_duplicates()
        .sort_values(["venue", "product_name"])
        .reset_index(drop=True)
    )

    tab_labels = [label for _, _, label in DEFAULT_COMMODITIES] + ["🔍 Vlastní výběr"]
    tabs = st.tabs(tab_labels)

    for (venue, code, _label), tab in zip(DEFAULT_COMMODITIES, tabs[:-1]):
        with tab:
            _render_commodity_section(df, venue, code)

    with tabs[-1]:
        options = list(all_products.itertuples(index=False, name="Product"))
        sel = st.selectbox(
            f"Komodita (všech {len(options)} dostupných)",
            options=options,
            format_func=lambda row: f"{row.venue} / {row.product_name} ({row.product_code})",
            key="cot_custom_select",
        )
        if sel is not None:
            _render_commodity_section(df, sel.venue, sel.product_code)
