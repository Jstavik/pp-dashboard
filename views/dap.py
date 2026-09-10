import pandas as pd
import streamlit as st

from data.entsoe import fetch_dap
from charts.reserves import (
    fig_reserve_volumes, fig_reserve_prices,
    fig_dap, calc_dap_stats, simulate_battery_dap, fig_battery_strategy,
)


def render_dap_tab(now, reserves, bat_capacity_kwh, bat_power_kw, max_cycles, cycle_cost, hold_enabled):
    s_d0 = fetch_dap(0)
    s_d1 = fetch_dap(1)
    st.plotly_chart(fig_dap(s_d0, s_d1, now), use_container_width=True,
                    config={"displayModeBar": False})
    c_l, c_r = st.columns(2)

    def _stat_table(stats, label):
        rows = [("Base",     stats["base"]),
                ("Peak 8-20", stats["peak"]),
                ("Off-peak",  stats["offpeak"]),
                ("Min",       stats["min"]),
                ("Max",       stats["max"])]
        st.markdown(f"**{label}**")
        for lbl, val in rows:
            v = f"{val:.2f} EUR" if val is not None else "—"
            st.markdown(f"- {lbl}: **{v}**")

    with c_l:
        _stat_table(calc_dap_stats(s_d0), f"D0 — {now.strftime('%d.%m.%Y')}")
    with c_r:
        _stat_table(calc_dap_stats(s_d1), f"D+1 — {(now+pd.Timedelta(days=1)).strftime('%d.%m.%Y')}")

    st.markdown('<div class="section-title">aFRR + mFRR — D0 + D+1 (objemy a ceny)</div>',
                unsafe_allow_html=True)
    dap_start = now.normalize()
    dap_end   = now.normalize() + pd.Timedelta(days=2)
    rc1, rc2  = st.columns(2)
    with rc1:
        st.plotly_chart(
            fig_reserve_volumes(reserves, now, dap_start, dap_end, height=320),
            use_container_width=True, config={"displayModeBar": False},
        )
    with rc2:
        st.plotly_chart(
            fig_reserve_prices(reserves, now, dap_start, dap_end, height=320),
            use_container_width=True, config={"displayModeBar": False},
        )

    st.markdown('<div class="section-title">Strategie baterie</div>', unsafe_allow_html=True)
    st.info(
        "ℹ️ Strategie nabíjí baterii při nízkých cenách a vybíjí při vysokých. "
        "Cena cyklování zahrnuje degradaci baterie a kompenzaci zákazníkovi. "
        "Strategie cykluje maximálně N×/den aby chránila životnost baterie."
    )
    _prices_combined = pd.concat([s_d0, s_d1]).sort_index().dropna()
    if not _prices_combined.empty:
        _avg = float(_prices_combined.mean())
        _low = _avg - cycle_cost / 2
        _hig = _avg + cycle_cost / 2
        _df_sim, _cycles_done = simulate_battery_dap(
            _prices_combined, bat_capacity_kwh, bat_power_kw,
            max_cycles, cycle_cost, hold_enabled,
        )
        st.plotly_chart(
            fig_battery_strategy(_df_sim, _low, _hig, _avg, now),
            use_container_width=True, config={"displayModeBar": False},
        )
        _total_rev = float(_df_sim["revenue_eur"].sum())
        m1, m2, m3 = st.columns(3)
        m1.metric("Celkový výnos D0+D+1", f"{_total_rev:.2f} EUR")
        m2.metric("Počet cyklů", f"{_cycles_done:.1f} / {max_cycles}")
        m3.metric("Výnos vs. bez strategie", f"{_total_rev:+.2f} EUR",
                  help="Porovnání s pasivní strategií (baterie nečinná)")
    else:
        st.info("DAP data nejsou dostupná pro simulaci.")
