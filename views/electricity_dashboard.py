import pandas as pd
import streamlit as st

from config import data_status_row
from data.ceps import (
    fetch_ceps_imbalance, fetch_ceps_svr, fetch_ceps_imbalance_price, fetch_ceps_all,
)
from data.entsoe import fetch_activation_prices, fetch_wind_solar_forecast
from charts.imbalance import (
    fig_ceps_combined, fig_ceps_svr,
    fig_activation_prices,
    balancing_strategy_ema, fig_balancing_strategy,
)
from charts.generation import (
    fig_generation_area, fig_generation_donut,
    fig_wind_solar_forecast, fig_load, render_mix_legend,
)
from charts.reserves import fig_reserve_volumes, fig_reserve_prices


def render_dashboard_tab(now, df_imbal, gen_raw, load_fc, reserves):
    st.markdown('<div class="section-title">Systémová odchylka + zatížení + cena odchylky — ČEPS</div>',
                unsafe_allow_html=True)
    df_ceps_imbal, now_ceps = fetch_ceps_imbalance()
    df_ceps_price = fetch_ceps_imbalance_price()
    ceps_d = fetch_ceps_all()
    _last_ceps = (
        pd.Timestamp(ceps_d["now"].tz_convert("Europe/Prague").date())
        if isinstance(ceps_d, dict) and ceps_d.get("now") is not None
        else None
    )
    _now_naive = now.tz_localize(None) if now.tzinfo is not None else now
    data_status_row([
        {"name": "ČEPS",
         "date": _last_ceps,
         "freshness_hours": 1, "source": "ČEPS SOAP"},
        {"name": "ENTSO-E",
         "date": _now_naive,
         "freshness_hours": 48, "source": "ENTSO-E"},
        {"name": "DAP",
         "date": _now_naive,
         "freshness_hours": 48, "source": "ENTSO-E"},
    ])
    _load_col = ("Load including pumping [MW]"
                 if "Load including pumping [MW]" in ceps_d["load"].columns
                 else "Load [MW]"
                 if "Load [MW]" in ceps_d["load"].columns
                 else None)
    ceps_load_series = (ceps_d["load"][_load_col]
                        if _load_col else pd.Series(dtype=float))
    st.plotly_chart(
        fig_ceps_combined(df_ceps_imbal, df_ceps_price, ceps_load_series, load_fc, now_ceps),
        use_container_width=True, config={"displayModeBar": False},
    )

    st.markdown('<div class="section-title">Aktivace SVR v ČR — ČEPS (minutová)</div>',
                unsafe_allow_html=True)
    df_svr = fetch_ceps_svr()
    st.plotly_chart(fig_ceps_svr(df_svr, now_ceps),
                    use_container_width=True, config={"displayModeBar": False})

    st.markdown('<div class="section-title">Balancing strategie</div>', unsafe_allow_html=True)
    st.info(
        "ℹ️ Data systémové odchylky mají zpoždění ~15 min. "
        "EMA (Exponential Moving Average) dává větší váhu posledním intervalům "
        "a slouží jako proxy pro odhad aktuálního stavu soustavy. "
        "Zákazníci v balancing segmentu pomáhají síti a jsou za to benefitováni."
    )
    st.subheader("⚡ Balancing strategie (EMA predikce)")
    _bc1, _bc2, _bc3 = st.columns(3)
    with _bc1:
        ema_periods = st.slider("EMA okno [ISP]", 1, 8, 4,
                                help="Počet 5min intervalů pro EMA. 4 = 20 minut.")
    with _bc2:
        threshold_mw = st.slider("Práh zásahu [MWh]", 10, 150, 50,
                                 help="Minimální predikovaná odchylka pro aktivaci signálu. "
                                      "Vyšší = méně zásahů, nižší = agresivnější balancing.")
    with _bc3:
        benefit_eur_mwh = st.number_input("Benefit zákazníka [EUR/MWh]", value=8.0,
                                          help="Kolik EUR/MWh zákazník vydělá za pomoc síti.")

    if not df_ceps_imbal.empty:
        imbal_5min = df_ceps_imbal["odchylka_MW"].resample("5min").mean().dropna()
        _ema, _signal = balancing_strategy_ema(imbal_5min, ema_periods, threshold_mw)
    elif not df_imbal.empty:
        imbal_5min = df_imbal["odchylka_MWh"]
        _ema, _signal = balancing_strategy_ema(imbal_5min, ema_periods, threshold_mw)
    if not df_ceps_imbal.empty or not df_imbal.empty:
        st.plotly_chart(
            fig_balancing_strategy(df_imbal, _ema, _signal, threshold_mw, now),
            use_container_width=True, config={"displayModeBar": False},
        )
        _n_int = int((_signal != "STANDBY").sum())
        _benefit = _n_int * 0.25 * benefit_eur_mwh
        _bm1, _bm2 = st.columns(2)
        _bm1.metric("Počet zásahů dnes", _n_int)
        _bm2.metric("Odhadovaný benefit zákazníka", f"{_benefit:.2f} EUR/den")
    else:
        st.info("Data odchylky nejsou dostupná.")

    st.markdown('<div class="section-title">Ceny aktivace záložních rezerv</div>',
                unsafe_allow_html=True)
    df_act = fetch_activation_prices()
    st.plotly_chart(fig_activation_prices(df_act, now), use_container_width=True,
                    config={"displayModeBar": False})

    st.markdown('<div class="section-title">Zatížení — skutečnost vs. prognóza D+1</div>',
                unsafe_allow_html=True)
    if load_fc.empty:
        st.info("Data zatížení nejsou dostupná.")
    else:
        st.plotly_chart(
            fig_load(load_fc, ceps_load_series, ceps_d["gen"], now),
            use_container_width=True, config={"displayModeBar": False},
        )

    st.markdown('<div class="section-title">Forecast solární výroby [MW] | D0 + D+1</div>',
                unsafe_allow_html=True)
    ws_raw = fetch_wind_solar_forecast()
    st.plotly_chart(
        fig_wind_solar_forecast(ws_raw, now, gen_raw=gen_raw),
        use_container_width=True, config={"displayModeBar": False},
    )

    st.markdown('<div class="section-title">Generace podle zdroje · Aktuální mix</div>',
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 1.2, 1.2])
    with c1:
        if gen_raw.empty:
            st.info("Data generace nejsou dostupná.")
        else:
            st.plotly_chart(fig_generation_area(gen_raw, now), use_container_width=True,
                            config={"displayModeBar": False})
    with c2:
        st.plotly_chart(fig_generation_donut(gen_raw), use_container_width=True,
                        config={"displayModeBar": False})
    with c3:
        st.markdown('<div class="section-title">Mix</div>', unsafe_allow_html=True)
        st.markdown(render_mix_legend(gen_raw), unsafe_allow_html=True)

    st.markdown('<div class="section-title">aFRR + mFRR — D0 (objemy a ceny)</div>',
                unsafe_allow_html=True)
    _d0_start = now.normalize()
    _d0_end   = now.normalize() + pd.Timedelta(days=1)
    rd1, rd2  = st.columns(2)
    with rd1:
        st.plotly_chart(
            fig_reserve_volumes(reserves, now, _d0_start, _d0_end, height=300),
            use_container_width=True, config={"displayModeBar": False},
        )
    with rd2:
        st.plotly_chart(
            fig_reserve_prices(reserves, now, _d0_start, _d0_end, height=300),
            use_container_width=True, config={"displayModeBar": False},
        )
